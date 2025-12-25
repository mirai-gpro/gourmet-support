# -*- coding: utf-8 -*-
"""
ビジネスロジック・コアクラス
- プロンプト管理
- セッション管理
- アシスタント(AI会話ロジック)
"""
import os
import json
import uuid
import logging
from datetime import datetime
from google import genai
from google.genai import types
from google.cloud import storage
import google.generativeai as genai_legacy

# api_integrations から必要な関数をインポート
from api_integrations import extract_shops_from_response

logger = logging.getLogger(__name__)

# Gemini クライアント初期化
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
genai_legacy.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai_legacy.GenerativeModel('gemini-2.0-flash-exp')

# ========================================
# RAMベースのセッション管理 (Firestore完全廃止)
# ========================================
_SESSION_CACHE = {}

# ========================================
# プロンプト読み込み (GCS優先、ローカルフォールバック)
# ========================================

def load_prompts_from_gcs():
    """
    GCSから2種類のプロンプトを読み込み
    - support_system_{lang}.txt: チャットモード用
    - concierge_{lang}.txt: コンシェルジュモード用
    """
    try:
        bucket_name = os.getenv('PROMPTS_BUCKET_NAME')
        if not bucket_name:
            logger.warning("[Prompt] PROMPTS_BUCKET_NAME が設定されていません。ローカルファイルを使用します。")
            return None

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        prompts = {
            'chat': {},      # チャットモード用
            'concierge': {}  # コンシェルジュモード用
        }

        for lang in ['ja', 'en', 'zh', 'ko']:
            # チャットモード用プロンプト
            chat_blob = bucket.blob(f'prompts/support_system_{lang}.txt')
            if chat_blob.exists():
                prompts['chat'][lang] = chat_blob.download_as_text(encoding='utf-8')
                logger.info(f"[Prompt] GCSから読み込み成功: support_system_{lang}.txt")
            else:
                logger.warning(f"[Prompt] GCSに見つかりません: support_system_{lang}.txt")

            # コンシェルジュモード用プロンプト
            concierge_blob = bucket.blob(f'prompts/concierge_{lang}.txt')
            if concierge_blob.exists():
                content = concierge_blob.download_as_text(encoding='utf-8')
                try:
                    json_data = json.loads(content)
                    prompts['concierge'][lang] = json_data.get('concierge_system', content)
                except json.JSONDecodeError:
                    prompts['concierge'][lang] = content
                logger.info(f"[Prompt] GCSから読み込み成功: concierge_{lang}.txt")
            else:
                logger.warning(f"[Prompt] GCSに見つかりません: concierge_{lang}.txt")

        return prompts if (prompts['chat'] or prompts['concierge']) else None

    except Exception as e:
        logger.error(f"[Prompt] GCS読み込み失敗: {e}")
        return None

def load_system_prompts():
    logger.info("[Prompt] プロンプト読み込み開始...")
    prompts = load_prompts_from_gcs()
    if not prompts:
        logger.info("[Prompt] GCSから読み込めませんでした。ローカルファイルを使用します。")
        prompts = load_prompts_from_local()

    if not prompts or (not prompts.get('chat') and not prompts.get('concierge')):
        logger.error("[Prompt] プロンプトの読み込みに失敗しました!")
        return {
            'chat': {'ja': 'エラー: チャットモードプロンプトが読み込めませんでした。'},
            'concierge': {'ja': 'エラー: コンシェルジュモードプロンプトが読み込めませんでした。'}
        }

    logger.info(f"[Prompt] プロンプト読み込み完了:")
    logger.info(f"  - チャットモード: {list(prompts.get('chat', {}).keys())}")
    logger.info(f"  - コンシェルジュモード: {list(prompts.get('concierge', {}).keys())}")
    return prompts

# プロンプト読み込み実行(モジュールロード時)
SYSTEM_PROMPTS = load_system_prompts()
INITIAL_GREETINGS = {
    'chat': {
        'ja': 'こんにちは!お店探しをお手伝いします。どのようなお店をお探しですか?(例:新宿で美味しいイタリアン、明日19時に予約できる焼肉店など)',
        'en': 'Hello! I\'m here to help you find restaurants. What kind of restaurant are you looking for?',
        'zh': '您好!我来帮您找餐厅。您在寻找什么样的餐厅?',
        'ko': '안녕하세요! 레스토랑 찾기를 도와드리겠습니다. 어떤 레스토랑을 찾으시나요?'
    },
    'concierge': {
        'ja': 'いらっしゃいませ。グルメコンシェルジュです。今日はどのようなシーンでお店をお探しでしょうか?接待、デート、女子会など、お気軽にお聞かせください。',
        'en': 'Welcome! I\'m your gourmet concierge. What kind of dining experience are you looking for today? Business dinner, date, gathering with friends?',
        'zh': '欢迎光临!我是您的美食礼宾员。今天您想寻找什么样的用餐场景?商务宴请、约会、朋友聚会?',
        'ko': '어서오세요! 저는 귀하의 미식 컨시어지입니다. 오늘은 어떤 식사 장면을 찾으시나요? 접대, 데이트, 모임 등?'
    }
}

