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

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ========================================
# CORS & SocketIO 設定 (Claudeアドバイス適用版)
# ========================================

# 許可するオリジン(末尾のスラッシュなし)
allowed_origins = [
    "https://gourmet-sp-two.vercel.app",
    "https://gourmet-sp.vercel.app",
    "http://localhost:4321"
]

# SocketIO初期化 (cors_allowed_originsを明示的に指定)
socketio = SocketIO(
    app,
    cors_allowed_origins=allowed_origins,
    async_mode='threading',
    logger=False,
    engineio_logger=False
)

# Flask-CORS初期化 (supports_credentials=True)
CORS(app, resources={
    r"/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# 【重要】全レスポンスに強制的にCORSヘッダーを注入するフック
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# Gemini クライアント初期化（1回だけ）
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# 旧SDK（互換性のため一時的に残す）※後で削除予定
import google.generativeai as genai_legacy
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

def load_prompts_from_local():
    """
    ローカルファイルから2種類のプロンプトを読み込み (フォールバック)
    """
    prompts = {
        'chat': {},
        'concierge': {}
    }
    
    for lang in ['ja', 'en', 'zh', 'ko']:
        # チャットモード用
        chat_file = f'prompts/support_system_{lang}.txt'
        try:
            with open(chat_file, 'r', encoding='utf-8') as f:
                prompts['chat'][lang] = f.read()
                logger.info(f"[Prompt] ローカルから読み込み成功: support_system_{lang}.txt")
        except FileNotFoundError:
            logger.warning(f"[Prompt] ローカルファイルが見つかりません: {chat_file}")
        except Exception as e:
            logger.error(f"[Prompt] ローカル読み込みエラー (chat/{lang}): {e}")

        # コンシェルジュモード用
        concierge_file = f'prompts/concierge_{lang}.txt'
        try:
            with open(concierge_file, 'r', encoding='utf-8') as f:
                content = f.read()
                try:
                    json_data = json.loads(content)
                    prompts['concierge'][lang] = json_data.get('concierge_system', content)
                except json.JSONDecodeError:
                    prompts['concierge'][lang] = content
                logger.info(f"[Prompt] ローカルから読み込み成功: concierge_{lang}.txt")
        except FileNotFoundError:
            logger.warning(f"[Prompt] ローカルファイルが見つかりません: {concierge_file}")
        except Exception as e:
            logger.error(f"[Prompt] ローカル読み込みエラー (concierge/{lang}): {e}")

    return prompts if (prompts['chat'] or prompts['concierge']) else None

# プロンプト読み込み実行
logger.info("[Prompt] プロンプト読み込み開始...")
SYSTEM_PROMPTS = load_prompts_from_gcs()
if not SYSTEM_PROMPTS:
    logger.info("[Prompt] GCSから読み込めませんでした。ローカルファイルを使用します。")
    SYSTEM_PROMPTS = load_prompts_from_local()

if not SYSTEM_PROMPTS or (not SYSTEM_PROMPTS.get('chat') and not SYSTEM_PROMPTS.get('concierge')):
    logger.error("[Prompt] プロンプトの読み込みに失敗しました!")
    SYSTEM_PROMPTS = {
        'chat': {'ja': 'エラー: チャットモードプロンプトが読み込めませんでした。'},
        'concierge': {'ja': 'エラー: コンシェルジュモードプロンプトが読み込めませんでした。'}
    }
else:
    logger.info(f"[Prompt] プロンプト読み込み完了:")
    logger.info(f"  - チャットモード: {list(SYSTEM_PROMPTS.get('chat', {}).keys())}")
    logger.info(f"  - コンシェルジュモード: {list(SYSTEM_PROMPTS.get('concierge', {}).keys())}")

# 多言語テンプレート (シンプルなものはここにハードコード)
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

# Google Cloud TTS/STT初期化
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

# Google Places API
GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')

# Google Geocoding API(Places APIと同じキーを使用)
GOOGLE_GEOCODING_API_KEY = os.getenv('GOOGLE_GEOCODING_API_KEY', GOOGLE_PLACES_API_KEY)

# ホットペッパーAPI
HOTPEPPER_API_KEY = os.getenv('HOTPEPPER_API_KEY', 'c22031a566715e40')

# TripAdvisor Content API
TRIPADVISOR_API_KEY = os.getenv('TRIPADVISOR_API_KEY', '')
MY_DOMAIN_URL = "https://unfix.co.jp"
# 【追加】ブラウザのふりをするためのUser-Agent設定 (Bot判定回避用)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Google Custom Search API(食べログ検索用)
GOOGLE_CSE_API_KEY = os.getenv('GOOGLE_CSE_API_KEY', '')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID', '')

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

# 定数
MAX_TOKENS = 4000

# ========================================
# ホットペッパーAPI 連携
# ========================================

def search_hotpepper(shop_name: str, area: str = '', geo_info: dict = None) -> str:
    """
    ホットペッパーAPIで店舗を検索して店舗ページURLを返す
    """
    if not HOTPEPPER_API_KEY:
        logger.warning("[Hotpepper API] APIキーが設定されていません")
        return None

    # Geocoding APIの結果から都道府県を取得
    large_area = 'Z011'  # デフォルト東京
    if geo_info:
        region = geo_info.get('region', '')
        # "東京都" → "東京" に変換してエリアコードを取得
        pref = region.rstrip('都道府県') if region else ''
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

        logger.info(f"[Hotpepper API] 検索: {shop_name} (エリア: {large_area})")

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        results = data.get('results', {})
        shops = results.get('shop', [])

        if shops:
            shop_url = shops[0].get('urls', {}).get('pc', '')
            logger.info(f"[Hotpepper API] 取得成功: {shop_name} -> {shop_url}")
            return shop_url
        else:
            logger.info(f"[Hotpepper API] 結果なし: {shop_name}")
            return None

    except Exception as e:
        logger.error(f"[Hotpepper API] エラー: {e}")
        return None

# ========================================
# TripAdvisor Content API 連携
# ========================================
def search_tripadvisor_location(shop_name: str, lat: float = None, lng: float = None, language: str = 'en') -> dict:
    """
    TripAdvisor Location Search APIで店舗のlocation_idを検索
    """
    if not TRIPADVISOR_API_KEY:
        logger.warning("[TripAdvisor API] APIキーが設定されていません")
        return None

    try:
        url = 'https://api.content.tripadvisor.com/api/v1/location/search'

        params = {
            'key': TRIPADVISOR_API_KEY,
            'searchQuery': shop_name,
            'language': language
        }

        # 座標がある場合は追加
        if lat is not None and lng is not None:
            params['latLong'] = f"{lat},{lng}"

        # 【修正】Referer (https付き) と User-Agent (ブラウザ偽装) を指定
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
    TripAdvisor Location Details APIで評価情報を取得
    """
    if not TRIPADVISOR_API_KEY or not location_id:
        return None

    try:
        url = f'https://api.content.tripadvisor.com/api/v1/location/{location_id}/details'

        params = {
            'key': TRIPADVISOR_API_KEY,
            'language': language
        }

        # 【修正】ここにも User-Agent を追加
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
    TripAdvisor APIで店舗情報を取得(検索 + 詳細)
    """
    # Location IDを検索
    location_data = search_tripadvisor_location(shop_name, lat, lng, language)
    if not location_data:
        return None

    # 詳細情報を取得
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
# Google Geocoding API 連携
# ========================================

def get_region_from_area(area: str, language: str = 'ja') -> dict:
    """
    Geocoding APIでエリアの地域情報(国、都道府県/州、座標)を取得
    """
    if not area:
        return None

    if not GOOGLE_GEOCODING_API_KEY:
        logger.warning("[Geocoding API] APIキーが設定されていません")
        return None

    try:
        url = 'https://maps.googleapis.com/maps/api/geocode/json'
        params = {
            'address': area,
            'key': GOOGLE_GEOCODING_API_KEY,
            'language': language
        }

        logger.info(f"[Geocoding API] エリア検索: {area}")

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK' or not data.get('results'):
            logger.warning(f"[Geocoding API] 結果なし: {area} (status: {data.get('status')})")
            return None

        result = data['results'][0]
        address_components = result.get('address_components', [])

        # 国と都道府県/州を抽出
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

        # 座標を取得
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

        logger.info(f"[Geocoding API] 取得成功: {area} → country={country}, region={region}, lat={lat}, lng={lng}")
        return geo_result

    except requests.exceptions.Timeout:
        logger.error(f"[Geocoding API] タイムアウト: {area}")
        return None
    except Exception as e:
        logger.error(f"[Geocoding API] エラー: {e}")
        return None


# ========================================
# Google Places API 連携
# ========================================

def get_place_details(place_id: str, language: str = 'ja') -> dict:
    """
    Place Details APIで電話番号と国コードを取得
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
            logger.warning(f"[Place Details API] 取得失敗: {data.get('status')} - {place_id}")
            return {'phone': None, 'country_code': None}

        result = data.get('result', {})

        # 電話番号取得(国内形式を優先、なければ国際形式)
        phone = result.get('formatted_phone_number') or result.get('international_phone_number')

        # 国コード取得
        country_code = None
        if result.get('address_components'):
            for component in result['address_components']:
                if 'country' in component.get('types', []):
                    country_code = component.get('short_name')
                    break

        if phone:
            logger.info(f"[Place Details API] 電話番号取得: {phone}, 国コード: {country_code}")

        return {'phone': phone, 'country_code': country_code}

    except requests.exceptions.Timeout:
        logger.error(f"[Place Details API] タイムアウト: {place_id}")
        return {'phone': None, 'country_code': None}
    except Exception as e:
        logger.error(f"[Place Details API] エラー: {e}")
        return None


def search_place(shop_name: str, area: str = '', geo_info: dict = None, language: str = 'ja') -> dict:
    """
    Google Places APIで店舗を検索(国コード検証付き)
    """
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("[Places API] APIキーが設定されていません")
        return None

    # Geocoding APIの結果から都道府県/州を取得
    region = geo_info.get('region', '') if geo_info else ''
    expected_country = geo_info.get('country_code', 'JP') if geo_info else 'JP'

    # 検索クエリを構築
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

        # Geocoding APIの座標があれば位置バイアスを追加
        if geo_info and geo_info.get('lat') and geo_info.get('lng'):
            params['location'] = f"{geo_info['lat']},{geo_info['lng']}"

            # 国によって検索半径を変える
            if expected_country == 'JP':
                params['radius'] = 3000
                params['region'] = 'jp'
            else:
                params['radius'] = 50000

        logger.info(f"[Places API] 検索クエリ: {query}")

        response = requests.get(search_url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK':
            logger.warning(f"[Places API] 検索失敗: {data.get('status')} - {query}")
            return None

        if not data.get('results'):
            logger.info(f"[Places API] 結果なし: {query}")
            return None

        place = data['results'][0]
        place_id = place['place_id']

        # 画像URLを生成
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

        # 座標を取得
        geometry = place.get('geometry', {})
        location = geometry.get('location', {})
        lat = location.get('lat')
        lng = location.get('lng')

        # ✅ Place Details APIで電話番号と国コードを取得
        details = get_place_details(place_id, language)
        actual_country = details.get('country_code')

        # ✅ 国コード検証
        if actual_country and expected_country and actual_country != expected_country:
            logger.warning(f"[Places API] 国コード不一致: {place.get('name')} "
                          f"(期待: {expected_country}, 実際: {actual_country}) - スキップ")
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

        logger.info(f"[Places API] 取得成功: {result['name']} (国: {actual_country}, 電話: {result['phone']})")
        return result

    except requests.exceptions.Timeout:
        logger.error(f"[Places API] タイムアウト: {query}")
        return None
    except Exception as e:
        logger.error(f"[Places API] エラー: {e}")
        return None

# ========================================
# ショップ情報 拡張ロジック (刷新版)
# ========================================

def enrich_shops_with_photos(shops: list, area: str = '', language: str = 'ja') -> list:
    """
    ショップリストに外部APIデータを追加(place_id重複排除付き、国コード検証強化版)
    - 基本: トリップアドバイザーを表示
    - 例外(日本語かつ日本国内): 国内3サイトを表示し、トリップアドバイザーは非表示
    """
    enriched_shops = []
    seen_place_ids = set()  # ✅ 重複チェック用
    duplicate_count = 0
    validation_failed_count = 0
    
    logger.info(f"[Enrich] 開始: area='{area}', language={language}, shops={len(shops)}件")

    # Geocodingはあくまで補助情報として取得(失敗しても止まらない)
    geo_info = None
    if area:
        try:
            geo_info = get_region_from_area(area, language)
            if geo_info:
                logger.info(f"[Enrich] Geocoding成功: {geo_info.get('formatted_address', '')} "
                           f"(国: {geo_info.get('country_code', '')}, "
                           f"座標: {geo_info.get('lat', '')}, {geo_info.get('lng', '')})")
        except Exception as e:
            logger.error(f"[Enrich] Geocoding Error: {e}")

    # LLMが回答した店舗名をログ出力
    logger.info(f"[Enrich] LLMの回答店舗:")
    for i, shop in enumerate(shops, 1):
        logger.info(f"[Enrich]   {i}. {shop.get('name', '')}")

    for i, shop in enumerate(shops, 1):
        shop_name = shop.get('name', '')
        if not shop_name:
            continue

        logger.info(f"[Enrich] ----------")
        logger.info(f"[Enrich] {i}/{len(shops)} 検索: '{shop_name}'")

        # -------------------------------------------------------
        # 1. Google Places APIで基本情報を取得(国コード検証付き)
        # -------------------------------------------------------
        place_data = search_place(shop_name, area, geo_info, language)
        
        if not place_data:
            logger.warning(f"[Enrich] Places APIで見つからない、または検証失敗でスキップ: {shop_name}")
            validation_failed_count += 1
            continue

        place_id = place_data.get('place_id')
        place_name = place_data.get('name')
        
        logger.info(f"[Enrich] → 検索結果: '{place_name}'")
        logger.info(f"[Enrich] → place_id: {place_id}")

        # ✅ place_id重複チェック
        if place_id in seen_place_ids:
            duplicate_count += 1
            logger.warning(f"[Enrich] → ❌ 重複検出!既に追加済み(スキップ)")
            logger.warning(f"[Enrich]    LLM店舗名: '{shop_name}' → Google店舗名: '{place_name}'")
            continue
        
        # ✅ place_idを記録
        seen_place_ids.add(place_id)
        logger.info(f"[Enrich] → ✅ 追加決定")

        # 国コードの取得
        shop_country = place_data.get('country_code', '')
        
        # -------------------------------------------------------
        # 2. ロジック判定(フラグ設定)
        # -------------------------------------------------------
        # デフォルト設定 (基本はTripAdvisorを表示)
        show_tripadvisor = True
        show_domestic_sites = False

        # 【例外ルール】言語が日本語(ja) かつ 日本国内(JP) の場合
        if language == 'ja' and shop_country == 'JP':
            show_tripadvisor = False      # トリップアドバイザーは出さない
            show_domestic_sites = True    # 国内3サイトを出す
        
        # 将来的な拡張(例:台湾・韓国でも食べログを出す場合)
        # if language == 'ja' and shop_country in ['TW', 'KR']:
        #     show_domestic_sites = True
        
        logger.info(f"[Enrich] 判定結果: {shop_name} (Country: {shop_country}, Lang: {language}) "
                   f"-> TripAdvisor: {show_tripadvisor}, Domestic: {show_domestic_sites}")

        # -------------------------------------------------------
        # 3. データの注入
        # -------------------------------------------------------
        # Google Placesの共通データ
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

        # A. 国内3サイトのリンク生成 (例外ルール適用時)
        if show_domestic_sites:
            try:
                # TripAdvisorフィールドを明示的に削除
                shop.pop('tripadvisor_url', None)
                shop.pop('tripadvisor_rating', None)
                shop.pop('tripadvisor_reviews', None)

                # ホットペッパー
                hotpepper_url = None
                try:
                    hotpepper_url = search_hotpepper(shop_name, area, geo_info)
                    if not hotpepper_url:
                        # 名前を変えて再トライ
                        places_name = place_data.get('name', '')
                        if places_name and places_name != shop_name:
                            hotpepper_url = search_hotpepper(places_name, area, geo_info)
                except Exception:
                    pass

                shop['hotpepper_url'] = hotpepper_url if hotpepper_url else f"https://www.google.com/search?q={shop_name}+{area}+ホットペッパーグルメ"

                # 食べログ
                try:
                    places_name = place_data.get('name', '')
                    region_name = geo_info.get('region', '') if geo_info else '東京'
                    # 都道府県コード変換(簡易版)
                    pref_code_map = {'東京': 'tokyo', '神奈川': 'kanagawa', '大阪': 'osaka', '京都': 'kyoto', '兵庫': 'hyogo', '北海道': 'hokkaido', '愛知': 'aichi', '福岡': 'fukuoka'}
                    pref = region_name.rstrip('都道府県') if region_name else '東京'
                    pref_code = pref_code_map.get(pref, 'tokyo')

                    tabelog_search_query = requests.utils.quote(places_name if places_name else shop_name)
                    shop['tabelog_url'] = f"https://tabelog.com/{pref_code}/rstLst/?sw={tabelog_search_query}"
                except Exception:
                    shop['tabelog_url'] = f"https://tabelog.com/tokyo/rstLst/?sw={shop_name}"

                # ぐるなび
                shop['gnavi_url'] = f"https://www.google.com/search?q={shop_name}+{area}+ぐるなび"

            except Exception as e:
                logger.error(f"[Enrich] Domestic Sites Error: {e}")

        # B. トリップアドバイザーのリンク生成 (デフォルト適用時)
        if show_tripadvisor:
            try:
                lat = place_data.get('lat')
                lng = place_data.get('lng')
                
                if TRIPADVISOR_API_KEY:
                    # 言語マッピング
                    tripadvisor_lang_map = {'ja': 'ja', 'en': 'en', 'zh': 'zh', 'ko': 'ko'}
                    search_lang = tripadvisor_lang_map.get(language, 'en')
                    
                    # 検索実行
                    tripadvisor_data = get_tripadvisor_data(shop_name, lat, lng, search_lang)

                    # 0件かつ日本語の場合、英語で再トライ(ヒット率向上策)
                    if not tripadvisor_data and search_lang == 'ja':
                        logger.info(f"[TripAdvisor] 日本語でヒットせず。英語で再検索: {shop_name}")
                        tripadvisor_data = get_tripadvisor_data(shop_name, lat, lng, 'en')

                    if tripadvisor_data:
                        shop['tripadvisor_url'] = tripadvisor_data.get('web_url')
                        shop['tripadvisor_rating'] = tripadvisor_data.get('rating')
                        shop['tripadvisor_reviews'] = tripadvisor_data.get('num_reviews')
                        logger.info(f"[TripAdvisor] リンク生成成功: {shop_name}")
            except Exception as e:
                logger.error(f"[Enrich] TripAdvisor Error: {e}")

        enriched_shops.append(shop)

    logger.info(f"[Enrich] ========== 完了 ==========")
    logger.info(f"[Enrich] 出力: {len(enriched_shops)}件")
    logger.info(f"[Enrich] 重複除外: {duplicate_count}件")
    logger.info(f"[Enrich] 検証失敗: {validation_failed_count}件")
    logger.info(f"[Enrich] 合計入力: {len(shops)}件")

    return enriched_shops


def extract_area_from_text(text: str, language: str = 'ja') -> str:
    """
    テキストからエリア名を抽出(Geocoding APIで動的に検証)
    """
    jp_chars = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9Fa-zA-Z]'
    patterns = [
        rf'({jp_chars}{{2,10}})の{jp_chars}',
        rf'({jp_chars}{{2,10}})で{jp_chars}',
        rf'({jp_chars}{{2,10}})にある',
        rf'({jp_chars}{{2,10}})周辺',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            geo_info = get_region_from_area(candidate, language)
            if geo_info and geo_info.get('region'):
                logger.info(f"[Extract Area] エリア抽出成功: '{candidate}' from '{text}'")
                return candidate

    logger.info(f"[Extract Area] エリア抽出失敗: '{text}'")
    return ''


def extract_shops_from_response(text: str) -> list:
    """
    LLMの応答テキストからショップ情報を抽出
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
            'category': 'レストラン'
        })

    logger.info(f"[Extract] {len(shops)}件のショップを抽出")
    return shops


