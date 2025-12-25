# -*- coding: utf-8 -*-
"""
Support Assistant module - handles AI conversation and shop recommendations
"""
import json
import logging
from datetime import datetime
from google.genai import types
from config import gemini_client, INITIAL_GREETINGS, CONVERSATION_SUMMARY_TEMPLATES, FINAL_SUMMARY_TEMPLATES
from session import SupportSession
from utils import extract_shops_from_response

logger = logging.getLogger(__name__)

class SupportAssistant:
    """サポートアシスタント - モード対応版"""

    def __init__(self, session: SupportSession, system_prompts: dict):
        self.session = session
        self.language = session.get_language()
        self.mode = session.get_mode()  # ★ モードを取得

        # ★★★ モードに応じたプロンプトを選択 ★★★
        mode_prompts = system_prompts.get(self.mode, system_prompts.get('chat', {}))
        self.system_prompt = mode_prompts.get(self.language, mode_prompts.get('ja', ''))

        logger.info(f"[Assistant] 初期化: mode={self.mode}, language={self.language}")

    def get_initial_message(self):
        """初回メッセージ - モード別"""
        greetings = INITIAL_GREETINGS.get(self.mode, INITIAL_GREETINGS.get('chat', {}))
        return greetings.get(self.language, greetings.get('ja', ''))

    def is_followup_question(self, user_message, current_shops):
        """深掘り質問かどうかを判定"""
        if not current_shops:
            return False

        # フォローアップ質問のパターン(料理名は除外 - 初回検索で誤判定されるため)
        followup_patterns = [
            'この中で', 'これらの中で', 'さっきの', '先ほどの',
            'どれが', 'どこが', 'どの店', '何番目',
            '予約', '電話番号', '営業時間', 'アクセス',
            '詳しく', 'もっと', 'について'
        ]

        message_lower = user_message.lower()
        return any(pattern in message_lower for pattern in followup_patterns)

    def process_user_message(self, user_message, conversation_stage='conversation'):
        """
        ユーザーメッセージを処理

        【重要】改善されたフロー:
        1. 履歴を構造化リストで取得
        2. 履歴には既に最新のユーザーメッセージが含まれている(add_messageで追加済み)
        3. そのため、履歴をそのままGeminiに渡す
        """
        # 履歴を構造化リストで取得(既に最新のユーザーメッセージを含む)
        history = self.session.get_history_for_api()
        current_shops = self.session.get_current_shops()

        is_followup = self.is_followup_question(user_message, current_shops)

        # フォローアップの場合は現在の店舗情報をシステムプロンプトに追加
        system_prompt = self.system_prompt
        if is_followup and current_shops:
            followup_messages = {
                'ja': {
                    'header': '【現在提案中の店舗情報】',
                    'footer': 'ユーザーは上記の店舗について質問しています。店舗情報を参照して回答してください。'
                },
                'en': {
                    'header': '【Currently Proposed Restaurants】',
                    'footer': 'The user is asking about the restaurants listed above. Please refer to the restaurant information when answering.'
                },
                'zh': {
                    'header': '【当前推荐的餐厅信息】',
                    'footer': '用户正在询问上述餐厅的信息。请参考餐厅信息进行回答。'
                },
                'ko': {
                    'header': '【현재 제안 중인 레스토랑 정보】',
                    'footer': '사용자는 위 레스토랑에 대해 질문하고 있습니다. 레스토랑 정보를 참조하여 답변하세요.'
                }
            }
            current_followup_msg = followup_messages.get(self.language, followup_messages['ja'])
            shop_context = f"\n\n{current_followup_msg['header']}\n{self._format_current_shops(current_shops)}\n\n{current_followup_msg['footer']}"
            system_prompt = self.system_prompt + shop_context
            logger.info("[Assistant] フォローアップ質問モード: 店舗情報をシステムプロンプトに追加")

        # ツール設定
        tools = None
        if not is_followup:
            tools = [types.Tool(google_search=types.GoogleSearch())]
            logger.info("[Assistant] Google検索グラウンディングを有効化")

        try:
            logger.info(f"[Assistant] Gemini API呼び出し開始: 履歴={len(history)}件")

            # 【重要】configパラメータを使用(SDKの正しい使い方)
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
                tools=tools if tools else None
            )

            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=history,
                config=config
            )

            logger.info("[Assistant] Gemini API呼び出し完了")

            # レスポンスからテキストを取得
            assistant_text = response.text

            if not assistant_text:
                logger.error("[Assistant] Empty response from Gemini")
                raise RuntimeError("Gemini returned empty response")

            logger.info(f"[Assistant] Gemini response received: {len(assistant_text)} chars")

            parsed_message, parsed_shops = self._parse_json_response(assistant_text)

            if parsed_shops:
                self.session.save_current_shops(parsed_shops)

            summary = None
            if conversation_stage == 'conversation':
                if parsed_shops:
                    summary_messages = {
                        'ja': lambda count: f"{count}軒のお店を提案しました。",
                        'en': lambda count: f"Suggested {count} restaurants.",
                        'zh': lambda count: f"推荐了{count}家餐厅。",
                        'ko': lambda count: f"{count}개의 레스토랑을 제안했습니다."
                    }
                    summary_func = summary_messages.get(self.language, summary_messages['ja'])
                    summary = summary_func(len(parsed_shops))
                else:
                    summary = self._generate_summary(user_message, parsed_message)

            return {
                'response': parsed_message,
                'summary': summary,
                'shops': parsed_shops,
                'should_confirm': conversation_stage == 'conversation',
                'is_followup': is_followup
            }

        except Exception as e:
            logger.error(f"[Assistant] Gemini API error: {e}", exc_info=True)
            error_messages = {
                'ja': 'エラーが発生しました。もう一度お試しください。',
                'en': 'An error occurred. Please try again.',
                'zh': '发生错误。请重试。',
                'ko': '오류가 발생했습니다. 다시 시도해주세요.'
            }
            return {
                'response': error_messages.get(self.language, error_messages['ja']),
                'summary': None,
                'shops': [],
                'should_confirm': False,
                'is_followup': False
            }

    def generate_final_summary(self):
        """最終要約を生成"""
        all_messages = self.session.get_history_for_api()

        # 会話テキストを整形
        # 【重要】all_messagesはtypes.Contentオブジェクトのリスト
        conversation_lines = []
        for msg in all_messages:
            role_name = 'ユーザー' if msg.role == 'user' else 'アシスタント'
            # msg.partsはtypes.Partオブジェクトのリストなので、最初の要素のtextを取得
            conversation_lines.append(f"{role_name}: {msg.parts[0].text}")
        conversation_text = '\n'.join(conversation_lines)

        template = FINAL_SUMMARY_TEMPLATES.get(self.language, FINAL_SUMMARY_TEMPLATES['ja'])
        summary_prompt = template.format(
            conversation_text=conversation_text,
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        try:
            logger.info("[Assistant] Generating final summary")
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=summary_prompt
            )
            summary = response.text

            self.session.update_status(
                'completed',
                inquiry_summary=summary
            )

            return summary

        except Exception as e:
            logger.error(f"[Assistant] Final summary error: {e}", exc_info=True)
            return "要約の生成中にエラーが発生しました。"

    def _format_current_shops(self, shops):
        """店舗情報を整形してプロンプトに追加"""
        # 多言語ラベル
        shop_labels = {
            'ja': {
                'description': '説明',
                'specialty': '看板メニュー',
                'price': '予算',
                'atmosphere': '雰囲気',
                'features': '特色'
            },
            'en': {
                'description': 'Description',
                'specialty': 'Specialty',
                'price': 'Price Range',
                'atmosphere': 'Atmosphere',
                'features': 'Features'
            },
            'zh': {
                'description': '说明',
                'specialty': '招牌菜',
                'price': '预算',
                'atmosphere': '氛围',
                'features': '特色'
            },
            'ko': {
                'description': '설명',
                'specialty': '대표 메뉴',
                'price': '예산',
                'atmosphere': '분위기',
                'features': '특징'
            }
        }

        current_shop_labels = shop_labels.get(self.language, shop_labels['ja'])
        lines = []
        for i, shop in enumerate(shops, 1):
            lines.append(f"{i}. {shop.get('name', '')} ({shop.get('area', '')})")
            lines.append(f"   - {current_shop_labels['description']}: {shop.get('description', '')}")
            if shop.get('specialty'):
                lines.append(f"   - {current_shop_labels['specialty']}: {shop.get('specialty')}")
            if shop.get('price_range'):
                lines.append(f"   - {current_shop_labels['price']}: {shop.get('price_range')}")
            if shop.get('atmosphere'):
                lines.append(f"   - {current_shop_labels['atmosphere']}: {shop.get('atmosphere')}")
            if shop.get('features'):
                lines.append(f"   - {current_shop_labels['features']}: {shop.get('features')}")
            lines.append("")
        return "\n".join(lines)

    def _parse_json_response(self, text: str) -> tuple:
        """JSONレスポンスをパース"""
        try:
            cleaned_text = text.strip()
            if cleaned_text.startswith('```'):
                lines = cleaned_text.split('\n')
                cleaned_text = '\n'.join(lines[1:])
            if cleaned_text.endswith('```'):
                cleaned_text = cleaned_text[:-3]
            cleaned_text = cleaned_text.strip()

            data = json.loads(cleaned_text)

            message = data.get('message', text)
            shops = data.get('shops', [])

            logger.info(f"[JSON Parse] 成功: message={len(message)}文字, shops={len(shops)}件")
            return message, shops

        except json.JSONDecodeError as e:
            logger.warning(f"[JSON Parse] パース失敗、平文として処理: {e}")
            shops = extract_shops_from_response(text)
            return text, shops

    def _generate_summary(self, user_message, assistant_response):
        """会話の要約を生成"""
        template = CONVERSATION_SUMMARY_TEMPLATES.get(self.language, CONVERSATION_SUMMARY_TEMPLATES['ja'])
        summary_prompt = template.format(
            user_message=user_message,
            assistant_response=assistant_response
        )

        try:
            logger.info("[Assistant] Generating summary")
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=summary_prompt
            )
            return response.text

        except Exception as e:
            logger.error(f"[Assistant] Summary generation error: {e}", exc_info=True)
            return None
