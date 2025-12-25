# -*- coding: utf-8 -*-
"""
外部API連携モジュール
- HotPepper API
- TripAdvisor API
- Google Geocoding API
- Google Places API  
- ショップ情報エンリッチメント
"""
import os
import re
import logging
import requests

# ロギング
logger = logging.getLogger(__name__)

# ========================================
# API Keys & Constants
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# エリアコード
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
        return {'phone': None, 'country_code': None, 'photos': None, 'formatted_address': None}

    try:
        details_url = 'https://maps.googleapis.com/maps/api/place/details/json'
        params = {
            'place_id': place_id,
            'fields': 'formatted_phone_number,international_phone_number,address_components,photos,formatted_address',
            'key': GOOGLE_PLACES_API_KEY,
            'language': language
        }

        response = requests.get(details_url, params=params, timeout=10)
        data = response.json()

        if data.get('status') != 'OK':
            logger.warning(f"[Place Details API] å–å¾—å¤±æ•—: {data.get('status')} - {place_id}")
            return {'phone': None, 'country_code': None, 'photos': None, 'formatted_address': None}

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

        # 写真取得
        photos = result.get('photos')
        
        # 住所取得
        formatted_address = result.get('formatted_address')

        if phone or photos or formatted_address:
            logger.info(f"[Place Details API] 取得成功: 電話={phone}, 国={country_code}, 写真={'あり' if photos else 'なし'}, 住所={'あり' if formatted_address else 'なし'}")

        return {
            'phone': phone, 
            'country_code': country_code,
            'photos': photos,
            'formatted_address': formatted_address
        }


    except requests.exceptions.Timeout:
        logger.error(f"[Place Details API] ã‚¿ã‚¤ãƒ ã‚¢ã‚¦ãƒˆ: {place_id}")
        return {'phone': None, 'country_code': None, 'photos': None, 'formatted_address': None}
    except Exception as e:
        logger.error(f"[Place Details API] ã‚¨ãƒ©ãƒ¼: {e}")
        return {'phone': None, 'country_code': None, 'photos': None, 'formatted_address': None}


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
    logger.info(f"[Places API] 📍 検索開始: shop_name='{shop_name}', area='{area}', region='{region}', expected_country={expected_country}")

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
        results_count = len(data.get('results', []))
        logger.info(f"[Places API] 📊 検索結果: {results_count}件ヒット")

        place_id = place['place_id']

        logger.info(f"[Places API] 🏆 1番目の結果: name='{place.get('name')}', address='{place.get('formatted_address', '')[:50]}...'")
        maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"

        # 座標を取得
        geometry = place.get('geometry', {})
        location = geometry.get('location', {})
        lat = location.get('lat')
        lng = location.get('lng')

        # ✅ Place Details APIで電話番号、国コード、写真、住所を取得
        details = get_place_details(place_id, language)
        actual_country = details.get('country_code')

        # 📷 画像URLを生成（Text Search API → Place Details API の順で試行）
        photo_url = None
        photos_source = place.get('photos') or details.get('photos')
        if photos_source:
            photo_reference = photos_source[0]['photo_reference']
            photo_url = (
                f"https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=800"
                f"&photo_reference={photo_reference}"
                f"&key={GOOGLE_PLACES_API_KEY}"
            )
            logger.info(f"[Places API] 📷 写真取得元: {'Text Search' if place.get('photos') else 'Place Details'}")
        else:
            logger.warning(f"[Places API] ⚠️ 写真データなし: {place.get('name')}")



        logger.info(f"[Places API] 🌍 国コード検証: expected={expected_country}, actual={actual_country}")

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
            'formatted_address': place.get('formatted_address') or details.get('formatted_address'),
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
    enriched_shops = []
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
        # 店舗ごとのエリアを使用（LLMのJSONから取得）
        shop_area = shop.get('area', '') or area  # LLMのareaを優先、なければグローバルのareaを使用
        logger.info(f"[Enrich] → 使用エリア: '{shop_area}'")
        place_data = search_place(shop_name, shop_area, geo_info, language)
        
        if not place_data:
            logger.warning(f"[Enrich] Places APIで見つからない。LLMデータをそのまま使用: {shop_name}")
            validation_failed_count += 1
            enriched_shops.append(shop)  # LLMデータを保持
            continue

        place_id = place_data.get('place_id')
        place_name = place_data.get('name')
        
        logger.info(f"[Enrich] â†’ æ¤œç´¢çµæžœ: '{place_name}'")
        logger.info(f"[Enrich] â†’ place_id: {place_id}")
        logger.info(f"[Enrich] → photo_url: {place_data.get('photo_url', 'なし')}")

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
