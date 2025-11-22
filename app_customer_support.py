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

# エリアの座標と都道府県マッピング
AREA_DATA = {
    '恵比寿': {'lat': 35.6467, 'lng': 139.7101, 'pref': '東京'},
    '渋谷': {'lat': 35.6580, 'lng': 139.7016, 'pref': '東京'},
    '新宿': {'lat': 35.6896, 'lng': 139.7006, 'pref': '東京'},
    '池袋': {'lat': 35.7295, 'lng': 139.7109, 'pref': '東京'},
    '銀座': {'lat': 35.6717, 'lng': 139.7649, 'pref': '東京'},
    '六本木': {'lat': 35.6627, 'lng': 139.7311, 'pref': '東京'},
    '表参道': {'lat': 35.6654, 'lng': 139.7121, 'pref': '東京'},
    '原宿': {'lat': 35.6702, 'lng': 139.7027, 'pref': '東京'},
    '品川': {'lat': 35.6284, 'lng': 139.7387, 'pref': '東京'},
    '目黒': {'lat': 35.6337, 'lng': 139.7158, 'pref': '東京'},
    '五反田': {'lat': 35.6262, 'lng': 139.7234, 'pref': '東京'},
    '大崎': {'lat': 35.6197, 'lng': 139.7283, 'pref': '東京'},
    '東京': {'lat': 35.6812, 'lng': 139.7671, 'pref': '東京'},
    '有楽町': {'lat': 35.6748, 'lng': 139.7630, 'pref': '東京'},
    '秋葉原': {'lat': 35.6984, 'lng': 139.7731, 'pref': '東京'},
    '上野': {'lat': 35.7141, 'lng': 139.7774, 'pref': '東京'},
    '浅草': {'lat': 35.7148, 'lng': 139.7967, 'pref': '東京'},
    '押上': {'lat': 35.7101, 'lng': 139.8136, 'pref': '東京'},
    '錦糸町': {'lat': 35.6966, 'lng': 139.8144, 'pref': '東京'},
    '亀戸': {'lat': 35.6969, 'lng': 139.8265, 'pref': '東京'},
    '北千住': {'lat': 35.7497, 'lng': 139.8049, 'pref': '東京'},
    '赤羽': {'lat': 35.7780, 'lng': 139.7209, 'pref': '東京'},
    '中野': {'lat': 35.7056, 'lng': 139.6656, 'pref': '東京'},
    '高円寺': {'lat': 35.7054, 'lng': 139.6496, 'pref': '東京'},
    '吉祥寺': {'lat': 35.7030, 'lng': 139.5796, 'pref': '東京'},
    '三鷹': {'lat': 35.7026, 'lng': 139.5606, 'pref': '東京'},
    '立川': {'lat': 35.6980, 'lng': 139.4143, 'pref': '東京'},
    '八王子': {'lat': 35.6554, 'lng': 139.3239, 'pref': '東京'},
    '町田': {'lat': 35.5424, 'lng': 139.4463, 'pref': '東京'},
    '東陽町': {'lat': 35.6699, 'lng': 139.8176, 'pref': '東京'},
    '門前仲町': {'lat': 35.6724, 'lng': 139.7963, 'pref': '東京'},
    '豊洲': {'lat': 35.6531, 'lng': 139.7967, 'pref': '東京'},
    '月島': {'lat': 35.6625, 'lng': 139.7834, 'pref': '東京'},
    '代官山': {'lat': 35.6486, 'lng': 139.7030, 'pref': '東京'},
    '中目黒': {'lat': 35.6443, 'lng': 139.6989, 'pref': '東京'},
    '自由が丘': {'lat': 35.6077, 'lng': 139.6686, 'pref': '東京'},
    '二子玉川': {'lat': 35.6115, 'lng': 139.6265, 'pref': '東京'},
    '下北沢': {'lat': 35.6614, 'lng': 139.6681, 'pref': '東京'},
    '三軒茶屋': {'lat': 35.6437, 'lng': 139.6702, 'pref': '東京'},
    # 神奈川
    '横浜': {'lat': 35.4437, 'lng': 139.6380, 'pref': '神奈川'},
    '川崎': {'lat': 35.5309, 'lng': 139.7030, 'pref': '神奈川'},
    'みなとみらい': {'lat': 35.4578, 'lng': 139.6325, 'pref': '神奈川'},
    '関内': {'lat': 35.4437, 'lng': 139.6380, 'pref': '神奈川'},
    '桜木町': {'lat': 35.4508, 'lng': 139.6310, 'pref': '神奈川'},
    '元町': {'lat': 35.4378, 'lng': 139.6508, 'pref': '神奈川'},
    '中華街': {'lat': 35.4421, 'lng': 139.6453, 'pref': '神奈川'},
    # 埼玉
    '大宮': {'lat': 35.9062, 'lng': 139.6237, 'pref': '埼玉'},
    '浦和': {'lat': 35.8585, 'lng': 139.6456, 'pref': '埼玉'},
    # 千葉
    '千葉': {'lat': 35.6073, 'lng': 140.1063, 'pref': '千葉'},
    '船橋': {'lat': 35.7015, 'lng': 139.9854, 'pref': '千葉'},
    '柏': {'lat': 35.8676, 'lng': 139.9757, 'pref': '千葉'},
}

