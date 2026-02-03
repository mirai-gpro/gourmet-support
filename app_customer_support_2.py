# -*- coding: utf-8 -*-
"""
汎用カスタマーサポートシステム (Gemini API版) - 改善版
- プロンプト駆動型の質問フロー
- 会話要約による確認
- 質問要約書の生成(即答しない設計)
- Google Cloud STT/TTS統合
- Google Places API連携(店舗写真取得)
- 深掘り質問対応 + 金額表記の自然化
- CORS完全対応 + 海外エリア検索ロジック改善
- RAMベースのセッション管理 (Firestore完全廃止) ★Step 1完了★
"""
import os
import re
import json
import uuid
import logging
import base64
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from google import genai
from google.genai import types
from google.cloud import texttospeech
from google.cloud import speech
from google.cloud import storage
import threading
import queue

# ãƒ­ã‚®ãƒ³ã‚°è¨­å®š
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ========================================
# CORS & SocketIO è¨­å®š (Claudeã‚¢ãƒ‰ãƒã‚¤ã‚¹é©ç”¨ç‰ˆ)
# ========================================

# è¨±å¯ã™ã‚‹ã‚ªãƒªã‚¸ãƒ³(æœ«å°¾ã®ã‚¹ãƒ©ãƒƒã‚·ãƒ¥ãªã—)
allowed_origins = [
    "https://gourmet-sp-two.vercel.app",
    "https://gourmet-sp.vercel.app",
    "http://localhost:4321"
]

# SocketIOåˆæœŸåŒ– (cors_allowed_originsã‚’æ˜Žç¤ºçš„ã«æŒ‡å®š)
socketio = SocketIO(
    app,
    cors_allowed_origins=allowed_origins,
    async_mode='threading',
    logger=False,
    engineio_logger=False
)

