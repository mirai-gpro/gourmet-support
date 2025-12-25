# -*- coding: utf-8 -*-
"""
External API integrations (HotPepper, TripAdvisor)
"""
import logging
import requests
from config import (
    HOTPEPPER_API_KEY,
    HOTPEPPER_AREA_CODES,
    TRIPADVISOR_API_KEY,
    MY_DOMAIN_URL,
    USER_AGENT
)

logger = logging.getLogger(__name__)

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
