"""
競合分析・トーン分析スクリプト
NitterRSSで競合アカウントの投稿を取得し、Claudeでパターン分析してtone_guide.jsonを生成する
"""

from __future__ import annotations

import feedparser
import json
import os
import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

# Nitterインスタンス（上から順に試してフォールバック）
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

MAX_POSTS_PER_ACCOUNT = 20
REQUEST_TIMEOUT = 10


def fetch_nitter_rss(username: str) -> List[Dict]:
    """NitterのRSSフィードから投稿を取得する（複数インスタンスにフォールバック）"""
    for instance in NITTER_INSTANCES:
        url = f"{instance}/{username}/rss"
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
            if feed.bozo and not feed.entries:
                continue

            posts = []
            for entry in feed.entries[:MAX_POSTS_PER_ACCOUNT]:
                title = getattr(entry, "title", "")
                summary = getattr(entry, "summary", title)
                # HTMLタグを除去
                clean_text = re.sub(r"<[^>]+>", "", summary).strip()
                if clean_text:
                    posts.append({
                        "username": username,
                        "text": clean_text[:500],
                        "url": getattr(entry, "link", ""),
                        "published_at": getattr(entry, "published", ""),
                        "source": "nitter",
                    })

            if posts:
                print(f"  [OK] @{username}: {len(posts)}件取得 ({instance})")
                return posts

        except Exception as e:
            print(f"  [RETRY] @{username} {instance}: {e}")
            time.sleep(2)
            continue

    print(f"  [SKIP] @{username}: 全インスタンスで取得失敗")
    return []


def load_seed_posts() -> List[Dict]:
    """手動で追加した競合投稿サンプルを読み込む"""
    seeds_path = DATA_DIR / "competitor_seeds.json"
    if not seeds_path.exists():
        return []
    with open(seeds_path, encoding="utf-8") as f:
        data = json.load(f)
    samples = data.get("samples", [])
    print(f"  [OK] シードデータ: {len(samples)}件")
    return samples


def load_analytics_insights() -> dict:
    """分析班のフィードバックを読み込む（存在する場合）"""
    insights_path = DATA_DIR / "analytics_insights.json"
    if not insights_path.exists():
        return {}
    with open(insights_path, encoding="utf-8") as f:
        return json.load(f)


def call_gemini(prompt: str, retries: int = 3) -> str:
    """Gemini REST APIを直接呼び出してテキストを返す（レート制限時は自動リトライ）"""
    api_key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(retries):
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  [WAIT] レート制限 → {wait}秒待機して再試行...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini APIのレート制限が続いています。しばらく後に再実行してください。")


def analyze_with_gemini(posts: List[Dict], insights: dict) -> dict:
    """Geminiで競合投稿のパターン分析とトーンガイドを生成する"""

    posts_text = "\n---\n".join([
        f"投稿{i+1}:\n{p['text']}"
        for i, p in enumerate(posts[:50])  # 最大50件
    ])

    insights_text = ""
    if insights:
        insights_text = f"""
## 過去の分析からのフィードバック（必ず反映すること）

伸びている投稿タイプ: {json.dumps(insights.get('best_post_types', []), ensure_ascii=False)}
トーンフィードバック: {json.dumps(insights.get('tone_feedback', {}), ensure_ascii=False)}
反応が良いトピック: {json.dumps(insights.get('topic_insights', {}), ensure_ascii=False)}
"""

    prompt = f"""あなたはXアカウント運用の専門家です。
関西の就活生（大学1〜3年生、28卒〜30卒）向けアカウント @kansai_job_ のための分析をしてください。

以下の就活系Xアカウントの投稿サンプルを分析してください：

{posts_text}

{insights_text}

以下の形式でJSONを返してください（コードブロックなし、JSONのみ）：

{{
  "analysis_summary": "競合投稿全体の傾向をまとめた文章（200字程度）",
  "patterns": {{
    "hook_patterns": ["効果的な1行目の型を3〜5個リストアップ"],
    "length_tendency": "投稿の長さの傾向",
    "emoji_usage": "絵文字の使い方の傾向",
    "cta_patterns": ["よく使われているCTAの型を2〜3個"],
    "popular_formats": ["バズりやすいフォーマットを3〜5個"]
  }},
  "tone_guide": {{
    "recommended_tone": "ターゲット（関西の大学1〜3年生）に響くトーンの説明",
    "voice": "文体の特徴（例：就活の先輩がフランクに教えてくれる感じ）",
    "language_style": "言葉遣いの特徴",
    "emoji_rule": "絵文字の使い方ルール",
    "good_expressions": ["効果的な表現パターンを5個"],
    "ng_expressions": ["避けるべき表現を3〜5個"],
    "hook_templates": [
      "使える1行目テンプレートを5〜8個（{{変数}}で穴埋め部分を示す）"
    ]
  }},
  "kansai_specific": {{
    "local_nuances": "関西ターゲットに特有の表現・ニュアンスの注意点",
    "local_topics": ["関西ならではのネタ・話題の提案を3個"]
  }}
}}"""

    text = call_gemini(prompt).strip()

    # JSONのみを抽出（コードブロックが含まれる場合は除去）
    if "```" in text:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        text = match.group(0) if match else text

    return json.loads(text)


