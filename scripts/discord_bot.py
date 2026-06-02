"""
Discord Bot スクリプト
投稿案の通知・承認管理・パフォーマンスデータ収集を行う
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

APPROVE_EMOJI = "✅"
REJECT_EMOJI  = "❌"

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 通知済みメッセージID → 投稿データ のマッピング（メモリ上で管理）
pending: Dict[int, dict] = {}


# ───────────────────────────────────────────────
# 起動イベント
# ───────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[Bot] ログイン完了: {bot.user}")

    # --notify フラグがあれば起動後すぐに通知を送る
    if "--notify" in sys.argv:
        await notify_posts()


# ───────────────────────────────────────────────
# 投稿案通知
# ───────────────────────────────────────────────

async def notify_posts():
    """reviewed_posts.json の OK 投稿を Discord に送信する"""
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[Bot] ERROR: チャンネル {CHANNEL_ID} が見つかりません")
        return

    reviewed_path = DATA_DIR / "reviewed_posts.json"
    if not reviewed_path.exists():
        await channel.send("⚠️ reviewed_posts.json が見つかりません。パイプラインを先に実行してください。")
        return

    with open(reviewed_path, encoding="utf-8") as f:
        data = json.load(f)

    ok_posts = [p for p in data.get("posts", []) if p.get("status") == "OK"]
    if not ok_posts:
        await channel.send("⚠️ 承認候補の投稿が0件です。")
        return

    total = len(ok_posts)
    await channel.send(f"📬 **本日の投稿案が届きました（{total}本）**\n各投稿に ✅ 承認 / ❌ 却下 を押してください。")

    for i, post in enumerate(ok_posts):
        msg_text = (
            f"📝 **投稿案 {i+1}/{total}（{post.get('type', '?')}）**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{post['content']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 出典: {post.get('source_title', '不明')}（信頼度{post.get('trust_score', '?')}）\n"
            f"🕐 推奨投稿時刻: {post.get('scheduled_date', '')} {post.get('scheduled_time', '')}\n"
            f"\n✅ 承認 / ❌ 却下"
        )
        msg = await channel.send(msg_text)
        await msg.add_reaction(APPROVE_EMOJI)
        await msg.add_reaction(REJECT_EMOJI)
        pending[msg.id] = post
        await asyncio.sleep(0.5)


# ───────────────────────────────────────────────
# リアクション監視（承認・却下）
# ───────────────────────────────────────────────

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot:
        return
    if reaction.message.id not in pending:
        return

    post = pending[reaction.message.id]
    emoji = str(reaction.emoji)

    if emoji == APPROVE_EMOJI:
        post["approved"] = True
        post["approved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _save_approved(post)
        await reaction.message.channel.send(
            f"✅ **承認しました** → {post.get('scheduled_time', '')} 投稿予定\n"
            f"> {post['content'][:50]}..."
        )
        del pending[reaction.message.id]
        await _check_all_done(reaction.message.channel)

    elif emoji == REJECT_EMOJI:
        post["approved"] = False
        await reaction.message.channel.send(
            f"❌ **却下しました**\n> {post['content'][:50]}..."
        )
        del pending[reaction.message.id]
        await _check_all_done(reaction.message.channel)


def _save_approved(post: dict):
    """承認済み投稿を approved_posts.json と publish_log.json に保存"""
    # approved_posts.json
    approved_path = DATA_DIR / "approved_posts.json"
    approved = []
    if approved_path.exists():
        with open(approved_path, encoding="utf-8") as f:
            approved = json.load(f)
    approved.append(post)
    with open(approved_path, "w", encoding="utf-8") as f:
        json.dump(approved, f, ensure_ascii=False, indent=2)

    # publish_log.json（承認=投稿予定として自動記録）
    log_path = DATA_DIR / "publish_log.json"
    log = []
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
    log.append({
        "post_id": post.get("post_id"),
        "content": post.get("content"),
        "type": post.get("type"),
        "scheduled_date": post.get("scheduled_date"),
        "scheduled_time": post.get("scheduled_time"),
        "approved_at": post.get("approved_at"),
        "impressions": None,
        "likes": None,
        "retweets": None,
        "replies": None,
    })
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


async def _check_all_done(channel: discord.TextChannel):
    """全投稿の承認/却下が完了したらコピペ用まとめを送信"""
    if pending:
        return  # まだ未決の投稿がある

    approved_path = DATA_DIR / "approved_posts.json"
    if not approved_path.exists():
        await channel.send("承認済みの投稿がありませんでした。")
        return

    with open(approved_path, encoding="utf-8") as f:
        approved = json.load(f)

    # 今日承認された投稿だけ抽出（直近の承認セッション）
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays = [p for p in approved if p.get("approved_at", "").startswith(today)]

    if not todays:
        await channel.send("本日承認済みの投稿がありません。")
        return

    todays.sort(key=lambda x: x.get("scheduled_time", ""))

    lines = ["📋 **本日の承認済み投稿まとめ（Bufferにコピペ用）**\n━━━━━━━━━━━━━━━"]
    for p in todays:
        lines.append(f"\n【{p.get('scheduled_time', '?')}投稿】\n{p['content']}")
        lines.append("─────────────────")

    summary = "\n".join(lines)

    # Discordの2000字制限を考慮して分割
    if len(summary) <= 1900:
        await channel.send(summary)
    else:
        await channel.send("📋 **本日の承認済み投稿まとめ（Bufferにコピペ用）**")
        for p in todays:
            await channel.send(
                f"**【{p.get('scheduled_time', '?')}投稿】**\n```\n{p['content']}\n```"
            )

    await channel.send("✨ Buffer にコピペして投稿時刻を設定してください！")


# ───────────────────────────────────────────────
# コマンド群
# ───────────────────────────────────────────────

@bot.command(name="stats")
async def cmd_stats(ctx, post_id: str, imp: int, like: int, rt: int, reply: int):
    """投稿パフォーマンスを記録: !stats p001 1000 50 20 5"""
    log_path = DATA_DIR / "publish_log.json"
    if not log_path.exists():
        await ctx.send("publish_log.json が見つかりません。")
        return

    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)

    updated = False
    for entry in log:
        if entry.get("post_id") == post_id:
            entry["impressions"] = imp
            entry["likes"] = like
            entry["retweets"] = rt
            entry["replies"] = reply
            entry["stats_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            updated = True
            break

    if updated:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        await ctx.send(f"✅ {post_id} のデータを記録しました。\nインプ: {imp} / いいね: {like} / RT: {rt} / リプ: {reply}")
    else:
        await ctx.send(f"❌ {post_id} が見つかりませんでした。")


@bot.command(name="followers")
async def cmd_followers(ctx, count: int):
    """フォロワー数を記録: !followers 1250"""
    log_path = DATA_DIR / "publish_log.json"
    log = []
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)

    log.append({
        "type": "followers",
        "count": count,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    await ctx.send(f"✅ フォロワー数 {count} を記録しました。")


@bot.command(name="report")
async def cmd_report(ctx):
    """最新の分析レポートを表示: !report"""
    insights_path = DATA_DIR / "analytics_insights.json"
    if not insights_path.exists():
        await ctx.send("まだ分析データがありません。パフォーマンスデータを !stats で入力してください。")
        return

    with open(insights_path, encoding="utf-8") as f:
        insights = json.load(f)

    report = insights.get("latest_report", "レポートがまだ生成されていません。")
    if len(report) > 1900:
        report = report[:1900] + "..."
    await ctx.send(f"📊 **最新レポート**\n{report}")


@bot.command(name="add_sample")
async def cmd_add_sample(ctx, *, text: str):
    """競合投稿サンプルを追加: !add_sample 投稿テキスト"""
    seeds_path = DATA_DIR / "competitor_seeds.json"
    seeds = {"samples": []}
    if seeds_path.exists():
        with open(seeds_path, encoding="utf-8") as f:
            seeds = json.load(f)

    seeds["samples"].append({
        "text": text,
        "username": "manual",
        "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "manual",
    })
    with open(seeds_path, "w", encoding="utf-8") as f:
        json.dump(seeds, f, ensure_ascii=False, indent=2)

    await ctx.send(f"✅ サンプル投稿を追加しました（合計 {len(seeds['samples'])} 件）")


@bot.command(name="cancel")
async def cmd_cancel(ctx, post_id: str):
    """承認済み投稿をキャンセル: !cancel p001"""
    approved_path = DATA_DIR / "approved_posts.json"
    if not approved_path.exists():
        await ctx.send("approved_posts.json が見つかりません。")
        return

    with open(approved_path, encoding="utf-8") as f:
        approved = json.load(f)

    before = len(approved)
    approved = [p for p in approved if p.get("post_id") != post_id]
    after = len(approved)

    if before == after:
        await ctx.send(f"❌ {post_id} が見つかりませんでした。")
        return

    with open(approved_path, "w", encoding="utf-8") as f:
        json.dump(approved, f, ensure_ascii=False, indent=2)

    await ctx.send(f"✅ {post_id} をキャンセルしました。")


@bot.command(name="help_x")
async def cmd_help(ctx):
    """コマンド一覧を表示"""
    help_text = """**📖 X投稿Bot コマンド一覧**

`!stats [投稿ID] [インプ] [いいね] [RT] [リプ]`
→ パフォーマンスデータを記録
例: `!stats p001 1000 50 20 5`

`!followers [数]`
→ フォロワー数を記録
例: `!followers 1250`

`!report`
→ 最新の分析レポートを表示

`!add_sample [投稿文]`
→ 競合投稿サンプルを追加

`!cancel [投稿ID]`
→ 承認済み投稿をキャンセル
"""
    await ctx.send(help_text)


# ───────────────────────────────────────────────
# 起動
# ───────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)
