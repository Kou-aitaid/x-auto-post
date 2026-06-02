"""
ファクトチェックスクリプト
news.json の各ニュースに信頼度スコアを付与して verified_news.json に保存する
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

# 信頼度Aが確定するソース
TRUSTED_SOURCES = ["厚生労働省", "文部科学省", "総務省", "NHK"]


def call_gemini(prompt: str, retries: int = 4) -> str:
    """Gemini REST APIを呼び出す（レート制限時は自動リトライ）"""
    key = os.environ["GEMINI_API_KEY"]
    url = GEMINI_URL.format(key=key)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(retries):
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 429:
            wait = 60 * (attempt + 1)  # 60→120→180→240秒
            print(f"  [WAIT] レート制限 → {wait}秒待機...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini APIのレート制限が続いています。後で再実行してください。")


def quick_score(item: Dict) -> str:
    """ソース名だけで即判定できるものはAIを使わず処理"""
    source = item.get("source", "")
    if any(s in source for s in TRUSTED_SOURCES):
        return "A"
    if item.get("trust_base") == "A":
        return "A"
    return None  # AIに判定させる


def batch_verify(items: List[Dict]) -> List[Dict]:
    """複数ニュースをまとめてGeminiに送ってスコア付与（API呼び出し削減）"""
    items_text = "\n".join([
        f"[{i}] タイトル: {item['title'][:100]}\n    出典: {item['source']}"
        for i, item in enumerate(items)
    ])

    prompt = f"""以下のニュース記事に就活情報としての信頼度スコアを付与してください。

スコア基準：
- A: 厚労省・文科省・企業公式・NHKなど一次情報源
- B: 複数の信頼できるメディアで報道あり（マイナビ・リクナビ・日経など）
- C: 単一ソースのみ・真偽不明・古い情報

ニュース一覧：
{items_text}

以下の形式でJSONのみ返してください（コードブロックなし）：
[{{"index": 0, "score": "A", "reason": "理由を10字以内"}}, ...]"""

    text = call_gemini(prompt).strip()
    if "```" in text:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        text = match.group(0) if match else text

    return json.loads(text)


def main():
    print("=== ファクトチェック開始 ===")

    news_path = DATA_DIR / "news.json"
    if not news_path.exists():
        print("ERROR: data/news.json が見つかりません。先に fetch_news.py を実行してください。")
        return

    with open(news_path, encoding="utf-8") as f:
        news_data = json.load(f)

    items = news_data.get("items", [])
    print(f"対象: {len(items)}件")

    # 即判定できるものと要AI判定に分ける
    auto_scored = []
    needs_ai = []
    for item in items:
        score = quick_score(item)
        if score:
            item["trust_score"] = score
            item["verified"] = True
            item["use_for_post"] = score != "C"
            auto_scored.append(item)
        else:
            needs_ai.append(item)

    print(f"  自動判定: {len(auto_scored)}件 / AI判定: {len(needs_ai)}件")

    # AIで10件ずつまとめて処理（API呼び出し回数を最小化）
    ai_scored = []
    BATCH = 10
    for i in range(0, len(needs_ai), BATCH):
        batch = needs_ai[i:i + BATCH]
        print(f"  AI判定中... ({i+1}〜{min(i+BATCH, len(needs_ai))}件目)")
        try:
            results = batch_verify(batch)
            for r in results:
                idx = r["index"]
                if idx < len(batch):
                    batch[idx]["trust_score"] = r["score"]
                    batch[idx]["trust_reason"] = r.get("reason", "")
                    batch[idx]["verified"] = True
                    batch[idx]["use_for_post"] = r["score"] != "C"
            ai_scored.extend(batch)
        except Exception as e:
            print(f"  [ERROR] AI判定失敗: {e} → このバッチはスコアBで処理")
            for item in batch:
                item["trust_score"] = "B"
                item["verified"] = False
                item["use_for_post"] = True
            ai_scored.extend(batch)
        time.sleep(5)  # バッチ間のレート制限回避

    all_verified = auto_scored + ai_scored

    # スコアA→B→Cの順にソート、Cは除外
    usable = [x for x in all_verified if x.get("use_for_post")]
    usable.sort(key=lambda x: ({"A": 0, "B": 1, "C": 2}.get(x["trust_score"], 2), not x["is_kansai"]))

    output = {
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_checked": len(all_verified),
        "usable_count": len(usable),
        "score_a": sum(1 for x in all_verified if x.get("trust_score") == "A"),
        "score_b": sum(1 for x in all_verified if x.get("trust_score") == "B"),
        "score_c": sum(1 for x in all_verified if x.get("trust_score") == "C"),
        "items": usable,
    }

    out_path = DATA_DIR / "verified_news.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"スコアA: {output['score_a']}件 / B: {output['score_b']}件 / C: {output['score_c']}件")
    print(f"投稿に使える件数: {output['usable_count']}件")
    print(f"保存先: {out_path}")


if __name__ == "__main__":
    main()
