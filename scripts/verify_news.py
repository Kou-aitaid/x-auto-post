"""
ファクトチェックスクリプト
Gemini API の Google Search Grounding を使い、リアルタイムでGoogle検索して
各ニュースの信頼度を確認する。タイトル・出典名だけでの判定はしない。
"""

from __future__ import annotations

import json
import os
import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.0-flash:generateContent?key={key}"
)

# これらは出典名だけで即A確定（Google検索不要）
TRUSTED_SOURCES = [
    "厚生労働省", "文部科学省", "総務省", "経済産業省", "内閣府",
    "NHK", "日本経済新聞",
]

# バッチあたりの処理件数（Groundingは1件ずつ確認）
SEARCH_INTERVAL = 4  # 秒（レート制限回避）


# ─────────────────────────────────────────────
# Gemini 呼び出し（通常）
# ─────────────────────────────────────────────

def call_gemini(prompt: str, retries: int = 4) -> str:
    key = os.environ["GEMINI_API_KEY"]
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(retries):
        resp = requests.post(GEMINI_URL.format(key=key), json=payload, timeout=60)
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            print(f"  [WAIT] レート制限 → {wait}秒待機...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini APIのレート制限が続いています。")


# ─────────────────────────────────────────────
# Gemini 呼び出し（Google Search Grounding）
# ─────────────────────────────────────────────

