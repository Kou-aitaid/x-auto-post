"""
自アカウント統計取得スクリプト
Nitterで @kansai_job_ の直近ツイートのいいね数・RT数・返信数を自動取得し
publish_log.json を更新する
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent / "data"

USERNAME = "kansai_job_"

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; bot)"}


def fetch_profile_page(instance: str) -> Optional[BeautifulSoup]:
    url = f"{instance}/{USERNAME}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  [RETRY] {instance}: {e}")
    return None


def parse_stat(text: str) -> int:
    """'1,234' や '12K' などを整数に変換"""
    text = text.strip().replace(",", "")
    if text.endswith("K"):
        return int(float(text[:-1]) * 1000)
    if text.endswith("M"):
        return int(float(text[:-1]) * 1000000)
    try:
        return int(text)
    except ValueError:
        return 0


def scrape_tweets(soup: BeautifulSoup) -> List[Dict]:
    """プロフィールページからツイート一覧を抽出"""
    tweets = []
    items = soup.select(".timeline-item")

    for item in items:
        # ピン留め・返信はスキップ
        if item.select_one(".pinned") or item.select_one(".replying-to"):
            continue

        content_el = item.select_one(".tweet-content")
        if not content_el:
            continue
        text = re.sub(r"\s+", " ", content_el.get_text()).strip()

        stats = {"replies": 0, "retweets": 0, "likes": 0}
        for stat_el in item.select(".tweet-stat"):
            icon = stat_el.select_one("[class*='icon-']")
            count_el = stat_el.select_one(".tweet-stat-count, span:last-child")
            if not icon or not count_el:
                continue
            cls = icon.get("class", [""])[0]
            val = parse_stat(count_el.get_text())
            if "comment" in cls or "reply" in cls:
                stats["replies"] = val
            elif "retweet" in cls:
                stats["retweets"] = val
            elif "heart" in cls or "like" in cls:
                stats["likes"] = val

        date_el = item.select_one(".tweet-date a")
        published = date_el["title"] if date_el and date_el.get("title") else ""

        tweets.append({
            "text": text[:300],
            "replies": stats["replies"],
            "retweets": stats["retweets"],
            "likes": stats["likes"],
            "published": published,
        })

    return tweets


def fetch_follower_count(soup: BeautifulSoup) -> Optional[int]:
    """プロフィールからフォロワー数を取得"""
    for el in soup.select(".profile-stat-num"):
        parent = el.parent
        if parent and "followers" in parent.get_text().lower():
            return parse_stat(el.get_text())
    # フォールバック: profile-stats の2番目
    stats = soup.select(".profile-stat-num")
    if len(stats) >= 2:
        return parse_stat(stats[1].get_text())
    return None


def match_to_log(tweet_text: str, log_entries: List[Dict]) -> Optional[int]:
    """ツイート本文をpublish_logのエントリと照合してインデックスを返す"""
    tweet_clean = re.sub(r"\s+", "", tweet_text[:80])
    for i, entry in enumerate(log_entries):
        if entry.get("type") == "followers":
            continue
        log_clean = re.sub(r"\s+", "", entry.get("content", "")[:80])
        if len(tweet_clean) > 10 and tweet_clean in log_clean or log_clean in tweet_clean:
            return i
    return None


def main():
    print("=== 自アカウント統計取得開始 ===")

    # Nitterからプロフィールページ取得
    soup = None
    for instance in NITTER_INSTANCES:
        print(f"  アクセス中: {instance}/{USERNAME}")
        soup = fetch_profile_page(instance)
        if soup:
            print(f"  [OK] {instance}")
            break
        time.sleep(2)

    if not soup:
        print("  [SKIP] 全Nitterインスタンスで取得失敗 → 既存データを維持")
        return

    # ツイートデータを取得
    tweets = scrape_tweets(soup)
    print(f"  ツイート取得: {len(tweets)}件")

    # フォロワー数を取得
    followers = fetch_follower_count(soup)
    if followers is not None:
        print(f"  フォロワー数: {followers}")

    # publish_log.json を読み込み
    log_path = DATA_DIR / "publish_log.json"
    logs = []
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            logs = json.load(f)

    # フォロワー数を記録
    if followers is not None:
        logs.append({
            "type": "followers",
            "count": followers,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "source": "nitter_auto",
        })

    # ツイートデータをpublish_logと照合して自動更新
    updated = 0
    for tweet in tweets:
        idx = match_to_log(tweet["text"], logs)
        if idx is not None and logs[idx].get("likes") is None:
            logs[idx]["likes"] = tweet["likes"]
            logs[idx]["retweets"] = tweet["retweets"]
            logs[idx]["replies"] = tweet["replies"]
            logs[idx]["stats_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            logs[idx]["stats_source"] = "nitter_auto"
            updated += 1

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

    print(f"  統計更新: {updated}件 / フォロワー記録: {'あり' if followers else 'なし'}")
    print("=== 完了 ===")


if __name__ == "__main__":
    main()
