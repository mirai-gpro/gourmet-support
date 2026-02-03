# 📑 カスタマーサポートシステム - ファイル一覧 (Gemini版)

## 🎯 プロジェクト情報

- **プロジェクトID**: `hp-support-477512`
- **ディレクトリ**: `C:\customer-support\`
- **バケット名**: `hp-support-477512-prompts`
- **リージョン**: `asia-northeast1`
- **AIモデル**: Google Gemini 2.0 Flash (最新版) 🆕

## ⚡ クイックスタート

```powershell
cd C:\customer-support

# ワンステップセットアップ（初回のみ）
.\quick_setup.ps1 -GeminiApiKey "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# デプロイ
.\deploy.ps1
```

## 📖 ドキュメント

### 基本ドキュメント
| ファイル | 説明 |
|---------|------|
| `README.md` | 詳細な仕様書、API仕様、運用tips |
| `ARCHITECTURE.md` | システム構成図、データフロー、技術スタック |
| `GEMINI_MIGRATION.md` | **Gemini版への変更内容とメリット** 🆕 |

### セットアップガイド
| ファイル | 対象 | 説明 |
|---------|------|------|
| `SETUP_GUIDE.md` | Windows | **推奨**：専用セットアップガイド |
| `QUICKSTART_POWERSHELL.md` | Windows | PowerShell詳細ガイド |
| `QUICKSTART.md` | Linux/Mac | Bash用セットアップガイド |

## 🔧 実行スクリプト

### デプロイスクリプト
| ファイル | 説明 |
|---------|------|
| `deploy.sh` | Cloud Runへデプロイ (Bash) |
| `deploy.ps1` | Cloud Runへデプロイ (PowerShell) |

### プロンプト管理
| ファイル | 説明 |
|---------|------|
| `upload_prompts.sh` | GCSにプロンプトをアップロード (Bash) |
| `upload_prompts.ps1` | GCSにプロンプトをアップロード (PowerShell) |

### セットアップ・実行
| ファイル | 説明 |
|---------|------|
| `quick_setup.ps1` | **推奨**：ワンステップセットアップ |
| `setup.ps1` | 対話型セットアップ（汎用版） |
| `run_local.ps1` | ローカル環境で実行 |

## 💻 アプリケーションコード

### メインアプリケーション
| ファイル | 説明 | 行数 |
|---------|------|------|
| `app_customer_support.py` | Flaskアプリケーション（API、セッション管理） | 420行 |
| `prompt_manager.py` | プロンプト管理（GCS連携、キャッシュ） | 150行 |

### フロントエンド
| ファイル | 説明 |
|---------|------|
| `templates/support.html` | チャットUI（HTML/CSS/JavaScript） |

## 📝 プロンプトファイル

すべて `prompts/` ディレクトリに格納:

| ファイル | 役割 | 使用タイミング |
|---------|------|---------------|
| `support_system.txt` | システムプロンプト | 全リクエスト |
| `initial_greeting.txt` | 初回挨拶 | セッション開始時 |
| `conversation_stage_instruction.txt` | 会話中の指示 | 情報収集フェーズ |
| `inquiry_stage_instruction.txt` | 質問受付の指示 | 質問受付フェーズ |
| `conversation_summary.txt` | 会話要約 | 各ターン後 |
| `final_summary.txt` | 最終要約書 | セッション完了時 |

## 🐳 デプロイ設定

| ファイル | 説明 |
|---------|------|
| `Dockerfile` | コンテナ定義 |
| `requirements.txt` | Python依存パッケージ |

## 📋 ディレクトリ構成

```
customer-support/
├── README.md                           # 詳細仕様書
├── ARCHITECTURE.md                     # システム構成図
├── SETUP_GUIDE.md                      # 専用セットアップガイド ★
├── QUICKSTART.md                       # Bashセットアップ
├── QUICKSTART_POWERSHELL.md           # PowerShellセットアップ
├── INDEX.md                            # このファイル
│
├── app_customer_support.py             # メインアプリ
├── prompt_manager.py                   # プロンプト管理
│
├── templates/
│   └── support.html                    # フロントエンド
│
├── prompts/                            # プロンプトファイル
│   ├── support_system.txt
│   ├── initial_greeting.txt
│   ├── conversation_stage_instruction.txt
│   ├── inquiry_stage_instruction.txt
│   ├── conversation_summary.txt
│   └── final_summary.txt
│
├── Dockerfile                          # コンテナ定義
├── requirements.txt                    # 依存パッケージ
│
├── deploy.sh                           # デプロイ (Bash)
├── deploy.ps1                          # デプロイ (PowerShell)
│
├── upload_prompts.sh                   # プロンプトアップロード (Bash)
├── upload_prompts.ps1                  # プロンプトアップロード (PowerShell)
│
├── quick_setup.ps1                     # ワンステップセットアップ ★
├── setup.ps1                           # 対話型セットアップ
└── run_local.ps1                       # ローカル実行
```

## 🚀 クイックスタート

### Windows (PowerShell) - 推奨

```powershell
cd C:\customer-support

