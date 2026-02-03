# 汎用カスタマーサポートシステム (Gemini版)

プロンプト駆動型のカスタマーサポートチャットシステムです。

## プロジェクト情報

- **プロジェクトID**: `hp-support-477512`
- **ディレクトリ**: `C:\customer-support\`
- **バケット名**: `hp-support-477512-prompts`
- **リージョン**: `asia-northeast1`
- **AIモデル**: Google Gemini 2.0 Flash (最新版)

## クイックスタート

```powershell
# APIキーを準備（Google AI Studio から取得）
$GEMINI_API_KEY = "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# ワンステップセットアップ
.\quick_setup.ps1 -GeminiApiKey $GEMINI_API_KEY

# デプロイ
.\deploy.ps1
```

詳細は `SETUP_GUIDE.md` を参照してください。

## 特徴

### 1. LLMが主導する会話フロー
- 初回に概要説明と質問を提示
- プロンプトで設定された質問フローに沿って情報収集

### 2. 会話要約による確認
- ユーザーの発言を都度要約して内容確認
- 誤解を防ぎ、正確な情報収集を実現

### 3. 即答しない設計
- 質問は受け付けるが、その場では回答しない
- 会話ログを要約した「質問要約書」を生成
- スタッフが後日、要約書を見て対応

### 4. Firestore連携
- 全会話ログを保存
- セッション管理とステータス追跡

### 5. GCS プロンプト管理
- プロンプトをGCSで一元管理
- デプロイ不要でプロンプト更新可能

## アーキテクチャ

```
[ブラウザ] 
    ↓
[Cloud Run] 
    ↓
[Claude API] ← プロンプト管理
    ↓
[Firestore] ← 会話ログ保存
```

## セットアップ

### 1. 環境変数の設定

```bash
ANTHROPIC_API_KEY=your_api_key
PROMPTS_BUCKET_NAME=your-prompts-bucket
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### 2. GCSバケットの準備

プロンプト用バケットを作成し、以下のファイルをアップロード:

```
gs://your-prompts-bucket/prompts/
├── support_system.txt           # システムプロンプト
├── initial_greeting.txt          # 初回挨拶
├── conversation_stage_instruction.txt  # 会話中の指示
├── inquiry_stage_instruction.txt       # 質問受付時の指示
├── conversation_summary.txt      # 会話要約用
└── final_summary.txt            # 最終要約書用
```

### 3. Firestoreの設定

コレクション `support_sessions` を作成（自動的に作成されます）

ドキュメント構造:
```json
{
  "session_id": "uuid",
  "created_at": "timestamp",
  "updated_at": "timestamp",
  "status": "active|completed",
  "messages": [
    {
      "role": "user|assistant",
      "content": "message text",
      "type": "chat|summary|final_summary",
      "timestamp": "ISO8601"
    }
  ],
  "inquiry_summary": "最終要約テキスト"
}
```

### 4. ローカル実行

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# アプリケーション起動
python app_customer_support.py
```

http://localhost:8080 にアクセス

### 5. Cloud Runへデプロイ

#### Bash (Linux/Mac)

```bash
# イメージをビルド
gcloud builds submit --tag gcr.io/PROJECT_ID/customer-support

# Cloud Runにデプロイ
gcloud run deploy customer-support \
  --image gcr.io/PROJECT_ID/customer-support \
  --platform managed \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=xxx,PROMPTS_BUCKET_NAME=your-bucket \
  --memory 512Mi \
  --timeout 300
```

または、デプロイスクリプトを使用:

```bash
./deploy.sh
```

#### PowerShell (Windows)

```powershell
# deploy.ps1を編集して設定
notepad deploy.ps1

# デプロイ実行
.\deploy.ps1
```

または、自動セットアップを使用:

```powershell
.\setup.ps1 -ProjectId "your-project-id" -AnthropicApiKey "your-key"
.\deploy.ps1
```

詳細は `QUICKSTART_POWERSHELL.md` を参照してください。

## API エンドポイント

### POST /api/session/start
セッション開始

**Request:**
```json
{
  "user_info": {}
}
```

**Response:**
```json
{
  "session_id": "uuid",
  "initial_message": "初回メッセージ"
}
```

### POST /api/chat
メッセージ送信

**Request:**
```json
{
  "session_id": "uuid",
  "message": "ユーザーメッセージ",
  "stage": "conversation"
}
```

**Response:**
```json
{
  "response": "アシスタントの応答",
  "summary": "会話要約（会話中の場合）",
  "should_confirm": true
}
```

### POST /api/finalize
セッション完了（質問要約書生成）

**Request:**
```json
{
  "session_id": "uuid"
}
```

**Response:**
```json
{
  "summary": "質問要約書の内容",
  "session_id": "uuid"
}
```

### GET /api/session/:session_id
セッション情報取得

**Response:**
```json
{
  "session_id": "uuid",
  "status": "active",
  "messages": [...],
  "inquiry_summary": "..."
}
```

## プロンプトのカスタマイズ

### 1. 初回挨拶の変更

`prompts/initial_greeting.txt` を編集:

```
こんにちは!〇〇サポートです。

今日はどのようなご用件でしょうか?
まずは以下についてお聞かせください...
```

### 2. 質問フローの設定

`prompts/conversation_stage_instruction.txt` で質問の進め方を設定:

```
【重要な指示】
次の順番で質問してください:
1. 課題の内容
2. 発生時期
3. 影響範囲
4. 希望する解決策
```

### 3. 要約書フォーマットの変更

`prompts/final_summary.txt` で出力形式を設定:

```
# お客様からのお問い合わせ

## 日時
{timestamp}

## 内容
...
```

## 運用tips

### スタッフ向けダッシュボードの追加

Firestoreから `support_sessions` コレクションを読み取り、
管理画面を構築することで、スタッフが要約書を確認できます。

```python
# 完了済みセッションを取得
sessions = db.collection('support_sessions')\
  .where('status', '==', 'completed')\
  .order_by('created_at', direction=firestore.Query.DESCENDING)\
  .limit(50)\
  .stream()
```

### メール通知の追加

セッション完了時に、要約書をメールで送信:

```python
# /api/finalize のレスポンス後に
send_email(
  to='support@example.com',
  subject='新しいお問い合わせ',
  body=final_summary
)
```

### ステージ自動遷移

会話の進行度に応じて自動的にステージを切り替える:

```python
# メッセージ数でステージ判定
message_count = len(session.get_messages(include_types=['chat']))
if message_count >= 10:
    current_stage = 'inquiry'
```

## トラブルシューティング

### プロンプトが読み込まれない

- GCSバケット名の確認
- サービスアカウントの権限確認（Storage Object Viewer）
- ログ確認: `[PromptManager]` で検索

### Claude APIエラー

- APIキーの確認
- レート制限の確認
- ログ確認: `[Assistant] Claude APIエラー`

### Firestore接続エラー

- サービスアカウントの権限確認
- Firestore APIの有効化確認
- プロジェクトIDの確認

## ライセンス

MIT License
