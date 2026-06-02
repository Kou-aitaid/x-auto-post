"""
アーカイブ・重複管理スクリプト（アーキビスト）
承認済み投稿を日付別にアーカイブし、古い一時ファイルを削除する
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)

# 一時ファイル（毎日上書きされるもの）
TEMP_FILES = ["news.json", "verified_news.json", "posts.json", "reviewed_posts.json"]
TEMP_KEEP_DAYS = 7


def archive_approved_posts():
    """承認済み投稿を日付別にアーカイブ"""
    approved_path = DATA_DIR / "approved_posts.json"
    if not approved_path.exists():
        print("  [SKIP] approved_posts.json なし")
        return 0

    with open(approved_path, encoding="utf-8") as f:
        approved = json.load(f)

    if not approved:
        print("  [SKIP] 承認済み投稿なし")
        return 0

    # 今日の日付でアーカイブ
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = ARCHIVE_DIR / f"{today}.json"

    # 既存アーカイブがあれば統合
    existing_posts = []
    if archive_path.exists():
        with open(archive_path, encoding="utf-8") as f:
            existing = json.load(f)
        existing_posts = existing.get("posts", [])

    # 重複を除外して統合
    existing_ids = {p.get("post_id") for p in existing_posts}
    new_posts = [p for p in approved if p.get("post_id") not in existing_ids]
    all_posts = existing_posts + new_posts

    # 使用済みニュースソースを記録
    used_sources = list({p.get("source_title", "") for p in all_posts if p.get("source_title")})

    archive_data = {
        "date": today,
        "post_count": len(all_posts),
        "posts": all_posts,
        "used_sources": used_sources,
    }

    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(archive_data, f, ensure_ascii=False, indent=2)

    print(f"  → {archive_path.name} にアーカイブ（{len(all_posts)}本）")

    # approved_posts.json をリセット（翌日分のため）
    with open(approved_path, "w", encoding="utf-8") as f:
        json.dump([], f)
    print("  → approved_posts.json をリセット")

    return len(new_posts)


def cleanup_temp_files():
    """古い一時ファイルをクリーンアップ"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TEMP_KEEP_DAYS)
    cleaned = 0

    for fname in TEMP_FILES:
        fpath = DATA_DIR / fname
        if not fpath.exists():
            continue
        mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            fpath.unlink()
            print(f"  → 削除: {fname}（{TEMP_KEEP_DAYS}日以上経過）")
            cleaned += 1

    return cleaned


def build_duplicate_index() -> dict:
    """過去投稿のインデックスを生成（重複チェック用）"""
    index = {"titles": [], "source_urls": [], "generated_at": ""}

    for archive_file in sorted(ARCHIVE_DIR.glob("*.json"))[-14:]:  # 直近2週間
        try:
            with open(archive_file, encoding="utf-8") as f:
                data = json.load(f)
            for post in data.get("posts", []):
                content = post.get("content", "")
                if content:
                    index["titles"].append(content[:60])
                source = post.get("source_title", "")
                if source:
                    index["source_urls"].append(source)
        except Exception:
            continue

    index["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index["count"] = len(index["titles"])

    index_path = DATA_DIR / "archive_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return index


def main():
    print("=== アーカイブ・クリーンアップ開始 ===")

    print("\n[1] 承認済み投稿をアーカイブ中...")
    archived = archive_approved_posts()

    print("\n[2] 一時ファイルをクリーンアップ中...")
    cleaned = cleanup_temp_files()

    print("\n[3] 重複チェック用インデックスを更新中...")
    index = build_duplicate_index()

    print(f"\n=== 完了 ===")
    print(f"アーカイブ: {archived}本 / 削除: {cleaned}件 / インデックス: {index['count']}件")


if __name__ == "__main__":
    main()
