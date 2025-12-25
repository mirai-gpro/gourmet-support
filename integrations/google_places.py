# -*- coding: utf-8 -*-
"""
Google Places API and Geocoding API integrations
"""
import logging
import requests
from config import GOOGLE_PLACES_API_KEY, GOOGLE_GEOCODING_API_KEY

logger = logging.getLogger(__name__)

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
