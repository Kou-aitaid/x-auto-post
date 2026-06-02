"""
Discord Webhook通知スクリプト
reviewed_posts.json の全投稿案をWebhook経由でDiscordに送信する。
各投稿は独立メッセージ＋コードブロックで届くのでワンクリックコピー可能。
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def send(content: str, retry: int = 3):
    """Webhookにメッセージを送信（レート制限対応）"""
    if not WEBHOOK_URL:
        print("[Webhook] DISCORD_WEBHOOK_URL が未設定です")
        return
    for i in range(retry):
        try:
            resp = requests.post(
                WEBHOOK_URL,
                json={"content": content},
                timeout=10,
            )
            if resp.status_code == 429:
                wait = resp.json().get("retry_after", 2)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            time.sleep(0.5)
            return
        except Exception as e:
            print(f"[Webhook] 送信エラー（{i+1}回目）: {e}")
            time.sleep(2)


def main():
    reviewed_path = DATA_DIR / "reviewed_posts.json"
    if not reviewed_path.exists():
        send("⚠️ reviewed_posts.json が見つかりません。パイプラインを確認してください。")
        sys.exit(1)

    with open(reviewed_path, encoding="utf-8") as f:
        data = json.load(f)

    posts = [p for p in data.get("posts", []) if p.get("status") == "OK"]

    if not posts:
        send("⚠️ 投稿案が0件です。generate/reviewステップを確認してください。")
        sys.exit(1)

    total = len(posts)
    today = datetime.now(timezone.utc).strftime("%m/%d")

    # ─── ヘッダー ───
    send(
        f"📬 **{today} の投稿案が届きました（{total}本）**\n"
        f"コードブロック右上のコピーボタンでコピー → Xに貼り付けてください。"
    )

    # ─── 投稿ごとに独立メッセージ ───
    for i, post in enumerate(posts):
        time_label = post.get("scheduled_time", "時刻未設定")
        post_type  = post.get("type", "")
        content    = post.get("content", "")

        msg = (
            f"**【{i+1}/{total}】{time_label}投稿｜{post_type}**\n"
            f"```\n{content}\n```"
        )
        send(msg)

    # ─── フッター ───
    send("✅ 以上です。投稿後は X でインプレッション数を確認してください！")


if __name__ == "__main__":
    main()
