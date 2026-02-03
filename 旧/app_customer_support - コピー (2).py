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
import google.generativeai as genai
from google.cloud import firestore
from google.cloud import texttospeech
from google.cloud import speech
from prompt_manager import PromptManager
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

# 許可するオリジン（末尾のスラッシュなし）
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

# Gemini API初期化
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.0-flash-exp')

# Firestore初期化
db = firestore.Client()
prompt_manager = PromptManager()

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
    TripAdvisor APIで店舗情報を取得（検索 + 詳細）
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

        # 電話番号取得（国内形式を優先、なければ国際形式）
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
    Google Places APIで店舗を検索 (改善版)
    """
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("[Places API] APIキーが設定されていません")
        return None

    # Geocoding APIの結果から都道府県/州を取得
    region = geo_info.get('region', '') if geo_info else ''

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

            # 【改善】国によって検索半径を変える
            if geo_info.get('country_code') == 'JP':
                params['radius'] = 3000
                params['region'] = 'jp'
            else:
                params['radius'] = 50000  # 海外は広めに50km

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

        # Place Details APIで電話番号と国コードを取得
        details = get_place_details(place_id, language)

        result = {
            'place_id': place_id,
            'name': place.get('name'),
            'rating': place.get('rating'),
            'user_ratings_total': place.get('user_ratings_total'),
            'formatted_address': place.get('formatted_address'),
            'country_code': details.get('country_code'),
            'lat': lat,
            'lng': lng,
            'photo_url': photo_url,
            'maps_url': maps_url,
            'phone': details.get('phone')
        }

        logger.info(f"[Places API] 取得成功: {result['name']} (電話: {result['phone']})")
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
    ショップリストに外部APIデータを追加（ロジック刷新版）
    - 基本: トリップアドバイザーを表示
    - 例外(日本語かつ日本国内): 国内3サイトを表示し、トリップアドバイザーは非表示
    """
    enriched_shops = []
    
    logger.info(f"[Enrich] 開始: area='{area}', language={language}, shops={len(shops)}件")

    # Geocodingはあくまで補助情報として取得（失敗しても止まらない）
    geo_info = None
    if area:
        try:
            geo_info = get_region_from_area(area, language)
        except Exception as e:
            logger.error(f"[Enrich] Geocoding Error: {e}")

    for shop in shops:
        shop_name = shop.get('name', '')
        if not shop_name:
            continue

        # -------------------------------------------------------
        # 1. Google Places APIで基本情報を取得
        # -------------------------------------------------------
        place_data = search_place(shop_name, area, geo_info, language)
        
        if not place_data:
            logger.warning(f"[Enrich] Places APIで見つからないためスキップ: {shop_name}")
            continue

        # 国コードの取得
        shop_country = place_data.get('country_code', '')
        
        # -------------------------------------------------------
        # 2. ロジック判定（フラグ設定）
        # -------------------------------------------------------
        # デフォルト設定 (基本はTripAdvisorを表示)
        show_tripadvisor = True
        show_domestic_sites = False

        # 【例外ルール】 言語が日本語(ja) かつ 日本国内(JP) の場合
        if language == 'ja' and shop_country == 'JP':
            show_tripadvisor = False      # トリップアドバイザーは出さない
            show_domestic_sites = True    # 国内3サイトを出す
        
        # 将来的な拡張（例：台湾・韓国でも食べログを出す場合）
        # if language == 'ja' and shop_country in ['TW', 'KR']:
        #     show_domestic_sites = True
        
        logger.info(f"[Enrich] 判定結果: {shop_name} (Country: {shop_country}, Lang: {language}) -> TA: {show_tripadvisor}, Domestic: {show_domestic_sites}")

        # -------------------------------------------------------
        # 3. データの注入
        # -------------------------------------------------------
        # Google Placesの共通データ
        if place_data.get('photo_url'): shop['image'] = place_data['photo_url']
        if place_data.get('rating'): shop['rating'] = place_data['rating']
        if place_data.get('user_ratings_total'): shop['reviewCount'] = place_data['user_ratings_total']
        if place_data.get('formatted_address'): shop['location'] = place_data['formatted_address']
        if place_data.get('maps_url'): shop['maps_url'] = place_data['maps_url']
        if place_data.get('phone'): shop['phone'] = place_data['phone']
        if place_data.get('place_id'): shop['place_id'] = place_data['place_id']

        # A. 国内3サイトのリンク生成 (例外ルール適用時)
        if show_domestic_sites:
            try:
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
                    # 都道府県コード変換（簡易版）
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

                    # 0件かつ日本語の場合、英語で再トライ（ヒット率向上策）
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

    return enriched_shops


