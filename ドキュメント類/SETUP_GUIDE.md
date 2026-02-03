# セットアップガイド (Gemini版)

**プロジェクト情報**
- プロジェクトID: `hp-support-477512`
- ディレクトリ: `C:\customer-support\`
- バケット名: `hp-support-477512-prompts`
- **AI モデル**: Google Gemini 2.0 Flash (最新版)

## 前提条件

- [x] Google Cloud SDK インストール済み
- [x] PowerShell 5.1以降
- [x] プロジェクト作成済み (`customer-support-477613`)
- [ ] Gemini APIキー取得

## セットアップ手順

### Step 1: Gemini APIキーを取得

https://aistudio.google.com/ にアクセスして、APIキーを取得してください。

1. **Get API key** をクリック
2. **Create API key** を選択
3. プロジェクト `customer-support-477613` を選択
4. 生成されたキーをコピー（`AIza...` で始まる文字列）

### Step 2: ディレクトリに移動

```powershell
cd C:\customer-support
```

### Step 3: 環境変数を設定

```powershell
# APIキーを設定（実際のキーに置き換え）
$env:GEMINI_API_KEY = "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# プロジェクトを設定
gcloud config set project hp-support-477512
```

### Step 4: GCP APIを有効化

```powershell
gcloud services enable `
  run.googleapis.com `
  cloudbuild.googleapis.com `
  firestore.googleapis.com `
  storage.googleapis.com `
  --project customer-support-477613
```

**所要時間: 約2-3分**

### Step 5: Firestoreを初期化

```powershell
gcloud firestore databases create `
  --location=asia-northeast1 `
  --project=customer-support-477613
```

**所要時間: 約1分**

### Step 6: GCSバケットを作成

```powershell
gsutil mb -l asia-northeast1 gs://customer-support-477613-prompts
```

### Step 7: プロンプトをアップロード

```powershell
.\upload_prompts.ps1
```

アップロードされるファイル:
- support_system.txt
- initial_greeting.txt
- conversation_stage_instruction.txt
- inquiry_stage_instruction.txt
- conversation_summary.txt
- final_summary.txt

### Step 8: deploy.ps1を編集

```powershell
notepad deploy.ps1
```

以下の行を編集:
```powershell
$GEMINI_API_KEY = "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
```

実際のAPIキーに置き換えて保存してください。

### Step 9: デプロイ実行

```powershell
.\deploy.ps1
```

**所要時間: 約5-7分**

デプロイが完了すると、URLが表示されます:
```
✅ デプロイが完了しました!
サービスURL: https://customer-support-xxxxxxxxx-an.a.run.app
```

### Step 10: 動作確認

表示されたURLにブラウザでアクセスして、チャットが開始されることを確認してください。

## チェックリスト

セットアップ完了の確認:

- [ ] GCP APIが有効化されている
- [ ] Firestoreデータベースが作成されている
- [ ] GCSバケットが作成されている
- [ ] プロンプトファイルがアップロードされている（6個）
- [ ] Cloud Runサービスがデプロイされている
- [ ] ブラウザでチャットが動作する

## トラブルシューティング

### エラー: "API not enabled"

```powershell
# APIを再度有効化
gcloud services enable run.googleapis.com --project customer-support-477613
```

### エラー: "Bucket already exists"

```powershell
# バケットが既に存在する場合はスキップしてOK
# または、別の名前を使用
gsutil mb -l asia-northeast1 gs://customer-support-477613-prompts-v2
```

その場合、`deploy.ps1`の`$PROMPTS_BUCKET_NAME`も変更してください。

### エラー: "Permission denied"

```powershell
# 認証を再実行
gcloud auth login
gcloud auth application-default login
```

### プロンプトが読み込まれない

```powershell
# アップロードされたか確認
gsutil ls gs://customer-support-477613-prompts/prompts/

# 再アップロード
.\upload_prompts.ps1
```

### デプロイが失敗する

```powershell
# ビルドログを確認
gcloud builds list --limit=1 --project=customer-support-477613

# エラーログを確認
gcloud builds log <BUILD_ID> --project=customer-support-477613
```

## 環境変数の永続化（オプション）

毎回APIキーを入力したくない場合:

```powershell
# PowerShellプロファイルに追加
notepad $PROFILE

# 以下を追加
$env:ANTHROPIC_API_KEY = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

## ローカルでの開発

デプロイせずにローカルで実行する場合:

```powershell
.\run_local.ps1 `
  -AnthropicApiKey "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx" `
  -BucketName "customer-support-477613-prompts"
```

http://localhost:8080 にアクセス

## プロンプトの更新

プロンプトを変更する場合:

```powershell
# 1. ローカルで編集
notepad prompts\initial_greeting.txt

# 2. アップロード
gsutil cp prompts\initial_greeting.txt gs://customer-support-477613-prompts/prompts/

# 3. 即座に反映させる場合（オプション）
gcloud run services update customer-support --region=asia-northeast1
```

## コスト管理

### 現在の費用を確認

Google Cloud Console → 請求 → customer-support-477613

### 概算コスト（月間1000セッション）

- Cloud Run: 無料枠内（$0）
- Firestore: 無料枠内（$0）
- Cloud Storage: 無料枠内（$0）
- Claude API: $3-5/月

**合計: 月額$5程度**

### コスト削減のヒント

1. **最小インスタンス数を0に設定**（デフォルト）
2. **タイムアウトを短く**（現在300秒）
3. **メモリを減らす**（512Mi → 256Mi、必要に応じて）

## 次のステップ

1. [ ] カスタマイズ: プロンプトを編集して動作を調整
2. [ ] 管理画面: Firestoreからセッション一覧を表示する画面を作成
3. [ ] 通知設定: 新規問い合わせをメールで通知
4. [ ] Slack連携: 要約書をSlackに自動投稿

詳細は `README.md` を参照してください。

---

**何か問題があれば、このガイドのトラブルシューティングセクションを確認してください！**