# 定数
MAX_TOKENS = 4000

# ========================================
# ホットペッパーAPI 連携
# ========================================

def search_hotpepper(shop_name: str, area: str = '') -> str:
    """
    ホットペッパーAPIで店舗を検索して店舗ページURLを返す
    
    Args:
        shop_name: 店舗名
        area: エリア名
    
    Returns:
        店舗ページURL、または None
    """
    if not HOTPEPPER_API_KEY:
        logger.warning("[Hotpepper API] APIキーが設定されていません")
        return None
    
    # エリアデータを取得
    area_info = AREA_DATA.get(area, {})
    pref = area_info.get('pref', '')
    large_area = HOTPEPPER_AREA_CODES.get(pref, 'Z011')  # デフォルト東京
    
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
# Google Places API 連携
# ========================================

def search_place(shop_name: str, area: str = '') -> dict:
    """
    Google Places APIで店舗を検索
    
    Args:
        shop_name: 店舗名
        area: エリア名（例: "恵比寿"）
    
    Returns:
        店舗情報の辞書、または None
    """
    if not GOOGLE_PLACES_API_KEY:
        logger.warning("[Places API] APIキーが設定されていません")
        return None
    
    # エリアデータを取得
    area_info = AREA_DATA.get(area, {})
    pref = area_info.get('pref', '')
    
    # 検索クエリを構築（都道府県名を追加）
    if pref:
        query = f"{shop_name} {area} {pref}".strip()
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

        # エリアの座標があれば位置バイアスを追加（日本国内のエリア）
        if 'lat' in area_info and 'lng' in area_info:
            params['location'] = f"{area_info['lat']},{area_info['lng']}"
            params['radius'] = 3000  # 3km以内
            params['region'] = 'jp'  # 日本国内エリアの場合のみ
        elif area_info:
            # AREA_DATAに登録済み（日本国内）だが座標がない場合
            params['region'] = 'jp'
        # 海外や不明なエリアの場合は地域制限なし
        
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
            'maps_url': maps_url
        }
        
        logger.info(f"[Places API] 取得成功: {result['name']}")
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
    
    # エリアデータを取得
    area_info = AREA_DATA.get(area, {})
    pref = area_info.get('pref', '')
    
    for shop in shops:
        shop_name = shop.get('name', '')
        
        if not shop_name:
            continue
        
        place_data = search_place(shop_name, area)
        
        # Places APIで見つからない店舗は除外
        if not place_data:
            logger.warning(f"[Places API] 店舗が見つからないため除外: {shop_name}")
            continue
        
        # エリア/都道府県が異なる場合は除外
        if area:
            address = place_data.get('formatted_address', '')
            area_info = AREA_DATA.get(area, {})
            pref = area_info.get('pref', '')

            if pref:
                # 日本国内: 都道府県名が住所に含まれていない場合は除外
                if pref not in address:
                    logger.warning(f"[Places API] 都道府県不一致のため除外: {shop_name} (検索: {pref}, 住所: {address})")
                    continue
            else:
                # 海外/AREA_DATA未登録: エリア名が住所に含まれていない場合は除外
                # 英語表記のバリエーションも考慮
                area_lower = area.lower()
                address_lower = address.lower()
                if area_lower not in address_lower and area not in address:
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
        
        # ホットペッパーAPIで検索
        hotpepper_url = search_hotpepper(shop_name, area)
        
        # LLMの店舗名で見つからない場合、Places APIの店舗名で再検索
        if not hotpepper_url:
            places_name = place_data.get('name', '')
            if places_name and places_name != shop_name:
                logger.info(f"[Hotpepper API] Places名で再検索: {places_name}")
                hotpepper_url = search_hotpepper(places_name, area)
        
        if hotpepper_url:
            shop['hotpepper_url'] = hotpepper_url
        else:
            # APIで見つからない場合はGoogle検索
            hp_query = f"{shop_name}+{area}+ホットペッパーグルメ".replace(' ', '+')
            shop['hotpepper_url'] = f"https://www.google.com/search?q={hp_query}"
        
        # 食べログURL（tabelog.comの検索機能を使用、該当なしの場合はGoogle検索）
        places_name = place_data.get('name', '')
        pref = area_info.get('pref', '東京')
        
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
    テキストからエリア名を抽出
    """
    areas = [
        '恵比寿', '渋谷', '新宿', '池袋', '銀座', '六本木', '表参道', '原宿',
        '品川', '目黒', '五反田', '大崎', '東京', '有楽町', '秋葉原', '上野',
        '浅草', '押上', '錦糸町', '亀戸', '北千住', '赤羽', '中野', '高円寺',
        '吉祥寺', '三鷹', '立川', '八王子', '町田', '横浜', '川崎', '大宮',
        '浦和', '千葉', '船橋', '柏', '東陽町', '門前仲町', '豊洲', '月島',
        '代官山', '中目黒', '自由が丘', '二子玉川', '下北沢', '三軒茶屋'
    ]
    
    for area in areas:
        if area in text:
            return area
    
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
