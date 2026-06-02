"""
投稿品質チェック・スケジュール設定スクリプト
posts.json をチェックして推奨投稿時刻を付与し reviewed_posts.json に保存する
"""

from __future__ import annotations

import json
import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"

# 投稿時刻スロット（大学生のアクティブ時間帯）
TIME_SLOTS = [
    {"time": "07:30", "label": "平日朝（通学電車）", "weekday_only": True},
    {"time": "08:00", "label": "平日朝（通学電車）", "weekday_only": True},
    {"time": "12:00", "label": "昼休み", "weekday_only": False},
    {"time": "12:30", "label": "昼休み", "weekday_only": False},
    {"time": "16:00", "label": "授業終わり", "weekday_only": True},
    {"time": "17:00", "label": "授業終わり", "weekday_only": True},
    {"time": "21:00", "label": "夜（ゴールデンタイム）", "weekday_only": False},
    {"time": "21:30", "label": "夜（ゴールデンタイム）", "weekday_only": False},
    {"time": "22:00", "label": "夜（ゴールデンタイム）", "weekday_only": False},
    {"time": "22:30", "label": "夜（ゴールデンタイム）", "weekday_only": False},
]


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
    raise RuntimeError("Gemini APIのレート制限が続いています。後で再実行してください。")


def get_tomorrow_date() -> str:
    """翌日の日付を返す（パイプラインは前日20時に実行するため）"""
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    # JSTに変換（UTC+9）
    jst = tomorrow + timedelta(hours=9)
    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
    return f"{jst.month}/{jst.day}（{weekday_names[jst.weekday()]}）"


def is_weekday_tomorrow() -> bool:
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    jst = tomorrow + timedelta(hours=9)
    return jst.weekday() < 5  # 0〜4が平日


def assign_time_slots(posts: List[Dict]) -> List[Dict]:
    """投稿に推奨時刻を割り当てる"""
    tomorrow = get_tomorrow_date()
    is_weekday = is_weekday_tomorrow()

    # 利用可能なスロットを絞る
    available = [s for s in TIME_SLOTS if not s["weekday_only"] or is_weekday]

    # 投稿数に合わせてスロットを選ぶ
    n = len(posts)
    if n <= len(available):
        selected_slots = available[:n]
    else:
        # スロット不足の場合は夜帯を増やす
        selected_slots = available + [{"time": "23:00", "label": "深夜"}] * (n - len(available))

    for i, post in enumerate(posts):
        slot = selected_slots[i] if i < len(selected_slots) else {"time": "22:00", "label": "夜"}
        post["scheduled_date"] = tomorrow
        post["scheduled_time"] = slot["time"]
        post["time_label"] = slot.get("label", "")

    return posts


def rule_based_check(post: Dict, tone: Dict, all_posts: List[Dict]) -> Dict:
    """ルールベースのチェック（API不要）"""
    issues = []
    content = post.get("content", "")

    # 文字数チェック
    char_count = len(content)
    if char_count > 500:
        issues.append(f"文字数超過（{char_count}字）")

    # URL含有チェック
    if re.search(r"https?://", content):
        issues.append("URLが含まれています（プロフィールのリンクから、に変更してください）")

    # NG表現チェック
    ng_words = tone.get("ng_expressions", [])
    for ng in ng_words:
        if ng in content:
            issues.append(f"NG表現を含む: {ng}")

    # 同じ型が3連続していないか
    current_type = post.get("type", "")
    idx = next((i for i, p in enumerate(all_posts) if p.get("post_id") == post.get("post_id")), -1)
    if idx >= 2:
        prev_types = [all_posts[idx-2].get("type"), all_posts[idx-1].get("type")]
        if prev_types[0] == current_type and prev_types[1] == current_type:
            issues.append(f"同じ型（{current_type}）が3連続しています")

    return {
        "status": "NG" if issues else "OK",
        "issues": issues,
    }


