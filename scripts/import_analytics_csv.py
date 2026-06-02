"""
Xアナリティクス CSVインポートスクリプト

使い方（週1回）:
  python scripts/import_analytics_csv.py

Downloadsフォルダから最新のXアナリティクスCSVを自動検出し、
publish_log.json を更新 → ダッシュボードを再生成 → GitHubにpush する。

XアナリティクスCSVの取得場所:
  x.com → 「もっと見る」→「クリエイタースタジオ」→「アナリティクス」→「エクスポート」
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

DATA_DIR    = Path(__file__).parent.parent / "data"
SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT   = SCRIPTS_DIR.parent
DOWNLOADS   = Path.home() / "Downloads"


# ─────────────────────────────────
# CSV 検出
# ─────────────────────────────────

def find_latest_csv() -> Optional[Path]:
    """Downloadsフォルダから最新のXアナリティクスCSVを自動検出"""
    patterns = [
        "account_analytics_content_*.csv",
        "tweet_activity_metrics_*.csv",  # 旧形式
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(DOWNLOADS.glob(pat))

    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ─────────────────────────────────
# CSV パース
# ─────────────────────────────────

def parse_content_csv(path: Path) -> List[Dict]:
    """Xアナリティクス投稿CSVをパース"""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # フィールド名の揺れを吸収
            def get(*keys) -> str:
                for k in keys:
                    for col in row:
                        if k in col:
                            return row[col].strip()
                return "0"

            text = get("ポスト本文", "Tweet text", "text")
            imp  = int(get("インプレッション数", "impressions") or 0)
            like = int(get("いいね",  "likes")       or 0)
            rt   = int(get("リポスト", "retweets")   or 0)
            rep  = int(get("返信",    "replies")     or 0)
            bm   = int(get("ブックマーク", "bookmarks") or 0)
            date = get("日付", "Date", "date")

            rows.append({
                "text":        text,
                "impressions": imp,
                "likes":       like,
                "retweets":    rt,
                "replies":     rep,
                "bookmarks":   bm,
                "date":        date,
            })
    return rows


# ─────────────────────────────────
# publish_log.json と照合
# ─────────────────────────────────

def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text[:100])


def update_publish_log(csv_rows: List[Dict]) -> int:
    log_path = DATA_DIR / "publish_log.json"
    logs: List[Dict] = []
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            logs = json.load(f)

    updated = 0
    unmatched = []

    for row in csv_rows:
        csv_norm = normalize(row["text"])
        matched = False

        for entry in logs:
            if entry.get("type") == "followers":
                continue
            log_norm = normalize(entry.get("content", ""))
            # 先頭80文字が一致 or 片方がもう片方を含む
            if csv_norm and log_norm and (csv_norm in log_norm or log_norm in csv_norm):
                entry["impressions"]      = row["impressions"]
                entry["likes"]            = row["likes"]
                entry["retweets"]         = row["retweets"]
                entry["replies"]          = row["replies"]
                entry["bookmarks"]        = row["bookmarks"]
                entry["stats_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                entry["stats_source"]     = "x_analytics_csv"
                updated += 1
                matched = True
                break

        if not matched and row["impressions"] > 0:
            # publish_logに存在しない投稿 → 新規エントリとして追加
            logs.append({
                "type":           "imported",
                "content":        row["text"][:200],
                "impressions":    row["impressions"],
                "likes":          row["likes"],
                "retweets":       row["retweets"],
                "replies":        row["replies"],
                "bookmarks":      row["bookmarks"],
                "scheduled_date": row["date"],
                "stats_updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "stats_source":   "x_analytics_csv",
            })
            unmatched.append(row["text"][:40])

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

    if unmatched:
        print(f"  ※ publish_logに未登録のため新規追加: {len(unmatched)}件")

    return updated


# ─────────────────────────────────
# ダッシュボード再生成 & push
# ─────────────────────────────────

def run(cmd: List[str]):
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [WARN] {' '.join(cmd)}: {result.stderr.strip()}")
    return result.returncode == 0


def push_dashboard():
    print("\n[3] パフォーマンス分析中...")
    run([sys.executable, str(SCRIPTS_DIR / "analyze_performance.py"), "weekly"])

    print("\n[4] ダッシュボードを再生成中...")
    run([sys.executable, str(SCRIPTS_DIR / "generate_dashboard.py")])

    print("[5] GitHubにpush中（翌日の投稿案生成に反映されます）...")
    run(["git", "add",
         "docs/index.html",
         "data/analytics_insights.json",
         "data/tone_guide.json"])
    result = subprocess.run(
        ["git", "diff", "--staged", "--quiet"], cwd=str(REPO_ROOT)
    )
    if result.returncode == 0:
        print("  変更なし（すでに最新です）")
        return
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    run(["git", "commit", "-m", f"週次分析・ダッシュボード更新 {today}"])
    if run(["git", "push", "origin", "main"]):
        print(f"  ✅ push完了 → 明日からの投稿案に分析結果が反映されます")
    else:
        print("  ⚠ pushに失敗しました。")


# ─────────────────────────────────
# main
# ─────────────────────────────────

def main():
    print("=== Xアナリティクス CSVインポート ===\n")

    # CSVファイルを探す（引数で指定 or Downloadsから自動検出）
    if len(sys.argv) > 1:
        csv_path = Path(sys.argv[1])
    else:
        csv_path = find_latest_csv()

    if not csv_path or not csv_path.exists():
        print("❌ CSVファイルが見つかりません。")
        print("   XアナリティクスページからCSVをダウンロードして ~/Downloads に置いてください。")
        print("   または: python scripts/import_analytics_csv.py /path/to/file.csv")
        sys.exit(1)

    print(f"[1] CSVを読み込み中: {csv_path.name}")
    rows = parse_content_csv(csv_path)
    print(f"    投稿数: {len(rows)}件")

    print("\n[2] publish_log.json を更新中...")
    updated = update_publish_log(rows)
    print(f"    更新: {updated}件")

    push_dashboard()

    print("\n✅ 完了！")
    print("   ・分析結果が翌日からの投稿案に自動反映されます")
    print("   ・ダッシュボード → https://kou-aitaid.github.io/x-auto-post/")


if __name__ == "__main__":
    main()
