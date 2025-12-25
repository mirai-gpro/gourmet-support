# -*- coding: utf-8 -*-
"""
ビジネスロジック・コアクラス
- プロンプト管理
- セッション管理
- アシスタント（AI会話ロジック）
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
# RAMãƒ™ãƒ¼ã‚¹ã®ã‚»ãƒƒã‚·ãƒ§ãƒ³ç®¡ç† (Firestoreå®Œå…¨å»ƒæ­¢)
# ========================================
_SESSION_CACHE = {}

# ========================================
# ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆèª­ã¿è¾¼ã¿ (GCSå„ªå…ˆã€ãƒ­ãƒ¼ã‚«ãƒ«ãƒ•ã‚©ãƒ¼ãƒ«ãƒãƒƒã‚¯)
# ========================================

def load_prompts_from_gcs():
    """
    GCSã‹ã‚‰2ç¨®é¡žã®ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã‚’èª­ã¿è¾¼ã¿
    - support_system_{lang}.txt: ãƒãƒ£ãƒƒãƒˆãƒ¢ãƒ¼ãƒ‰ç”¨
    - concierge_{lang}.txt: ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ãƒ¢ãƒ¼ãƒ‰ç”¨
    """
    try:
        bucket_name = os.getenv('PROMPTS_BUCKET_NAME')
        if not bucket_name:
            logger.warning("[Prompt] PROMPTS_BUCKET_NAME ãŒè¨­å®šã•ã‚Œã¦ã„ã¾ã›ã‚“ã€‚ãƒ­ãƒ¼ã‚«ãƒ«ãƒ•ã‚¡ã‚¤ãƒ«ã‚’ä½¿ç”¨ã—ã¾ã™ã€‚")
            return None

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        prompts = {
            'chat': {},      # ãƒãƒ£ãƒƒãƒˆãƒ¢ãƒ¼ãƒ‰ç”¨
            'concierge': {}  # ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ãƒ¢ãƒ¼ãƒ‰ç”¨
        }

        for lang in ['ja', 'en', 'zh', 'ko']:
            # ãƒãƒ£ãƒƒãƒˆãƒ¢ãƒ¼ãƒ‰ç”¨ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆ
            chat_blob = bucket.blob(f'prompts/support_system_{lang}.txt')
            if chat_blob.exists():
                prompts['chat'][lang] = chat_blob.download_as_text(encoding='utf-8')
                logger.info(f"[Prompt] GCSã‹ã‚‰èª­ã¿è¾¼ã¿æˆåŠŸ: support_system_{lang}.txt")
            else:
                logger.warning(f"[Prompt] GCSã«è¦‹ã¤ã‹ã‚Šã¾ã›ã‚“: support_system_{lang}.txt")

            # ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ãƒ¢ãƒ¼ãƒ‰ç”¨ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆ
            concierge_blob = bucket.blob(f'prompts/concierge_{lang}.txt')
            if concierge_blob.exists():
                content = concierge_blob.download_as_text(encoding='utf-8')
                try:
                    json_data = json.loads(content)
                    prompts['concierge'][lang] = json_data.get('concierge_system', content)
                except json.JSONDecodeError:
                    prompts['concierge'][lang] = content
                logger.info(f"[Prompt] GCSã‹ã‚‰èª­ã¿è¾¼ã¿æˆåŠŸ: concierge_{lang}.txt")
            else:
                logger.warning(f"[Prompt] GCSã«è¦‹ã¤ã‹ã‚Šã¾ã›ã‚“: concierge_{lang}.txt")

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

# プロンプト読み込み実行（モジュールロード時）
SYSTEM_PROMPTS = load_system_prompts()
INITIAL_GREETINGS = {
    'chat': {
        'ja': 'ã“ã‚“ã«ã¡ã¯!ãŠåº—æŽ¢ã—ã‚’ãŠæ‰‹ä¼ã„ã—ã¾ã™ã€‚ã©ã®ã‚ˆã†ãªãŠåº—ã‚’ãŠæŽ¢ã—ã§ã™ã‹?(ä¾‹:æ–°å®¿ã§ç¾Žå‘³ã—ã„ã‚¤ã‚¿ãƒªã‚¢ãƒ³ã€æ˜Žæ—¥19æ™‚ã«äºˆç´„ã§ãã‚‹ç„¼è‚‰åº—ãªã©)',
        'en': 'Hello! I\'m here to help you find restaurants. What kind of restaurant are you looking for?',
        'zh': 'æ‚¨å¥½!æˆ‘æ¥å¸®æ‚¨æ‰¾é¤åŽ…ã€‚æ‚¨åœ¨å¯»æ‰¾ä»€ä¹ˆæ ·çš„é¤åŽ…?',
        'ko': 'ì•ˆë…•í•˜ì„¸ìš”! ë ˆìŠ¤í† ëž‘ ì°¾ê¸°ë¥¼ ë„ì™€ë“œë¦¬ê² ìŠµë‹ˆë‹¤. ì–´ë–¤ ë ˆìŠ¤í† ëž‘ì„ ì°¾ìœ¼ì‹œë‚˜ìš”?'
    },
    'concierge': {
        'ja': 'ã„ã‚‰ã£ã—ã‚ƒã„ã¾ã›ã€‚ã‚°ãƒ«ãƒ¡ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ã§ã™ã€‚ä»Šæ—¥ã¯ã©ã®ã‚ˆã†ãªã‚·ãƒ¼ãƒ³ã§ãŠåº—ã‚’ãŠæŽ¢ã—ã§ã—ã‚‡ã†ã‹?æŽ¥å¾…ã€ãƒ‡ãƒ¼ãƒˆã€å¥³å­ä¼šãªã©ã€ãŠæ°—è»½ã«ãŠèžã‹ã›ãã ã•ã„ã€‚',
        'en': 'Welcome! I\'m your gourmet concierge. What kind of dining experience are you looking for today? Business dinner, date, gathering with friends?',
        'zh': 'æ¬¢è¿Žå…‰ä¸´!æˆ‘æ˜¯æ‚¨çš„ç¾Žé£Ÿç¤¼å®¾å‘˜ã€‚ä»Šå¤©æ‚¨æƒ³å¯»æ‰¾ä»€ä¹ˆæ ·çš„ç”¨é¤åœºæ™¯?å•†åŠ¡å®´è¯·ã€çº¦ä¼šã€æœ‹å‹èšä¼š?',
        'ko': 'ì–´ì„œì˜¤ì„¸ìš”! ì €ëŠ” ê·€í•˜ì˜ ë¯¸ì‹ ì»¨ì‹œì–´ì§€ìž…ë‹ˆë‹¤. ì˜¤ëŠ˜ì€ ì–´ë–¤ ì‹ì‚¬ ìž¥ë©´ì„ ì°¾ìœ¼ì‹œë‚˜ìš”? ì ‘ëŒ€, ë°ì´íŠ¸, ëª¨ìž„ ë“±?'
    }
}

CONVERSATION_SUMMARY_TEMPLATES = {
    'ja': 'ä»¥ä¸‹ã®ä¼šè©±ã‚’1æ–‡ã§è¦ç´„ã—ã¦ãã ã•ã„ã€‚\n\nãƒ¦ãƒ¼ã‚¶ãƒ¼: {user_message}\nã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆ: {assistant_response}\n\nè¦ç´„:',
    'en': 'Summarize the following conversation in one sentence.\n\nUser: {user_message}\nAssistant: {assistant_response}\n\nSummary:',
    'zh': 'è¯·ç”¨ä¸€å¥è¯æ€»ç»“ä»¥ä¸‹å¯¹è¯ã€‚\n\nç”¨æˆ·:{user_message}\nåŠ©æ‰‹:{assistant_response}\n\næ€»ç»“:',
    'ko': 'ë‹¤ìŒ ëŒ€í™”ë¥¼ í•œ ë¬¸ìž¥ìœ¼ë¡œ ìš”ì•½í•˜ì„¸ìš”.\n\nì‚¬ìš©ìž: {user_message}\nì–´ì‹œìŠ¤í„´íŠ¸: {assistant_response}\n\nìš”ì•½:'
}

FINAL_SUMMARY_TEMPLATES = {
    'ja': 'ä»¥ä¸‹ã®ä¼šè©±å…¨ä½“ã‚’è¦ç´„ã—ã€å•ã„åˆã‚ã›å†…å®¹ã‚’ã¾ã¨ã‚ã¦ãã ã•ã„ã€‚\n\n{conversation_text}\n\nä½œæˆæ—¥æ™‚: {timestamp}\n\nè¦ç´„:',
    'en': 'Summarize the entire conversation below and organize the inquiry content.\n\n{conversation_text}\n\nCreated: {timestamp}\n\nSummary:',
    'zh': 'è¯·æ€»ç»“ä»¥ä¸‹æ•´ä¸ªå¯¹è¯å¹¶æ•´ç†å’¨è¯¢å†…å®¹ã€‚\n\n{conversation_text}\n\nåˆ›å»ºæ—¶é—´:{timestamp}\n\næ€»ç»“:',
    'ko': 'ë‹¤ìŒ ì „ì²´ ëŒ€í™”ë¥¼ ìš”ì•½í•˜ê³  ë¬¸ì˜ ë‚´ìš©ì„ ì •ë¦¬í•˜ì„¸ìš”.\n\n{conversation_text}\n\nìž‘ì„± ì‹œê°„: {timestamp}\n\nìš”ì•½:'
}

class SupportSession:
    """ã‚µãƒãƒ¼ãƒˆã‚»ãƒƒã‚·ãƒ§ãƒ³ç®¡ç† (RAMç‰ˆ)"""

    def __init__(self, session_id=None):
        self.session_id = session_id or str(uuid.uuid4())

    def initialize(self, user_info=None, language='ja', mode='chat'):
        """æ–°è¦ã‚»ãƒƒã‚·ãƒ§ãƒ³åˆæœŸåŒ– - ãƒ¢ãƒ¼ãƒ‰å¯¾å¿œ"""
        data = {
            'session_id': self.session_id,
            'messages': [],  # SDKãƒã‚¤ãƒ†ã‚£ãƒ–ã®ãƒªã‚¹ãƒˆå½¢å¼ç”¨
            'status': 'active',
            'user_info': user_info or {},
            'language': language,
            'mode': mode,
            'summary': None,
            'inquiry_summary': None,
            'current_shops': []
        }
        _SESSION_CACHE[self.session_id] = data
        logger.info(f"[Session] RAMä½œæˆ: {self.session_id}, è¨€èªž: {language}, ãƒ¢ãƒ¼ãƒ‰: {mode}")
        return data

    def add_message(self, role, content, message_type='chat'):
        """ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ã‚’è¿½åŠ ï¼ˆå½¹å‰²(Role)åˆ¥ã®æ§‹é€ ã§ä¿å­˜ï¼‰"""
        data = self.get_data()
        if not data:
            return None
        
        # genai SDKãŒç†è§£ã§ãã‚‹æ§‹é€ ã§ä¿å­˜
        message = {
            'role': 'user' if role == 'user' else 'model',
            'parts': [content],
            'type': message_type,  # å†…éƒ¨ç®¡ç†ç”¨
            'timestamp': datetime.now().isoformat()
        }
        data['messages'].append(message)
        logger.info(f"[Session] ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸è¿½åŠ : role={message['role']}, type={message_type}")
        return message

    def get_history_for_api(self):
        """SDKã«ãã®ã¾ã¾æ¸¡ã›ã‚‹å½¢å¼ã®ãƒªã‚¹ãƒˆã‚’è¿”ã™ï¼ˆtypes.Contentã‚ªãƒ–ã‚¸ã‚§ã‚¯ãƒˆã®ãƒªã‚¹ãƒˆï¼‰"""
        data = self.get_data()
        if not data:
            return []
        
        # ã€é‡è¦ã€‘è¾žæ›¸ã§ã¯ãªãtypes.Contentã‚ªãƒ–ã‚¸ã‚§ã‚¯ãƒˆã‚’ä½œæˆ
        history = []
        for m in data['messages']:
            if m['type'] == 'chat':
                # types.Contentã‚ªãƒ–ã‚¸ã‚§ã‚¯ãƒˆã‚’ä½œæˆ
                content = types.Content(
                    role=m['role'],
                    parts=[types.Part(text=m['parts'][0])]  # partsã¯æ–‡å­—åˆ—ã®ãƒªã‚¹ãƒˆãªã®ã§æœ€åˆã®è¦ç´ ã‚’å–å¾—
                )
                history.append(content)
        
        logger.info(f"[Session] APIç”¨å±¥æ­´ç”Ÿæˆ: {len(history)}ä»¶ã®ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸")
        return history

    def get_messages(self, include_types=None):
        """ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸å±¥æ­´ã‚’å–å¾—ï¼ˆäº’æ›æ€§ã®ãŸã‚æ®‹ã™ï¼‰"""
        data = self.get_data()
        if not data:
            return []

        messages = data.get('messages', [])

        if include_types:
            messages = [m for m in messages if m.get('type') in include_types]

        return messages

    def save_current_shops(self, shops):
        """ç¾åœ¨ã®åº—èˆ—ãƒªã‚¹ãƒˆã‚’ä¿å­˜"""
        data = self.get_data()
        if data:
            data['current_shops'] = shops
            logger.info(f"[Session] åº—èˆ—ãƒªã‚¹ãƒˆä¿å­˜: {len(shops)}ä»¶")

    def get_current_shops(self):
        """ç¾åœ¨ã®åº—èˆ—ãƒªã‚¹ãƒˆã‚’å–å¾—"""
        data = self.get_data()
        return data.get('current_shops', []) if data else []

    def update_status(self, status, **kwargs):
        """ã‚¹ãƒ†ãƒ¼ã‚¿ã‚¹æ›´æ–°"""
        data = self.get_data()
        if data:
            data['status'] = status
            data.update(kwargs)
            logger.info(f"[Session] ã‚¹ãƒ†ãƒ¼ã‚¿ã‚¹æ›´æ–°: {status}")

    def get_data(self):
        """ã‚»ãƒƒã‚·ãƒ§ãƒ³ãƒ‡ãƒ¼ã‚¿å–å¾—"""
        return _SESSION_CACHE.get(self.session_id)

    def get_language(self):
        """ã‚»ãƒƒã‚·ãƒ§ãƒ³è¨€èªžã‚’å–å¾—"""
        data = self.get_data()
        return data.get('language', 'ja') if data else 'ja'

    def get_mode(self):
        """ã‚»ãƒƒã‚·ãƒ§ãƒ³ãƒ¢ãƒ¼ãƒ‰ã‚’å–å¾—"""
        data = self.get_data()
        return data.get('mode', 'chat') if data else 'chat'

    def update_language(self, language: str):
        """ã‚»ãƒƒã‚·ãƒ§ãƒ³è¨€èªžã‚’æ›´æ–°"""
        data = self.get_data()
        if data:
            data['language'] = language
            logger.info(f"[Session] è¨€èªžæ›´æ–°: {language}")

    def update_mode(self, mode: str):
        """ã‚»ãƒƒã‚·ãƒ§ãƒ³ãƒ¢ãƒ¼ãƒ‰ã‚’æ›´æ–°"""
        data = self.get_data()
        if data:
            data['mode'] = mode
            logger.info(f"[Session] ãƒ¢ãƒ¼ãƒ‰æ›´æ–°: {mode}")


class SupportAssistant:
    """ã‚µãƒãƒ¼ãƒˆã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆ - ãƒ¢ãƒ¼ãƒ‰å¯¾å¿œç‰ˆ"""

    def __init__(self, session: SupportSession, system_prompts: dict):
        self.session = session
        self.language = session.get_language()
        self.mode = session.get_mode()  # â˜… ãƒ¢ãƒ¼ãƒ‰ã‚’å–å¾—
        
        # â˜…â˜…â˜… ãƒ¢ãƒ¼ãƒ‰ã«å¿œã˜ãŸãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã‚’é¸æŠž â˜…â˜…â˜…
        mode_prompts = system_prompts.get(self.mode, SYSTEM_PROMPTS.get('chat', {}))
        self.system_prompt = mode_prompts.get(self.language, mode_prompts.get('ja', ''))
        
        logger.info(f"[Assistant] åˆæœŸåŒ–: mode={self.mode}, language={self.language}")

    def get_initial_message(self):
        """åˆå›žãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ - ãƒ¢ãƒ¼ãƒ‰åˆ¥"""
        greetings = INITIAL_GREETINGS.get(self.mode, INITIAL_GREETINGS.get('chat', {}))
        return greetings.get(self.language, greetings.get('ja', ''))

    def is_followup_question(self, user_message, current_shops):
        """æ·±æŽ˜ã‚Šè³ªå•ã‹ã©ã†ã‹ã‚’åˆ¤å®š"""
        if not current_shops:
            return False

        # ãƒ•ã‚©ãƒ­ãƒ¼ã‚¢ãƒƒãƒ—è³ªå•ã®ãƒ‘ã‚¿ãƒ¼ãƒ³(æ–™ç†åã¯é™¤å¤– - åˆå›žæ¤œç´¢ã§èª¤åˆ¤å®šã•ã‚Œã‚‹ãŸã‚)
        followup_patterns = [
            'ã“ã®ä¸­ã§', 'ã“ã‚Œã‚‰ã®ä¸­ã§', 'ã•ã£ãã®', 'å…ˆã»ã©ã®',
            'ã©ã‚ŒãŒ', 'ã©ã“ãŒ', 'ã©ã®åº—', 'ä½•ç•ªç›®',
            'äºˆç´„', 'é›»è©±ç•ªå·', 'å–¶æ¥­æ™‚é–“', 'ã‚¢ã‚¯ã‚»ã‚¹',
            'è©³ã—ã', 'ã‚‚ã£ã¨', 'ã«ã¤ã„ã¦'
        ]

        message_lower = user_message.lower()
        return any(pattern in message_lower for pattern in followup_patterns)

    def process_user_message(self, user_message, conversation_stage='conversation'):
        """
        ãƒ¦ãƒ¼ã‚¶ãƒ¼ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ã‚’å‡¦ç†
        
        ã€é‡è¦ã€‘æ”¹å–„ã•ã‚ŒãŸãƒ•ãƒ­ãƒ¼:
        1. å±¥æ­´ã‚’æ§‹é€ åŒ–ãƒªã‚¹ãƒˆã§å–å¾—
        2. å±¥æ­´ã«ã¯æ—¢ã«æœ€æ–°ã®ãƒ¦ãƒ¼ã‚¶ãƒ¼ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ãŒå«ã¾ã‚Œã¦ã„ã‚‹ï¼ˆadd_messageã§è¿½åŠ æ¸ˆã¿ï¼‰
        3. ãã®ãŸã‚ã€å±¥æ­´ã‚’ãã®ã¾ã¾Geminiã«æ¸¡ã™
        """
        # å±¥æ­´ã‚’æ§‹é€ åŒ–ãƒªã‚¹ãƒˆã§å–å¾—ï¼ˆæ—¢ã«æœ€æ–°ã®ãƒ¦ãƒ¼ã‚¶ãƒ¼ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ã‚’å«ã‚€ï¼‰
        history = self.session.get_history_for_api()
        current_shops = self.session.get_current_shops()

        is_followup = self.is_followup_question(user_message, current_shops)

        # ãƒ•ã‚©ãƒ­ãƒ¼ã‚¢ãƒƒãƒ—ã®å ´åˆã¯ç¾åœ¨ã®åº—èˆ—æƒ…å ±ã‚’ã‚·ã‚¹ãƒ†ãƒ ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã«è¿½åŠ 
        system_prompt = self.system_prompt
        if is_followup and current_shops:
            followup_messages = {
                'ja': {
                    'header': 'ã€ç¾åœ¨ææ¡ˆä¸­ã®åº—èˆ—æƒ…å ±ã€‘',
                    'footer': 'ãƒ¦ãƒ¼ã‚¶ãƒ¼ã¯ä¸Šè¨˜ã®åº—èˆ—ã«ã¤ã„ã¦è³ªå•ã—ã¦ã„ã¾ã™ã€‚åº—èˆ—æƒ…å ±ã‚’å‚ç…§ã—ã¦å›žç­”ã—ã¦ãã ã•ã„ã€‚'
                },
                'en': {
                    'header': 'ã€Currently Proposed Restaurantsã€‘',
                    'footer': 'The user is asking about the restaurants listed above. Please refer to the restaurant information when answering.'
                },
                'zh': {
                    'header': 'ã€å½“å‰æŽ¨èçš„é¤åŽ…ä¿¡æ¯ã€‘',
                    'footer': 'ç”¨æˆ·æ­£åœ¨è¯¢é—®ä¸Šè¿°é¤åŽ…çš„ä¿¡æ¯ã€‚è¯·å‚è€ƒé¤åŽ…ä¿¡æ¯è¿›è¡Œå›žç­”ã€‚'
                },
                'ko': {
                    'header': 'ã€í˜„ìž¬ ì œì•ˆ ì¤‘ì¸ ë ˆìŠ¤í† ëž‘ ì •ë³´ã€‘',
                    'footer': 'ì‚¬ìš©ìžëŠ” ìœ„ ë ˆìŠ¤í† ëž‘ì— ëŒ€í•´ ì§ˆë¬¸í•˜ê³  ìžˆìŠµë‹ˆë‹¤. ë ˆìŠ¤í† ëž‘ ì •ë³´ë¥¼ ì°¸ì¡°í•˜ì—¬ ë‹µë³€í•˜ì„¸ìš”.'
                }
            }
            current_followup_msg = followup_messages.get(self.language, followup_messages['ja'])
            shop_context = f"\n\n{current_followup_msg['header']}\n{self._format_current_shops(current_shops)}\n\n{current_followup_msg['footer']}"
            system_prompt = self.system_prompt + shop_context
            logger.info("[Assistant] ãƒ•ã‚©ãƒ­ãƒ¼ã‚¢ãƒƒãƒ—è³ªå•ãƒ¢ãƒ¼ãƒ‰: åº—èˆ—æƒ…å ±ã‚’ã‚·ã‚¹ãƒ†ãƒ ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã«è¿½åŠ ")

        # ãƒ„ãƒ¼ãƒ«è¨­å®š
        tools = None
        if not is_followup:
            tools = [types.Tool(google_search=types.GoogleSearch())]
            logger.info("[Assistant] Googleæ¤œç´¢ã‚°ãƒ©ã‚¦ãƒ³ãƒ‡ã‚£ãƒ³ã‚°ã‚’æœ‰åŠ¹åŒ–")

        try:
            logger.info(f"[Assistant] Gemini APIå‘¼ã³å‡ºã—é–‹å§‹: å±¥æ­´={len(history)}ä»¶")

            # ã€é‡è¦ã€‘configãƒ‘ãƒ©ãƒ¡ãƒ¼ã‚¿ã‚’ä½¿ç”¨ï¼ˆSDKã®æ­£ã—ã„ä½¿ã„æ–¹ï¼‰
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

            logger.info("[Assistant] Gemini APIå‘¼ã³å‡ºã—å®Œäº†")

            # ãƒ¬ã‚¹ãƒãƒ³ã‚¹ã‹ã‚‰ãƒ†ã‚­ã‚¹ãƒˆã‚’å–å¾—
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
                        'ja': lambda count: f"{count}è»’ã®ãŠåº—ã‚’ææ¡ˆã—ã¾ã—ãŸã€‚",
                        'en': lambda count: f"Suggested {count} restaurants.",
                        'zh': lambda count: f"æŽ¨èäº†{count}å®¶é¤åŽ…ã€‚",
                        'ko': lambda count: f"{count}ê°œì˜ ë ˆìŠ¤í† ëž‘ì„ ì œì•ˆí–ˆìŠµë‹ˆë‹¤."
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
                'ja': 'ã‚¨ãƒ©ãƒ¼ãŒç™ºç”Ÿã—ã¾ã—ãŸã€‚ã‚‚ã†ä¸€åº¦ãŠè©¦ã—ãã ã•ã„ã€‚',
                'en': 'An error occurred. Please try again.',
                'zh': 'ç™ºç”ŸéŒ¯èª¤ã€‚è«‹é‡è©¦ã€‚',
                'ko': 'ì˜¤ë¥˜ê°€ ë°œìƒí–ˆìŠµë‹ˆë‹¤. ë‹¤ì‹œ ì‹œë„í•´ì£¼ì„¸ìš”.'
            }
            return {
                'response': error_messages.get(self.language, error_messages['ja']),
                'summary': None,
                'shops': [],
                'should_confirm': False,
                'is_followup': False
            }

    def generate_final_summary(self):
        """æœ€çµ‚è¦ç´„ã‚’ç”Ÿæˆ"""
        all_messages = self.session.get_history_for_api()
        
        # ä¼šè©±ãƒ†ã‚­ã‚¹ãƒˆã‚’æ•´å½¢
        # ã€é‡è¦ã€‘all_messagesã¯types.Contentã‚ªãƒ–ã‚¸ã‚§ã‚¯ãƒˆã®ãƒªã‚¹ãƒˆ
        conversation_lines = []
        for msg in all_messages:
            role_name = 'ãƒ¦ãƒ¼ã‚¶ãƒ¼' if msg.role == 'user' else 'ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆ'
            # msg.partsã¯types.Partã‚ªãƒ–ã‚¸ã‚§ã‚¯ãƒˆã®ãƒªã‚¹ãƒˆãªã®ã§ã€æœ€åˆã®è¦ç´ ã®textã‚’å–å¾—
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
            return "è¦ç´„ã®ç”Ÿæˆä¸­ã«ã‚¨ãƒ©ãƒ¼ãŒç™ºç”Ÿã—ã¾ã—ãŸã€‚"

    def _format_current_shops(self, shops):
        """åº—èˆ—æƒ…å ±ã‚’æ•´å½¢ã—ã¦ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã«è¿½åŠ """
        # å¤šè¨€èªžãƒ©ãƒ™ãƒ«
        shop_labels = {
            'ja': {
                'description': 'èª¬æ˜Ž',
                'specialty': 'çœ‹æ¿ãƒ¡ãƒ‹ãƒ¥ãƒ¼',
                'price': 'äºˆç®—',
                'atmosphere': 'é›°å›²æ°—',
                'features': 'ç‰¹è‰²'
            },
            'en': {
                'description': 'Description',
                'specialty': 'Specialty',
                'price': 'Price Range',
                'atmosphere': 'Atmosphere',
                'features': 'Features'
            },
            'zh': {
                'description': 'è¯´æ˜Ž',
                'specialty': 'æ‹›ç‰Œèœ',
                'price': 'é¢„ç®—',
                'atmosphere': 'æ°›å›´',
                'features': 'ç‰¹è‰²'
            },
            'ko': {
                'description': 'ì„¤ëª…',
                'specialty': 'ëŒ€í‘œ ë©”ë‰´',
                'price': 'ì˜ˆì‚°',
                'atmosphere': 'ë¶„ìœ„ê¸°',
                'features': 'íŠ¹ì§•'
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
        """ä¼šè©±ã®è¦ç´„ã‚’ç”Ÿæˆ"""
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
# API ã‚¨ãƒ³ãƒ‰ãƒã‚¤ãƒ³ãƒˆ
# ========================================

