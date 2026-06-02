FROM python:3.11-slim

WORKDIR /app

# 依存パッケージのインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードのコピー
COPY scripts/ ./scripts/
COPY data/ ./data/

# dataディレクトリを書き込み可能にする
RUN mkdir -p /app/data/archive

CMD ["python", "scripts/discord_bot.py"]
