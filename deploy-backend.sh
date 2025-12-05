#!/bin/bash

# Google Cloud Run デプロイスクリプト

# 設定
PROJECT_ID="your-gcp-project-id"  # ← GCPプロジェクトIDに変更
SERVICE_NAME="gourmet-support-api"
REGION="asia-northeast1"  # 東京リージョン
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "🚀 Google Cloud Runにデプロイ開始..."

# 1. Dockerイメージをビルド
echo "📦 Dockerイメージをビルド中..."
docker build -t ${IMAGE_NAME} .

# 2. イメージをGoogle Container Registryにプッシュ
echo "☁️ GCRにイメージをプッシュ中..."
docker push ${IMAGE_NAME}

# 3. Cloud Runにデプロイ
echo "🌐 Cloud Runにデプロイ中..."
gcloud run deploy ${SERVICE_NAME} \
  --image ${IMAGE_NAME} \
  --platform managed \
  --region ${REGION} \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}" \
  --set-env-vars "GOOGLE_PLACES_API_KEY=${GOOGLE_PLACES_API_KEY}" \
  --set-env-vars "TRIPADVISOR_API_KEY=${TRIPADVISOR_API_KEY}" \
  --set-env-vars "HOTPEPPER_API_KEY=${HOTPEPPER_API_KEY}" \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 10

echo "✅ デプロイ完了！"
echo "🔗 サービスURL:"
gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format 'value(status.url)'
