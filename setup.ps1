# 初期セットアップスクリプト (PowerShell)

param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectId,
    
    [Parameter(Mandatory=$true)]
    [string]$AnthropicApiKey,
    
    [string]$Region = "asia-northeast1"
)

$BUCKET_NAME = "$ProjectId-prompts"

Write-Host "====================================" -ForegroundColor Cyan
Write-Host "カスタマーサポートシステム セットアップ" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "プロジェクトID: $ProjectId" -ForegroundColor Yellow
Write-Host "リージョン: $Region" -ForegroundColor Yellow
Write-Host "バケット名: $BUCKET_NAME" -ForegroundColor Yellow
Write-Host ""

# 1. GCP APIを有効化
Write-Host "[1/5] GCP APIを有効化中..." -ForegroundColor Yellow
$apis = @(
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com"
)

foreach ($api in $apis) {
    Write-Host "  → $api" -ForegroundColor Gray
    gcloud services enable $api --project $ProjectId
}

Write-Host "✅ API有効化完了" -ForegroundColor Green
Write-Host ""

# 2. Firestoreを初期化
Write-Host "[2/5] Firestoreを初期化中..." -ForegroundColor Yellow
gcloud firestore databases create --location=$Region --project=$ProjectId 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Firestore初期化完了" -ForegroundColor Green
} else {
    Write-Host "⚠️  Firestoreは既に初期化済みです" -ForegroundColor Yellow
}
Write-Host ""

# 3. GCSバケットを作成
Write-Host "[3/5] GCSバケットを作成中..." -ForegroundColor Yellow
gsutil mb -l $Region "gs://$BUCKET_NAME" 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ バケット作成完了: gs://$BUCKET_NAME" -ForegroundColor Green
} else {
    Write-Host "⚠️  バケットは既に存在します" -ForegroundColor Yellow
}
Write-Host ""

# 4. プロンプトをアップロード
Write-Host "[4/5] プロンプトファイルをアップロード中..." -ForegroundColor Yellow

$files = Get-ChildItem -Path "prompts\*.txt"
foreach ($file in $files) {
    $filename = $file.Name
    Write-Host "  → $filename" -ForegroundColor Gray
    gsutil cp $file.FullName "gs://$BUCKET_NAME/prompts/$filename" 2>&1 | Out-Null
}

Write-Host "✅ プロンプトアップロード完了" -ForegroundColor Green
Write-Host ""

# 5. deploy.ps1を更新
Write-Host "[5/5] デプロイスクリプトを設定中..." -ForegroundColor Yellow

$deployScript = Get-Content "deploy.ps1" -Raw
$deployScript = $deployScript -replace 'PROJECT_ID = "your-project-id"', "PROJECT_ID = `"$ProjectId`""
$deployScript = $deployScript -replace 'ANTHROPIC_API_KEY = "your-api-key"', "ANTHROPIC_API_KEY = `"$AnthropicApiKey`""
$deployScript = $deployScript -replace 'PROMPTS_BUCKET_NAME = "your-prompts-bucket"', "PROMPTS_BUCKET_NAME = `"$BUCKET_NAME`""
$deployScript | Set-Content "deploy.ps1"

Write-Host "✅ デプロイスクリプト設定完了" -ForegroundColor Green
Write-Host ""

# 完了メッセージ
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "✅ セットアップが完了しました!" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "次のステップ:" -ForegroundColor Cyan
Write-Host "1. デプロイを実行:" -ForegroundColor White
Write-Host "   .\deploy.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "2. デプロイ後、URLにアクセスして動作確認" -ForegroundColor White
Write-Host ""