def call_gemini_with_search(prompt: str, retries: int = 4) -> Tuple[str, List[str]]:
    """Google Search Groundingを有効にしてリアルタイム検索付きで呼び出す"""
    key = os.environ["GEMINI_API_KEY"]
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    for attempt in range(retries):
        resp = requests.post(GEMINI_URL.format(key=key), json=payload, timeout=90)
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            print(f"  [WAIT] レート制限 → {wait}秒待機...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]

        # 検索で参照したURLを記録
        grounding = data["candidates"][0].get("groundingMetadata", {})
        sources = [
            chunk.get("web", {}).get("uri", "")
            for chunk in grounding.get("groundingChunks", [])
            if chunk.get("web", {}).get("uri")
        ]
        return text, sources

    raise RuntimeError("Gemini APIのレート制限が続いています。")


# ─────────────────────────────────────────────
# 即判定（Google検索不要）
# ─────────────────────────────────────────────

def quick_score(item: Dict) -> Optional[str]:
    """一次情報源は検索不要で即A確定"""
    source = item.get("source", "")
    if any(s in source for s in TRUSTED_SOURCES):
        return "A"
    if item.get("trust_base") == "A":
        return "A"
    return None


# ─────────────────────────────────────────────
# リアルタイムファクトチェック（1件ずつ）
# ─────────────────────────────────────────────

def verify_single(item: Dict) -> Dict:
    """Google Search Groundingで1件ずつリアルタイム確認"""
    title  = item.get("title", "")[:150]
    source = item.get("source", "")
    url    = item.get("url", "")
    summary = item.get("summary", "")[:100]

    prompt = f"""以下の就活ニュースについて、今すぐGoogle検索で事実確認をしてからスコアを付けてください。
記憶や学習データではなく、必ずリアルタイムの検索結果を使って判断してください。

【確認するニュース】
タイトル: {title}
出典: {source}
URL: {url}
概要: {summary}

【確認してほしいこと】
1. このニュースは実際に報道・公表されているか
2. 情報は現在も有効か（古くなっていないか）
3. 出典は信頼できるか

【スコア基準】
A: 一次情報源（省庁・企業公式・NHK）または複数の信頼できるメディアで確認できた
B: 信頼できるメディアで報道されているが、確認は1件のみ
C: 情報が古い・内容に誤りがある・確認できない・信頼性が低い

以下のJSON形式のみで返してください（コードブロックなし・余計な文章なし）：
{{"score": "A", "reason": "理由を20字以内", "confirmed": true}}"""

    try:
        text, search_sources = call_gemini_with_search(prompt)

        # JSONを抽出（Groundingレスポンスには説明文が混入することがある）
        text = text.strip()
        match = re.search(r'\{[^{}]*"score"[^{}]*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            result = json.loads(text)

        result["search_sources"] = search_sources[:3]
        result["grounding_used"] = True
        return result

    except Exception as e:
        print(f"    [WARN] Grounding失敗 → 通常判定にフォールバック: {e}")
        return _fallback_verify(item)


def _fallback_verify(item: Dict) -> Dict:
    """Grounding失敗時の通常判定（フォールバック）"""
    prompt = f"""以下の就活ニュースの信頼度スコアを付けてください。

タイトル: {item.get('title', '')[:100]}
出典: {item.get('source', '')}

スコア基準：
A: 厚労省・文科省・企業公式・NHKなど一次情報源
B: マイナビ・リクナビ・日経など信頼できるメディア
C: 信頼性不明・古い情報

JSONのみ返してください：{{"score": "B", "reason": "理由を15字以内", "confirmed": false}}"""

    try:
        text = call_gemini(prompt).strip()
        match = re.search(r'\{[^{}]*"score"[^{}]*\}', text, re.DOTALL)
        result = json.loads(match.group(0) if match else text)
        result["grounding_used"] = False
        return result
    except Exception:
        return {"score": "B", "reason": "判定失敗", "confirmed": False, "grounding_used": False}


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────

def main():
    print("=== ファクトチェック開始（Google Search Grounding）===")

    news_path = DATA_DIR / "news.json"
    if not news_path.exists():
        print("ERROR: data/news.json が見つかりません。先に fetch_news.py を実行してください。")
        return

    with open(news_path, encoding="utf-8") as f:
        news_data = json.load(f)

    items = news_data.get("items", [])
    print(f"対象: {len(items)}件")

    auto_scored = []
    needs_search = []

    for item in items:
        score = quick_score(item)
        if score:
            item["trust_score"] = score
            item["trust_reason"] = "一次情報源"
            item["verified"] = True
            item["grounding_used"] = False
            item["use_for_post"] = True
            auto_scored.append(item)
        else:
            needs_search.append(item)

    print(f"  即確定（一次情報源）: {len(auto_scored)}件")
    print(f"  Google検索で確認: {len(needs_search)}件")

    search_scored = []
    for i, item in enumerate(needs_search):
        short_title = item.get("title", "")[:35]
        print(f"  [{i+1}/{len(needs_search)}] 検索中: {short_title}...")

        result = verify_single(item)

        item["trust_score"]    = result.get("score", "B")
        item["trust_reason"]   = result.get("reason", "")
        item["verified"]       = result.get("confirmed", False)
        item["grounding_used"] = result.get("grounding_used", False)
        item["search_sources"] = result.get("search_sources", [])
        item["use_for_post"]   = item["trust_score"] != "C"
        search_scored.append(item)

        # レート制限回避（最後の1件は不要）
        if i < len(needs_search) - 1:
            time.sleep(SEARCH_INTERVAL)

    all_verified = auto_scored + search_scored

    # A → B → C の順にソート、Cは除外
    usable = [x for x in all_verified if x.get("use_for_post")]
    usable.sort(key=lambda x: (
        {"A": 0, "B": 1, "C": 2}.get(x.get("trust_score"), 2),
        not x.get("is_kansai", False)
    ))

    grounding_count = sum(1 for x in search_scored if x.get("grounding_used"))

    output = {
        "verified_at":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_checked":  len(all_verified),
        "usable_count":   len(usable),
        "score_a":        sum(1 for x in all_verified if x.get("trust_score") == "A"),
        "score_b":        sum(1 for x in all_verified if x.get("trust_score") == "B"),
        "score_c":        sum(1 for x in all_verified if x.get("trust_score") == "C"),
        "grounding_used": grounding_count,
        "items":          usable,
    }

    out_path = DATA_DIR / "verified_news.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"スコアA: {output['score_a']}件 / B: {output['score_b']}件 / C: {output['score_c']}件")
    print(f"Google検索で確認: {grounding_count}件 / 即確定: {len(auto_scored)}件")
    print(f"投稿に使える件数: {output['usable_count']}件")


if __name__ == "__main__":
    main()
