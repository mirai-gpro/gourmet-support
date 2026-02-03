# 🚀 Gemini 2.0 版 セットアップ手順（簡易版）

## ✅ 準備完了しているもの
- Google Cloud SDK インストール済み
- プロジェクト作成済み: `hp-support-477512`
- ディレクトリ作成済み: `C:\customer-support\`
- ファイル配置準備OK

---

## 📝 セットアップ（3ステップ）

### Step 1: Gemini APIキーを取得（5分）

1. https://aistudio.google.com/ を開く
2. ログイン（`gpro.mirai@gmail.com`）
3. **Get API key** → **Create API key**
4. プロジェクト `hp-support-477512` を選択
5. 生成されたキーをコピー（`AIza...`で始まる）

---

### Step 2: セットアップ実行（5分）

PowerShellを開いて実行:

```powershell
# ディレクトリに移動
cd C:\customer-support

# プロジェクト設定
gcloud config set project hp-support-477512

# セットアップ（APIキーを実際のものに置き換え）
.\quick_setup.ps1 -GeminiApiKey "AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
```

このコマンドが自動実行すること:
- ✅ GCP APIを有効化
- ✅ Firestoreを作成
- ✅ GCSバケットを作成
- ✅ プロンプトをアップロード
- ✅ deploy.ps1にAPIキーを設定

---

### Step 3: デプロイ（7分）

```powershell
.\deploy.ps1
```

完了するとURLが表示されます:
```
✅ デプロイが完了しました!
サービスURL: https://customer-support-xxxxx-an.a.run.app
```

このURLをブラウザで開いて動作確認！

---

## 🎉 完了！

所要時間: **約15分**

---

## 💰 コスト

**現在無料！**（Gemini 2.0 Flash は実験版として無料提供中）

正式版後の予想: 月間1000セッション約$0.50
（それでもClaude版の10分の1！）

---

## ✨ Gemini 2.0 の特徴

- 🆕 **最新モデル**（2024年12月リリース）
- 🚀 **超高速**（従来の2倍）
- 💰 **現在無料**
- 🧠 **推論能力向上**
- 🇯🇵 **日本語が得意**

---

## ❓ トラブルシューティング

### エラーが出たら

```powershell
# ログを確認
gcloud run services logs read customer-support --limit=50 --region=asia-northeast1
```

### プロンプトを変更したい

```powershell
# 編集
notepad prompts\initial_greeting.txt

# アップロード
.\upload_prompts.ps1
```

---

## 📚 詳細情報

- **SETUP_GUIDE.md** - 詳細な手順
- **GEMINI_MIGRATION.md** - Gemini 2.0の説明
- **README.md** - 全体仕様

---

**それでは始めましょう！** 🚀
