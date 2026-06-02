"""
パフォーマンス分析スクリプト（アナリスト）
publish_log.json を分析して analytics_insights.json を更新し週次・月次レポートを生成する
"""

from __future__ import annotations

import json
import os
import re
import time
import requests
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"


def call_gemini(prompt: str, retries: int = 3) -> str:
    key = os.environ["GEMINI_API_KEY"]
    url = GEMINI_URL.format(key=key)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(retries):
        resp = requests.post(url, json=payload, timeout=90)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  [WAIT] レート制限 → {wait}秒待機...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini APIのレート制限が続いています。")


def calc_engagement_rate(imp: int, like: int, rt: int, reply: int) -> float:
    if not imp:
        return 0.0
    return round((like + rt * 2 + reply * 3) / imp * 100, 2)


def analyze_logs(logs: List[Dict]) -> Dict:
    """ルールベースの統計分析"""
    post_logs = [l for l in logs if l.get("type") != "followers" and l.get("impressions")]

    if not post_logs:
        return {"has_data": False}

    # 投稿タイプ別パフォーマンス
    type_stats = defaultdict(lambda: {"count": 0, "total_imp": 0, "total_like": 0, "total_rt": 0, "total_eng": 0.0})
    for log in post_logs:
        t = log.get("type", "不明")
        imp = log.get("impressions", 0) or 0
        like = log.get("likes", 0) or 0
        rt = log.get("retweets", 0) or 0
        reply = log.get("replies", 0) or 0
        eng = calc_engagement_rate(imp, like, rt, reply)
        type_stats[t]["count"] += 1
        type_stats[t]["total_imp"] += imp
        type_stats[t]["total_like"] += like
        type_stats[t]["total_rt"] += rt
        type_stats[t]["total_eng"] += eng

    # タイプ別平均エンゲージメント率でランキング
    type_ranking = sorted(
        [{"type": t, "avg_eng": round(v["total_eng"] / v["count"], 2), "count": v["count"]}
         for t, v in type_stats.items()],
        key=lambda x: x["avg_eng"], reverse=True
    )

    # 時間帯別パフォーマンス
    time_stats = defaultdict(lambda: {"count": 0, "total_eng": 0.0})
    for log in post_logs:
        hour = log.get("scheduled_time", "00:00")[:2]
        imp = log.get("impressions", 0) or 0
        like = log.get("likes", 0) or 0
        rt = log.get("retweets", 0) or 0
        reply = log.get("replies", 0) or 0
        eng = calc_engagement_rate(imp, like, rt, reply)
        time_stats[hour]["count"] += 1
        time_stats[hour]["total_eng"] += eng

    time_ranking = sorted(
        [{"hour": f"{h}時台", "avg_eng": round(v["total_eng"] / v["count"], 2)}
         for h, v in time_stats.items()],
        key=lambda x: x["avg_eng"], reverse=True
    )

    # フォロワー推移
    follower_logs = [l for l in logs if l.get("type") == "followers"]
    follower_trend = [{"date": l["recorded_at"][:10], "count": l["count"]} for l in follower_logs]

    # 全体サマリー
    total_imp = sum(l.get("impressions", 0) or 0 for l in post_logs)
    total_like = sum(l.get("likes", 0) or 0 for l in post_logs)
    total_rt = sum(l.get("retweets", 0) or 0 for l in post_logs)

    return {
        "has_data": True,
        "post_count": len(post_logs),
        "total_impressions": total_imp,
        "total_likes": total_like,
        "total_retweets": total_rt,
        "avg_impressions": round(total_imp / len(post_logs)) if post_logs else 0,
        "best_post_types": type_ranking,
        "best_time_slots": time_ranking,
        "follower_trend": follower_trend,
    }


def generate_ai_insights(stats: Dict, existing_insights: Dict) -> Dict:
    """Geminiで改善提案を生成"""
    prompt = f"""就活Xアカウント @kansai_job_ のパフォーマンス分析をしてください。

## 統計データ
投稿数: {stats['post_count']}本
総インプレッション: {stats['total_impressions']}
総いいね: {stats['total_likes']}
総RT: {stats['total_retweets']}
平均インプレッション: {stats['avg_impressions']}

投稿タイプ別エンゲージメント率:
{json.dumps(stats['best_post_types'], ensure_ascii=False)}

時間帯別エンゲージメント率:
{json.dumps(stats['best_time_slots'], ensure_ascii=False)}

以下の形式でJSONのみ返してください（コードブロックなし）:
{{
  "latest_report": "分析レポートの文章（300字程度）",
  "tone_feedback": {{
    "what_works": "効いているトーン・表現の特徴",
    "what_to_improve": "改善すべき点"
  }},
  "topic_insights": {{
    "hot_topics": ["反応が良いトピック3つ"],
    "avoid_topics": ["反応が悪いトピック"]
  }},
  "action_items": ["次週に試すべきこと3つ"]
}}"""

    try:
        text = call_gemini(prompt).strip()
        if "```" in text:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            text = match.group(0) if match else text
        return json.loads(text)
    except Exception as e:
        print(f"  [WARN] AI分析失敗: {e}")
        return {
            "latest_report": f"分析データ {stats['post_count']}本。平均インプレッション {stats['avg_impressions']}。",
            "tone_feedback": {},
            "topic_insights": {},
            "action_items": [],
        }


def main(mode: str = "daily"):
    print(f"=== パフォーマンス分析開始（{mode}） ===")

    log_path = DATA_DIR / "publish_log.json"
    if not log_path.exists():
        print("publish_log.json が見つかりません。")
        return

    with open(log_path, encoding="utf-8") as f:
        logs = json.load(f)

    stats = analyze_logs(logs)
    if not stats["has_data"]:
        print("パフォーマンスデータがまだありません。!stats コマンドでデータを入力してください。")
        return

    print(f"分析対象: {stats['post_count']}本")
    print(f"総インプレッション: {stats['total_impressions']}")
    print(f"平均インプレッション: {stats['avg_impressions']}")

    # AI分析
    print("\nGemini で改善提案を生成中...")
    existing = {}
    insights_path = DATA_DIR / "analytics_insights.json"
    if insights_path.exists():
        with open(insights_path, encoding="utf-8") as f:
            existing = json.load(f)

    ai_insights = generate_ai_insights(stats, existing)

    # analytics_insights.json を更新
    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": mode,
        **stats,
        **ai_insights,
    }

    with open(insights_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("  → analytics_insights.json を更新しました")

    # 週次・月次レポートを保存
    if mode == "weekly":
        week = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = DATA_DIR / f"weekly_report_{week}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"  → {report_path.name} を保存しました")

    elif mode == "monthly":
        month = datetime.now(timezone.utc).strftime("%Y%m")
        report_path = DATA_DIR / f"monthly_report_{month}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"  → {report_path.name} を保存しました")

    print(f"\n=== 完了 ===")
    print(f"レポート: {ai_insights.get('latest_report', '')[:100]}...")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    main(mode)
