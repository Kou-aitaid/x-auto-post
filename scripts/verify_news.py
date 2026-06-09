"""
ファクトチェックスクリプト
出典名・タイトルキーワードでルールベース判定する。
URL取得はGitHub ActionsのIPでブロックされるため使わない。
Gemini APIはレート制限節約のため最小限の件数のみ使用。
"""

from __future__ import annotations

import json
import os
import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.0-flash:generateContent?key={key}"
)

# 即A確定（一次情報源）
TRUSTED_A = [
    "厚生労働省", "文部科学省", "総務省", "経済産業省", "内閣府",
    "NHK", "日本経済新聞",
]

# 即B確定（信頼できるメディア）
TRUSTED_B = [
    "マイナビ", "リクナビ", "リクルート", "ダイヤモンド", "東洋経済",
    "朝日新聞", "毎日新聞", "読売新聞", "産経新聞", "共同通信",
    "doda", "エン転職", "キャリタス", "就職四季報", "日経HR",
    "プレスリリース", "PR TIMES", "Google News",
]

# タイトルにこれが含まれていたらCに落とす
SPAM_KEYWORDS = [
    "競馬", "パチンコ", "FX", "仮想通貨", "副業詐欺",
    "出会い", "アダルト", "18禁",
]

# Geminiで確認する件数（レート制限節約）
MAX_AI_CHECK = 5
BATCH_SIZE   = 5
BATCH_WAIT   = 5


def call_gemini(prompt: str, retries: int = 3) -> str:
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


def quick_score(item: Dict) -> Optional[str]:
    """出典名とタイトルキーワードでスコアを即判定"""
    source = item.get("source", "")
    title  = item.get("title", "")

    # スパム・無関係キーワードはC
    if any(kw in title for kw in SPAM_KEYWORDS):
        return "C"

    # 一次情報源はA
    if any(s in source for s in TRUSTED_A) or item.get("trust_base") == "A":
        return "A"

    # 信頼できるメディアはB
    if any(s in source for s in TRUSTED_B):
        return "B"

    return None  # 判断できないものはGeminiへ


def ai_batch_score(items: List[Dict]) -> List[Dict]:
    """Geminiで就活関連性と信頼度をまとめて判定"""
    items_text = "\n".join([
        f"[{i}] {item['title'][:80]}（出典: {item['source']}）"
        for i, item in enumerate(items)
    ])

    prompt = f"""以下の記事タイトルと出典を見て、就活情報としての信頼度スコアを付けてください。

スコア基準：
A: 一次情報源（省庁・企業公式・NHK）
B: 信頼できるメディア、または就活に有用な内容
C: 就活と無関係・信頼性不明・古い情報・重複

記事一覧：
{items_text}

JSONのみ返してください（コードブロックなし）：
[{{"index": 0, "score": "B", "reason": "理由10字以内"}}, ...]"""

    try:
        text = call_gemini(prompt).strip()
        if "```" in text:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            text = match.group(0) if match else text
        return json.loads(text)
    except Exception as e:
        print(f"  [WARN] AI判定失敗: {e} → スコアBで処理")
        return [{"index": i, "score": "B", "reason": "判定失敗"} for i in range(len(items))]


def main():
    print("=== ファクトチェック開始（ルールベース + 最小Gemini）===")

    news_path = DATA_DIR / "news.json"
    if not news_path.exists():
        print("ERROR: data/news.json が見つかりません。")
        return

    with open(news_path, encoding="utf-8") as f:
        news_data = json.load(f)

    items = news_data.get("items", [])
    print(f"対象: {len(items)}件")

    auto_scored  = []
    needs_ai     = []

    for item in items:
        score = quick_score(item)
        if score:
            item.update({
                "trust_score": score,
                "trust_reason": "出典で即確定",
                "verified": score in ("A", "B"),
                "use_for_post": score != "C",
            })
            auto_scored.append(item)
        else:
            needs_ai.append(item)

    a = sum(1 for x in auto_scored if x["trust_score"] == "A")
    b = sum(1 for x in auto_scored if x["trust_score"] == "B")
    c = sum(1 for x in auto_scored if x["trust_score"] == "C")
    print(f"  即確定 A:{a} / B:{b} / C:{c} / AI確認:{len(needs_ai)}件")

    # Gemini確認は上限まで（残りはBで自動処理）
    ai_targets  = needs_ai[:MAX_AI_CHECK]
    auto_b_rest = needs_ai[MAX_AI_CHECK:]

    ai_scored = []
    if ai_targets:
        print(f"  Geminiで判定中（{len(ai_targets)}件）...")
        results = ai_batch_score(ai_targets)
        for r in results:
            idx = r["index"]
            if idx < len(ai_targets):
                ai_targets[idx].update({
                    "trust_score":  r["score"],
                    "trust_reason": r.get("reason", ""),
                    "verified":     True,
                    "use_for_post": r["score"] != "C",
                })
        ai_scored.extend(ai_targets)

    for item in auto_b_rest:
        item.update({
            "trust_score": "B",
            "trust_reason": "自動B（件数上限）",
            "verified": False,
            "use_for_post": True,
        })
        ai_scored.append(item)

    all_verified = auto_scored + ai_scored

    usable = [x for x in all_verified if x.get("use_for_post")]
    usable.sort(key=lambda x: (
        {"A": 0, "B": 1, "C": 2}.get(x.get("trust_score"), 2),
        not x.get("is_kansai", False),
    ))

    output = {
        "verified_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_checked": len(all_verified),
        "usable_count":  len(usable),
        "score_a": sum(1 for x in all_verified if x.get("trust_score") == "A"),
        "score_b": sum(1 for x in all_verified if x.get("trust_score") == "B"),
        "score_c": sum(1 for x in all_verified if x.get("trust_score") == "C"),
        "items": usable,
    }

    out_path = DATA_DIR / "verified_news.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"A:{output['score_a']} / B:{output['score_b']} / C:{output['score_c']}")
    print(f"投稿に使える件数: {output['usable_count']}件")


if __name__ == "__main__":
    main()
