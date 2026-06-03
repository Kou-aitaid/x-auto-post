"""
ファクトチェックスクリプト
① 信頼できる出典は即確定（API不要）
② それ以外はURLにアクセスして本文を取得 → Geminiでバッチ判定
   → リアルタイム確認しつつ処理を高速に保つ
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

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

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

# 即B確定（信頼できるメディア・URL確認不要）
TRUSTED_B = [
    "マイナビ", "リクナビ", "リクルート", "ダイヤモンド", "東洋経済",
    "朝日新聞", "毎日新聞", "読売新聞", "産経新聞", "共同通信",
    "doda", "エン転職", "キャリタス", "就職四季報", "日経HR",
    "プレスリリース", "PR TIMES", "Business Wire",
]

BATCH_SIZE     = 10   # Geminiに一度に送る件数
BATCH_INTERVAL = 5    # バッチ間の待機秒数
URL_TIMEOUT    = 8    # URL取得のタイムアウト秒数


# ─────────────────────────────────────────
# Gemini 呼び出し
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# 即判定
# ─────────────────────────────────────────

def quick_score(item: Dict) -> Optional[str]:
    source = item.get("source", "")
    if any(s in source for s in TRUSTED_A) or item.get("trust_base") == "A":
        return "A"
    if any(s in source for s in TRUSTED_B):
        return "B"
    return None


# ─────────────────────────────────────────
# URLアクセスして本文を取得
# ─────────────────────────────────────────

def fetch_article_text(url: str) -> str:
    """記事URLにアクセスして本文テキストを取得（失敗したら空文字）"""
    if not url or not url.startswith("http"):
        return ""
    try:
        resp = requests.get(
            url,
            timeout=URL_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; factcheck-bot)"},
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""

        if BS4_AVAILABLE:
            soup = BeautifulSoup(resp.text, "html.parser")
            # script/style を除去
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = " ".join(p.get_text(strip=True) for p in soup.find_all("p"))
        else:
            # BeautifulSoupがない場合はHTMLタグを正規表現で除去
            text = re.sub(r"<[^>]+>", " ", resp.text)

        # 改行・余白を整理して先頭300文字を返す
        text = re.sub(r"\s+", " ", text).strip()
        return text[:300]

    except Exception:
        return ""


# ─────────────────────────────────────────
# URLテキスト付きでGeminiにバッチ判定
# ─────────────────────────────────────────

def batch_verify_with_content(items: List[Dict]) -> List[Dict]:
    """URL本文込みでGeminiにバッチ判定させる"""
    items_text = "\n".join([
        f"[{i}] タイトル: {item['title'][:100]}\n"
        f"    出典: {item['source']}\n"
        f"    本文抜粋: {item.get('_article_text', '取得不可')[:150]}"
        for i, item in enumerate(items)
    ])

    prompt = f"""以下の就活ニュース記事を確認し、信頼度スコアを付けてください。
本文抜粋が取得できている場合は、タイトルと本文の内容が一致しているかも確認してください。

スコア基準：
A: 一次情報源（省庁・企業公式・NHK）または内容が確認できた信頼性の高い記事
B: 信頼できるメディアの記事、または内容が妥当と判断できる記事
C: 本文が取得できず内容不明 / タイトルと本文が乖離 / 古い情報 / 信頼性不明

ニュース一覧：
{items_text}

JSON形式のみで返してください（コードブロックなし）：
[{{"index": 0, "score": "A", "reason": "理由15字以内"}}, ...]"""

    text = call_gemini(prompt).strip()
    if "```" in text:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        text = match.group(0) if match else text
    return json.loads(text)


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────

def main():
    print("=== ファクトチェック開始（URL取得 + Gemini判定）===")

    news_path = DATA_DIR / "news.json"
    if not news_path.exists():
        print("ERROR: data/news.json が見つかりません。")
        return

    with open(news_path, encoding="utf-8") as f:
        news_data = json.load(f)

    items = news_data.get("items", [])
    print(f"対象: {len(items)}件")

    # ① 即判定
    auto_scored  = []
    needs_verify = []

    for item in items:
        score = quick_score(item)
        if score:
            item.update({
                "trust_score": score,
                "trust_reason": "出典で即確定",
                "verified": True,
                "url_checked": False,
                "use_for_post": True,
            })
            auto_scored.append(item)
        else:
            needs_verify.append(item)

    a_count = sum(1 for x in auto_scored if x["trust_score"] == "A")
    b_count = sum(1 for x in auto_scored if x["trust_score"] == "B")
    print(f"  即確定 A:{a_count}件 / B:{b_count}件 / URL確認が必要:{len(needs_verify)}件")

    # ② URLアクセスして本文を取得
    if needs_verify:
        print(f"  URLから本文を取得中...")
        for item in needs_verify:
            text = fetch_article_text(item.get("url", ""))
            item["_article_text"] = text
            if text:
                print(f"    [OK] {item['title'][:30]}...")
            else:
                print(f"    [NG] {item['title'][:30]}... （取得失敗）")

    # ③ Geminiでバッチ判定
    ai_scored = []
    for i in range(0, len(needs_verify), BATCH_SIZE):
        batch = needs_verify[i:i + BATCH_SIZE]
        print(f"  Gemini判定中... ({i+1}〜{min(i+BATCH_SIZE, len(needs_verify))}件目)")
        try:
            results = batch_verify_with_content(batch)
            for r in results:
                idx = r["index"]
                if idx < len(batch):
                    batch[idx].update({
                        "trust_score":  r["score"],
                        "trust_reason": r.get("reason", ""),
                        "verified":     True,
                        "url_checked":  bool(batch[idx].get("_article_text")),
                        "use_for_post": r["score"] != "C",
                    })
            ai_scored.extend(batch)
        except Exception as e:
            print(f"  [ERROR] 判定失敗: {e} → スコアBで処理")
            for item in batch:
                item.update({
                    "trust_score": "B", "trust_reason": "判定失敗",
                    "verified": False, "url_checked": False, "use_for_post": True,
                })
            ai_scored.extend(batch)

        if i + BATCH_SIZE < len(needs_verify):
            time.sleep(BATCH_INTERVAL)

    # _article_text（内部用）を除去
    for item in ai_scored:
        item.pop("_article_text", None)

    all_verified = auto_scored + ai_scored

    # A→B→C でソート、Cは除外
    usable = [x for x in all_verified if x.get("use_for_post")]
    usable.sort(key=lambda x: (
        {"A": 0, "B": 1, "C": 2}.get(x.get("trust_score"), 2),
        not x.get("is_kansai", False)
    ))

    output = {
        "verified_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_checked": len(all_verified),
        "usable_count":  len(usable),
        "score_a":  sum(1 for x in all_verified if x.get("trust_score") == "A"),
        "score_b":  sum(1 for x in all_verified if x.get("trust_score") == "B"),
        "score_c":  sum(1 for x in all_verified if x.get("trust_score") == "C"),
        "url_checked": sum(1 for x in ai_scored if x.get("url_checked")),
        "items": usable,
    }

    out_path = DATA_DIR / "verified_news.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"A:{output['score_a']} / B:{output['score_b']} / C:{output['score_c']} / URL確認:{output['url_checked']}件")
    print(f"投稿に使える件数: {output['usable_count']}件")


if __name__ == "__main__":
    main()