def main():
    print("=== 競合分析・トーン分析開始 ===")

    # 競合アカウント一覧を読み込む
    accounts_path = DATA_DIR / "competitor_accounts.json"
    with open(accounts_path, encoding="utf-8") as f:
        accounts_data = json.load(f)

    active_accounts = [a for a in accounts_data["accounts"] if a.get("active", True)]

    # Nitter RSSで投稿を取得
    print("\n[1] Nitter RSSで競合投稿を取得中...")
    all_posts = []
    for account in active_accounts:
        posts = fetch_nitter_rss(account["username"])
        all_posts.extend(posts)
        time.sleep(1)

    # シードデータを追加
    print("\n[2] シードデータを読み込み中...")
    seed_posts = load_seed_posts()
    all_posts.extend(seed_posts)

    if not all_posts:
        print("⚠ 投稿データが0件です。competitor_seeds.jsonにサンプルを追加するか、competitor_accounts.jsonのusernameを確認してください。")
        # データがなくてもデフォルトのトーンガイドを生成する
        all_posts = [{"text": "（サンプルデータなし）", "username": "default"}]

    # 過去の分析フィードバックを読み込む
    print("\n[3] 過去のフィードバックを確認中...")
    insights = load_analytics_insights()
    if insights:
        print(f"  [OK] analytics_insights.json を参照します")
    else:
        print(f"  [INFO] analytics_insights.json なし（初回実行）")

    # Geminiで分析（失敗時は既存のtone_guide.jsonを維持）
    print(f"\n[4] Gemini で分析中（投稿{len(all_posts)}件）...")
    try:
        analysis = analyze_with_gemini(all_posts, insights)
    except Exception as e:
        print(f"  [WARN] Gemini API 失敗: {e}")
        print(f"  [INFO] 既存の tone_guide.json を維持します")
        analysis = None

    if analysis is None:
        print(f"\n=== 完了（Gemini API 一時停止中のためトーンガイド更新スキップ） ===")
        return

    # competitor_analysis.json を保存
    competitor_output = {
        "analyzed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "post_count": len(all_posts),
        "accounts_analyzed": [a["username"] for a in active_accounts],
        "seed_count": len(seed_posts),
        "analysis_summary": analysis.get("analysis_summary", ""),
        "patterns": analysis.get("patterns", {}),
        "kansai_specific": analysis.get("kansai_specific", {}),
    }

    competitor_path = DATA_DIR / "competitor_analysis.json"
    with open(competitor_path, "w", encoding="utf-8") as f:
        json.dump(competitor_output, f, ensure_ascii=False, indent=2)
    print(f"  → competitor_analysis.json を保存しました")

    # tone_guide.json を保存
    tone_guide = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "version": 1,
        **analysis.get("tone_guide", {}),
    }

    tone_path = DATA_DIR / "tone_guide.json"
    # 既存のtone_guide.jsonがあればバージョンをインクリメント
    if tone_path.exists():
        with open(tone_path, encoding="utf-8") as f:
            existing = json.load(f)
        tone_guide["version"] = existing.get("version", 1) + 1

    with open(tone_path, "w", encoding="utf-8") as f:
        json.dump(tone_guide, f, ensure_ascii=False, indent=2)
    print(f"  → tone_guide.json を保存しました（v{tone_guide['version']}）")

    print(f"\n=== 完了 ===")
    print(f"分析投稿数: {len(all_posts)}件")
    print(f"トーンガイドバージョン: v{tone_guide['version']}")


if __name__ == "__main__":
    main()