# 1. ワンステップセットアップ
.\quick_setup.ps1 -AnthropicApiKey "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 2. デプロイ
.\deploy.ps1
```

### Linux/Mac (Bash)

```bash
# 1. 環境変数設定
export PROJECT_ID="your-project"
export ANTHROPIC_API_KEY="your-key"

# 2. プロンプトアップロード
./upload_prompts.sh

# 3. デプロイ
./deploy.sh
```

## 📚 読む順番

### 初めて使う方（推奨）

1. **SETUP_GUIDE.md**
   - プロジェクト専用の手順書
   - 最短10分でセットアップ完了

2. **README.md**
   - 機能の詳細、API仕様を理解

3. **ARCHITECTURE.md**
   - システムの全体像を把握

### 別環境で使いたい方

1. **QUICKSTART_POWERSHELL.md** (Windows) または **QUICKSTART.md** (Linux/Mac)
   - 最短5分でセットアップ完了

2. **README.md**
   - 機能の詳細、API仕様を理解

3. **ARCHITECTURE.md**
   - システムの全体像を把握

### カスタマイズしたい方

1. **README.md** の「プロンプトのカスタマイズ」セクション
2. **prompts/** 内のファイルを編集
3. `upload_prompts.ps1` または `upload_prompts.sh` で再アップロード

### 開発者向け

1. **ARCHITECTURE.md** でシステム構成を理解
2. **app_customer_support.py** のコードを確認
3. **prompt_manager.py** でプロンプト管理の仕組みを理解

## 🔍 目的別ファイル検索

### セットアップしたい
- Windows: `QUICKSTART_POWERSHELL.md`, `setup.ps1`
- Linux/Mac: `QUICKSTART.md`, `deploy.sh`

### 会話フローを変更したい
- `prompts/conversation_stage_instruction.txt`
- `prompts/initial_greeting.txt`

### 要約書のフォーマットを変更したい
- `prompts/final_summary.txt`

### ローカルで開発したい
- `run_local.ps1` (Windows)
- `README.md` の「ローカル実行」セクション (Linux/Mac)

### システム構成を理解したい
- `ARCHITECTURE.md`

### API仕様を知りたい
- `README.md` の「API エンドポイント」セクション

### トラブルシューティング
- `QUICKSTART_POWERSHELL.md` (Windows)
- `QUICKSTART.md` (Linux/Mac)
- `README.md` の「トラブルシューティング」セクション

## 💡 ヒント

### プロンプトの即時反映

GCSのプロンプトを更新後、60分以内にキャッシュから自動更新されます。
即座に反映させたい場合:

```powershell
# Cloud Runを再起動
gcloud run services update customer-support --region=asia-northeast1
```

### ログ確認

```powershell
# リアルタイム
gcloud run services logs tail customer-support --region=asia-northeast1

# 過去のログ
gcloud run services logs read customer-support --limit=50
```

### コスト管理

- Cloud Runは使用量に応じた従量課金
- 無料枠: 月間200万リクエスト、36万GB秒
- Claude API: 会話の長さに応じて課金
- 概算: 月間1000セッションで$5程度

## 📞 サポート

問題が発生した場合:

1. 該当するトラブルシューティングセクションを確認
2. ログでエラーメッセージを確認
3. システム構成（ARCHITECTURE.md）で全体像を把握

---

**プロジェクトバージョン**: 1.0.0  
**最終更新**: 2024-11-08
