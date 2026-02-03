# ローカル実行スクリプト (PowerShell)

param(
    [string]$AnthropicApiKey,
    [string]$BucketName,
    [string]$CredentialsPath = "",
    [int]$Port = 8080
)

Write-Host "====================================" -ForegroundColor Cyan
Write-Host "ローカル環境で実行" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# 環境変数の確認
if (-not $AnthropicApiKey) {
    Write-Host "ANTHROPIC_API_KEYを入力してください:" -ForegroundColor Yellow
    $AnthropicApiKey = Read-Host
}

if (-not $BucketName) {
    Write-Host "PROMPTS_BUCKET_NAMEを入力してください:" -ForegroundColor Yellow
    $BucketName = Read-Host
}

if (-not $CredentialsPath) {
    Write-Host "サービスアカウントキーのパスを入力してください (空でEnter = デフォルト認証):" -ForegroundColor Yellow
    $CredentialsPath = Read-Host
}

# 環境変数を設定
$env:ANTHROPIC_API_KEY = $AnthropicApiKey
$env:PROMPTS_BUCKET_NAME = $BucketName
$env:PORT = $Port

if ($CredentialsPath -and (Test-Path $CredentialsPath)) {
    $env:GOOGLE_APPLICATION_CREDENTIALS = $CredentialsPath
    Write-Host "✅ 認証情報を設定: $CredentialsPath" -ForegroundColor Green
} else {
    Write-Host "ℹ️  デフォルト認証を使用" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "設定:" -ForegroundColor Yellow
Write-Host "  API Key: $($AnthropicApiKey.Substring(0, 10))..." -ForegroundColor Gray
Write-Host "  Bucket: $BucketName" -ForegroundColor Gray
Write-Host "  Port: $Port" -ForegroundColor Gray
Write-Host ""

# 依存パッケージの確認
Write-Host "依存パッケージをインストール中..." -ForegroundColor Yellow
pip install -r requirements.txt --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ パッケージのインストールに失敗しました" -ForegroundColor Red
    exit 1
}

Write-Host "✅ パッケージインストール完了" -ForegroundColor Green
Write-Host ""

# アプリケーション起動
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "アプリケーションを起動します" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "URL: http://localhost:$Port" -ForegroundColor Yellow
Write-Host "終了: Ctrl+C" -ForegroundColor Gray
Write-Host ""

python app_customer_support.py
