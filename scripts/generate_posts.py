"""
投稿文生成スクリプト
verified_news.json・tone_guide.json・competitor_analysis.json を元に
8〜10本の投稿案を生成して data/posts.json に保存する
"""

from __future__ import annotations

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

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"

POST_TYPES = [
    "ニュース速報型",
    "データ提示型",
    "問いかけ型",
    "ノウハウ型",
    "共感型",
    "CTA型",
]


def call_gemini(prompt: str, retries: int = 4) -> str:
    key = os.environ["GEMINI_API_KEY"]
    url = GEMINI_URL.format(key=key)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(retries):
        resp = requests.post(url, json=payload, timeout=90)
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)  # 60→120→180→240秒
            print(f"  [WAIT] レート制限 → {wait}秒待機...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini APIのレート制限が続いています。後で再実行してください。")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_prompt(news_items: List[Dict], tone: Dict, competitor: Dict, insights: Dict, archive_titles: List[str]) -> str:
    # ニュースをテキスト化（最大15件）
    news_text = "\n".join([
        f"[{i+1}] [{item['trust_score']}] {item['title']}\n    出典: {item['source']} / 要約: {item['summary'][:80]}"
        for i, item in enumerate(news_items[:10])  # 15→10件に削減（プロンプト軽量化）
    ])

    # トーンガイドのポイントを抽出
    tone_points = f"""
- トーン: {tone.get('recommended_tone', '就活の先輩がフランクに教えてくれる感じ')}
- 言葉遣い: {tone.get('language_style', '敬語ベース、堅すぎない')}
- 絵文字: {tone.get('emoji_rule', '0〜1個、控えめ')}
- 効果的な表現: {', '.join(tone.get('good_expressions', [])[:3])}
- 避ける表現: {', '.join(tone.get('ng_expressions', [])[:3])}
- フックテンプレート例: {', '.join(tone.get('hook_templates', [])[:3])}
"""

    # 競合パターン
    patterns_text = ""
    if competitor and competitor.get("patterns"):
        p = competitor["patterns"]
        patterns_text = f"""
競合アカウントで伸びているパターン:
- フック: {', '.join(p.get('hook_patterns', [])[:3])}
- フォーマット: {', '.join(p.get('popular_formats', [])[:3])}
"""

    # 過去の分析フィードバック
    insights_text = ""
    if insights:
        insights_text = f"""
過去の分析フィードバック（必ず反映すること）:
- 伸びている投稿タイプ: {json.dumps(insights.get('best_post_types', []), ensure_ascii=False)}
- 反応が良いトピック: {json.dumps(insights.get('topic_insights', {}), ensure_ascii=False)}
"""

    # 過去投稿（重複防止）
    archive_text = ""
    if archive_titles:
        archive_text = f"\n過去に投稿済み（類似内容は避けること）:\n" + "\n".join(f"- {t}" for t in archive_titles[:10])

    today = datetime.now(timezone.utc).strftime("%Y年%m月%d日")

    return f"""あなたはXアカウント @kansai_job_ の投稿ライターです。
関西の大学1〜3年生（28卒・29卒・30卒）に就活・インターン情報を届けるアカウントです。

今日は {today} です。以下のニュース・情報を元に、本日分の投稿案を8本生成してください。

## 使用可能なニュース（信頼度A/Bのみ）
{news_text}

## トーンガイド
{tone_points}
{patterns_text}
{insights_text}
{archive_text}

## 投稿ルール
- 1投稿200〜280字が目安（短くて刺さるなら100字以下もOK）
- URLは本文に含めない（「プロフィールのリンクから」と表現する）
- 信頼度Aのニュースを優先して使う
- 数値を使う場合は出典を（）で明記する
- 8本のうち、以下の型を必ずバリエーションよく含める:
  - ニュース速報型: 1〜2本
  - データ提示型: 1〜2本
  - 問いかけ型: 1〜2本
  - ノウハウ型: 1〜2本
  - 共感型: 1本
  - CTA型: 1本（売り込み感を出しすぎず、価値を先に出してからインターンに自然に触れる）

## 出力形式
以下のJSON形式のみで返してください（コードブロックなし）:

[
  {{
    "post_id": "p001",
    "type": "投稿の型（上記6種類から1つ）",
    "content": "投稿本文（そのままXにコピペできる形）",
    "source_index": 1,
    "source_title": "参照したニュースのタイトル",
    "trust_score": "A",
    "char_count": 120
  }},
  ...
]"""


def main():
    print("=== 投稿文生成開始 ===")

    # 必要ファイルの確認
    verified_path = DATA_DIR / "verified_news.json"
    if not verified_path.exists():
        print("ERROR: verified_news.json が見つかりません。verify_news.py を先に実行してください。")
        return

    # データ読み込み
    verified = load_json(verified_path, {})
    tone = load_json(DATA_DIR / "tone_guide.json", {})
    competitor = load_json(DATA_DIR / "competitor_analysis.json", {})
    insights = load_json(DATA_DIR / "analytics_insights.json", {})

    news_items = verified.get("items", [])
    print(f"使用可能なニュース: {len(news_items)}件")

    # 過去投稿タイトルを重複防止用に読み込む
    archive_titles = []
    archive_dir = DATA_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.json"))[-7:]:  # 直近7日分
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for post in data.get("posts", []):
                    archive_titles.append(post.get("content", "")[:50])
            except Exception:
                pass

    # Geminiで生成
    print("Gemini で投稿文を生成中...")
    prompt = build_prompt(news_items, tone, competitor, insights, archive_titles)

    try:
        text = call_gemini(prompt).strip()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        # レート制限でも空ファイルを保存して後続ステップを止めない
        out_path = DATA_DIR / "posts.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                       "count": 0, "posts": [], "error": str(e)}, f, ensure_ascii=False, indent=2)
        return

    # JSON抽出
    if "```" in text:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        text = match.group(0) if match else text

    try:
        posts = json.loads(text)
    except json.JSONDecodeError:
        # JSON修正を試みる
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            posts = json.loads(match.group(0))
        else:
            print("ERROR: Geminiの出力をJSONとして解析できませんでした。")
            print(text[:500])
            return

    # 文字数を実際にカウントして更新
    for post in posts:
        post["char_count"] = len(post.get("content", ""))
        post["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(posts),
        "posts": posts,
    }

    out_path = DATA_DIR / "posts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"生成本数: {len(posts)}本")
    for p in posts:
        print(f"  [{p.get('type', '?')}] {p.get('char_count', 0)}字: {p.get('content', '')[:40]}...")
    print(f"保存先: {out_path}")


if __name__ == "__main__":
    main()
