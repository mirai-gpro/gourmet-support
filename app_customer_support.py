# -*- coding: utf-8 -*-
"""
汎用カスタマーサポートシステム (Gemini API版)
- プロンプト駆動型の質問フロー
- 会話要約による確認
- 質問要約書の生成（即答しない設計）
- Google Cloud STT/TTS統合
- Google Places API連携（店舗写真取得）
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
import google.generativeai as genai
from google.cloud import firestore
from google.cloud import texttospeech
from google.cloud import speech
from prompt_manager import PromptManager

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

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

# Google Geocoding API（Places APIと同じキーを使用）
GOOGLE_GEOCODING_API_KEY = os.getenv('GOOGLE_GEOCODING_API_KEY', GOOGLE_PLACES_API_KEY)

# ホットペッパーAPI
HOTPEPPER_API_KEY = os.getenv('HOTPEPPER_API_KEY', 'c22031a566715e40')

# Google Custom Search API（食べログ検索用）
GOOGLE_CSE_API_KEY = os.getenv('GOOGLE_CSE_API_KEY', '')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID', '')

# Google Custom Search API（食べログ検索用）
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

    Args:
        shop_name: 店舗名
        area: エリア名
        geo_info: Geocoding APIから取得した地域情報（オプション）

    Returns:
        店舗ページURL、または None
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
            'keyword': shop_name,  # 店舗名のみ（エリアはlarge_areaで絞り込み）
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
# Google Geocoding API 連携
# ========================================

def get_region_from_area(area: str) -> dict:
    """
    Geocoding APIでエリアの地域情報（国、都道府県/州、座標）を取得

    Args:
        area: エリア名（例: "麻布十番", "Manhattan", "梅田"）

    Returns:
        {
            'country': '日本',
            'country_code': 'JP',
            'region': '東京都',  # 都道府県 or 州
            'formatted_address': '日本、〒106-0045 東京都港区麻布十番',
            'lat': 35.6541,
            'lng': 139.7370
        }
        または None（取得失敗時）
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
            'language': 'ja'
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