def extract_area_from_text(text: str, language: str = 'ja') -> str:
    """
    テキストからエリア名を抽出(Geocoding APIで動的に検証)
    """
    import re

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
    pattern = r'(\d+)\.\s*\*\*([^*]+)\*\*\s*(?:\([^)]+\))?\s*[-:：]\s*([^\n]+)'
    matches = re.findall(pattern, text)

    for match in matches:
        full_name = match[1].strip()
        description = match[2].strip()

        name = full_name
        name_match = re.match(r'^([^（(]+)[（(]([^）)]+)[）)]', full_name)
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
    """サポートセッション管理"""

    def __init__(self, session_id=None):
        self.session_id = session_id or str(uuid.uuid4())
        self.collection = db.collection('support_sessions')
        self.doc_ref = self.collection.document(self.session_id)

    def initialize(self, user_info=None, language='ja'):
        """新規セッション初期化"""
        data = {
            'session_id': self.session_id,
            'created_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP,
            'messages': [],
            'status': 'active',
            'user_info': user_info or {},
            'language': language,  # ユーザー言語を保存
            'summary': None,
            'inquiry_summary': None,
            'current_shops': []
        }
        self.doc_ref.set(data)
        logger.info(f"[Session] 新規作成: {self.session_id}, 言語: {language}")
        return data

    def add_message(self, role, content, message_type='chat'):
        """メッセージを追加"""
        message = {
            'role': role,
            'content': content,
            'type': message_type,
            'timestamp': datetime.now().isoformat()
        }

        self.doc_ref.update({
            'messages': firestore.ArrayUnion([message]),
            'updated_at': firestore.SERVER_TIMESTAMP
        })

        logger.info(f"[Session] メッセージ追加: {role} ({message_type})")
        return message

    def get_messages(self, include_types=None):
        """メッセージ履歴を取得"""
        doc = self.doc_ref.get()
        if not doc.exists:
            return []

        messages = doc.to_dict().get('messages', [])

        if include_types:
            messages = [m for m in messages if m.get('type') in include_types]

        return messages

    def save_current_shops(self, shops):
        """現在の店舗リストを保存"""
        self.doc_ref.update({
            'current_shops': shops,
            'updated_at': firestore.SERVER_TIMESTAMP
        })
        logger.info(f"[Session] 店舗リスト保存: {len(shops)}件")

    def get_current_shops(self):
        """現在の店舗リストを取得"""
        doc = self.doc_ref.get()
        if not doc.exists:
            return []
        return doc.to_dict().get('current_shops', [])

    def update_status(self, status, **kwargs):
        """ステータス更新"""
        update_data = {
            'status': status,
            'updated_at': firestore.SERVER_TIMESTAMP
        }
        update_data.update(kwargs)

        self.doc_ref.update(update_data)
        logger.info(f"[Session] ステータス更新: {status}")

    def get_data(self):
        """セッションデータ取得"""
        doc = self.doc_ref.get()
        return doc.to_dict() if doc.exists else None

    def get_language(self):
        """セッション言語を取得"""
        data = self.get_data()
        return data.get('language', 'ja') if data else 'ja'

    def update_language(self, language: str):
        """セッション言語を更新"""
        self.doc_ref.update({
            'language': language,
            'updated_at': firestore.SERVER_TIMESTAMP
        })
        logger.info(f"[Session] 言語更新: {language}")


