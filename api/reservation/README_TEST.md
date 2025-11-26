# ローカル音声会話テストツール

Twilioクレジットを消費せずに、PCのマイク/ヘッドセットで会話テストができます。

## 必要なもの

- PC（macOS/Linux/Windows）
- マイク付きヘッドセット
- Google Cloud 認証情報
- GOOGLE_API_KEY 環境変数

## セットアップ

### 1. 依存ライブラリのインストール

```bash
cd /home/user/gourmet-support
pip install -r api/requirements.txt
```

**注意**: PyAudioのインストールにはシステムライブラリが必要です：

**macOS:**
```bash
brew install portaudio
pip install pyaudio
```

**Ubuntu/Debian:**
```bash
sudo apt-get install portaudio19-dev python3-pyaudio
pip install pyaudio
```

**Windows:**
```bash
pip install pyaudio
```

### 2. 環境変数の設定

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/credentials.json
export GOOGLE_API_KEY=your_gemini_api_key
```

## 使用方法

### 基本的な使い方（音声保存なし）

```bash
python api/reservation/test_voice_conversation.py
```

### 音声ファイルを保存する場合

```bash
python api/reservation/test_voice_conversation.py --save-audio
```

## 操作手順

1. **プログラム起動**
   - ターミナルでコマンドを実行
   - AI挨拶音声と即答相槌音声が事前生成されます

2. **会話開始**
   - マイクに向かって店員役として話す
   - 例: 「はい、レストラン岡部でございます」

3. **発話終了検知**
   - 話し終わると自動的に無音検知が働きます
   - 約1秒の無音で発話終了と判定されます

4. **AI応答**
   - 最初の発話: AI挨拶が再生されます
   - 2回目以降:
     - 「確認」「待って」などのキーワード → 即答相槌
     - それ以外 → Gemini応答

5. **会話を続ける**
   - 再度マイクに向かって話す
   - この流れを繰り返す

6. **終了**
   - Ctrl+C で終了
   - 会話履歴が表示されます
   - `--save-audio` を指定していた場合、音声ファイルが保存されます

## 保存されるファイル

`--save-audio` オプションを使用すると、以下のファイルが `output/conversation_YYYYMMDD_HHMMSS/` に保存されます：

```
output/conversation_20231126_143022/
├── turn_01_staff.wav              # ターン1: 店員発話（WAV）
├── turn_01_ai_greeting.mp3        # ターン1: AI挨拶（MP3）
├── turn_02_staff.wav              # ターン2: 店員発話（WAV）
├── turn_02_ai_quick.mp3           # ターン2: AI即答相槌（MP3）
├── turn_03_staff.wav              # ターン3: 店員発話（WAV）
├── turn_03_ai_gemini.mp3          # ターン3: AIGemini応答（MP3）
└── conversation_transcript.txt    # 会話履歴テキスト
```

## テストシナリオ例

### シナリオ1: 標準的な予約受付

```
店員: はい、レストラン岡部でございます
AI: お忙しいところ恐れ入ります。レストラン岡部様へ...（挨拶）

店員: 確認しますので少々お待ちください
AI: [即答] はい、かしこまりました。

店員: 12月25日19時でしたら空きがございます
AI: [Gemini] ありがとうございます。それでは12月25日19時で...

店員: お名前をお伺いできますでしょうか
AI: [Gemini] 山田太郎様でお願いいたします。

店員: 復唱いたします。12月25日19時、山田太郎様、4名様、テーブル席でお取りいたしました
AI: [Gemini] ありがとうございます。よろしくお願いいたします。
```

### シナリオ2: 満席の場合

```
店員: はい、レストラン岡部でございます
AI: お忙しいところ恐れ入ります...（挨拶）

店員: 確認しますので少々お待ちください
AI: [即答] はい、かしこまりました。

店員: 申し訳ございません。12月25日19時は満席となっております
AI: [Gemini] かしこまりました。それでは、前後30分程度であれば調整可能ですが...
```

## トラブルシューティング

### マイクが認識されない

```bash
# macOS: システム環境設定 > セキュリティとプライバシー > マイク
# で、ターミナルまたはPythonへのマイクアクセスを許可

# Linux: ALSAの設定を確認
arecord -l
```

### PyAudioのインストールエラー

```bash
# macOS
brew install portaudio
pip install --global-option='build_ext' --global-option='-I/opt/homebrew/include' \
    --global-option='-L/opt/homebrew/lib' pyaudio

# Ubuntu
sudo apt-get install python3-dev portaudio19-dev
pip install pyaudio
```

### 音声認識精度が低い

- 静かな環境でテストしてください
- マイクとの距離を調整してください（10-30cm推奨）
- `SILENCE_THRESHOLD` の値を調整できます（test_voice_conversation.py 内）

### 発話終了が検知されない

- `SILENCE_DURATION` を短くしてください（現在1.0秒 → 0.5秒など）
- test_voice_conversation.py の line 68 を編集

## チューニングポイント

会話ロジックをチューニングしたい場合、以下を調整できます：

### 即答キーワード

```python
# line 332
quick_response_keywords = ["確認", "待って", "お待ち", "少々"]
```

新しいキーワードを追加すると、即答相槌が発動します。

### Geminiプロンプト

```python
# line 129-153
def get_gemini_response(user_input: str) -> str:
```

プロンプトを編集して、AI応答の傾向を変更できます。

### VAD感度

```python
# line 67-68
SILENCE_THRESHOLD = 500  # 音声エネルギー閾値（小さいほど敏感）
SILENCE_DURATION = 1.0   # 無音検知時間（秒）
```

## 比較: Twilioテスト vs ローカルテスト

| 項目 | Twilioテスト | ローカルテスト |
|------|-------------|--------------|
| コスト | クレジット消費 | 無料（Google API のみ） |
| 音声品質 | 電話音質（8kHz） | 高音質（16kHz） |
| レイテンシ | 実環境 | ローカル処理のみ |
| テスト内容 | 全フロー | 会話ロジックのみ |
| 推奨用途 | 本番前最終確認 | 開発中のチューニング |

## まとめ

このツールを使って、以下を効率的にテストできます：

1. **会話のかみ合わせ** - Geminiの応答が適切か
2. **即答キーワード** - 検知が正しく動作するか
3. **タイミング** - 応答の速度とリズム（音声ファイルを聞いて確認）

Twilioクレジットを節約しながら、素早く反復テストが可能です！
