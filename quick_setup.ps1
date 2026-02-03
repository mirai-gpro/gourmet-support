# ワンステップセットアップスクリプト (Gemini版)
# プロジェクト: customer-support-477613

param(
    [Parameter(Mandatory=$true)]
    [string]$GeminiApiKey
)

$PROJECT_ID = "hp-support-477512"
$REGION = "asia-northeast1"
$BUCKET_NAME = "hp-support-477512-prompts"

Write-Host "====================================" -ForegroundColor Cyan
Write-Host "カスタマーサポートシステム" -ForegroundColor Cyan
Write-Host "ワンステップセットアップ" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "プロジェクトID: $PROJECT_ID" -ForegroundColor Yellow
Write-Host "リージョン: $REGION" -ForegroundColor Yellow
Write-Host ""

# プロジェクト設定
Write-Host "[0/6] プロジェクトを設定中..." -ForegroundColor Yellow
gcloud config set project $PROJECT_ID
Write-Host "✅ プロジェクト設定完了" -ForegroundColor Green
Write-Host ""

# 1. GCP APIを有効化
Write-Host "[1/6] GCP APIを有効化中..." -ForegroundColor Yellow
$apis = @(
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com"
)

foreach ($api in $apis) {
    Write-Host "  → $api" -ForegroundColor Gray
    gcloud services enable $api --project $PROJECT_ID 2>&1 | Out-Null
}
Write-Host "✅ API有効化完了" -ForegroundColor Green
Write-Host ""

# 2. Firestoreを初期化
Write-Host "[2/6] Firestoreを初期化中..." -ForegroundColor Yellow
$firestoreResult = gcloud firestore databases create --location=$REGION --project=$PROJECT_ID 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Firestore初期化完了" -ForegroundColor Green
} else {
    if ($firestoreResult -like "*already exists*") {
        Write-Host "⚠️  Firestoreは既に初期化済みです" -ForegroundColor Yellow
    } else {
        Write-Host "❌ Firestore初期化エラー: $firestoreResult" -ForegroundColor Red
    }
}
Write-Host ""

# 3. GCSバケットを作成
Write-Host "[3/6] GCSバケットを作成中..." -ForegroundColor Yellow
$bucketResult = gsutil mb -l $REGION "gs://$BUCKET_NAME" 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ バケット作成完了: gs://$BUCKET_NAME" -ForegroundColor Green
} else {
    if ($bucketResult -like "*already exists*" -or $bucketResult -like "*409*") {
        Write-Host "⚠️  バケットは既に存在します" -ForegroundColor Yellow
    } else {
        Write-Host "❌ バケット作成エラー: $bucketResult" -ForegroundColor Red
    }
}
Write-Host ""

# 4. プロンプトをアップロード
Write-Host "[4/6] プロンプトファイルをアップロード中..." -ForegroundColor Yellow

if (Test-Path "prompts") {
    $files = Get-ChildItem -Path "prompts\*.txt"
    $uploadCount = 0
    
    foreach ($file in $files) {
        $filename = $file.Name
        gsutil cp $file.FullName "gs://$BUCKET_NAME/prompts/$filename" 2>&1 | Out-Null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✓ $filename" -ForegroundColor Gray
            $uploadCount++
        } else {
            Write-Host "  ✗ $filename" -ForegroundColor Red
        }
    }
    
    Write-Host "✅ プロンプトアップロード完了 ($uploadCount/$($files.Count))" -ForegroundColor Green
} else {
    Write-Host "❌ promptsディレクトリが見つかりません" -ForegroundColor Red
}
Write-Host ""

# 5. deploy.ps1を更新
Write-Host "[5/6] デプロイスクリプトを設定中..." -ForegroundColor Yellow

if (Test-Path "deploy.ps1") {
    $deployScript = Get-Content "deploy.ps1" -Raw
    $deployScript = $deployScript -replace '\$GEMINI_API_KEY = "your-gemini-api-key"', "`$GEMINI_API_KEY = `"$GeminiApiKey`""
    $deployScript | Set-Content "deploy.ps1"
    
    Write-Host "✅ デプロイスクリプト設定完了" -ForegroundColor Green
} else {
    Write-Host "❌ deploy.ps1が見つかりません" -ForegroundColor Red
}
Write-Host ""

# 6. 確認
Write-Host "[6/6] セットアップ状況を確認中..." -ForegroundColor Yellow
Write-Host ""

# API確認
Write-Host "📌 有効化されたAPI:" -ForegroundColor Cyan
foreach ($api in $apis) {
    $status = gcloud services list --enabled --filter="name:$api" --format="value(name)" 2>&1
    if ($status) {
        Write-Host "  ✓ $api" -ForegroundColor Green
    } else {
        Write-Host "  ✗ $api" -ForegroundColor Red
    }
}
Write-Host ""

# バケット確認
Write-Host "📌 プロンプトファイル:" -ForegroundColor Cyan
$promptFiles = gsutil ls "gs://$BUCKET_NAME/prompts/" 2>&1
if ($LASTEXITCODE -eq 0) {
    foreach ($file in $promptFiles) {
        $filename = Split-Path $file -Leaf
        Write-Host "  ✓ $filename" -ForegroundColor Green
    }
} else {
    Write-Host "  ✗ プロンプトファイルが見つかりません" -ForegroundColor Red
}
Write-Host ""

# 完了メッセージ
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "✅ セットアップが完了しました!" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "次のステップ:" -ForegroundColor Yellow
Write-Host "1. デプロイを実行:" -ForegroundColor White
Write-Host "   .\deploy.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "2. デプロイ後、URLにアクセスして動作確認" -ForegroundColor White
Write-Host ""
Write-Host "詳細なログは以下で確認できます:" -ForegroundColor Gray
Write-Host "  gcloud run services logs tail customer-support --region=$REGION" -ForegroundColor Gray
Write-Host ""
