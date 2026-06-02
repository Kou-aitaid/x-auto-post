"""
パイプライン統合スクリプト
毎日20:00にGitHub Actionsから実行される。全スクリプトを順番に呼び出す。
"""

from __future__ import annotations

import subprocess
import sys
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
DATA_DIR = SCRIPTS_DIR.parent / "data"


def run_step(name: str, script: str, args: list = None) -> bool:
    """1ステップを実行してOK/NGを返す"""
    cmd = [sys.executable, str(SCRIPTS_DIR / script)] + (args or [])
    print(f"\n{'='*50}")
    print(f"[{name}] 開始")
    print(f"{'='*50}")

    result = subprocess.run(cmd, cwd=str(SCRIPTS_DIR.parent))
    ok = result.returncode == 0

    if ok:
        print(f"[{name}] 完了 ✅")
    else:
        print(f"[{name}] エラー ❌ (returncode={result.returncode})")

    return ok


def notify_discord(message: str):
    """DiscordにWebhookでシステムメッセージを送信"""
    try:
        import requests
        from dotenv import load_dotenv
        load_dotenv()
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return
        requests.post(webhook_url, json={"content": message}, timeout=10)
    except Exception:
        pass


def main(mode: str = "daily"):
    start_time = datetime.now(timezone.utc)
    print(f"\n🚀 X投稿自動化パイプライン開始 ({mode}) - {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    notify_discord(f"🚀 パイプライン開始 ({start_time.strftime('%m/%d %H:%M')} UTC)")

    results = {}

    if mode == "daily":
        # ① アーカイブ（前日データの整理）
        results["archive"] = run_step("アーキビスト", "archive_posts.py")

        # ② ニュース収集
        results["fetch"] = run_step("リサーチャー①", "fetch_news.py")
        if not results["fetch"]:
            notify_discord("❌ ニュース収集に失敗しました。")
            return

        # ③ 競合分析・トーン分析（失敗しても続行）
        results["competitor"] = run_step("リサーチャー②", "analyze_competitors.py")

        # ④ ファクトチェック
        results["verify"] = run_step("ファクトチェッカー", "verify_news.py")
        if not results["verify"]:
            notify_discord("❌ ファクトチェックに失敗しました。")
            return

        # ⑤ 投稿文生成
        results["generate"] = run_step("ライター", "generate_posts.py")
        if not results["generate"]:
            notify_discord("❌ 投稿文の生成に失敗しました。")
            return

        # ⑥ 品質チェック・スケジュール設定
        results["review"] = run_step("エディター", "review_posts.py")
        if not results["review"]:
            notify_discord("❌ 品質チェックに失敗しました。")
            return

        # ⑦ Discord Webhook通知（投稿案を独立ブロックで送信）
        results["notify"] = run_step("Webhook通知", "notify_webhook.py")

    elif mode == "weekly":
        results["analyze"] = run_step("アナリスト（週次）", "analyze_performance.py", ["weekly"])

    elif mode == "monthly":
        results["analyze"] = run_step("アナリスト（月次）", "analyze_performance.py", ["monthly"])

    elapsed = (datetime.now(timezone.utc) - start_time).seconds
    ok_count = sum(1 for v in results.values() if v)
    total = len(results)

    summary = f"✅ パイプライン完了 ({ok_count}/{total}ステップ成功, {elapsed}秒)"
    print(f"\n{summary}")
    notify_discord(summary)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    main(mode)