# Flask-CORSåˆæœŸåŒ– (supports_credentials=True)
CORS(app, resources={
    r"/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# ã€é‡è¦ã€‘å…¨ãƒ¬ã‚¹ãƒãƒ³ã‚¹ã«å¼·åˆ¶çš„ã«CORSãƒ˜ãƒƒãƒ€ãƒ¼ã‚’æ³¨å…¥ã™ã‚‹ãƒ•ãƒƒã‚¯
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# Gemini ã‚¯ãƒ©ã‚¤ã‚¢ãƒ³ãƒˆåˆæœŸåŒ–ï¼ˆ1å›žã ã‘ï¼‰
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# æ—§SDKï¼ˆäº’æ›æ€§ã®ãŸã‚ä¸€æ™‚çš„ã«æ®‹ã™ï¼‰â€»å¾Œã§å‰Šé™¤äºˆå®š
import google.generativeai as genai_legacy
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
        logger.error(f"[Prompt] GCSèª­ã¿è¾¼ã¿å¤±æ•—: {e}")
        return None

def load_prompts_from_local():
    """
    ãƒ­ãƒ¼ã‚«ãƒ«ãƒ•ã‚¡ã‚¤ãƒ«ã‹ã‚‰2ç¨®é¡žã®ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã‚’èª­ã¿è¾¼ã¿ (ãƒ•ã‚©ãƒ¼ãƒ«ãƒãƒƒã‚¯)
    """
    prompts = {
        'chat': {},
        'concierge': {}
    }
    
    for lang in ['ja', 'en', 'zh', 'ko']:
        # ãƒãƒ£ãƒƒãƒˆãƒ¢ãƒ¼ãƒ‰ç”¨
        chat_file = f'prompts/support_system_{lang}.txt'
        try:
            with open(chat_file, 'r', encoding='utf-8') as f:
                prompts['chat'][lang] = f.read()
                logger.info(f"[Prompt] ãƒ­ãƒ¼ã‚«ãƒ«ã‹ã‚‰èª­ã¿è¾¼ã¿æˆåŠŸ: support_system_{lang}.txt")
        except FileNotFoundError:
            logger.warning(f"[Prompt] ãƒ­ãƒ¼ã‚«ãƒ«ãƒ•ã‚¡ã‚¤ãƒ«ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“: {chat_file}")
        except Exception as e:
            logger.error(f"[Prompt] ãƒ­ãƒ¼ã‚«ãƒ«èª­ã¿è¾¼ã¿ã‚¨ãƒ©ãƒ¼ (chat/{lang}): {e}")

        # ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ãƒ¢ãƒ¼ãƒ‰ç”¨
        concierge_file = f'prompts/concierge_{lang}.txt'
        try:
            with open(concierge_file, 'r', encoding='utf-8') as f:
                content = f.read()
                try:
                    json_data = json.loads(content)
                    prompts['concierge'][lang] = json_data.get('concierge_system', content)
                except json.JSONDecodeError:
                    prompts['concierge'][lang] = content
                logger.info(f"[Prompt] ãƒ­ãƒ¼ã‚«ãƒ«ã‹ã‚‰èª­ã¿è¾¼ã¿æˆåŠŸ: concierge_{lang}.txt")
        except FileNotFoundError:
            logger.warning(f"[Prompt] ãƒ­ãƒ¼ã‚«ãƒ«ãƒ•ã‚¡ã‚¤ãƒ«ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“: {concierge_file}")
        except Exception as e:
            logger.error(f"[Prompt] ãƒ­ãƒ¼ã‚«ãƒ«èª­ã¿è¾¼ã¿ã‚¨ãƒ©ãƒ¼ (concierge/{lang}): {e}")

    return prompts if (prompts['chat'] or prompts['concierge']) else None

# ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆèª­ã¿è¾¼ã¿å®Ÿè¡Œ
logger.info("[Prompt] ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆèª­ã¿è¾¼ã¿é–‹å§‹...")
SYSTEM_PROMPTS = load_prompts_from_gcs()
if not SYSTEM_PROMPTS:
    logger.info("[Prompt] GCSã‹ã‚‰èª­ã¿è¾¼ã‚ã¾ã›ã‚“ã§ã—ãŸã€‚ãƒ­ãƒ¼ã‚«ãƒ«ãƒ•ã‚¡ã‚¤ãƒ«ã‚’ä½¿ç”¨ã—ã¾ã™ã€‚")
    SYSTEM_PROMPTS = load_prompts_from_local()

if not SYSTEM_PROMPTS or (not SYSTEM_PROMPTS.get('chat') and not SYSTEM_PROMPTS.get('concierge')):
    logger.error("[Prompt] ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã®èª­ã¿è¾¼ã¿ã«å¤±æ•—ã—ã¾ã—ãŸ!")
    SYSTEM_PROMPTS = {
        'chat': {'ja': 'ã‚¨ãƒ©ãƒ¼: ãƒãƒ£ãƒƒãƒˆãƒ¢ãƒ¼ãƒ‰ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆãŒèª­ã¿è¾¼ã‚ã¾ã›ã‚“ã§ã—ãŸã€‚'},
        'concierge': {'ja': 'ã‚¨ãƒ©ãƒ¼: ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ãƒ¢ãƒ¼ãƒ‰ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆãŒèª­ã¿è¾¼ã‚ã¾ã›ã‚“ã§ã—ãŸã€‚'}
    }
else:
    logger.info(f"[Prompt] ãƒ—ãƒ­ãƒ³ãƒ—ãƒˆèª­ã¿è¾¼ã¿å®Œäº†:")
    logger.info(f"  - ãƒãƒ£ãƒƒãƒˆãƒ¢ãƒ¼ãƒ‰: {list(SYSTEM_PROMPTS.get('chat', {}).keys())}")
    logger.info(f"  - ã‚³ãƒ³ã‚·ã‚§ãƒ«ã‚¸ãƒ¥ãƒ¢ãƒ¼ãƒ‰: {list(SYSTEM_PROMPTS.get('concierge', {}).keys())}")

# å¤šè¨€èªžãƒ†ãƒ³ãƒ—ãƒ¬ãƒ¼ãƒˆ (ã‚·ãƒ³ãƒ—ãƒ«ãªã‚‚ã®ã¯ã“ã“ã«ãƒãƒ¼ãƒ‰ã‚³ãƒ¼ãƒ‰)
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

# Google Cloud TTS/STTåˆæœŸåŒ–
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

# Google Places API
GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')

# Google Geocoding API(Places APIã¨åŒã˜ã‚­ãƒ¼ã‚’ä½¿ç”¨)
GOOGLE_GEOCODING_API_KEY = os.getenv('GOOGLE_GEOCODING_API_KEY', GOOGLE_PLACES_API_KEY)

# ãƒ›ãƒƒãƒˆãƒšãƒƒãƒ‘ãƒ¼API
HOTPEPPER_API_KEY = os.getenv('HOTPEPPER_API_KEY', 'c22031a566715e40')

# TripAdvisor Content API
TRIPADVISOR_API_KEY = os.getenv('TRIPADVISOR_API_KEY', '')
MY_DOMAIN_URL = "https://unfix.co.jp"
# ã€è¿½åŠ ã€‘ãƒ–ãƒ©ã‚¦ã‚¶ã®ãµã‚Šã‚’ã™ã‚‹ãŸã‚ã®User-Agentè¨­å®š (Botåˆ¤å®šå›žé¿ç”¨)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Google Custom Search API(é£Ÿã¹ãƒ­ã‚°æ¤œç´¢ç”¨)
GOOGLE_CSE_API_KEY = os.getenv('GOOGLE_CSE_API_KEY', '')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID', '')

# é£Ÿã¹ãƒ­ã‚°ã®éƒ½é“åºœçœŒã‚³ãƒ¼ãƒ‰
TABELOG_PREF_CODES = {
    'æ±äº¬': 'tokyo',
    'ç¥žå¥ˆå·': 'kanagawa',
    'åŸ¼çŽ‰': 'saitama',
    'åƒè‘‰': 'chiba',
    'å¤§é˜ª': 'osaka',
    'äº¬éƒ½': 'kyoto',
    'å…µåº«': 'hyogo',
    'æ„›çŸ¥': 'aichi',
    'ç¦å²¡': 'fukuoka',
    'åŒ—æµ·é“': 'hokkaido',
}

# ãƒ›ãƒƒãƒˆãƒšãƒƒãƒ‘ãƒ¼ã®ã‚¨ãƒªã‚¢ã‚³ãƒ¼ãƒ‰
HOTPEPPER_AREA_CODES = {
    'æ±äº¬': 'Z011',
    'ç¥žå¥ˆå·': 'Z012',
    'åŸ¼çŽ‰': 'Z013',
    'åƒè‘‰': 'Z014',
    'å¤§é˜ª': 'Z023',
    'äº¬éƒ½': 'Z026',
    'å…µåº«': 'Z024',
    'æ„›çŸ¥': 'Z033',
    'ç¦å²¡': 'Z091',
    'åŒ—æµ·é“': 'Z011',
}

# å®šæ•°
MAX_TOKENS = 4000

# ========================================
# ãƒ›ãƒƒãƒˆãƒšãƒƒãƒ‘ãƒ¼API é€£æº
# ========================================

def search_hotpepper(shop_name: str, area: str = '', geo_info: dict = None) -> str:
    """
    ãƒ›ãƒƒãƒˆãƒšãƒƒãƒ‘ãƒ¼APIã§åº—èˆ—ã‚’æ¤œç´¢ã—ã¦åº—èˆ—ãƒšãƒ¼ã‚¸URLã‚’è¿”ã™
    """
    if not HOTPEPPER_API_KEY:
        logger.warning("[Hotpepper API] APIã‚­ãƒ¼ãŒè¨­å®šã•ã‚Œã¦ã„ã¾ã›ã‚“")
        return None

    # Geocoding APIã®çµæžœã‹ã‚‰éƒ½é“åºœçœŒã‚’å–å¾—
    large_area = 'Z011'  # ãƒ‡ãƒ•ã‚©ãƒ«ãƒˆæ±äº¬
    if geo_info:
        region = geo_info.get('region', '')
        # "æ±äº¬éƒ½" â†’ "æ±äº¬" ã«å¤‰æ›ã—ã¦ã‚¨ãƒªã‚¢ã‚³ãƒ¼ãƒ‰ã‚’å–å¾—
        pref = region.rstrip('éƒ½é“åºœçœŒ') if region else ''
        large_area = HOTPEPPER_AREA_CODES.get(pref, 'Z011')

    try:
        url = 'http://webservice.recruit.co.jp/hotpepper/gourmet/v1/'
        params = {
            'key': HOTPEPPER_API_KEY,
            'keyword': shop_name,
            'large_area': large_area,
            'format': 'json',
            'count': 1
        }

        logger.info(f"[Hotpepper API] æ¤œç´¢: {shop_name} (ã‚¨ãƒªã‚¢: {large_area})")

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        results = data.get('results', {})
        shops = results.get('shop', [])

        if shops:
            shop_url = shops[0].get('urls', {}).get('pc', '')
            logger.info(f"[Hotpepper API] å–å¾—æˆåŠŸ: {shop_name} -> {shop_url}")
            return shop_url
        else:
            logger.info(f"[Hotpepper API] çµæžœãªã—: {shop_name}")
            return None

    except Exception as e:
        logger.error(f"[Hotpepper API] ã‚¨ãƒ©ãƒ¼: {e}")
        return None

# ========================================
# TripAdvisor Content API é€£æº
# ========================================
def search_tripadvisor_location(shop_name: str, lat: float = None, lng: float = None, language: str = 'en') -> dict:
    """
    TripAdvisor Location Search APIã§åº—èˆ—ã®location_idã‚’æ¤œç´¢
    """
    if not TRIPADVISOR_API_KEY:
        logger.warning("[TripAdvisor API] APIã‚­ãƒ¼ãŒè¨­å®šã•ã‚Œã¦ã„ã¾ã›ã‚“")
        return None

    try:
        url = 'https://api.content.tripadvisor.com/api/v1/location/search'

        params = {
            'key': TRIPADVISOR_API_KEY,
            'searchQuery': shop_name,
            'language': language
        }

        # åº§æ¨™ãŒã‚ã‚‹å ´åˆã¯è¿½åŠ 
        if lat is not None and lng is not None:
            params['latLong'] = f"{lat},{lng}"

        # ã€ä¿®æ­£ã€‘Referer (httpsä»˜ã) ã¨ User-Agent (ãƒ–ãƒ©ã‚¦ã‚¶å½è£…) ã‚’æŒ‡å®š
        headers = {
            'accept': 'application/json',
            'Referer': MY_DOMAIN_URL,
            'User-Agent': USER_AGENT
        }

        logger.info(f"[TripAdvisor API] Location Search: {shop_name} ({language})")

        response = requests.get(url, params=params, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if data.get('data') and len(data['data']) > 0:
                location = data['data'][0]
                location_id = location.get('location_id')
                logger.info(f"[TripAdvisor API] Location found: {location_id}")
                return {
                    'location_id': location_id,
                    'name': location.get('name'),
                    'address': location.get('address_obj', {}).get('address_string', '')
                }
            else:
                logger.info(f"[TripAdvisor API] Location not found for: {shop_name}")
                return None
        else:
            logger.warning(f"[TripAdvisor API] Search failed: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"[TripAdvisor API] Error: {e}")
        return None


def get_tripadvisor_details(location_id: str, language: str = 'en') -> dict:
    """
    TripAdvisor Location Details APIã§è©•ä¾¡æƒ…å ±ã‚’å–å¾—
    """
    if not TRIPADVISOR_API_KEY or not location_id:
        return None

    try:
        url = f'https://api.content.tripadvisor.com/api/v1/location/{location_id}/details'

        params = {
            'key': TRIPADVISOR_API_KEY,
            'language': language
        }

        # ã€ä¿®æ­£ã€‘ã“ã“ã«ã‚‚ User-Agent ã‚’è¿½åŠ 
        headers = {
            'accept': 'application/json',
            'Referer': MY_DOMAIN_URL,
            'User-Agent': USER_AGENT
        }

        logger.info(f"[TripAdvisor API] Getting details for location: {location_id}")

        response = requests.get(url, params=params, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            rating = data.get('rating')
            num_reviews = data.get('num_reviews', 0)
            web_url = data.get('web_url')

            logger.info(f"[TripAdvisor API] Details: rating={rating}, reviews={num_reviews}")

            return {
                'rating': float(rating) if rating else None,
                'num_reviews': num_reviews,
                'web_url': web_url,
                'location_id': location_id
            }
        else:
            logger.warning(f"[TripAdvisor API] Details failed: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"[TripAdvisor API] Error: {e}")
        return None


def get_tripadvisor_data(shop_name: str, lat: float = None, lng: float = None, language: str = 'en') -> dict:
    """
    TripAdvisor APIã§åº—èˆ—æƒ…å ±ã‚’å–å¾—(æ¤œç´¢ + è©³ç´°)
    """
    # Location IDã‚’æ¤œç´¢
    location_data = search_tripadvisor_location(shop_name, lat, lng, language)
    if not location_data:
        return None

    # è©³ç´°æƒ…å ±ã‚’å–å¾—
    details = get_tripadvisor_details(location_data['location_id'], language)
    if not details:
        return None

    return {
        'rating': details['rating'],
        'num_reviews': details['num_reviews'],
        'web_url': details['web_url'],
        'location_id': details['location_id']
    }

# ========================================
# Google Geocoding API é€£æº
# ========================================

def get_region_from_area(area: str, language: str = 'ja') -> dict:
    """
    Geocoding APIã§ã‚¨ãƒªã‚¢ã®åœ°åŸŸæƒ…å ±(å›½ã€éƒ½é“åºœçœŒ/å·žã€åº§æ¨™)ã‚’å–å¾—
    """
    if not area:
        return None

    if not GOOGLE_GEOCODING_API_KEY:
        logger.warning("[Geocoding API] APIã‚­ãƒ¼ãŒè¨­å®šã•ã‚Œã¦ã„ã¾ã›ã‚“")
        return None

    try:
        url = 'https://maps.googleapis.com/maps/api/geocode/json'
        params = {
            'address': area,
            'key': GOOGLE_GEOCODING_API_KEY,
            'language': language
        }

        logger.info(f"[Geocoding API] ã‚¨ãƒªã‚¢æ¤œç´¢: {area}")

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK' or not data.get('results'):
            logger.warning(f"[Geocoding API] çµæžœãªã—: {area} (status: {data.get('status')})")
            return None

        result = data['results'][0]
        address_components = result.get('address_components', [])

        # å›½ã¨éƒ½é“åºœçœŒ/å·žã‚’æŠ½å‡º
        country = None
        country_code = None
        region = None

        for component in address_components:
            types = component.get('types', [])

            if 'country' in types:
                country = component.get('long_name')
                country_code = component.get('short_name')

            if 'administrative_area_level_1' in types:
                region = component.get('long_name')

        # åº§æ¨™ã‚’å–å¾—
        location = result.get('geometry', {}).get('location', {})
        lat = location.get('lat')
        lng = location.get('lng')

        geo_result = {
            'country': country,
            'country_code': country_code,
            'region': region,
            'formatted_address': result.get('formatted_address', ''),
            'lat': lat,
            'lng': lng
        }

        logger.info(f"[Geocoding API] å–å¾—æˆåŠŸ: {area} â†’ country={country}, region={region}, lat={lat}, lng={lng}")
        return geo_result

    except requests.exceptions.Timeout:
        logger.error(f"[Geocoding API] ã‚¿ã‚¤ãƒ ã‚¢ã‚¦ãƒˆ: {area}")
        return None
    except Exception as e:
        logger.error(f"[Geocoding API] ã‚¨ãƒ©ãƒ¼: {e}")
        return None


# ========================================
# Google Places API é€£æº
# ========================================

def get_place_details(place_id: str, language: str = 'ja') -> dict:
    """
    Place Details APIã§é›»è©±ç•ªå·ã¨å›½ã‚³ãƒ¼ãƒ‰ã‚’å–å¾—
    """
    if not GOOGLE_PLACES_API_KEY or not place_id:
        return {'phone': None, 'country_code': None}

    try:
        details_url = 'https://maps.googleapis.com/maps/api/place/details/json'
        params = {
            'place_id': place_id,
            'fields': 'formatted_phone_number,international_phone_number,address_components',
            'key': GOOGLE_PLACES_API_KEY,
            'language': language
        }

        response = requests.get(details_url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK':
            logger.warning(f"[Place Details API] å–å¾—å¤±æ•—: {data.get('status')} - {place_id}")
            return {'phone': None, 'country_code': None}

        result = data.get('result', {})

        # é›»è©±ç•ªå·å–å¾—(å›½å†…å½¢å¼ã‚’å„ªå…ˆã€ãªã‘ã‚Œã°å›½éš›å½¢å¼)
        phone = result.get('formatted_phone_number') or result.get('international_phone_number')

        # å›½ã‚³ãƒ¼ãƒ‰å–å¾—
        country_code = None
        if result.get('address_components'):
            for component in result['address_components']:
                if 'country' in component.get('types', []):
                    country_code = component.get('short_name')
                    break

        if phone:
            logger.info(f"[Place Details API] é›»è©±ç•ªå·å–å¾—: {phone}, å›½ã‚³ãƒ¼ãƒ‰: {country_code}")

        return {'phone': phone, 'country_code': country_code}

    except requests.exceptions.Timeout:
        logger.error(f"[Place Details API] ã‚¿ã‚¤ãƒ ã‚¢ã‚¦ãƒˆ: {place_id}")
        return {'phone': None, 'country_code': None}
    except Exception as e:
        logger.error(f"[Place Details API] ã‚¨ãƒ©ãƒ¼: {e}")
        return None


def search_place(shop_name: str, area: str = '', geo_info: dict = None, language: str = 'ja') -> dict:
    """
    Google Places APIã§åº—èˆ—ã‚’æ¤œç´¢(å›½ã‚³ãƒ¼ãƒ‰æ¤œè¨¼ä»˜ã)
    """
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("[Places API] APIã‚­ãƒ¼ãŒè¨­å®šã•ã‚Œã¦ã„ã¾ã›ã‚“")
        return None

    # Geocoding APIã®çµæžœã‹ã‚‰éƒ½é“åºœçœŒ/å·žã‚’å–å¾—
    region = geo_info.get('region', '') if geo_info else ''
    expected_country = geo_info.get('country_code', 'JP') if geo_info else 'JP'

    # æ¤œç´¢ã‚¯ã‚¨ãƒªã‚’æ§‹ç¯‰
    if region:
        query = f"{shop_name} {area} {region}".strip()
    else:
        query = f"{shop_name} {area}".strip()

    try:
        search_url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
        params = {
            'query': query,
            'key': GOOGLE_PLACES_API_KEY,
            'language': language,
            'type': 'restaurant'
        }

        # Geocoding APIã®åº§æ¨™ãŒã‚ã‚Œã°ä½ç½®ãƒã‚¤ã‚¢ã‚¹ã‚’è¿½åŠ 
        if geo_info and geo_info.get('lat') and geo_info.get('lng'):
            params['location'] = f"{geo_info['lat']},{geo_info['lng']}"

            # å›½ã«ã‚ˆã£ã¦æ¤œç´¢åŠå¾„ã‚’å¤‰ãˆã‚‹
            if expected_country == 'JP':
                params['radius'] = 3000
                params['region'] = 'jp'
            else:
                params['radius'] = 50000

        logger.info(f"[Places API] æ¤œç´¢ã‚¯ã‚¨ãƒª: {query}")

        response = requests.get(search_url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK':
            logger.warning(f"[Places API] æ¤œç´¢å¤±æ•—: {data.get('status')} - {query}")
            return None

        if not data.get('results'):
            logger.info(f"[Places API] çµæžœãªã—: {query}")
            return None

        place = data['results'][0]
        place_id = place['place_id']

        # ç”»åƒURLã‚’ç”Ÿæˆ
        photo_url = None
        if place.get('photos'):
            photo_reference = place['photos'][0]['photo_reference']
            photo_url = (
                f"https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=800"
                f"&photo_reference={photo_reference}"
                f"&key={GOOGLE_PLACES_API_KEY}"
            )

        maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"

        # åº§æ¨™ã‚’å–å¾—
        geometry = place.get('geometry', {})
        location = geometry.get('location', {})
        lat = location.get('lat')
        lng = location.get('lng')

        # âœ… Place Details APIã§é›»è©±ç•ªå·ã¨å›½ã‚³ãƒ¼ãƒ‰ã‚’å–å¾—
        details = get_place_details(place_id, language)
        actual_country = details.get('country_code')

        # âœ… å›½ã‚³ãƒ¼ãƒ‰æ¤œè¨¼
        if actual_country and expected_country and actual_country != expected_country:
            logger.warning(f"[Places API] å›½ã‚³ãƒ¼ãƒ‰ä¸ä¸€è‡´: {place.get('name')} "
                          f"(æœŸå¾…: {expected_country}, å®Ÿéš›: {actual_country}) - ã‚¹ã‚­ãƒƒãƒ—")
            return None

        result = {
            'place_id': place_id,
            'name': place.get('name'),
            'rating': place.get('rating'),
            'user_ratings_total': place.get('user_ratings_total'),
            'formatted_address': place.get('formatted_address'),
            'country_code': actual_country,
            'lat': lat,
            'lng': lng,
            'photo_url': photo_url,
            'maps_url': maps_url,
            'phone': details.get('phone')
        }

        logger.info(f"[Places API] å–å¾—æˆåŠŸ: {result['name']} (å›½: {actual_country}, é›»è©±: {result['phone']})")
        return result

    except requests.exceptions.Timeout:
        logger.error(f"[Places API] ã‚¿ã‚¤ãƒ ã‚¢ã‚¦ãƒˆ: {query}")
        return None
    except Exception as e:
        logger.error(f"[Places API] ã‚¨ãƒ©ãƒ¼: {e}")
        return None

# ========================================
# ã‚·ãƒ§ãƒƒãƒ—æƒ…å ± æ‹¡å¼µãƒ­ã‚¸ãƒƒã‚¯ (åˆ·æ–°ç‰ˆ)
# ========================================

def enrich_shops_with_photos(shops: list, area: str = '', language: str = 'ja') -> list:
    """
    ã‚·ãƒ§ãƒƒãƒ—ãƒªã‚¹ãƒˆã«å¤–éƒ¨APIãƒ‡ãƒ¼ã‚¿ã‚’è¿½åŠ (place_idé‡è¤‡æŽ’é™¤ä»˜ãã€å›½ã‚³ãƒ¼ãƒ‰æ¤œè¨¼å¼·åŒ–ç‰ˆ)
    - åŸºæœ¬: ãƒˆãƒªãƒƒãƒ—ã‚¢ãƒ‰ãƒã‚¤ã‚¶ãƒ¼ã‚’è¡¨ç¤º
    - ä¾‹å¤–(æ—¥æœ¬èªžã‹ã¤æ—¥æœ¬å›½å†…): å›½å†…3ã‚µã‚¤ãƒˆã‚’è¡¨ç¤ºã—ã€ãƒˆãƒªãƒƒãƒ—ã‚¢ãƒ‰ãƒã‚¤ã‚¶ãƒ¼ã¯éžè¡¨ç¤º
    """

    seen_place_ids = set()  # âœ… é‡è¤‡ãƒã‚§ãƒƒã‚¯ç”¨
    duplicate_count = 0
    validation_failed_count = 0
    
    logger.info(f"[Enrich] é–‹å§‹: area='{area}', language={language}, shops={len(shops)}ä»¶")

    # Geocodingã¯ã‚ãã¾ã§è£œåŠ©æƒ…å ±ã¨ã—ã¦å–å¾—(å¤±æ•—ã—ã¦ã‚‚æ­¢ã¾ã‚‰ãªã„)
    geo_info = None
    if area:
        try:
            geo_info = get_region_from_area(area, language)
            if geo_info:
                logger.info(f"[Enrich] GeocodingæˆåŠŸ: {geo_info.get('formatted_address', '')} "
                           f"(å›½: {geo_info.get('country_code', '')}, "
                           f"åº§æ¨™: {geo_info.get('lat', '')}, {geo_info.get('lng', '')})")
        except Exception as e:
            logger.error(f"[Enrich] Geocoding Error: {e}")

    # LLMãŒå›žç­”ã—ãŸåº—èˆ—åã‚’ãƒ­ã‚°å‡ºåŠ›
    logger.info(f"[Enrich] LLMã®å›žç­”åº—èˆ—:")
    for i, shop in enumerate(shops, 1):
        logger.info(f"[Enrich]   {i}. {shop.get('name', '')}")

    for i, shop in enumerate(shops, 1):
        shop_name = shop.get('name', '')
        if not shop_name:
            continue

        logger.info(f"[Enrich] ----------")
        logger.info(f"[Enrich] {i}/{len(shops)} æ¤œç´¢: '{shop_name}'")

        # -------------------------------------------------------
        # 1. Google Places APIã§åŸºæœ¬æƒ…å ±ã‚’å–å¾—(å›½ã‚³ãƒ¼ãƒ‰æ¤œè¨¼ä»˜ã)
        # -------------------------------------------------------
        place_data = search_place(shop_name, area, geo_info, language)
        
        if not place_data:
            logger.warning(f"[Enrich] Places APIで見つからない。LLMデータをそのまま使用: {shop_name}")
            validation_failed_count += 1
            enriched_shops.append(shop)  # LLMデータを保持
            continue

        place_id = place_data.get('place_id')
        place_name = place_data.get('name')
        
        logger.info(f"[Enrich] â†’ æ¤œç´¢çµæžœ: '{place_name}'")
        logger.info(f"[Enrich] â†’ place_id: {place_id}")

        # âœ… place_idé‡è¤‡ãƒã‚§ãƒƒã‚¯
        if place_id in seen_place_ids:
            duplicate_count += 1
            logger.warning(f"[Enrich] â†’ âŒ é‡è¤‡æ¤œå‡º!æ—¢ã«è¿½åŠ æ¸ˆã¿(ã‚¹ã‚­ãƒƒãƒ—)")
            logger.warning(f"[Enrich]    LLMåº—èˆ—å: '{shop_name}' â†’ Googleåº—èˆ—å: '{place_name}'")
            continue
        
        # âœ… place_idã‚’è¨˜éŒ²
        seen_place_ids.add(place_id)
        logger.info(f"[Enrich] â†’ âœ… è¿½åŠ æ±ºå®š")

        # å›½ã‚³ãƒ¼ãƒ‰ã®å–å¾—
        shop_country = place_data.get('country_code', '')
        
        # -------------------------------------------------------
        # 2. ãƒ­ã‚¸ãƒƒã‚¯åˆ¤å®š(ãƒ•ãƒ©ã‚°è¨­å®š)
        # -------------------------------------------------------
        # ãƒ‡ãƒ•ã‚©ãƒ«ãƒˆè¨­å®š (åŸºæœ¬ã¯TripAdvisorã‚’è¡¨ç¤º)
        show_tripadvisor = True
        show_domestic_sites = False

        # ã€ä¾‹å¤–ãƒ«ãƒ¼ãƒ«ã€‘è¨€èªžãŒæ—¥æœ¬èªž(ja) ã‹ã¤ æ—¥æœ¬å›½å†…(JP) ã®å ´åˆ
        if language == 'ja' and shop_country == 'JP':
            show_tripadvisor = False      # ãƒˆãƒªãƒƒãƒ—ã‚¢ãƒ‰ãƒã‚¤ã‚¶ãƒ¼ã¯å‡ºã•ãªã„
            show_domestic_sites = True    # å›½å†…3ã‚µã‚¤ãƒˆã‚’å‡ºã™
        
        # å°†æ¥çš„ãªæ‹¡å¼µ(ä¾‹:å°æ¹¾ãƒ»éŸ“å›½ã§ã‚‚é£Ÿã¹ãƒ­ã‚°ã‚’å‡ºã™å ´åˆ)
        # if language == 'ja' and shop_country in ['TW', 'KR']:
        #     show_domestic_sites = True
        
        logger.info(f"[Enrich] åˆ¤å®šçµæžœ: {shop_name} (Country: {shop_country}, Lang: {language}) "
                   f"-> TripAdvisor: {show_tripadvisor}, Domestic: {show_domestic_sites}")

        # -------------------------------------------------------
        # 3. ãƒ‡ãƒ¼ã‚¿ã®æ³¨å…¥
        # -------------------------------------------------------
        # Google Placesã®å…±é€šãƒ‡ãƒ¼ã‚¿
        if place_data.get('name'): 
            shop['name'] = place_data['name']
        if place_data.get('photo_url'): 
            shop['image'] = place_data['photo_url']
        if place_data.get('rating'): 
            shop['rating'] = place_data['rating']
        if place_data.get('user_ratings_total'): 
            shop['reviewCount'] = place_data['user_ratings_total']
        if place_data.get('formatted_address'): 
            shop['location'] = place_data['formatted_address']
        if place_data.get('maps_url'): 
            shop['maps_url'] = place_data['maps_url']
        if place_data.get('phone'): 
            shop['phone'] = place_data['phone']
        if place_data.get('place_id'): 
            shop['place_id'] = place_data['place_id']

        # A. å›½å†…3ã‚µã‚¤ãƒˆã®ãƒªãƒ³ã‚¯ç”Ÿæˆ (ä¾‹å¤–ãƒ«ãƒ¼ãƒ«é©ç”¨æ™‚)
        if show_domestic_sites:
            try:
                # TripAdvisorãƒ•ã‚£ãƒ¼ãƒ«ãƒ‰ã‚’æ˜Žç¤ºçš„ã«å‰Šé™¤
                shop.pop('tripadvisor_url', None)
                shop.pop('tripadvisor_rating', None)
                shop.pop('tripadvisor_reviews', None)

                # ãƒ›ãƒƒãƒˆãƒšãƒƒãƒ‘ãƒ¼
                hotpepper_url = None
                try:
                    hotpepper_url = search_hotpepper(shop_name, area, geo_info)
                    if not hotpepper_url:
                        # åå‰ã‚’å¤‰ãˆã¦å†ãƒˆãƒ©ã‚¤
                        places_name = place_data.get('name', '')
                        if places_name and places_name != shop_name:
                            hotpepper_url = search_hotpepper(places_name, area, geo_info)
                except Exception:
                    pass

                shop['hotpepper_url'] = hotpepper_url if hotpepper_url else f"https://www.google.com/search?q={shop_name}+{area}+ãƒ›ãƒƒãƒˆãƒšãƒƒãƒ‘ãƒ¼ã‚°ãƒ«ãƒ¡"

                # é£Ÿã¹ãƒ­ã‚°
                try:
                    places_name = place_data.get('name', '')
                    region_name = geo_info.get('region', '') if geo_info else 'æ±äº¬'
                    # éƒ½é“åºœçœŒã‚³ãƒ¼ãƒ‰å¤‰æ›(ç°¡æ˜“ç‰ˆ)
                    pref_code_map = {'æ±äº¬': 'tokyo', 'ç¥žå¥ˆå·': 'kanagawa', 'å¤§é˜ª': 'osaka', 'äº¬éƒ½': 'kyoto', 'å…µåº«': 'hyogo', 'åŒ—æµ·é“': 'hokkaido', 'æ„›çŸ¥': 'aichi', 'ç¦å²¡': 'fukuoka'}
                    pref = region_name.rstrip('éƒ½é“åºœçœŒ') if region_name else 'æ±äº¬'
                    pref_code = pref_code_map.get(pref, 'tokyo')

                    tabelog_search_query = requests.utils.quote(places_name if places_name else shop_name)
                    shop['tabelog_url'] = f"https://tabelog.com/{pref_code}/rstLst/?sw={tabelog_search_query}"
                except Exception:
                    shop['tabelog_url'] = f"https://tabelog.com/tokyo/rstLst/?sw={shop_name}"

                # ãã‚‹ãªã³
                shop['gnavi_url'] = f"https://www.google.com/search?q={shop_name}+{area}+ãã‚‹ãªã³"

            except Exception as e:
                logger.error(f"[Enrich] Domestic Sites Error: {e}")

        # B. ãƒˆãƒªãƒƒãƒ—ã‚¢ãƒ‰ãƒã‚¤ã‚¶ãƒ¼ã®ãƒªãƒ³ã‚¯ç”Ÿæˆ (ãƒ‡ãƒ•ã‚©ãƒ«ãƒˆé©ç”¨æ™‚)
        if show_tripadvisor:
            try:
                lat = place_data.get('lat')
                lng = place_data.get('lng')
                
                if TRIPADVISOR_API_KEY:
                    # è¨€èªžãƒžãƒƒãƒ”ãƒ³ã‚°
                    tripadvisor_lang_map = {'ja': 'ja', 'en': 'en', 'zh': 'zh', 'ko': 'ko'}
                    search_lang = tripadvisor_lang_map.get(language, 'en')
                    
                    # æ¤œç´¢å®Ÿè¡Œ
                    tripadvisor_data = get_tripadvisor_data(shop_name, lat, lng, search_lang)

                    # 0ä»¶ã‹ã¤æ—¥æœ¬èªžã®å ´åˆã€è‹±èªžã§å†ãƒˆãƒ©ã‚¤(ãƒ’ãƒƒãƒˆçŽ‡å‘ä¸Šç­–)
                    if not tripadvisor_data and search_lang == 'ja':
                        logger.info(f"[TripAdvisor] æ—¥æœ¬èªžã§ãƒ’ãƒƒãƒˆã›ãšã€‚è‹±èªžã§å†æ¤œç´¢: {shop_name}")
                        tripadvisor_data = get_tripadvisor_data(shop_name, lat, lng, 'en')

                    if tripadvisor_data:
                        shop['tripadvisor_url'] = tripadvisor_data.get('web_url')
                        shop['tripadvisor_rating'] = tripadvisor_data.get('rating')
                        shop['tripadvisor_reviews'] = tripadvisor_data.get('num_reviews')
                        logger.info(f"[TripAdvisor] ãƒªãƒ³ã‚¯ç”ŸæˆæˆåŠŸ: {shop_name}")
            except Exception as e:
                logger.error(f"[Enrich] TripAdvisor Error: {e}")

        enriched_shops.append(shop)

    logger.info(f"[Enrich] ========== å®Œäº† ==========")
    logger.info(f"[Enrich] å‡ºåŠ›: {len(enriched_shops)}ä»¶")
    logger.info(f"[Enrich] é‡è¤‡é™¤å¤–: {duplicate_count}ä»¶")
    logger.info(f"[Enrich] æ¤œè¨¼å¤±æ•—: {validation_failed_count}ä»¶")
    logger.info(f"[Enrich] åˆè¨ˆå…¥åŠ›: {len(shops)}ä»¶")

    return enriched_shops


def extract_area_from_text(text: str, language: str = 'ja') -> str:
    """
    ãƒ†ã‚­ã‚¹ãƒˆã‹ã‚‰ã‚¨ãƒªã‚¢åã‚’æŠ½å‡º(Geocoding APIã§å‹•çš„ã«æ¤œè¨¼)
    """
    jp_chars = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9Fa-zA-Z]'
    patterns = [
        rf'({jp_chars}{{2,10}})ã®{jp_chars}',
        rf'({jp_chars}{{2,10}})ã§{jp_chars}',
        rf'({jp_chars}{{2,10}})ã«ã‚ã‚‹',
        rf'({jp_chars}{{2,10}})å‘¨è¾º',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            geo_info = get_region_from_area(candidate, language)
            if geo_info and geo_info.get('region'):
                logger.info(f"[Extract Area] ã‚¨ãƒªã‚¢æŠ½å‡ºæˆåŠŸ: '{candidate}' from '{text}'")
                return candidate

    logger.info(f"[Extract Area] ã‚¨ãƒªã‚¢æŠ½å‡ºå¤±æ•—: '{text}'")
    return ''


def extract_shops_from_response(text: str) -> list:
    """
    LLMã®å¿œç­”ãƒ†ã‚­ã‚¹ãƒˆã‹ã‚‰ã‚·ãƒ§ãƒƒãƒ—æƒ…å ±ã‚’æŠ½å‡º
    """
    shops = []
    pattern = r'(\d+)\.\s*\*\*([^*]+)\*\*\s*(?:\([^)]+\))?\s*[-:]:]\s*([^\n]+)'
    matches = re.findall(pattern, text)

    for match in matches:
        full_name = match[1].strip()
        description = match[2].strip()

        name = full_name
        name_match = re.match(r'^([^(]+)[(]([^)]+)[)]', full_name)
        if name_match:
            name = name_match.group(1).strip()

        shops.append({
            'name': name,
            'description': description,
            'category': 'ãƒ¬ã‚¹ãƒˆãƒ©ãƒ³'
        })

    logger.info(f"[Extract] {len(shops)}ä»¶ã®ã‚·ãƒ§ãƒƒãƒ—ã‚’æŠ½å‡º")
    return shops


# ========================================
# ã‚»ãƒƒã‚·ãƒ§ãƒ³ç®¡ç†
# ========================================

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

    def __init__(self, session: SupportSession):
        self.session = session
        self.language = session.get_language()
        self.mode = session.get_mode()  # â˜… ãƒ¢ãƒ¼ãƒ‰ã‚’å–å¾—
        
        # â˜…â˜…â˜… ãƒ¢ãƒ¼ãƒ‰ã«å¿œã˜ãŸãƒ—ãƒ­ãƒ³ãƒ—ãƒˆã‚’é¸æŠž â˜…â˜…â˜…
        mode_prompts = SYSTEM_PROMPTS.get(self.mode, SYSTEM_PROMPTS.get('chat', {}))
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

@app.route('/')
def index():
    """ãƒ•ãƒ­ãƒ³ãƒˆã‚¨ãƒ³ãƒ‰è¡¨ç¤º"""
    return render_template('support.html')


@app.route('/api/session/start', methods=['POST', 'OPTIONS'])
def start_session():
    """
    ã‚»ãƒƒã‚·ãƒ§ãƒ³é–‹å§‹ - ãƒ¢ãƒ¼ãƒ‰å¯¾å¿œ
    
    ã€é‡è¦ã€‘æ”¹å–„ã•ã‚ŒãŸãƒ•ãƒ­ãƒ¼:
    1. ã‚»ãƒƒã‚·ãƒ§ãƒ³åˆæœŸåŒ–ï¼ˆãƒ¢ãƒ¼ãƒ‰ãƒ»è¨€èªžè¨­å®šï¼‰
    2. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆä½œæˆï¼ˆæœ€æ–°ã®çŠ¶æ…‹ã§ï¼‰
    3. åˆå›žãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ç”Ÿæˆ
    4. å±¥æ­´ã«è¿½åŠ 
    """
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json or {}
        user_info = data.get('user_info', {})
        language = data.get('language', 'ja')
        mode = data.get('mode', 'chat')

        # 1. ã‚»ãƒƒã‚·ãƒ§ãƒ³åˆæœŸåŒ–
        session = SupportSession()
        session.initialize(user_info, language=language, mode=mode)
        logger.info(f"[Start Session] æ–°è¦ã‚»ãƒƒã‚·ãƒ§ãƒ³ä½œæˆ: {session.session_id}")

        # 2. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆä½œæˆï¼ˆæœ€æ–°ã®çŠ¶æ…‹ã§ï¼‰
        assistant = SupportAssistant(session)
        
        # 3. åˆå›žãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ç”Ÿæˆ
        initial_message = assistant.get_initial_message()

        # 4. å±¥æ­´ã«è¿½åŠ ï¼ˆroleã¯'model'ï¼‰
        session.add_message('model', initial_message, 'chat')

        logger.info(f"[API] ã‚»ãƒƒã‚·ãƒ§ãƒ³é–‹å§‹: {session.session_id}, è¨€èªž: {language}, ãƒ¢ãƒ¼ãƒ‰: {mode}")

        return jsonify({
            'session_id': session.session_id,
            'initial_message': initial_message
        })

    except Exception as e:
        logger.error(f"[API] ã‚»ãƒƒã‚·ãƒ§ãƒ³é–‹å§‹ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    """
    ãƒãƒ£ãƒƒãƒˆå‡¦ç† - æ”¹å–„ç‰ˆ
    
    ã€é‡è¦ã€‘æ”¹å–„ã•ã‚ŒãŸãƒ•ãƒ­ãƒ¼ï¼ˆé †åºã‚’åŽ³å®ˆï¼‰:
    1. çŠ¶æ…‹ç¢ºå®š (State First): ãƒ¢ãƒ¼ãƒ‰ãƒ»è¨€èªžã‚’æ›´æ–°
    2. ãƒ¦ãƒ¼ã‚¶ãƒ¼å…¥åŠ›ã‚’è¨˜éŒ²: ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ã‚’å±¥æ­´ã«è¿½åŠ 
    3. çŸ¥èƒ½ç”Ÿæˆ (Assistantä½œæˆ): æœ€æ–°ã®çŠ¶æ…‹ã§ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆã‚’ä½œæˆ
    4. æŽ¨è«–é–‹å§‹: Gemini APIã‚’å‘¼ã³å‡ºã—
    5. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆå¿œç­”ã‚’è¨˜éŒ²: å±¥æ­´ã«è¿½åŠ 
    """
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')
        user_message = data.get('message')
        stage = data.get('stage', 'conversation')
        language = data.get('language', 'ja')
        mode = data.get('mode', 'chat')

        if not session_id or not user_message:
            return jsonify({'error': 'session_idã¨messageãŒå¿…è¦ã§ã™'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'ã‚»ãƒƒã‚·ãƒ§ãƒ³ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“'}), 404

        logger.info(f"[Chat] ã‚»ãƒƒã‚·ãƒ§ãƒ³: {session_id}, ãƒ¢ãƒ¼ãƒ‰: {mode}, è¨€èªž: {language}")

        # 1. çŠ¶æ…‹ç¢ºå®š (State First)
        session.update_language(language)
        session.update_mode(mode)

        # 2. ãƒ¦ãƒ¼ã‚¶ãƒ¼å…¥åŠ›ã‚’è¨˜éŒ²
        session.add_message('user', user_message, 'chat')

        # 3. çŸ¥èƒ½ç”Ÿæˆ (Assistantä½œæˆ)
        assistant = SupportAssistant(session)
        
        # 4. æŽ¨è«–é–‹å§‹
        result = assistant.process_user_message(user_message, stage)
        
        # 5. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆå¿œç­”ã‚’è¨˜éŒ²
        session.add_message('model', result['response'], 'chat')

        if result['summary']:
            session.add_message('model', result['summary'], 'summary')

        # ã‚·ãƒ§ãƒƒãƒ—ãƒ‡ãƒ¼ã‚¿å‡¦ç†
        shops = result.get('shops', [])
        response_text = result['response']
        is_followup = result.get('is_followup', False)

        # å¤šè¨€èªžãƒ¡ãƒƒã‚»ãƒ¼ã‚¸è¾žæ›¸
        shop_messages = {
            'ja': {
                'intro': lambda count: f"ã”å¸Œæœ›ã«åˆã†ãŠåº—ã‚’{count}ä»¶ã”ç´¹ä»‹ã—ã¾ã™ã€‚\n\n",
                'not_found': "ç”³ã—è¨³ã”ã–ã„ã¾ã›ã‚“ã€‚æ¡ä»¶ã«åˆã†ãŠåº—ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“ã§ã—ãŸã€‚åˆ¥ã®æ¡ä»¶ã§ãŠæŽ¢ã—ã„ãŸã ã‘ã¾ã™ã‹?"
            },
            'en': {
                'intro': lambda count: f"Here are {count} restaurant recommendations for you.\n\n",
                'not_found': "Sorry, we couldn't find any restaurants matching your criteria. Would you like to search with different conditions?"
            },
            'zh': {
                'intro': lambda count: f"ä¸ºæ‚¨æŽ¨è{count}å®¶é¤åŽ…ã€‚\n\n",
                'not_found': "å¾ˆæŠ±æ­‰,æ²¡æœ‰æ‰¾åˆ°ç¬¦åˆæ¡ä»¶çš„é¤åŽ…ã€‚è¦ç”¨å…¶ä»–æ¡ä»¶æœç´¢å—?"
            },
            'ko': {
                'intro': lambda count: f"ê³ ê°ë‹˜ê»˜ {count}ê°œì˜ ì‹ë‹¹ì„ ì¶”ì²œí•©ë‹ˆë‹¤.\n\n",
                'not_found': "ì£„ì†¡í•©ë‹ˆë‹¤. ì¡°ê±´ì— ë§žëŠ” ì‹ë‹¹ì„ ì°¾ì„ ìˆ˜ ì—†ì—ˆìŠµë‹ˆë‹¤. ë‹¤ë¥¸ ì¡°ê±´ìœ¼ë¡œ ì°¾ìœ¼ì‹œê² ìŠµë‹ˆê¹Œ?"
            }
        }

        current_messages = shop_messages.get(language, shop_messages['ja'])

        if shops and not is_followup:
            original_count = len(shops)
            area = extract_area_from_text(user_message, language)
            logger.info(f"[Chat] æŠ½å‡ºã‚¨ãƒªã‚¢: '{area}' from '{user_message}'")

            # Places APIã§å†™çœŸã‚’å–å¾—
            shops = enrich_shops_with_photos(shops, area, language)

            if shops:
                shop_list = []
                for i, shop in enumerate(shops, 1):
                    name = shop.get('name', '')
                    shop_area = shop.get('area', '')
                    description = shop.get('description', '')
                    if shop_area:
                        shop_list.append(f"{i}. **{name}**({shop_area}): {description}")
                    else:
                        shop_list.append(f"{i}. **{name}**: {description}")

                response_text = current_messages['intro'](len(shops)) + "\n\n".join(shop_list)
                logger.info(f"[Chat] {len(shops)}ä»¶ã®ã‚·ãƒ§ãƒƒãƒ—ãƒ‡ãƒ¼ã‚¿ã‚’è¿”å´(å…ƒ: {original_count}ä»¶, è¨€èªž: {language})")
            else:
                response_text = current_messages['not_found']
                logger.warning(f"[Chat] å…¨åº—èˆ—ãŒé™¤å¤–ã•ã‚Œã¾ã—ãŸ(å…ƒ: {original_count}ä»¶)")

        elif is_followup:
            logger.info(f"[Chat] æ·±æŽ˜ã‚Šè³ªå•ã¸ã®å›žç­”: {response_text[:100]}...")

        # 【デバッグ】最終的なshopsの内容を確認
        logger.info(f"[Chat] 最終shops配列: {len(shops)}件")
        if shops:
            logger.info(f"[Chat] shops[0] keys: {list(shops[0].keys())}")
        return jsonify({
            'response': response_text,
            'summary': result['summary'],
            'shops': shops,
            'should_confirm': result['should_confirm'],
            'is_followup': is_followup
        })

    except Exception as e:
        logger.error(f"[API] ãƒãƒ£ãƒƒãƒˆã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/finalize', methods=['POST', 'OPTIONS'])
def finalize_session():
    """ã‚»ãƒƒã‚·ãƒ§ãƒ³å®Œäº†"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'error': 'session_idãŒå¿…è¦ã§ã™'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'ã‚»ãƒƒã‚·ãƒ§ãƒ³ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“'}), 404

        assistant = SupportAssistant(session)
        final_summary = assistant.generate_final_summary()

        return jsonify({
            'summary': final_summary,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"[API] å®Œäº†å‡¦ç†ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel', methods=['POST', 'OPTIONS'])
def cancel_processing():
    """å‡¦ç†ä¸­æ­¢"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'session_idãŒå¿…è¦ã§ã™'}), 400
        
        logger.info(f"[API] å‡¦ç†ä¸­æ­¢ãƒªã‚¯ã‚¨ã‚¹ãƒˆ: {session_id}")
        
        # ã‚»ãƒƒã‚·ãƒ§ãƒ³ã®ã‚¹ãƒ†ãƒ¼ã‚¿ã‚¹ã‚’æ›´æ–°
        session = SupportSession(session_id)
        session_data = session.get_data()
        
        if session_data:
            session.update_status('cancelled')
        
        return jsonify({
            'success': True,
            'message': 'å‡¦ç†ã‚’ä¸­æ­¢ã—ã¾ã—ãŸ'
        })
        
    except Exception as e:
        logger.error(f"[API] ä¸­æ­¢å‡¦ç†ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tts/synthesize', methods=['POST', 'OPTIONS'])
def synthesize_speech():
    """éŸ³å£°åˆæˆ"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        text = data.get('text', '')
        language_code = data.get('language_code', 'ja-JP')
        voice_name = data.get('voice_name', 'ja-JP-Chirp3-HD-Leda')
        speaking_rate = data.get('speaking_rate', 1.0)
        pitch = data.get('pitch', 0.0)

        if not text:
            return jsonify({'success': False, 'error': 'ãƒ†ã‚­ã‚¹ãƒˆãŒå¿…è¦ã§ã™'}), 400

        MAX_CHARS = 1000
        if len(text) > MAX_CHARS:
            logger.warning(f"[TTS] ãƒ†ã‚­ã‚¹ãƒˆãŒé•·ã™ãŽã‚‹ãŸã‚åˆ‡ã‚Šè©°ã‚ã¾ã™: {len(text)} â†’ {MAX_CHARS} æ–‡å­—")
            text = text[:MAX_CHARS] + '...'

        logger.info(f"[TTS] åˆæˆé–‹å§‹: {len(text)} æ–‡å­—")

        synthesis_input = texttospeech.SynthesisInput(text=text)

        try:
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )
        except Exception as voice_error:
            logger.warning(f"[TTS] æŒ‡å®šéŸ³å£°ãŒç„¡åŠ¹ã€ãƒ‡ãƒ•ã‚©ãƒ«ãƒˆã«å¤‰æ›´: {voice_error}")
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name='ja-JP-Neural2-B'
            )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
            pitch=pitch
        )

        response = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        audio_base64 = base64.b64encode(response.audio_content).decode('utf-8')

        logger.info(f"[TTS] åˆæˆæˆåŠŸ: {len(audio_base64)} bytes (base64)")

        return jsonify({
            'success': True,
            'audio': audio_base64
        })

    except Exception as e:
        logger.error(f"[TTS] ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/transcribe', methods=['POST', 'OPTIONS'])
def transcribe_audio():
    """éŸ³å£°èªè­˜"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': 'éŸ³å£°ãƒ‡ãƒ¼ã‚¿ãŒå¿…è¦ã§ã™'}), 400

        logger.info(f"[STT] èªè­˜é–‹å§‹: {len(audio_base64)} bytes (base64)")

        audio_content = base64.b64decode(audio_base64)
        audio = speech.RecognitionAudio(content=audio_content)

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code=language_code,
            enable_automatic_punctuation=True,
            model='default'
        )

        response = stt_client.recognize(config=config, audio=audio)

        transcript = ''
        if response.results:
            transcript = response.results[0].alternatives[0].transcript
            confidence = response.results[0].alternatives[0].confidence
            logger.info(f"[STT] èªè­˜æˆåŠŸ: '{transcript}' (ä¿¡é ¼åº¦: {confidence:.2f})")
        else:
            logger.warning("[STT] éŸ³å£°ãŒèªè­˜ã•ã‚Œã¾ã›ã‚“ã§ã—ãŸ")

        return jsonify({
            'success': True,
            'transcript': transcript
        })

    except Exception as e:
        logger.error(f"[STT] ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/stream', methods=['POST', 'OPTIONS'])
def transcribe_audio_streaming():
    """éŸ³å£°èªè­˜ (Streaming)"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': 'éŸ³å£°ãƒ‡ãƒ¼ã‚¿ãŒå¿…è¦ã§ã™'}), 400

        logger.info(f"[STT Streaming] èªè­˜é–‹å§‹: {len(audio_base64)} bytes (base64)")

        audio_content = base64.b64decode(audio_base64)

        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code=language_code,
            enable_automatic_punctuation=True,
            model='default'
        )

        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=False,
            single_utterance=True
        )

        CHUNK_SIZE = 1024 * 16

        def audio_generator():
            for i in range(0, len(audio_content), CHUNK_SIZE):
                chunk = audio_content[i:i + CHUNK_SIZE]
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        responses = stt_client.streaming_recognize(streaming_config, audio_generator())

        transcript = ''
        confidence = 0.0

        for response in responses:
            if not response.results:
                continue

            for result in response.results:
                if result.is_final and result.alternatives:
                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence
                    logger.info(f"[STT Streaming] èªè­˜æˆåŠŸ: '{transcript}' (ä¿¡é ¼åº¦: {confidence:.2f})")
                    break

            if transcript:
                break

        if not transcript:
            logger.warning("[STT Streaming] éŸ³å£°ãŒèªè­˜ã•ã‚Œã¾ã›ã‚“ã§ã—ãŸ")

        return jsonify({
            'success': True,
            'transcript': transcript,
            'confidence': confidence
        })

    except Exception as e:
        logger.error(f"[STT Streaming] ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/session/<session_id>', methods=['GET', 'OPTIONS'])
def get_session(session_id):
    """ã‚»ãƒƒã‚·ãƒ§ãƒ³æƒ…å ±å–å¾—"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        session = SupportSession(session_id)
        data = session.get_data()

        if not data:
            return jsonify({'error': 'ã‚»ãƒƒã‚·ãƒ§ãƒ³ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“'}), 404

        return jsonify(data)

    except Exception as e:
        logger.error(f"[API] ã‚»ãƒƒã‚·ãƒ§ãƒ³å–å¾—ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET', 'OPTIONS'])
def health_check():
    """ãƒ˜ãƒ«ã‚¹ãƒã‚§ãƒƒã‚¯"""
    if request.method == 'OPTIONS':
        return '', 204

    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'gemini': 'ok',
            'ram_session': 'ok',
            'tts': 'ok',
            'stt': 'ok',
            'places_api': 'ok' if GOOGLE_PLACES_API_KEY else 'not configured'
        }
    })


# ========================================
# WebSocket Streaming STT
# ========================================

active_streams = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f"[WebSocket STT] ã‚¯ãƒ©ã‚¤ã‚¢ãƒ³ãƒˆæŽ¥ç¶š: {request.sid}")
    emit('connected', {'status': 'ready'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"[WebSocket STT] ã‚¯ãƒ©ã‚¤ã‚¢ãƒ³ãƒˆåˆ‡æ–­: {request.sid}")
    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        if 'stop_event' in stream_data:
            stream_data['stop_event'].set()
        del active_streams[request.sid]

@socketio.on('start_stream')
def handle_start_stream(data):
    language_code = data.get('language_code', 'ja-JP')
    sample_rate = data.get('sample_rate', 16000)  # ãƒ•ãƒ­ãƒ³ãƒˆã‚¨ãƒ³ãƒ‰ã‹ã‚‰å—ã‘å–ã‚‹
    client_sid = request.sid
    logger.info(f"[WebSocket STT] ã‚¹ãƒˆãƒªãƒ¼ãƒ é–‹å§‹: {client_sid}, è¨€èªž: {language_code}, ã‚µãƒ³ãƒ—ãƒ«ãƒ¬ãƒ¼ãƒˆ: {sample_rate}Hz")

    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,  # å‹•çš„ã«è¨­å®š
        language_code=language_code,
        enable_automatic_punctuation=True,
        model='latest_long'  # ã‚ˆã‚Šé«˜ç²¾åº¦ãªãƒ¢ãƒ‡ãƒ«ã«å¤‰æ›´
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,
        single_utterance=False
    )

    audio_queue = queue.Queue()
    stop_event = threading.Event()

    active_streams[client_sid] = {
        'audio_queue': audio_queue,
        'stop_event': stop_event,
        'streaming_config': streaming_config
    }

    def audio_generator():
        while not stop_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.5)
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)
            except queue.Empty:
                continue

    def recognition_thread():
        try:
            logger.info(f"[WebSocket STT] èªè­˜ã‚¹ãƒ¬ãƒƒãƒ‰é–‹å§‹: {client_sid}")
            responses = stt_client.streaming_recognize(streaming_config, audio_generator())

            for response in responses:
                if stop_event.is_set():
                    break

                if not response.results:
                    continue

                result = response.results[0]

                if result.alternatives:
                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence if result.is_final else 0.0

                    socketio.emit('transcript', {
                        'text': transcript,
                        'is_final': result.is_final,
                        'confidence': confidence
                    }, room=client_sid)

                    if result.is_final:
                        logger.info(f"[WebSocket STT] æœ€çµ‚èªè­˜: '{transcript}' (ä¿¡é ¼åº¦: {confidence:.2f})")
                    else:
                        logger.debug(f"[WebSocket STT] é€”ä¸­èªè­˜: '{transcript}'")

        except Exception as e:
            logger.error(f"[WebSocket STT] èªè­˜ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
            socketio.emit('error', {'message': str(e)}, room=client_sid)

    thread = threading.Thread(target=recognition_thread, daemon=True)
    thread.start()

    emit('stream_started', {'status': 'streaming'})

@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    if request.sid not in active_streams:
        logger.warning(f"[WebSocket STT] æœªåˆæœŸåŒ–ã®ã‚¹ãƒˆãƒªãƒ¼ãƒ : {request.sid}")
        return

    try:
        chunk_base64 = data.get('chunk', '')
        if not chunk_base64:
            return

        # â˜…â˜…â˜… sample_rateã‚’å–å¾—(16kHzã§å—ä¿¡) â˜…â˜…â˜…
        sample_rate = data.get('sample_rate', 16000)
        
        # â˜…â˜…â˜… çµ±è¨ˆæƒ…å ±ã‚’å–å¾—ã—ã¦ãƒ­ã‚°å‡ºåŠ›(å¿…ãšå‡ºåŠ›) â˜…â˜…â˜…
        stats = data.get('stats')
        logger.info(f"[audio_chunkå—ä¿¡] sample_rate: {sample_rate}Hz, stats: {stats}")
        
        if stats:
            logger.info(f"[AudioWorkletçµ±è¨ˆ] ã‚µãƒ³ãƒ—ãƒ«ãƒ¬ãƒ¼ãƒˆ: {sample_rate}Hz, "
                       f"ã‚µãƒ³ãƒ—ãƒ«ç·æ•°: {stats.get('totalSamples')}, "
                       f"é€ä¿¡ãƒãƒ£ãƒ³ã‚¯æ•°: {stats.get('chunksSent')}, "
                       f"ç©ºå…¥åŠ›å›žæ•°: {stats.get('emptyInputCount')}, "
                       f"processå‘¼ã³å‡ºã—å›žæ•°: {stats.get('processCalls')}, "
                       f"ã‚ªãƒ¼ãƒãƒ¼ãƒ•ãƒ­ãƒ¼å›žæ•°: {stats.get('overflowCount', 0)}")  # â˜… ã‚ªãƒ¼ãƒãƒ¼ãƒ•ãƒ­ãƒ¼è¿½åŠ 

        audio_chunk = base64.b64decode(chunk_base64)
        
        # â˜…â˜…â˜… 16kHzãã®ã¾ã¾Google STTã«é€ã‚‹ â˜…â˜…â˜…
        stream_data = active_streams[request.sid]
        stream_data['audio_queue'].put(audio_chunk)

    except Exception as e:
        logger.error(f"[WebSocket STT] ãƒãƒ£ãƒ³ã‚¯å‡¦ç†ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)

@socketio.on('stop_stream')
def handle_stop_stream():
    logger.info(f"[WebSocket STT] ã‚¹ãƒˆãƒªãƒ¼ãƒ åœæ­¢: {request.sid}")

    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        stream_data['audio_queue'].put(None)
        stream_data['stop_event'].set()
        del active_streams[request.sid]

    emit('stream_stopped', {'status': 'stopped'})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)