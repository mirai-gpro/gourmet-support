# -*- coding: utf-8 -*-
"""
改善されたプロンプト管理 (グルメサポートAI対応)
"""
import os
from google.cloud import storage
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class PromptManager:
    def __init__(self):
        self.bucket_name = os.getenv("PROMPTS_BUCKET_NAME", "")
        self.prompts = {}  # プロンプトのキャッシュ
        self.last_loaded = None
        self.cache_minutes = 60  # キャッシュ有効期限を60分に延長

        # 初期化時に即座に読み込み
        if self.bucket_name:
            try:
                self.client = storage.Client()
                self.bucket = self.client.bucket(self.bucket_name)
                logger.info(f"[PromptManager] バケット: {self.bucket_name}")
                self._load_from_gcs()  # 初期化時に読み込み
            except Exception as e:
                logger.error(f"[PromptManager] GCS初期化失敗: {e}")
                self._load_defaults()
        else:
            logger.warning("[PromptManager] バケット未設定 - デフォルトプロンプト使用")
            self._load_defaults()

    def get(self, name, **variables):
        """
        プロンプトを取得

        Args:
            name: プロンプト名（例: "support_system"）
            **variables: テンプレート変数（例: session_id="abc"）

        Returns:
            展開されたプロンプト文字列
        """
        # キャッシュチェック（非同期で更新）
        if self._is_cache_expired():
            try:
                self._load_from_gcs()
            except Exception as e:
                logger.warning(f"[PromptManager] 更新失敗、キャッシュ使用: {e}")

        # プロンプト取得
        template = self.prompts.get(name)
        if not template:
            logger.warning(f"[PromptManager] プロンプト未登録: {name}")
            template = self._get_default(name)

        # 変数展開
        try:
            return template.format(**variables) if variables else template
        except KeyError as e:
            logger.warning(f"[PromptManager] 変数不足: {e}")
            return template

    def reload(self):
        """プロンプトを強制再読み込み（管理用API）"""
        self._load_from_gcs(force=True)
        return {
            "loaded": list(self.prompts.keys()),
            "time": self.last_loaded.isoformat() if self.last_loaded else None,
            "source": "gcs" if self.bucket_name else "default"
        }

    def _is_cache_expired(self):
        """キャッシュが期限切れか確認"""
        if not self.last_loaded:
            return True

        elapsed = datetime.now() - self.last_loaded
        return elapsed.total_seconds() > (self.cache_minutes * 60)

    def _load_from_gcs(self, force=False):
        """GCSからプロンプトを読み込み"""
        if not self.bucket_name:
            return

        try:
            # prompts/ ディレクトリ内の .txt ファイルを読み込み
            blobs = list(self.bucket.list_blobs(prefix="prompts/"))

            loaded_count = 0
            for blob in blobs:
                if blob.name.endswith(".txt"):
                    # ファイル名からキー名を作成
                    key = blob.name.split("/")[-1].replace(".txt", "")
                    content = blob.download_as_text(encoding='utf-8')
                    self.prompts[key] = content
                    loaded_count += 1
                    logger.debug(f"[PromptManager] 読み込み: {key} ({len(content)}文字)")

            self.last_loaded = datetime.now()
            logger.info(f"[PromptManager] ✓ GCSから{loaded_count}個のプロンプトを読み込み完了")

        except Exception as e:
            logger.error(f"[PromptManager] GCS読み込み失敗: {e}")
            if not self.prompts:  # 初回読み込み失敗時のみデフォルトを使用
                self._load_defaults()

    def _load_defaults(self):
        """デフォルトプロンプトを読み込み（グルメサポートAI対応）"""
        self.prompts = {
            # グルメサポートAI システムプロンプト
            "support_system": """あなたはグルメサポートアシスタントです。

## 役割
1. ユーザーが希望する飲食店をGoogleMapを中心に情報を検索
2. まずは、細かな条件を確認する前に、お店のおススメ候補の5軒を即答する
3. 追加の条件や質問が無いか確認する

## 店舗提案のルール
1. 必ず実在するお店のみを提案してください
2. 架空の店舗名や、存在が不確かな店舗は絶対に提案しないでください
3. 確実に存在する店舗のみを、正式な店舗名で提案してください
4. 店舗名は公式の表記を使用してください
5. 閉店した店舗は提案しないでください
6. 不確かな場合は、より有名で確実に存在する店舗を優先してください

## 応答フォーマット（重要）

すべての応答は必ず以下のJSON形式で返してください：

### 店舗を提案する場合：
```json
{
  "message": "おすすめの5軒はこちらです。\\n\\n1. **店舗名**（最寄り駅）- おすすめポイント（1〜2文）\\n2. **店舗名**（最寄り駅）- おすすめポイント（1〜2文）\\n3. **店舗名**（最寄り駅）- おすすめポイント（1〜2文）\\n4. **店舗名**（最寄り駅）- おすすめポイント（1〜2文）\\n5. **店舗名**（最寄り駅）- おすすめポイント（1〜2文）\\n\\n他に、予算やお店の雰囲気など、何かご希望はありますか？",
  "shops": [
    {
      "name": "正式な店舗名（エリア名は含めない）",
      "area": "最寄り駅・エリア",
      "description": "おすすめポイント（1〜2文）"
    }
  ]
}
```

**重要**:
- messageの冒頭は「おすすめの5軒はこちらです。」で始めてください
- 確認文（「〜をお探しですね」）や追加の説明は含めないでください
- 追加質問は必ず最後に配置してください

### 店舗提案がない通常の会話：
```json
{
  "message": "通常の応答メッセージ"
}
```

## 会話の進め方
- 明確で簡潔な日本語を使用する
- 丁寧で親しみやすいトーンを保つ
- 長文は避け、分かりやすく伝える

## 注意事項
- 応答は必ずJSON形式で返すこと（```json マークダウンは不要、純粋なJSONのみ）
- messageには店舗リストを含めない（shopsで別途提供するため）
- 店舗名は正式名称を使用すること""",

            # 初期挨拶
            "initial_greeting": """こんにちは！グルメサポートAIです。
お店探しのお手伝いをさせていただきます。
どのようなお店をお探しですか？""",

            # ステージ別指示（既存互換性のため保持）
            "inquiry_stage_instruction": """ユーザーの要望を詳しく聞き出してください。
- 料理のジャンル
- 希望のエリア
- 予算
- 人数
- その他の条件""",

            "confirmation_stage_instruction": """提案したお店について確認してください。
ユーザーが気に入った場合は予約に進みます。""",

            "completion_stage_instruction": """予約が完了しました。
楽しい食事をお過ごしください。""",

            # 汎用プロンプト（他のシステムとの互換性）
            "fix_instructions": """以下の会話から修正指示書を作成してください：

# 修正指示書

## 基本情報
- 作成日時: {timestamp}
- セッションID: {session_id}

## 要件
（会話から抽出）

## 修正手順
1.
2.
3.

【会話ログ】
{conversation_text}
""",
            "chat_system": """あなたはWebサイト制作のアシスタントです。
クライアントの要望を理解し、具体的な提案をしてください。""",

            "selection_analysis": """以下の選択内容を分析してください：

{selection_content}

タイプ: {selection_type}
コメント: {user_comment}
"""
        }
        self.last_loaded = datetime.now()
        logger.info(f"[PromptManager] デフォルトプロンプト使用: {len(self.prompts)}個")

    def _get_default(self, name):
        """デフォルトプロンプトを取得"""
        defaults = {
            "support_system": "あなたはグルメサポートアシスタントです。",
            "initial_greeting": "こんにちは！グルメサポートAIです。",
            "inquiry_stage_instruction": "ユーザーの要望を聞き出してください。",
            "confirmation_stage_instruction": "提案したお店について確認してください。",
            "completion_stage_instruction": "予約が完了しました。",
            "fix_instructions": "修正指示書を作成してください。\n\n{conversation_text}",
            "chat_system": "あなたはアシスタントです。",
            "selection_analysis": "以下を分析してください：\n{selection_content}"
        }
        return defaults.get(name, f"プロンプト '{name}' が見つかりません")
