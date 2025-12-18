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
            # 各言語のプロンプトファイルを読み込み
            for lang in ['ja', 'en', 'zh', 'ko']:
                prompt_file = self.prompts_dir / f'{lang}.json'
                if prompt_file.exists():
                    with open(prompt_file, 'r', encoding='utf-8') as f:
                        self.prompts_cache[lang] = json.load(f)
                    logger.info(f"[PromptManager] {lang}.jsonを読み込みました")
                else:
                    logger.warning(f"[PromptManager] {prompt_file}が見つかりません")
        except Exception as e:
            logger.error(f"[PromptManager] プロンプト読み込みエラー: {e}")

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