def ai_review(posts: List[Dict], tone: Dict, verified_news: Dict) -> List[Dict]:
    """Geminiで品質チェック（バッチ処理）"""
    posts_text = "\n---\n".join([
        f"投稿{i+1}（{p.get('type','?')}）:\n{p.get('content','')}"
        for i, p in enumerate(posts)
    ])

    prompt = f"""以下の就活Xアカウント投稿案を品質チェックしてください。

ターゲット: 関西の大学1〜3年生（28卒〜30卒）
アカウント: @kansai_job_

投稿一覧:
{posts_text}

各投稿について以下の観点でチェックし、JSONのみ返してください（コードブロックなし）:
1. 炎上リスク（誤解を招く表現・センシティブな内容がないか）
2. ターゲット適合性（大学1〜3年生に響くか）
3. CTA投稿は売り込み感が強すぎないか

形式:
[
  {{"index": 0, "ok": true, "feedback": "問題なし or 改善点を1行で"}},
  ...
]"""

    try:
        text = call_gemini(prompt).strip()
        if "```" in text:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            text = match.group(0) if match else text
        return json.loads(text)
    except Exception as e:
        print(f"  [WARN] AI品質チェック失敗: {e} → ルールチェックのみで進めます")
        return [{"index": i, "ok": True, "feedback": "AI確認スキップ"} for i in range(len(posts))]


def main():
    print("=== 投稿品質チェック・スケジュール設定開始 ===")

    posts_path = DATA_DIR / "posts.json"
    if not posts_path.exists():
        print("ERROR: posts.json が見つかりません。generate_posts.py を先に実行してください。")
        return

    with open(posts_path, encoding="utf-8") as f:
        posts_data = json.load(f)

    posts = posts_data.get("posts", [])
    print(f"チェック対象: {len(posts)}本")

    tone = {}
    tone_path = DATA_DIR / "tone_guide.json"
    if tone_path.exists():
        with open(tone_path, encoding="utf-8") as f:
            tone = json.load(f)

    verified_news = {}
    verified_path = DATA_DIR / "verified_news.json"
    if verified_path.exists():
        with open(verified_path, encoding="utf-8") as f:
            verified_news = json.load(f)

    # ルールベースチェック
    print("\n[1] ルールベースチェック中...")
    for post in posts:
        result = rule_based_check(post, tone, posts)
        post["rule_check"] = result

    # AI品質チェック
    print("\n[2] AI品質チェック中...")
    ai_results = ai_review(posts, tone, verified_news)
    for r in ai_results:
        idx = r.get("index", -1)
        if 0 <= idx < len(posts):
            posts[idx]["ai_feedback"] = r.get("feedback", "")
            posts[idx]["ai_ok"] = r.get("ok", True)

    # ステータス確定（ルールNGまたはAIがNGなら除外）
    ok_posts = []
    ng_posts = []
    for post in posts:
        rule_ok = post.get("rule_check", {}).get("status") == "OK"
        ai_ok = post.get("ai_ok", True)
        if rule_ok and ai_ok:
            post["status"] = "OK"
            ok_posts.append(post)
        else:
            post["status"] = "NG"
            ng_posts.append(post)

    print(f"\n  OK: {len(ok_posts)}本 / NG: {len(ng_posts)}本")

    # 推奨投稿時刻を割り当て
    print("\n[3] 投稿時刻を割り当て中...")
    ok_posts = assign_time_slots(ok_posts)

    # 結果を保存
    output = {
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "ok_count": len(ok_posts),
        "ng_count": len(ng_posts),
        "posts": ok_posts + ng_posts,
    }

    out_path = DATA_DIR / "reviewed_posts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"承認候補: {len(ok_posts)}本")
    for p in ok_posts:
        print(f"  {p.get('scheduled_date')} {p.get('scheduled_time')} [{p.get('type','?')}] {p.get('content','')[:40]}...")
    if ng_posts:
        print(f"除外（NG）: {len(ng_posts)}本")
    print(f"保存先: {out_path}")


if __name__ == "__main__":
    main()
