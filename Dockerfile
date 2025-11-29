FROM python:3.11-slim

WORKDIR /app

# 依存関係をインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションファイルをコピー
COPY app_customer_support.py .
COPY prompt_manager.py .

# ポートを公開
EXPOSE 8080

# Gunicorn + eventlet ワーカーで実行（WebSocket対応）
CMD exec gunicorn --bind :$PORT --workers 1 --worker-class eventlet --timeout 0 app_customer_support:app