class SupportAssistant:
    """サポートアシスタント"""

    def __init__(self, session: SupportSession):
        self.session = session
        self.language = session.get_language()
        self.system_prompt = prompt_manager.get('support_system', language=self.language)

    def get_initial_message(self):
        """初回メッセージ"""
        return prompt_manager.get('initial_greeting', language=self.language)

    def is_followup_question(self, user_message, current_shops):
        """深掘り質問かどうかを判定"""
        if not current_shops:
            return False

        # フォローアップ質問のパターン（料理名は除外 - 初回検索で誤判定されるため）
        followup_patterns = [
            'この中で', 'これらの中で', 'さっきの', '先ほどの',
            'どれが', 'どこが', 'どの店', '何番目',
            '予約', '電話番号', '営業時間', 'アクセス',
            '詳しく', 'もっと', 'について'
        ]

        message_lower = user_message.lower()
        return any(pattern in message_lower for pattern in followup_patterns)

    def process_user_message(self, user_message, conversation_stage='conversation'):
        """ユーザーメッセージを処理"""
        history = self.session.get_messages(include_types=['chat', 'summary'])
        current_shops = self.session.get_current_shops()

        is_followup = self.is_followup_question(user_message, current_shops)

        if conversation_stage == 'inquiry':
            stage_instruction = prompt_manager.get('inquiry_stage_instruction', language=self.language)
        else:
            stage_instruction = prompt_manager.get('conversation_stage_instruction', language=self.language)

        if is_followup:
            # 深掘り質問用の多言語メッセージ
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
                    'header': '【当前推荐的餐?信息】',
                    'footer': '用?正在??上述餐?的信息。?参考餐?信息?行回答。'
                },
                'ko': {
                    'header': '【?? ?? ?? ??? ??】',
                    'footer': '???? ? ???? ?? ???? ????. ??? ??? ???? ??? ???.'
                }
            }
            current_followup_msg = followup_messages.get(self.language, followup_messages['ja'])
            stage_instruction += f"\n\n{current_followup_msg['header']}\n{self._format_current_shops(current_shops)}\n\n{current_followup_msg['footer']}"

        prompt = self._build_prompt(history, user_message, stage_instruction)

        try:
            response = model.generate_content(prompt)
            assistant_text = response.text

            parsed_message, parsed_shops = self._parse_json_response(assistant_text)

            if parsed_shops:
                self.session.save_current_shops(parsed_shops)

            summary = None
            if conversation_stage == 'conversation':
                if parsed_shops:
                    # 多言語サマリー
                    summary_messages = {
                        'ja': lambda count: f"{count}軒のお店を提案しました。",
                        'en': lambda count: f"Suggested {count} restaurants.",
                        'zh': lambda count: f"推荐了{count}家餐?。",
                        'ko': lambda count: f"{count}?? ???? ??????."
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
            logger.error(f"[Assistant] Gemini APIエラー: {e}")
            return {
                'response': 'エラーが発生しました。もう一度お試しください。',
                'summary': None,
                'shops': [],
                'should_confirm': False,
                'is_followup': False
            }

    def generate_final_summary(self):
        """最終要約を生成"""
        all_messages = self.session.get_messages()
        conversation_text = self._format_conversation(all_messages)

        summary_prompt = prompt_manager.get(
            'final_summary',
            language=self.language,
            conversation_text=conversation_text,
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        try:
            response = model.generate_content(summary_prompt)
            summary = response.text

            self.session.update_status(
                'completed',
                inquiry_summary=summary
            )

            return summary

        except Exception as e:
            logger.error(f"[Assistant] 要約生成エラー: {e}")
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
                'description': '?明',
                'specialty': '招牌菜',
                'price': '?算',
                'atmosphere': '氛?',
                'features': '特色'
            },
            'ko': {
                'description': '??',
                'specialty': '?? ??',
                'price': '??',
                'atmosphere': '???',
                'features': '??'
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

    def _build_prompt(self, history, current_message, stage_instruction):
        """プロンプトを構築"""
        # 多言語ラベル
        labels = {
            'ja': {
                'system': 'システム指示',
                'history': '会話履歴',
                'user': 'ユーザー',
                'assistant': 'アシスタント',
                'current': '【ユーザーの発言】'
            },
            'en': {
                'system': 'System Instructions',
                'history': 'Conversation History',
                'user': 'User',
                'assistant': 'Assistant',
                'current': '【Current User Message】'
            },
            'zh': {
                'system': '系?指示',
                'history': '???史',
                'user': '用?',
                'assistant': '助手',
                'current': '【用?的?言】'
            },
            'ko': {
                'system': '??? ??',
                'history': '?? ??',
                'user': '???',
                'assistant': '?????',
                'current': '【???? ??】'
            }
        }

        current_labels = labels.get(self.language, labels['ja'])
        prompt_parts = []

        prompt_parts.append(f"{current_labels['system']}:\n{self.system_prompt}\n")

        if history:
            prompt_parts.append(f"{current_labels['history']}:")
            for msg in history:
                role_name = current_labels['user'] if msg['role'] == 'user' else current_labels['assistant']
                prompt_parts.append(f"{role_name}: {msg['content']}")
            prompt_parts.append("")

        prompt_parts.append(stage_instruction)
        prompt_parts.append(f"\n{current_labels['current']}\n{current_message}")

        return "\n".join(prompt_parts)

    def _generate_summary(self, user_message, assistant_response):
        """会話の要約を生成"""
        summary_prompt = prompt_manager.get(
            'conversation_summary',
            language=self.language,
            user_message=user_message,
            assistant_response=assistant_response
        )

        try:
            response = model.generate_content(summary_prompt)
            return response.text

        except Exception as e:
            logger.error(f"[Assistant] 要約生成エラー: {e}")
            return None

    def _format_conversation(self, messages):
        """会話ログを整形"""
        # 多言語ラベル
        role_labels = {
            'ja': {'user': 'ユーザー', 'assistant': 'アシスタント'},
            'en': {'user': 'User', 'assistant': 'Assistant'},
            'zh': {'user': '用?', 'assistant': '助手'},
            'ko': {'user': '???', 'assistant': '?????'}
        }

        current_role_labels = role_labels.get(self.language, role_labels['ja'])
        lines = []
        for msg in messages:
            if msg.get('type') == 'chat':
                role = current_role_labels['user'] if msg['role'] == 'user' else current_role_labels['assistant']
                lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)


# ========================================
# API エンドポイント
# ========================================

@app.route('/')
def index():
    """フロントエンド表示"""
    return render_template('support.html')


@app.route('/api/session/start', methods=['POST', 'OPTIONS'])
def start_session():
    """セッション開始"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json or {}
        user_info = data.get('user_info', {})
        language = data.get('language', 'ja')  # デフォルトは日本語

        session = SupportSession()
        session.initialize(user_info, language=language)

        assistant = SupportAssistant(session)
        initial_message = assistant.get_initial_message()

        session.add_message('assistant', initial_message, 'chat')

        logger.info(f"[API] セッション開始: {session.session_id}, 言語: {language}")

        return jsonify({
            'session_id': session.session_id,
            'initial_message': initial_message
        })

    except Exception as e:
        logger.error(f"[API] セッション開始エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    """チャット処理"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')
        user_message = data.get('message')
        stage = data.get('stage', 'conversation')
        language = data.get('language', 'ja')  # 言語パラメータを取得

        if not session_id or not user_message:
            return jsonify({'error': 'session_idとmessageが必要です'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'セッションが見つかりません'}), 404

        # 言語が変更されている場合、セッションを更新
        current_language = session.get_language()
        if language != current_language:
            session.update_language(language)
            logger.info(f"[Chat] 言語切り替え: {current_language} -> {language}")

        session.add_message('user', user_message, 'chat')

        assistant = SupportAssistant(session)
        result = assistant.process_user_message(user_message, stage)

        session.add_message('assistant', result['response'], 'chat')

        if result['summary']:
            session.add_message('assistant', result['summary'], 'summary')

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
                'intro': lambda count: f"??推荐{count}家餐?。\n\n",
                'not_found': "很抱歉，没有找到符合条件的餐?。?要用其他条件搜索?？"
            },
            'ko': {
                'intro': lambda count: f"?? ??? {count}?? ??? ????.\n\n",
                'not_found': "?????. ??? ?? ???? ?? ? ?????. ?? ???? ?????????"
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
            'firestore': 'ok',
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
    client_sid = request.sid
    logger.info(f"[WebSocket STT] ストリーム開始: {client_sid}, 言語: {language_code}")

    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code=language_code,
        enable_automatic_punctuation=True,
        model='default'
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

        audio_chunk = base64.b64decode(chunk_base64)
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