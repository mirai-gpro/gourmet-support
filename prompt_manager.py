# -*- coding: utf-8 -*-
"""
多言語プロンプト管理 (グルメサポートAI対応)
- ローカルJSONファイル優先（DB不要）
- GCSオプション対応（バケット設定時のみ）
- 4言語対応: ja, en, zh, ko
"""
import os
import json
import logging
from datetime import datetime, timedelta
from google.cloud import storage

logger = logging.getLogger(__name__)

class PromptManager:
    def __init__(self, prompts_dir='prompts', default_language='ja'):
        """
        初期化

        Args:
            prompts_dir: プロンプトJSONファイルが格納されているディレクトリ
            default_language: デフォルト言語（ja, en, zh, ko）
        """
        self.prompts_dir = prompts_dir
        self.default_language = default_language
        self.current_language = default_language

        # GCS設定（オプション）
        self.bucket_name = os.getenv("PROMPTS_BUCKET_NAME", "")
        self.client = None
        self.bucket = None

        # プロンプトキャッシュ
        self.prompts_cache = {}  # {language: {key: value}}
        self.last_loaded = None
        self.cache_minutes = 60

        # 初期化時に読み込み
        self._initialize()

    def _initialize(self):
        """初期化処理"""
        # 優先順位1: GCS（デプロイ不要で修正可能）
        if self.bucket_name:
            try:
                self.client = storage.Client()
                self.bucket = self.client.bucket(self.bucket_name)
                logger.info(f"[PromptManager] GCSバケット接続: {self.bucket_name}")

                if self._load_from_gcs():
                    logger.info("[PromptManager] ✅ GCSから読み込み成功")
                    return
            except Exception as e:
                logger.error(f"[PromptManager] GCS初期化失敗: {e}")

        # 優先順位2: ローカルJSONファイル（GCS障害時のフォールバック）
        if self._load_from_local_json():
            logger.info("[PromptManager] ⚠️ ローカルJSONから読み込み（GCS未使用）")
            return

        # 優先順位3: デフォルト（ハードコード）
        logger.warning("[PromptManager] ⚠️ デフォルトプロンプト使用")
        self._load_defaults()

    def _load_from_local_json(self) -> bool:
        """
        ローカルJSONファイルから読み込み

        Returns:
            読み込み成功したかどうか
        """
        supported_languages = ['ja', 'en', 'zh', 'ko']
        loaded_count = 0

        for lang in supported_languages:
            file_path = os.path.join(self.prompts_dir, f'{lang}.json')

            if not os.path.exists(file_path):
                logger.debug(f"[PromptManager] ファイル未検出: {file_path}")
                continue

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.prompts_cache[lang] = json.load(f)
                loaded_count += 1
                logger.info(f"[PromptManager] 読み込み成功: {lang} ({len(self.prompts_cache[lang])}個のキー)")
            except Exception as e:
                logger.error(f"[PromptManager] 読み込み失敗 ({lang}): {e}")

        if loaded_count > 0:
            self.last_loaded = datetime.now()
            return True

        return False

    def _load_from_gcs(self) -> bool:
        """
        GCSからプロンプトを読み込み（多言語JSON対応 + .txtフォールバック）

        Returns:
            読み込み成功したかどうか
        """
        if not self.bucket_name:
            return False

        try:
            blobs = list(self.bucket.list_blobs(prefix="prompts/"))

            # 優先順位1: JSONファイルを読み込み（多言語対応）
            json_loaded = False
            for lang in ['ja', 'en', 'zh', 'ko']:
                json_blob_name = f"prompts/{lang}.json"
                json_blob = self.bucket.blob(json_blob_name)

                if json_blob.exists():
                    try:
                        content = json_blob.download_as_text(encoding='utf-8')
                        self.prompts_cache[lang] = json.loads(content)
                        json_loaded = True
                        logger.info(f"[PromptManager] GCS JSON読み込み成功: {lang} ({len(self.prompts_cache[lang])}個のキー)")
                    except Exception as e:
                        logger.error(f"[PromptManager] GCS JSON読み込み失敗 ({lang}): {e}")

            if json_loaded:
                self.last_loaded = datetime.now()
                return True

            # 優先順位2: .txtファイルを読み込み（後方互換性）
            logger.info("[PromptManager] JSON未検出、.txtファイルを読み込み")

            loaded_count = 0
            if 'ja' not in self.prompts_cache:
                self.prompts_cache['ja'] = {}

            for blob in blobs:
                if blob.name.endswith(".txt"):
                    key = blob.name.split("/")[-1].replace(".txt", "")
                    content = blob.download_as_text(encoding='utf-8')
                    self.prompts_cache['ja'][key] = content
                    loaded_count += 1
                    logger.debug(f"[PromptManager] GCS .txt読み込み: {key} ({len(content)}文字)")

            if loaded_count > 0:
                self.last_loaded = datetime.now()
                logger.info(f"[PromptManager] GCSから{loaded_count}個の.txtプロンプトを読み込み完了")
                return True

        except Exception as e:
            logger.error(f"[PromptManager] GCS読み込み失敗: {e}")

        return False

    def _load_defaults(self):
        """デフォルトプロンプトを読み込み（日本語のみ）"""
        self.prompts_cache = {
            'ja': {
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
  "message": "おすすめの5軒はこちらです。\\n\\n1. **店舗名**（最寄り駅）- おすすめポイント（1～2文）\\n2. **店舗名**（最寄り駅）- おすすめポイント（1～2文）\\n3. **店舗名**（最寄り駅）- おすすめポイント（1～2文）\\n4. **店舗名**（最寄り駅）- おすすめポイント（1～2文）\\n5. **店舗名**（最寄り駅）- おすすめポイント（1～2文）\\n\\n他に、予算やお店の雰囲気など、何かご希望はありますか？",
  "shops": [
    {
      "name": "正式な店舗名（エリア名は含めない）",
      "area": "最寄り駅・エリア",
      "description": "おすすめポイント（1～2文）"
    }
  ]
}
```

**重要**:
- messageの冒頭は「おすすめの5軒はこちらです。」で始めてください
- 確認文（「～をお探しですね」）や追加の説明は含めないでください
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

                # ステージ別指示
                "conversation_stage_instruction": """ユーザーの質問から以下を抽出してJSON形式で返してください：
- エリア（例: 渋谷、新宿、銀座）
- ジャンル（例: イタリアン、和食、フレンチ）
- 予算、人数、雰囲気、特徴

店舗を提案する場合は shops 配列に含めてください。""",

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

                # 要約プロンプト
                "conversation_summary": """【会話要約】
ユーザー: {user_message}
アシスタント: {assistant_response}

上記のやり取りを1文で要約してください。""",

                "final_summary": """【最終要約書生成】
以下の会話履歴から、ユーザーの質問内容と提案内容を要約してください。

{conversation_text}

作成日時: {timestamp}""",

                # 深掘り質問パターン
                "followup_patterns": [
                    "この中で", "これらの中で", "さっきの", "先ほどの",
                    "どれが", "どこが", "どの店", "何番目",
                    "予算", "値段", "価格", "安い", "高い",
                    "ピザ", "パスタ", "ワイン", "デザート",
                    "カジュアル", "高級", "個室", "テラス",
                    "雰囲気", "特徴", "おすすめ"
                ],

                # エラーメッセージ
                "no_shops_found": "申し訳ございません。条件に合うお店が見つかりませんでした。別の条件でお探しいただけますか？",

                # エリア抽出プロンプト
                "area_extraction_prompt": """以下のテキストからエリア名（地名）を抽出して、日本語で返してください。エリアが見つからない場合は空文字を返してください。

テキスト: {text}

回答（エリア名のみ）:"""
            }
        }
        self.last_loaded = datetime.now()
        logger.info(f"[PromptManager] デフォルトプロンプト使用: {len(self.prompts_cache['ja'])}個")

    def set_language(self, language: str):
        """
        言語を切り替え

        Args:
            language: 言語コード（ja, en, zh, ko）
        """
        if language not in self.prompts_cache:
            logger.warning(f"[PromptManager] 未対応言語: {language}, デフォルト({self.default_language})を使用")
            self.current_language = self.default_language
        else:
            self.current_language = language
            logger.info(f"[PromptManager] 言語切り替え: {language}")

    def get(self, key: str, language: str = None, **kwargs) -> str:
        """
        プロンプトを取得

        Args:
            key: プロンプトキー
            language: 言語コード（指定なしの場合は current_language を使用）
            **kwargs: プロンプトに埋め込む変数（例: user_message="..."）

        Returns:
            プロンプト文字列
        """
        # キャッシュ更新チェック（GCS使用時のみ）
        if self.bucket_name and self._is_cache_expired():
            try:
                self._load_from_gcs()
            except Exception as e:
                logger.warning(f"[PromptManager] GCS更新失敗、キャッシュ使用: {e}")

        # 言語決定
        lang = language or self.current_language

        if lang not in self.prompts_cache:
            logger.warning(f"[PromptManager] 言語未ロード: {lang}, デフォルト使用")
            lang = self.default_language

        # プロンプト取得
        prompts = self.prompts_cache.get(lang, {})
        prompt = prompts.get(key, '')

        if not prompt:
            logger.warning(f"[PromptManager] プロンプトキーが見つかりません: {key} (言語: {lang})")
            prompt = self._get_fallback(key)

        # 変数展開
        if kwargs:
            try:
                prompt = prompt.format(**kwargs)
            except KeyError as e:
                logger.warning(f"[PromptManager] プロンプト変数エラー: {e}")

        return prompt

    def get_followup_patterns(self, language: str = None) -> list:
        """
        深掘り質問パターンを取得

        Args:
            language: 言語コード

        Returns:
            パターンリスト
        """
        lang = language or self.current_language
        prompts = self.prompts_cache.get(lang, {})
        return prompts.get('followup_patterns', [])

    def reload(self):
        """プロンプトを強制再読み込み（管理用API）"""
        self._initialize()

        return {
            "loaded_languages": list(self.prompts_cache.keys()),
            "current_language": self.current_language,
            "time": self.last_loaded.isoformat() if self.last_loaded else None,
            "source": "local_json" if os.path.exists(self.prompts_dir) else "gcs" if self.bucket_name else "default"
        }

    def _is_cache_expired(self) -> bool:
        """キャッシュが期限切れか確認"""
        if not self.last_loaded:
            return True

        elapsed = datetime.now() - self.last_loaded
        return elapsed.total_seconds() > (self.cache_minutes * 60)

    def _get_fallback(self, key: str) -> str:
        """フォールバックプロンプトを取得"""
        fallbacks = {
            "support_system": "あなたはグルメサポートアシスタントです。",
            "initial_greeting": "こんにちは！グルメサポートAIです。",
            "conversation_stage_instruction": "ユーザーの質問に答えてください。",
            "inquiry_stage_instruction": "ユーザーの要望を聞き出してください。",
            "confirmation_stage_instruction": "提案したお店について確認してください。",
            "completion_stage_instruction": "予約が完了しました。",
            "conversation_summary": "会話を要約してください。",
            "final_summary": "最終要約を作成してください。",
            "no_shops_found": "お店が見つかりませんでした。",
            "area_extraction_prompt": "エリア名を抽出してください。"
        }
        return fallbacks.get(key, f"プロンプト '{key}' が見つかりません")
