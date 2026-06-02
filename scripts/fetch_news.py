"""
ニュース収集スクリプト
就活・インターン関連ニュースをRSSフィードから収集してdata/news.jsonに保存する
"""

from __future__ import annotations

import feedparser
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# 就活関連キーワード（いずれかが含まれていれば収集対象）
RELEVANT_KEYWORDS = [
    "就活", "就職", "採用", "インターン", "インターンシップ",
    "28卒", "29卒", "30卒", "新卒", "大学生", "学生",
    "キャリア", "内定", "エントリーシート", "面接", "説明会",
    "求人倍率", "内定率", "雇用", "労働市場", "新卒採用",
    "夏インターン", "秋インターン", "オープンカンパニー",
]

# 関西関連キーワード（ボーナス：スコアアップ用）
KANSAI_KEYWORDS = ["関西", "大阪", "京都", "神戸", "兵庫", "滋賀", "奈良", "和歌山", "近畿"]

# RSSフィード一覧（無料・安定して使えるもの）
RSS_FEEDS = [
    # 政府・公式機関（信頼度A確定）
    {
        "name": "厚生労働省 報道発表",
        "url": "https://www.mhlw.go.jp/rss/topics.rss",
        "category": "official",
        "trust_base": "A",
    },
    {
        "name": "文部科学省 報道発表",
        "url": "https://www.mext.go.jp/rss/main.xml",
        "category": "official",
        "trust_base": "A",
    },
    # ニュースメディア（信頼度B）
    {
        "name": "NHKニュース 社会",
        "url": "https://www.nhk.or.jp/rss/news/cat1.xml",
        "category": "news",
        "trust_base": "B",
    },
    {
        "name": "NHKニュース 経済",
        "url": "https://www.nhk.or.jp/rss/news/cat3.xml",
        "category": "news",
        "trust_base": "B",
    },
    {
        "name": "マイナビニュース",
        "url": "https://news.mynavi.jp/rss/top.xml",
        "category": "media",
        "trust_base": "B",
    },
    # Google Newsで就活キーワード検索（最も網羅的）
    {
        "name": "Google News: 就活・採用",
        "url": "https://news.google.com/rss/search?q=%E5%B0%B1%E6%B4%BB+%E6%8E%A1%E7%94%A8+%E6%96%B0%E5%8D%92&hl=ja&gl=JP&ceid=JP:ja",
        "category": "aggregator",
        "trust_base": "B",
    },
    {
        "name": "Google News: インターンシップ",
        "url": "https://news.google.com/rss/search?q=%E3%82%A4%E3%83%B3%E3%82%BF%E3%83%BC%E3%83%B3%E3%82%B7%E3%83%83%E3%83%97+%E5%A4%A7%E5%AD%A6%E7%94%9F&hl=ja&gl=JP&ceid=JP:ja",
        "category": "aggregator",
        "trust_base": "B",
    },
    {
        "name": "Google News: 関西 就職",
        "url": "https://news.google.com/rss/search?q=%E9%96%A2%E8%A5%BF+%E5%B0%B1%E8%81%B7+%E6%8E%A1%E7%94%A8&hl=ja&gl=JP&ceid=JP:ja",
        "category": "aggregator",
        "trust_base": "B",
    },
    {
        "name": "Google News: 28卒 29卒",
        "url": "https://news.google.com/rss/search?q=28%E5%8D%92+29%E5%8D%92+30%E5%8D%92&hl=ja&gl=JP&ceid=JP:ja",
        "category": "aggregator",
        "trust_base": "B",
    },
]

# 収集対象期間（直近7日）
DAYS_BACK = 7


def is_relevant(title: str, summary: str) -> bool:  # type: ignore
    """タイトルまたは要約に就活関連キーワードが含まれるか判定"""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in RELEVANT_KEYWORDS)


def has_kansai(title: str, summary: str) -> bool:
    """関西関連キーワードを含むか判定"""
    text = title + " " + summary
    return any(kw in text for kw in KANSAI_KEYWORDS)


def parse_date(entry) -> str:
    """feedparserのエントリから日付文字列を取得"""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def is_recent(entry, days: int = DAYS_BACK) -> bool:
    """直近N日以内の記事か判定"""
    if not hasattr(entry, "published_parsed") or not entry.published_parsed:
        return True  # 日付不明は含める
    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return dt >= cutoff


def fetch_feed(feed_info: Dict) -> List[Dict]:
    """1つのRSSフィードからニュースを取得"""
    items = []
    try:
        feed = feedparser.parse(feed_info["url"])
        if feed.bozo and not feed.entries:
            print(f"  [SKIP] {feed_info['name']}: フィード取得失敗")
            return items

        for entry in feed.entries:
            if not is_recent(entry):
                continue

            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link = getattr(entry, "link", "")

            if not is_relevant(title, summary):
                continue

            items.append({
                "title": title,
                "summary": summary[:300] if summary else "",
                "url": link,
                "published_at": parse_date(entry),
                "source": feed_info["name"],
                "category": feed_info["category"],
                "trust_base": feed_info["trust_base"],
                "is_kansai": has_kansai(title, summary),
                "verified": False,
                "trust_score": None,
            })

        print(f"  [OK] {feed_info['name']}: {len(items)}件取得")

    except Exception as e:
        print(f"  [ERROR] {feed_info['name']}: {e}")

    return items


def deduplicate(items: List[Dict]) -> List[Dict]:
    """URLの重複を除去"""
    seen = set()
    unique = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


def main():
    print("=== ニュース収集開始 ===")
    all_items = []

    for feed_info in RSS_FEEDS:
        items = fetch_feed(feed_info)
        all_items.extend(items)
        time.sleep(1)  # サーバー負荷軽減

    all_items = deduplicate(all_items)

    # 関西ニュースを優先してソート
    all_items.sort(key=lambda x: (not x["is_kansai"], x["published_at"]), reverse=False)
    all_items.sort(key=lambda x: x["is_kansai"], reverse=True)

    output = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(all_items),
        "kansai_count": sum(1 for x in all_items if x["is_kansai"]),
        "items": all_items,
    }

    output_path = DATA_DIR / "news.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完了 ===")
    print(f"合計: {len(all_items)}件（関西関連: {output['kansai_count']}件）")
    print(f"保存先: {output_path}")


if __name__ == "__main__":
    main()