# ========================================
# セッション管理
# ========================================

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
        """メッセージを追加（役割(Role)別の構造で保存）"""
        data = self.get_data()
        if not data:
            return None
        
        # genai SDKが理解できる構造で保存
        message = {
            'role': 'user' if role == 'user' else 'model',
            'parts': [content],
            'type': message_type,  # 内部管理用
            'timestamp': datetime.now().isoformat()
        }
        data['messages'].append(message)
        logger.info(f"[Session] メッセージ追加: role={message['role']}, type={message_type}")
        return message

    def get_history_for_api(self):
        """SDKにそのまま渡せる形式のリストを返す（types.Contentオブジェクトのリスト）"""
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
                    parts=[types.Part(text=m['parts'][0])]  # partsは文字列のリストなので最初の要素を取得
                )
                history.append(content)
        
        logger.info(f"[Session] API用履歴生成: {len(history)}件のメッセージ")
        return history

    def get_messages(self, include_types=None):
        """メッセージ履歴を取得（互換性のため残す）"""
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

    def __init__(self, session: SupportSession):
        self.session = session
        self.language = session.get_language()
        self.mode = session.get_mode()  # ★ モードを取得
        
        # ★★★ モードに応じたプロンプトを選択 ★★★
        mode_prompts = SYSTEM_PROMPTS.get(self.mode, SYSTEM_PROMPTS.get('chat', {}))
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
        2. 履歴には既に最新のユーザーメッセージが含まれている（add_messageで追加済み）
        3. そのため、履歴をそのままGeminiに渡す
        """
        # 履歴を構造化リストで取得（既に最新のユーザーメッセージを含む）
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

            # 【重要】改善版: configを条件付きで生成
            # system_promptとtoolsの両方またはいずれかがある場合のみconfigを作成
            if system_prompt or tools:
                config_dict = {}
                if system_prompt:
                    config_dict["system_instruction"] = system_prompt
                if tools:
                    config_dict["tools"] = tools
                
                config = types.GenerateContentConfig(**config_dict)
                
                response = gemini_client.models.generate_content(
                    model="gemini-2.0-flash-exp",
                    contents=history,
                    config=config
                )
            else:
                # configが不要な場合はconfigパラメータを渡さない
                response = gemini_client.models.generate_content(
                    model="gemini-2.0-flash-exp",
                    contents=history
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
        conversation_lines = []
        for msg in all_messages:
            role_name = 'ユーザー' if msg['role'] == 'user' else 'アシスタント'
            conversation_lines.append(f"{role_name}: {msg['parts'][0]}")
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


# ========================================
# API エンドポイント
# ========================================

@app.route('/')
def index():
    """フロントエンド表示"""
    return render_template('support.html')


@app.route('/api/session/start', methods=['POST', 'OPTIONS'])
def start_session():
    """
    セッション開始 - モード対応
    
    【重要】改善されたフロー:
    1. セッション初期化（モード・言語設定）
    2. アシスタント作成（最新の状態で）
    3. 初回メッセージ生成
    4. 履歴に追加
    """
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json or {}
        user_info = data.get('user_info', {})
        language = data.get('language', 'ja')
        mode = data.get('mode', 'chat')

        # 1. セッション初期化
        session = SupportSession()
        session.initialize(user_info, language=language, mode=mode)
        logger.info(f"[Start Session] 新規セッション作成: {session.session_id}")

        # 2. アシスタント作成（最新の状態で）
        assistant = SupportAssistant(session)
        
        # 3. 初回メッセージ生成
        initial_message = assistant.get_initial_message()

        # 4. 履歴に追加（roleは'model'）
        session.add_message('model', initial_message, 'chat')

        logger.info(f"[API] セッション開始: {session.session_id}, 言語: {language}, モード: {mode}")

        return jsonify({
            'session_id': session.session_id,
            'initial_message': initial_message
        })

    except Exception as e:
        logger.error(f"[API] セッション開始エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    """
    チャット処理 - 改善版
    
    【重要】改善されたフロー（順序を厳守）:
    1. 状態確定 (State First): モード・言語を更新
    2. ユーザー入力を記録: メッセージを履歴に追加
    3. 知能生成 (Assistant作成): 最新の状態でアシスタントを作成
    4. 推論開始: Gemini APIを呼び出し
    5. アシスタント応答を記録: 履歴に追加
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
            return jsonify({'error': 'session_idとmessageが必要です'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'セッションが見つかりません'}), 404

        logger.info(f"[Chat] セッション: {session_id}, モード: {mode}, 言語: {language}")

        # 1. 状態確定 (State First)
        session.update_language(language)
        session.update_mode(mode)

        # 2. ユーザー入力を記録
        session.add_message('user', user_message, 'chat')

        # 3. 知能生成 (Assistant作成)
        assistant = SupportAssistant(session)
        
        # 4. 推論開始
        result = assistant.process_user_message(user_message, stage)
        
        # 5. アシスタント応答を記録
        session.add_message('model', result['response'], 'chat')

        if result['summary']:
            session.add_message('model', result['summary'], 'summary')

        # ショップデータ処理
        shops = result.get('shops', [])
        response_text = result['response']
        is_followup = result.get('is_followup', False)

        # 多言語メッセージ辞書
        shop_messages = {
            'ja': {
                'intro': lambda count: f"ご希望に合うお店を{count}件ご紹介します。\n\n",
                'not_found': "申し訳ございません。条件に合うお店が見つかりませんでした。別の条件でお探しいただけますか?"
            },
            'en': {
                'intro': lambda count: f"Here are {count} restaurant recommendations for you.\n\n",
                'not_found': "Sorry, we couldn't find any restaurants matching your criteria. Would you like to search with different conditions?"
            },
            'zh': {
                'intro': lambda count: f"为您推荐{count}家餐厅。\n\n",
                'not_found': "很抱歉,没有找到符合条件的餐厅。要用其他条件搜索吗?"
            },
            'ko': {
                'intro': lambda count: f"고객님께 {count}개의 식당을 추천합니다.\n\n",
                'not_found': "죄송합니다. 조건에 맞는 식당을 찾을 수 없었습니다. 다른 조건으로 찾으시겠습니까?"
            }
        }

        current_messages = shop_messages.get(language, shop_messages['ja'])

        if shops and not is_followup:
            original_count = len(shops)
            area = extract_area_from_text(user_message, language)
            logger.info(f"[Chat] 抽出エリア: '{area}' from '{user_message}'")

            # Places APIで写真を取得
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
                logger.info(f"[Chat] {len(shops)}件のショップデータを返却(元: {original_count}件, 言語: {language})")
            else:
                response_text = current_messages['not_found']
                logger.warning(f"[Chat] 全店舗が除外されました(元: {original_count}件)")

        elif is_followup:
            logger.info(f"[Chat] 深掘り質問への回答: {response_text[:100]}...")

        return jsonify({
            'response': response_text,
            'summary': result['summary'],
            'shops': shops,
            'should_confirm': result['should_confirm'],
            'is_followup': is_followup
        })

    except Exception as e:
        logger.error(f"[API] チャットエラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/finalize', methods=['POST', 'OPTIONS'])
def finalize_session():
    """セッション完了"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'error': 'session_idが必要です'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'セッションが見つかりません'}), 404

        assistant = SupportAssistant(session)
        final_summary = assistant.generate_final_summary()

        return jsonify({
            'summary': final_summary,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"[API] 完了処理エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel', methods=['POST', 'OPTIONS'])
def cancel_processing():
    """処理中止"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'session_idが必要です'}), 400
        
        logger.info(f"[API] 処理中止リクエスト: {session_id}")
        
        # セッションのステータスを更新
        session = SupportSession(session_id)
        session_data = session.get_data()
        
        if session_data:
            session.update_status('cancelled')
        
        return jsonify({
            'success': True,
            'message': '処理を中止しました'
        })
        
    except Exception as e:
        logger.error(f"[API] 中止処理エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tts/synthesize', methods=['POST', 'OPTIONS'])
def synthesize_speech():
    """音声合成"""
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
            return jsonify({'success': False, 'error': 'テキストが必要です'}), 400

        MAX_CHARS = 1000
        if len(text) > MAX_CHARS:
            logger.warning(f"[TTS] テキストが長すぎるため切り詰めます: {len(text)} → {MAX_CHARS} 文字")
            text = text[:MAX_CHARS] + '...'

        logger.info(f"[TTS] 合成開始: {len(text)} 文字")

        synthesis_input = texttospeech.SynthesisInput(text=text)

        try:
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )
        except Exception as voice_error:
            logger.warning(f"[TTS] 指定音声が無効、デフォルトに変更: {voice_error}")
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

        logger.info(f"[TTS] 合成成功: {len(audio_base64)} bytes (base64)")

        return jsonify({
            'success': True,
            'audio': audio_base64
        })

    except Exception as e:
        logger.error(f"[TTS] エラー: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/transcribe', methods=['POST', 'OPTIONS'])
def transcribe_audio():
    """音声認識"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': '音声データが必要です'}), 400

        logger.info(f"[STT] 認識開始: {len(audio_base64)} bytes (base64)")

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
            logger.info(f"[STT] 認識成功: '{transcript}' (信頼度: {confidence:.2f})")
        else:
            logger.warning("[STT] 音声が認識されませんでした")

        return jsonify({
            'success': True,
            'transcript': transcript
        })

    except Exception as e:
        logger.error(f"[STT] エラー: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/stream', methods=['POST', 'OPTIONS'])
def transcribe_audio_streaming():
    """音声認識 (Streaming)"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': '音声データが必要です'}), 400

        logger.info(f"[STT Streaming] 認識開始: {len(audio_base64)} bytes (base64)")

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
                    logger.info(f"[STT Streaming] 認識成功: '{transcript}' (信頼度: {confidence:.2f})")
                    break

            if transcript:
                break

        if not transcript:
            logger.warning("[STT Streaming] 音声が認識されませんでした")

        return jsonify({
            'success': True,
            'transcript': transcript,
            'confidence': confidence
        })

    except Exception as e:
        logger.error(f"[STT Streaming] エラー: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/session/<session_id>', methods=['GET', 'OPTIONS'])
def get_session(session_id):
    """セッション情報取得"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        session = SupportSession(session_id)
        data = session.get_data()

        if not data:
            return jsonify({'error': 'セッションが見つかりません'}), 404

        return jsonify(data)

    except Exception as e:
        logger.error(f"[API] セッション取得エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET', 'OPTIONS'])
def health_check():
    """ヘルスチェック"""
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
    logger.info(f"[WebSocket STT] クライアント接続: {request.sid}")
    emit('connected', {'status': 'ready'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"[WebSocket STT] クライアント切断: {request.sid}")
    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        if 'stop_event' in stream_data:
            stream_data['stop_event'].set()
        del active_streams[request.sid]

@socketio.on('start_stream')
def handle_start_stream(data):
    language_code = data.get('language_code', 'ja-JP')
    sample_rate = data.get('sample_rate', 16000)  # フロントエンドから受け取る
    client_sid = request.sid
    logger.info(f"[WebSocket STT] ストリーム開始: {client_sid}, 言語: {language_code}, サンプルレート: {sample_rate}Hz")

    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,  # 動的に設定
        language_code=language_code,
        enable_automatic_punctuation=True,
        model='latest_long'  # より高精度なモデルに変更
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
            logger.info(f"[WebSocket STT] 認識スレッド開始: {client_sid}")
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
                        logger.info(f"[WebSocket STT] 最終認識: '{transcript}' (信頼度: {confidence:.2f})")
                    else:
                        logger.debug(f"[WebSocket STT] 途中認識: '{transcript}'")

        except Exception as e:
            logger.error(f"[WebSocket STT] 認識エラー: {e}", exc_info=True)
            socketio.emit('error', {'message': str(e)}, room=client_sid)

    thread = threading.Thread(target=recognition_thread, daemon=True)
    thread.start()

    emit('stream_started', {'status': 'streaming'})

@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    if request.sid not in active_streams:
        logger.warning(f"[WebSocket STT] 未初期化のストリーム: {request.sid}")
        return

    try:
        chunk_base64 = data.get('chunk', '')
        if not chunk_base64:
            return

        # ★★★ sample_rateを取得(16kHzで受信) ★★★
        sample_rate = data.get('sample_rate', 16000)
        
        # ★★★ 統計情報を取得してログ出力(必ず出力) ★★★
        stats = data.get('stats')
        logger.info(f"[audio_chunk受信] sample_rate: {sample_rate}Hz, stats: {stats}")
        
        if stats:
            logger.info(f"[AudioWorklet統計] サンプルレート: {sample_rate}Hz, "
                       f"サンプル総数: {stats.get('totalSamples')}, "
                       f"送信チャンク数: {stats.get('chunksSent')}, "
                       f"空入力回数: {stats.get('emptyInputCount')}, "
                       f"process呼び出し回数: {stats.get('processCalls')}, "
                       f"オーバーフロー回数: {stats.get('overflowCount', 0)}")  # ★ オーバーフロー追加

        audio_chunk = base64.b64decode(chunk_base64)
        
        # ★★★ 16kHzそのままGoogle STTに送る ★★★
        stream_data = active_streams[request.sid]
        stream_data['audio_queue'].put(audio_chunk)

    except Exception as e:
        logger.error(f"[WebSocket STT] チャンク処理エラー: {e}", exc_info=True)

@socketio.on('stop_stream')
def handle_stop_stream():
    logger.info(f"[WebSocket STT] ストリーム停止: {request.sid}")

    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        stream_data['audio_queue'].put(None)
        stream_data['stop_event'].set()
        del active_streams[request.sid]

    emit('stream_stopped', {'status': 'stopped'})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)