CONVERSATION_SUMMARY_TEMPLATES = {
    'ja': '以下の会話を1文で要約してください。\n\nユーザー: {user_message}\nアシスタント: {assistant_response}\n\n要約:',
    'en': 'Summarize the following conversation in one sentence.\n\nUser: {user_message}\nAssistant: {assistant_response}\n\nSummary:',
    'zh': '请用一句话总结以下对话。\n\n用户:{user_message}\n助手:{assistant_response}\n\n总结:',
    'ko': '다음 대화를 한 문장으로 요약하세요.\n\n사용자: {user_message}\n어시스턴트: {assistant_response}\n\n요약:'
}

FINAL_SUMMARY_TEMPLATES = {
    'ja': '以下の会話全体を要約し、問い合わせ内容をまとめてください。\n\n{conversation_text}\n\n作成日時: {timestamp}\n\n要約:',
    'en': 'Summarize the entire conversation below and organize the inquiry content.\n\n{conversation_text}\n\nCreated: {timestamp}\n\nSummary:',
    'zh': '请总结以下整个对话并整理咨询内容。\n\n{conversation_text}\n\n创建时间:{timestamp}\n\n总结:',
    'ko': '다음 대화를 한 문장으로 요약하세요.\n\n사용자: {user_message}\n어시스턴트: {assistant_response}\n\n요약:'
}

class SupportSession:
    """サポートセッション管理 (RAM版)"""

    def __init__(self, session_id=None):
        self.session_id = session_id or str(uuid.uuid4())

    def initialize(self, user_info=None, language='ja', mode='chat'):
        """新規セッション初期化 - モード対応"""
        data = {
            'session_id': self.session_id,
            'messages': [],  # SDKネイティブのリスト形式用
            'status': 'active',
            'user_info': user_info or {},
            'language': language,
            'mode': mode,
            'summary': None,
            'inquiry_summary': None,
            'current_shops': []
        }
        _SESSION_CACHE[self.session_id] = data
        logger.info(f"[Session] RAM作成: {self.session_id}, 言語: {language}, モード: {mode}")
        return data

    def add_message(self, role, content, message_type='chat'):
        """メッã'»ãƒ¼ã'¸ã''è¿½åŠ ï¼ˆå½¹å‰²(Role)åˆ¥ã®æ§‹é€ ã§ä¿å­˜ï¼‰"""
        data = self.get_data()
        if not data:
            return None
        
        # genai SDKが理解できã'‹æ§‹é€ で保存
        message = {
            'role': 'user' if role == 'user' else 'model',
            'parts': [content],
            'type': message_type,  # 内部管理用
            'timestamp': datetime.now().isoformat()
        }
        data['messages'].append(message)
        logger.info(f"[Session] メッã'»ãƒ¼ã'¸è¿½åŠ : role={message['role']}, type={message_type}")
        return message

    def get_history_for_api(self):
        """SDKにそのまま渡せる形式のリストを返す(types.Contentオブジェクトのリスト)"""
        data = self.get_data()
        if not data:
            return []
        
        # 【重要】辞書ではなくtypes.Contentオブジェクトを作成
        history = []
        for m in data['messages']:
            if m['type'] == 'chat':
                # types.Contentオブジェクトを作成
                content = types.Content(
                    role=m['role'],
                    parts=[types.Part(text=m['parts'][0])]  # partsは文字列のリã'¹ãƒˆãªã®ã§æœ€åˆã®è¦ç´ ã''取得
                )
                history.append(content)
        
        logger.info(f"[Session] API用履歴生成: {len(history)}件のメッセージ")
        return history

    def get_messages(self, include_types=None):
        """メッセージ履歴を取得(互換性のため残す)"""
        data = self.get_data()
        if not data:
            return []

        messages = data.get('messages', [])

        if include_types:
            messages = [m for m in messages if m.get('type') in include_types]

        return messages

    def save_current_shops(self, shops):
        """現在の店舗リストを保存"""
        data = self.get_data()
        if data:
            data['current_shops'] = shops
            logger.info(f"[Session] 店舗リスト保存: {len(shops)}件")

    def get_current_shops(self):
        """現在の店舗リストを取得"""
        data = self.get_data()
        return data.get('current_shops', []) if data else []

    def update_status(self, status, **kwargs):
        """ステータス更新"""
        data = self.get_data()
        if data:
            data['status'] = status
            data.update(kwargs)
            logger.info(f"[Session] ステータス更新: {status}")

    def get_data(self):
        """セッションデータ取得"""
        return _SESSION_CACHE.get(self.session_id)

    def get_language(self):
        """セッション言語を取得"""
        data = self.get_data()
        return data.get('language', 'ja') if data else 'ja'

    def get_mode(self):
        """セッションモードを取得"""
        data = self.get_data()
        return data.get('mode', 'chat') if data else 'chat'

    def update_language(self, language: str):
        """セッション言語を更新"""
        data = self.get_data()
        if data:
            data['language'] = language
            logger.info(f"[Session] 言語更新: {language}")

    def update_mode(self, mode: str):
        """セッションモードを更新"""
        data = self.get_data()
        if data:
            data['mode'] = mode
            logger.info(f"[Session] モード更新: {mode}")