def get_place_phone(place_id: str) -> str:
    """
    Place Details APIで電話番号を取得

    Args:
        place_id: Google Place ID

    Returns:
        電話番号（formatted_phone_number）、または None
    """
    if not GOOGLE_PLACES_API_KEY or not place_id:
        return None

    try:
        details_url = 'https://maps.googleapis.com/maps/api/place/details/json'
        params = {
            'place_id': place_id,
            'fields': 'formatted_phone_number,international_phone_number',
            'key': GOOGLE_PLACES_API_KEY,
            'language': 'ja'
        }

        response = requests.get(details_url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK':
            logger.warning(f"[Place Details API] 取得失敗: {data.get('status')} - {place_id}")
            return None

        result = data.get('result', {})
        # 国内形式を優先、なければ国際形式
        phone = result.get('formatted_phone_number') or result.get('international_phone_number')

        if phone:
            logger.info(f"[Place Details API] 電話番号取得: {phone}")

        return phone

    except requests.exceptions.Timeout:
        logger.error(f"[Place Details API] タイムアウト: {place_id}")
        return None
    except Exception as e:
        logger.error(f"[Place Details API] エラー: {e}")
        return None


def search_place(shop_name: str, area: str = '', geo_info: dict = None) -> dict:
    """
    Google Places APIで店舗を検索

    Args:
        shop_name: 店舗名
        area: エリア名（例: "恵比寿"）
        geo_info: Geocoding APIから取得した地域情報（オプション）

    Returns:
        店舗情報の辞書、または None
    """
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("[Places API] APIキーが設定されていません")
        return None

    # Geocoding APIの結果から都道府県/州を取得
    region = geo_info.get('region', '') if geo_info else ''

    # 検索クエリを構築（都道府県/州名を追加）
    if region:
        query = f"{shop_name} {area} {region}".strip()
    else:
        query = f"{shop_name} {area}".strip()

    try:
        search_url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
        params = {
            'query': query,
            'key': GOOGLE_PLACES_API_KEY,
            'language': 'ja',
            'type': 'restaurant'
        }

        # Geocoding APIの座標があれば位置バイアスを追加
        if geo_info and geo_info.get('lat') and geo_info.get('lng'):
            params['location'] = f"{geo_info['lat']},{geo_info['lng']}"
            params['radius'] = 3000  # 3km以内
            # 日本の場合のみregion=jpを設定
            if geo_info.get('country_code') == 'JP':
                params['region'] = 'jp'
        
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
        
        result = {
            'place_id': place_id,
            'name': place.get('name'),
            'rating': place.get('rating'),
            'user_ratings_total': place.get('user_ratings_total'),
            'formatted_address': place.get('formatted_address'),
            'photo_url': photo_url,
            'maps_url': maps_url,
            'phone': None  # Place Details APIで取得
        }

        # Place Details APIで電話番号を取得
        phone = get_place_phone(place_id)
        if phone:
            result['phone'] = phone

        logger.info(f"[Places API] 取得成功: {result['name']} (電話: {result['phone']})")
        return result
        
    except requests.exceptions.Timeout:
        logger.error(f"[Places API] タイムアウト: {query}")
        return None
    except Exception as e:
        logger.error(f"[Places API] エラー: {e}")
        return None


def enrich_shops_with_photos(shops: list, area: str = '') -> list:
    """
    ショップリストにGoogle Places APIのデータを追加
    Places APIで見つからない店舗は除外（ハルシネーション対策）
    """
    enriched_shops = []

    # Geocoding APIでエリアの地域情報を取得（1回だけ）
    geo_info = get_region_from_area(area) if area else None
    if geo_info:
        logger.info(f"[Enrich] エリア地域情報: {area} → region={geo_info.get('region')}, country={geo_info.get('country')}")

    for shop in shops:
        shop_name = shop.get('name', '')
        
        if not shop_name:
            continue
        
        place_data = search_place(shop_name, area, geo_info)
        
        # Places APIで見つからない店舗は除外
        if not place_data:
            logger.warning(f"[Places API] 店舗が見つからないため除外: {shop_name}")
            continue
        
        # エリア/都道府県が異なる場合は除外（Geocoding APIで動的に判定）
        if area:
            address = place_data.get('formatted_address', '')
            address_lower = address.lower()

            if geo_info:
                region = geo_info.get('region', '')  # 都道府県 or 州
                country = geo_info.get('country', '')
                logger.info(f"[Places API] 住所検証: shop={shop_name}, area={area}, region={region}, country={country}, address={address}")

                # 都道府県/州レベルで検証
                region_matched = False

                if region:
                    # 都道府県/州名が住所に含まれるかチェック
                    # 日本の場合: "東京都" → "東京" でもマッチ
                    region_variants = [region]
                    if region.endswith(('都', '道', '府', '県')):
                        region_variants.append(region[:-1])  # "東京都" → "東京"

                    for variant in region_variants:
                        if variant in address or variant.lower() in address_lower:
                            region_matched = True
                            break

                if not region_matched:
                    logger.warning(f"[Places API] 都道府県/州不一致のため除外: {shop_name} (検索エリア: {area}, 期待: {region}, 住所: {address})")
                    continue
            else:
                # Geocoding API失敗時: エリア名が住所に直接含まれるかチェック
                logger.warning(f"[Geocoding API] 地域情報取得失敗: {area}")
                if area not in address and area.lower() not in address_lower:
                    logger.warning(f"[Places API] エリア不一致のため除外: {shop_name} (検索: {area}, 住所: {address})")
                    continue
        
        # データを追加
        if place_data.get('photo_url'):
            shop['image'] = place_data['photo_url']

        if not shop.get('rating') and place_data.get('rating'):
            shop['rating'] = place_data['rating']

        if not shop.get('reviewCount') and place_data.get('user_ratings_total'):
            shop['reviewCount'] = place_data['user_ratings_total']

        if not shop.get('location') and place_data.get('formatted_address'):
            shop['location'] = place_data['formatted_address']

        if place_data.get('maps_url'):
            shop['maps_url'] = place_data['maps_url']

        # 電話番号と Place ID を追加（予約機能用）
        if place_data.get('phone'):
            shop['phone'] = place_data['phone']

        if place_data.get('place_id'):
            shop['place_id'] = place_data['place_id']
        
        # ホットペッパーAPIで検索
        hotpepper_url = search_hotpepper(shop_name, area, geo_info)

        # LLMの店舗名で見つからない場合、Places APIの店舗名で再検索
        if not hotpepper_url:
            places_name = place_data.get('name', '')
            if places_name and places_name != shop_name:
                logger.info(f"[Hotpepper API] Places名で再検索: {places_name}")
                hotpepper_url = search_hotpepper(places_name, area, geo_info)
        
        if hotpepper_url:
            shop['hotpepper_url'] = hotpepper_url
        else:
            # APIで見つからない場合はGoogle検索
            hp_query = f"{shop_name}+{area}+ホットペッパーグルメ".replace(' ', '+')
            shop['hotpepper_url'] = f"https://www.google.com/search?q={hp_query}"
        
        # 食べログURL（tabelog.comの検索機能を使用、該当なしの場合はGoogle検索）
        places_name = place_data.get('name', '')

        # Geocoding APIの結果から都道府県を取得
        region = geo_info.get('region', '') if geo_info else ''
        pref = region.rstrip('都道府県') if region else '東京'

        # 都道府県コードを取得（デフォルトは東京）
        pref_code_map = {
            '東京': 'tokyo',
            '神奈川': 'kanagawa',
            '大阪': 'osaka',
            '愛知': 'aichi',
            '福岡': 'fukuoka',
            '北海道': 'hokkaido',
            '京都': 'kyoto',
            '兵庫': 'hyogo',
            '埼玉': 'saitama',
            '千葉': 'chiba'
        }
        pref_code = pref_code_map.get(pref, 'tokyo')
        
        # Places APIの店舗名で検索URLを生成
        search_query = requests.utils.quote(places_name if places_name else shop_name)
        tabelog_search_url = f"https://tabelog.com/{pref_code}/rstLst/?sw={search_query}"
        
        # 食べログ検索結果を確認（該当なしの場合はGoogle検索）
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(tabelog_search_url, headers=headers, timeout=5)
            
            # 「該当するお店は見つかりませんでした」が含まれていたらGoogle検索
            if '該当するお店は見つかりませんでした' in response.text or 'お店は見つかりませんでした' in response.text:
                logger.info(f"[Tabelog] 該当なし、Google検索にフォールバック: {shop_name}")
                tabelog_query = f"{shop_name}+{area}+食べログ".replace(' ', '+')
                shop['tabelog_url'] = f"https://www.google.com/search?q={tabelog_query}"
            else:
                shop['tabelog_url'] = tabelog_search_url
                logger.info(f"[Tabelog] 検索URL生成: {shop_name} -> {tabelog_search_url}")
        except:
            # エラー時は検索URLをそのまま使用
            shop['tabelog_url'] = tabelog_search_url
            logger.info(f"[Tabelog] 検索URL生成: {shop_name} -> {tabelog_search_url}")
        
        # ぐるなびURL（Google検索経由）
        gnavi_query = f"{shop_name}+{area}+ぐるなび".replace(' ', '+')
        shop['gnavi_url'] = f"https://www.google.com/search?q={gnavi_query}"
        
        enriched_shops.append(shop)
    
    logger.info(f"[Enrich] {len(shops)}件中{len(enriched_shops)}件が有効")
    return enriched_shops


def extract_area_from_text(text: str) -> str:
    """
    テキストからエリア名を抽出（Geocoding APIで動的に検証）
    """
    import re

    # 「〇〇の」「〇〇で」「〇〇にある」などのパターンでエリア候補を抽出
    # 日本語文字（ひらがな、カタカナ、漢字）とアルファベットにマッチ
    jp_chars = r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9Fa-zA-Z]'
    patterns = [
        rf'({jp_chars}{{2,10}})の{jp_chars}',  # 「麻布十番のイタリアン」
        rf'({jp_chars}{{2,10}})で{jp_chars}',  # 「渋谷でランチ」
        rf'({jp_chars}{{2,10}})にある',  # 「新宿にあるお店」
        rf'({jp_chars}{{2,10}})周辺',  # 「銀座周辺」
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            # Geocoding APIで検証（地名かどうか確認）
            geo_info = get_region_from_area(candidate)
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
    
    # パターン1: 番号. **店名** (エリア) - 説明
    # パターン2: 番号. **店名**: 説明
    # パターン3: 番号. **店名（読み）**: 説明
    pattern = r'(\d+)\.\s*\*\*([^*]+)\*\*\s*(?:\([^)]+\))?\s*[-:：]\s*([^\n]+)'
    matches = re.findall(pattern, text)
    
    for match in matches:
        full_name = match[1].strip()
        description = match[2].strip()
        
        # 店名から読み仮名を分離
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
    
    def initialize(self, user_info=None):
        """新規セッション初期化"""
        data = {
            'session_id': self.session_id,
            'created_at': firestore.SERVER_TIMESTAMP,
            'updated_at': firestore.SERVER_TIMESTAMP,
            'messages': [],
            'status': 'active',
            'user_info': user_info or {},
            'summary': None,
            'inquiry_summary': None
        }
        self.doc_ref.set(data)
        logger.info(f"[Session] 新規作成: {self.session_id}")
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


class SupportAssistant:
    """サポートアシスタント（Gemini API連携）"""
    
    def __init__(self, session: SupportSession):
        self.session = session
        self.system_prompt = prompt_manager.get('support_system')
    
    def get_initial_message(self):
        """初回メッセージ"""
        return prompt_manager.get('initial_greeting')
    
    def process_user_message(self, user_message, conversation_stage='conversation'):
        """ユーザーメッセージを処理"""
        history = self.session.get_messages(include_types=['chat', 'summary'])
        
        if conversation_stage == 'inquiry':
            stage_instruction = prompt_manager.get('inquiry_stage_instruction')
        else:
            stage_instruction = prompt_manager.get('conversation_stage_instruction')
        
        prompt = self._build_prompt(history, user_message, stage_instruction)
        
        try:
            response = model.generate_content(prompt)
            assistant_text = response.text
            
            # JSON応答をパース
            parsed_message, parsed_shops = self._parse_json_response(assistant_text)
            
            # 会話中の場合は要約を生成
            summary = None
            if conversation_stage == 'conversation':
                # 店舗提案がある場合は簡潔な要約のみ
                if parsed_shops:
                    summary = f"{len(parsed_shops)}軒のお店を提案しました。"
                else:
                    summary = self._generate_summary(user_message, parsed_message)
            
            return {
                'response': parsed_message,
                'summary': summary,
                'shops': parsed_shops,
                'should_confirm': conversation_stage == 'conversation'
            }
            
        except Exception as e:
            logger.error(f"[Assistant] Gemini APIエラー: {e}")
            return {
                'response': 'エラーが発生しました。もう一度お試しください。',
                'summary': None,
                'shops': [],
                'should_confirm': False
            }
    
    def generate_final_summary(self):
        """最終要約を生成"""
        all_messages = self.session.get_messages()
        conversation_text = self._format_conversation(all_messages)
        
        summary_prompt = prompt_manager.get(
            'final_summary',
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
    
    def _parse_json_response(self, text: str) -> tuple:
        """
        LLMの応答からJSONをパースしてmessageとshopsを抽出
        
        Returns:
            (message, shops) のタプル
        """
        try:
            # ```json マークダウンを除去
            cleaned_text = text.strip()
            if cleaned_text.startswith('```'):
                # 最初の行を除去
                lines = cleaned_text.split('\n')
                cleaned_text = '\n'.join(lines[1:])
            if cleaned_text.endswith('```'):
                cleaned_text = cleaned_text[:-3]
            cleaned_text = cleaned_text.strip()
            
            # JSONをパース
            data = json.loads(cleaned_text)
            
            message = data.get('message', text)
            shops = data.get('shops', [])

            logger.info(f"[JSON Parse] 成功: message={len(message)}文字, shops={len(shops)}件")
            return message, shops
            
        except json.JSONDecodeError as e:
            logger.warning(f"[JSON Parse] パース失敗、平文として処理: {e}")
            # JSONでない場合は従来通りテキストから抽出
            shops = extract_shops_from_response(text)
            return text, shops
            
            return summary
            
        except Exception as e:
            logger.error(f"[Assistant] 要約生成エラー: {e}")
            return "要約の生成中にエラーが発生しました。"
    
    def _build_prompt(self, history, current_message, stage_instruction):
        """プロンプトを構築"""
        prompt_parts = []
        
        prompt_parts.append(f"システム指示:\n{self.system_prompt}\n")
        
        if history:
            prompt_parts.append("会話履歴:")
            for msg in history:
                role_name = "ユーザー" if msg['role'] == 'user' else "アシスタント"
                prompt_parts.append(f"{role_name}: {msg['content']}")
            prompt_parts.append("")
        
        prompt_parts.append(stage_instruction)
        prompt_parts.append(f"\n【ユーザーの発言】\n{current_message}")
        
        return "\n".join(prompt_parts)
    
    def _generate_summary(self, user_message, assistant_response):
        """会話の要約を生成"""
        summary_prompt = prompt_manager.get(
            'conversation_summary',
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
        lines = []
        for msg in messages:
            if msg.get('type') == 'chat':
                role = "ユーザー" if msg['role'] == 'user' else "アシスタント"
                lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)


# ========================================
# API エンドポイント
# ========================================

@app.route('/')
def index():
    """フロントエンド表示"""
    return render_template('support.html')


@app.route('/api/session/start', methods=['POST'])
def start_session():
    """セッション開始"""
    try:
        data = request.json or {}
        user_info = data.get('user_info', {})
        
        session = SupportSession()
        session.initialize(user_info)
        
        assistant = SupportAssistant(session)
        initial_message = assistant.get_initial_message()
        
        session.add_message('assistant', initial_message, 'chat')
        
        return jsonify({
            'session_id': session.session_id,
            'initial_message': initial_message
        })
        
    except Exception as e:
        logger.error(f"[API] セッション開始エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def chat():
    """チャット処理"""
    try:
        data = request.json
        session_id = data.get('session_id')
        user_message = data.get('message')
        stage = data.get('stage', 'conversation')
        
        if not session_id or not user_message:
            return jsonify({'error': 'session_idとmessageが必要です'}), 400
        
        session = SupportSession(session_id)
        session_data = session.get_data()
        
        if not session_data:
            return jsonify({'error': 'セッションが見つかりません'}), 404
        
        session.add_message('user', user_message, 'chat')
        
        assistant = SupportAssistant(session)
        result = assistant.process_user_message(user_message, stage)
        
        session.add_message('assistant', result['response'], 'chat')
        
        if result['summary']:
            session.add_message('assistant', result['summary'], 'summary')
        
        # ========== ショップデータ処理 ==========
        # JSONパース済みのショップデータを取得
        shops = result.get('shops', [])
        response_text = result['response']

        if shops:
            original_count = len(shops)
            # エリア名を抽出
            area = extract_area_from_text(user_message)
            logger.info(f"[Chat] 抽出エリア: '{area}' from '{user_message}'")
            # Places APIで写真を取得（存在しない店舗は除外）
            shops = enrich_shops_with_photos(shops, area)

            # 確認済みのお店のみでレスポンステキストを再構成
            if shops:
                shop_list = []
                for i, shop in enumerate(shops, 1):
                    name = shop.get('name', '')
                    shop_area = shop.get('area', '')
                    description = shop.get('description', '')
                    if shop_area:
                        shop_list.append(f"{i}. **{name}**（{shop_area}）: {description}")
                    else:
                        shop_list.append(f"{i}. **{name}**: {description}")

                response_text = f"ご希望に合うお店を{len(shops)}件ご紹介します。\n\n" + "\n\n".join(shop_list)
                logger.info(f"[Chat] {len(shops)}件のショップデータを返却（元: {original_count}件）")
            else:
                # 全て除外された場合
                response_text = "申し訳ございません。条件に合うお店が見つかりませんでした。別の条件でお探しいただけますか？"
                logger.warning(f"[Chat] 全店舗が除外されました（元: {original_count}件）")
        # ========================================

        return jsonify({
            'response': response_text,
            'summary': result['summary'],
            'shops': shops,
            'should_confirm': result['should_confirm']
        })
        
    except Exception as e:
        logger.error(f"[API] チャットエラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/finalize', methods=['POST'])
def finalize_session():
    """セッション完了（最終要約生成）"""
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


@app.route('/api/tts/synthesize', methods=['POST'])
def synthesize_speech():
    """音声合成 (Google Cloud Text-to-Speech)"""
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


@app.route('/api/stt/transcribe', methods=['POST'])
def transcribe_audio():
    """音声認識 (Google Cloud Speech-to-Text)"""
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


@app.route('/api/stt/stream', methods=['POST'])
def transcribe_audio_streaming():
    """
    音声認識 (Google Cloud Streaming Speech-to-Text)
    案2: test_voice_conversation.py準拠のStreaming STT

    より高速な認識のため、streaming_recognize()を使用
    """
    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': '音声データが必要です'}), 400

        logger.info(f"[STT Streaming] 認識開始: {len(audio_base64)} bytes (base64)")

        audio_content = base64.b64decode(audio_base64)

        # Streaming STT設定 (test_voice_conversation.py準拠)
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code=language_code,
            enable_automatic_punctuation=True,
            model='default'
        )

        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=False,  # is_finalのみ取得
            single_utterance=True   # 単一発話
        )

        # 音声データをチャンクに分割してストリーミング
        CHUNK_SIZE = 1024 * 16  # 16KB chunks

        def audio_generator():
            """音声チャンクを生成"""
            for i in range(0, len(audio_content), CHUNK_SIZE):
                chunk = audio_content[i:i + CHUNK_SIZE]
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        # Streaming認識実行
        responses = stt_client.streaming_recognize(streaming_config, audio_generator())

        transcript = ''
        confidence = 0.0

        # is_final=Trueの結果のみ取得
        for response in responses:
            if not response.results:
                continue

            for result in response.results:
                if result.is_final and result.alternatives:
                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence
                    logger.info(f"[STT Streaming] 認識成功: '{transcript}' (信頼度: {confidence:.2f})")
                    break

            if transcript:  # is_final取得したら終了
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


@app.route('/api/session/<session_id>', methods=['GET'])
def get_session(session_id):
    """セッション情報取得"""
    try:
        session = SupportSession(session_id)
        data = session.get_data()
        
        if not data:
            return jsonify({'error': 'セッションが見つかりません'}), 404
        
        return jsonify(data)
        
    except Exception as e:
        logger.error(f"[API] セッション取得エラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """ヘルスチェック"""
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


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
