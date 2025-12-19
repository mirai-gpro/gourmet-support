# -*- coding: utf-8 -*-
"""
プロンプトマネジャー
多言語対応のプロンプトテンプレート管理
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PromptManager:
    """プロンプトテンプレート管理クラス"""

    def __init__(self, prompts_dir='prompts'):
        self.prompts_dir = Path(prompts_dir)
        self.prompts_cache = {}
        self._load_prompts()

    def _load_prompts(self):
        """プロンプトファイルを読み込み"""
        try:
            # 各言語のプロンプトファイルを読み込み（.txt形式）
            for lang in ['ja', 'en', 'zh', 'ko']:
                prompt_file = self.prompts_dir / f'support_system_{lang}.txt'
                if prompt_file.exists():
                    with open(prompt_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # 単純な辞書構造で保存
                        self.prompts_cache[lang] = {
                            'support_system': content,
                            'initial_greeting': self._get_default_greeting(lang),
                            'conversation_stage_instruction': '',
                            'inquiry_stage_instruction': self._get_default_inquiry(lang),
                            'conversation_summary': self._get_default_summary(lang),
                            'final_summary': self._get_default_final_summary(lang)
                        }
                    logger.info(f"[PromptManager] support_system_{lang}.txtを読み込みました")
                else:
                    logger.warning(f"[PromptManager] {prompt_file}が見つかりません")
        except Exception as e:
            logger.error(f"[PromptManager] プロンプト読み込みエラー: {e}")

    def _get_default_greeting(self, lang):
        """デフォルトの初期挨拶"""
        greetings = {
            'ja': 'こんにちは！お店探しをお手伝いします。どのようなお店をお探しですか？（例：新宿で美味しいイタリアン、明日19時に予約できる焼肉店など）',
            'en': 'Hello! I\'m here to help you find restaurants. What kind of restaurant are you looking for?',
            'zh': '您好！我来帮您找餐厅。您在寻找什么样的餐厅？',
            'ko': '안녕하세요! 레스토랑 찾기를 도와드리겠습니다. 어떤 레스토랑을 찾으시나요?'
        }
        return greetings.get(lang, greetings['ja'])

    def _get_default_inquiry(self, lang):
        """デフォルトの問い合わせ確認"""
        inquiry = {
            'ja': '【問い合わせ確認フェーズ】\nユーザーの質問内容を整理し、最終確認を行ってください。',
            'en': '【Inquiry Confirmation Phase】\nOrganize the user\'s inquiry and perform final confirmation.',
            'zh': '【咨询确认阶段】\n整理用户的问题内容，进行最终确认。',
            'ko': '【문의 확인 단계】\n사용자의 질문 내용을 정리하고 최종 확인을 수행하세요。'
        }
        return inquiry.get(lang, inquiry['ja'])

    def _get_default_summary(self, lang):
        """デフォルトの会話要約"""
        summary = {
            'ja': '以下の会話を1文で要約してください。\n\nユーザー: {user_message}\nアシスタント: {assistant_response}\n\n要約:',
            'en': 'Summarize the following conversation in one sentence.\n\nUser: {user_message}\nAssistant: {assistant_response}\n\nSummary:',
            'zh': '请用一句话总结以下对话。\n\n用户：{user_message}\n助手：{assistant_response}\n\n总结：',
            'ko': '다음 대화를 한 문장으로 요약하세요.\n\n사용자: {user_message}\n어시스턴트: {assistant_response}\n\n요약:'
        }
        return summary.get(lang, summary['ja'])

    def _get_default_final_summary(self, lang):
        """デフォルトの最終要約"""
        final = {
            'ja': '以下の会話全体を要約し、問い合わせ内容をまとめてください。\n\n{conversation_text}\n\n作成日時: {timestamp}\n\n要約:',
            'en': 'Summarize the entire conversation below and organize the inquiry content.\n\n{conversation_text}\n\nCreated: {timestamp}\n\nSummary:',
            'zh': '请总结以下整个对话并整理咨询内容。\n\n{conversation_text}\n\n创建时间：{timestamp}\n\n总结：',
            'ko': '다음 전체 대화를 요약하고 문의 내용을 정리하세요。\n\n{conversation_text}\n\n작성 시간: {timestamp}\n\n요약:'
        }
        return final.get(lang, final['ja'])

    def get(self, prompt_key: str, language: str = 'ja', **kwargs) -> str:
        """
        プロンプトテンプレートを取得

        Args:
            prompt_key: プロンプトキー
            language: 言語コード (ja, en, zh, ko)
            **kwargs: テンプレート変数

        Returns:
            フォーマット済みプロンプト文字列
        """
        # 言語のプロンプトを取得
        lang_prompts = self.prompts_cache.get(language, self.prompts_cache.get('ja', {}))

        # プロンプトキーで取得
        prompt_template = lang_prompts.get(prompt_key, '')

        if not prompt_template:
            logger.warning(f"[PromptManager] プロンプトが見つかりません: {prompt_key} ({language})")
            return ''

        # テンプレート変数を展開
        try:
            if kwargs:
                prompt = prompt_template.format(**kwargs)
            else:
                prompt = prompt_template
            return prompt
        except KeyError as e:
            logger.error(f"[PromptManager] テンプレート変数が不足しています: {e}")
            return prompt_template

    def get_full_prompt(self, language: str = 'ja') -> dict:
        """
        言語の全プロンプトを取得

        Args:
            language: 言語コード

        Returns:
            プロンプト辞書
        """
        return self.prompts_cache.get(language, self.prompts_cache.get('ja', {}))