class SupportAssistant:
    """サポートアシスタント - モード対応版"""

    def __init__(self, session: SupportSession, system_prompts: dict):
        self.session = session
        self.language = session.get_language()
        self.mode = session.get_mode()  # ★ モードを取得
        
        # ★★★ モードに応じたプロンプトを選択 ★★★
        mode_prompts = system_prompts.get(self.mode, SYSTEM_PROMPTS.get('chat', {}))
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
        1. 履歴ã''æ§‹é€ åŒ–ãƒªã'¹ãƒˆã§å–å¾—
        2. 履歴には既に最新のユーザーメッセージが含まれている(add_messageã§è¿½åŠ æ¸ˆã¿ï¼‰
        3. そのため、履歴をそのままGeminiに渡す
        """
        # 履歴ã''æ§‹é€ åŒ–ãƒªã'¹ãƒˆã§å–得(既に最新のユーã'¶ãƒ¼ãƒ¡ãƒƒã'»ãƒ¼ã'¸ã''含ã'€ï¼‰
        history = self.session.get_history_for_api()
        current_shops = self.session.get_current_shops()

        is_followup = self.is_followup_question(user_message, current_shops)

        # フã'©ãƒ­ãƒ¼ã'¢ãƒƒãƒ—ã®å ´åˆã¯ç¾åœ¨ã®åº—èˆ—æƒ…å ±ã''ã'·ã'¹ãƒ†ãƒ ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã«è¿½åŠ 
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
            shop_context = f"\n\n{current_followup_msg['header']}\n{self._format_current_shops(current_shops)}\n\n{current_followup_msg['footer']}"
            system_prompt = self.system_prompt + shop_context
            logger.info("[Assistant] フォローアップ質問モード: åº—èˆ—æƒ…å ±ã''ã'·ã'¹ãƒ†ãƒ ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã«è¿½åŠ ")

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
                tools=tools if tools else None,
                response_mime_type="application/json"  # JSON形式を強制
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


            # 【デバッグ】エンコーディング確認用ログ
            logger.info(f"[DEBUG] Response encoding type: {type(assistant_text)}")
            logger.info(f"[DEBUG] Response first 200 chars: {repr(assistant_text[:200])}")

            # UTF-8として正しくエンコードされているか確認
            try:
                test_encode = assistant_text.encode('utf-8')
                logger.info(f"[DEBUG] UTF-8 encoding test: OK ({len(test_encode)} bytes)")
            except Exception as e:
                logger.error(f"[DEBUG] UTF-8 encoding test: FAILED - {e}")
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
                'zh': '発生錯誤。請重試。',
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
            # msg.partsはtypes.Partã'ªãƒ–ã'¸ã'§ã'¯ãƒˆã®ãƒªã'¹ãƒˆãªã®ã§ã€æœ€åˆã®è¦ç´ のtextを取得
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
        """åº—èˆ—æƒ…å ±ã''æ•´å½¢ã—ã¦ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã«è¿½åŠ """
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
        """JSONレスポンスをパース - 最初のJSONオブジェクトのみ抽出"""
        try:
            # 【重要】最初の { から 対応する } までを抽出
            # 入れ子のJSONに対応するため、ブレースのカウントを行う
            start_idx = text.find('{')
            if start_idx == -1:
                logger.warning("[JSON Parse] JSON形式が見つかりません")
                shops = extract_shops_from_response(text)
                return text, shops
            
            # ブレースのカウントで対応する閉じブレースを見つける
            brace_count = 0
            end_idx = -1
            for i in range(start_idx, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            
            if end_idx == -1:
                logger.warning("[JSON Parse] JSONの閉じブレースが見つかりません")
                shops = extract_shops_from_response(text)
                return text, shops
            
            json_str = text[start_idx:end_idx].strip()
            logger.info(f"[JSON Parse] JSONオブジェクトを検出: {len(json_str)}文字")
            
            data = json.loads(json_str)
            
            message = data.get('message', text)
            shops = data.get('shops', [])
            
            logger.info(f"[JSON Parse] 成功: message={len(message)}文字, shops={len(shops)}件")
            return message, shops
            
        except json.JSONDecodeError as e:
            logger.warning(f"[JSON Parse] パース失敗: {e}")
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


# ========================================
# API エンドポイント
# ========================================

