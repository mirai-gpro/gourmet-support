# -*- coding: utf-8 -*-
"""
Prompt Manager - GCS Bucket からプロンプトを読み込む
"""
import os
from google.cloud import storage

class PromptManager:
    """プロンプト管理クラス（GCS統合）"""

    def __init__(self):
        self.bucket_name = os.getenv('PROMPTS_BUCKET_NAME', '')
        self.prompts = {}

        if self.bucket_name:
            self._load_from_gcs()
        else:
            # フォールバック: デフォルトプロンプト
            self._load_defaults()

    def _load_from_gcs(self):
        """GCS バケットからプロンプトを読み込む"""
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(self.bucket_name)

            # 各プロンプトファイルを読み込み
            prompt_files = [
                'support_system.txt',
                'initial_greeting.txt',
                'inquiry_stage_instruction.txt',
                'confirmation_stage_instruction.txt',
                'completion_stage_instruction.txt'
            ]

            for filename in prompt_files:
                try:
                    blob = bucket.blob(filename)
                    content = blob.download_as_text()
                    key = filename.replace('.txt', '')
                    self.prompts[key] = content
                except Exception as e:
                    print(f"[PromptManager] {filename} 読み込み失敗: {e}")

        except Exception as e:
            print(f"[PromptManager] GCS読み込みエラー: {e}")
            self._load_defaults()

    def _load_defaults(self):
        """デフォルトプロンプトを設定"""
        self.prompts = {
            'support_system': """あなたは丁寧で親切なグルメサポートAIアシスタントです。
ユーザーの要望を聞き、最適なレストランを提案します。
会話は自然で、ユーザーフレンドリーに進めてください。""",

            'initial_greeting': """こんにちは！グルメサポートAIです。
お店探しのお手伝いをさせていただきます。
どのようなお店をお探しですか？""",

            'inquiry_stage_instruction': """ユーザーの要望を詳しく聞き出してください。
- 料理のジャンル
- 希望のエリア
- 予算
- 人数
- その他の条件""",

            'confirmation_stage_instruction': """提案したお店について確認してください。
ユーザーが気に入った場合は予約に進みます。""",

            'completion_stage_instruction': """予約が完了しました。
楽しい食事をお過ごしください。"""
        }

    def get(self, key: str, default: str = '') -> str:
        """プロンプトを取得"""
        return self.prompts.get(key, default)
