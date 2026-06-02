# X投稿自動化システム（@kansai_job_）

関西の就活生（28〜30卒）向けXアカウントの投稿運用を自動化するシステム。

## アーキテクチャ

```
GitHub Actions（毎日20:00 JST）
  → リサーチ → ファクトチェック → 投稿文生成 → 品質チェック
  → Fly.io（Discord Bot 24時間常駐）
    → Discordに投稿案が届く
    → スマホで ✅ / ❌ 承認
    → Buffer にコピペ（5分）
    → Bufferが時間通りに自動投稿
```

## セットアップ手順

### 1. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各トークンを設定
```

### 2. 依存パッケージのインストール

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Discord Bot の作成

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリを作成
2. Bot トークンを `.env` の `DISCORD_BOT_TOKEN` に設定
3. 自分専用サーバーのチャンネルIDを `DISCORD_CHANNEL_ID` に設定

### 4. GitHub Actions のシークレット設定

リポジトリの Settings → Secrets に以下を登録：
- `DISCORD_BOT_TOKEN`
- `DISCORD_CHANNEL_ID`
- `ANTHROPIC_API_KEY`

### 5. Fly.io への Discord Bot デプロイ

```bash
# Fly.io CLIのインストール
brew install flyctl
flyctl auth login

# デプロイ
cd x-auto-post
flyctl launch
```

## 日次オペレーション

1. 毎日20:00にGitHub Actionsが自動で投稿案を生成
2. スマホのDiscordアプリで ✅ / ❌ を押して承認（5分）
3. 承認済み投稿をBufferアプリにコピペ（5分）
4. Bufferが翌日の指定時刻に自動投稿

## Discordコマンド

| コマンド | 説明 |
|----------|------|
| `!stats [投稿ID] [インプ] [いいね] [RT] [リプ]` | パフォーマンスデータを記録 |
| `!followers [数]` | その日のフォロワー数を記録 |
| `!report` | 最新の分析レポートを表示 |
| `!add_sample [投稿文]` | 競合アカウントの参考投稿を追加 |
| `!cancel [投稿ID]` | 承認済み投稿をキャンセル |

## ファイル構成

```
x-auto-post/
├── .env                    # 環境変数（gitignore済み）
├── .env.example            # 環境変数テンプレート
├── .github/workflows/      # GitHub Actions設定
├── .claude/agents/         # サブエージェント定義
├── scripts/                # 実行スクリプト
├── data/                   # データファイル（gitignore済み）
└── requirements.txt
```
