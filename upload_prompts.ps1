# プロンプトをGCSにアップロード (PowerShell)

# 設定
$BUCKET_NAME = "hp-support-477512-prompts"

Write-Host "====================================" -ForegroundColor Cyan
Write-Host "プロンプトをGCSにアップロード" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# バケットの存在確認
$bucketExists = gsutil ls "gs://$BUCKET_NAME" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "バケットが存在しません。作成しますか? (Y/N)" -ForegroundColor Yellow
    $answer = Read-Host
    
    if ($answer -eq "Y" -or $answer -eq "y") {
        gsutil mb -l asia-northeast1 "gs://$BUCKET_NAME"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ バケットを作成しました" -ForegroundColor Green
        } else {
            Write-Host "❌ バケットの作成に失敗しました" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "❌ 中断しました" -ForegroundColor Red
        exit 1
    }
}

# プロンプトファイルをアップロード
Write-Host ""
Write-Host "プロンプトファイルをアップロード中..." -ForegroundColor Yellow
Write-Host ""

$files = Get-ChildItem -Path "prompts\*.txt"
foreach ($file in $files) {
    $filename = $file.Name
    Write-Host "  → $filename" -ForegroundColor Gray
    gsutil cp $file.FullName "gs://$BUCKET_NAME/prompts/$filename"
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    ❌ アップロード失敗: $filename" -ForegroundColor Red
    } else {
        Write-Host "    ✅ アップロード完了: $filename" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "✅ アップロード完了!" -ForegroundColor Green
Write-Host ""
Write-Host "アップロードされたファイル:" -ForegroundColor Cyan
gsutil ls "gs://$BUCKET_NAME/prompts/"
