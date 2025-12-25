# -*- coding: utf-8 -*-
"""
Configuration module for Gourmet Customer Support System
Contains all environment variables, API keys, constants, and lookup tables
"""
import os
import logging
from google.cloud import texttospeech
from google.cloud import speech
from google import genai
import google.generativeai as genai_legacy

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ========================================
# CORS設定
# ========================================

# 許可するオリジン(末尾のスラッシュなし)
ALLOWED_ORIGINS = [
    "https://gourmet-sp-two.vercel.app",
    "https://gourmet-sp.vercel.app",
    "http://localhost:4321"
]

# ========================================
# Google Cloud Clients
# ========================================

# Gemini クライアント初期化(1回だけ)
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 旧SDK(互換性のため一時的に残す)※後で削除予定
genai_legacy.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai_legacy.GenerativeModel('gemini-2.0-flash-exp')

# Google Cloud TTS/STT初期化
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

# ========================================
# API Keys & Environment Variables
# ========================================

# Google Places API
GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')

# Google Geocoding API(Places APIと同じキーを使用)
GOOGLE_GEOCODING_API_KEY = os.getenv('GOOGLE_GEOCODING_API_KEY', GOOGLE_PLACES_API_KEY)

# ホットペッパーAPI
HOTPEPPER_API_KEY = os.getenv('HOTPEPPER_API_KEY', 'c22031a566715e40')

# TripAdvisor Content API
TRIPADVISOR_API_KEY = os.getenv('TRIPADVISOR_API_KEY', '')
MY_DOMAIN_URL = "https://unfix.co.jp"

# ブラウザのふりをするためのUser-Agent設定 (Bot判定回避用)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Google Custom Search API(食べログ検索用)
GOOGLE_CSE_API_KEY = os.getenv('GOOGLE_CSE_API_KEY', '')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID', '')

# Cloud Storage Bucket
PROMPTS_BUCKET_NAME = os.getenv('PROMPTS_BUCKET_NAME', '')

# ========================================
# Constants
# ========================================

MAX_TOKENS = 4000

# ========================================
# Lookup Tables
# ========================================

# 食べログの都道府県コード
TABELOG_PREF_CODES = {
    '東京': 'tokyo',
    '神奈川': 'kanagawa',
    '埼玉': 'saitama',
    '千葉': 'chiba',
    '大阪': 'osaka',
    '京都': 'kyoto',
    '兵庫': 'hyogo',
    '愛知': 'aichi',
    '福岡': 'fukuoka',
    '北海道': 'hokkaido',
}

# ホットペッパーのエリアコード
HOTPEPPER_AREA_CODES = {
    '東京': 'Z011',
    '神奈川': 'Z012',
    '埼玉': 'Z013',
    '千葉': 'Z014',
    '大阪': 'Z023',
    '京都': 'Z026',
    '兵庫': 'Z024',
    '愛知': 'Z033',
    '福岡': 'Z091',
    '北海道': 'Z011',
}

# ========================================
# Multi-language Templates
# ========================================

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
    'ko': '다음 전체 대화를 요약하고 문의 내용을 정리하세요.\n\n{conversation_text}\n\n작성 시간: {timestamp}\n\n요약:'
}
