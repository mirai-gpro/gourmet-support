# -*- coding: utf-8 -*-
"""
Shop enrichment logic - adds external API data to shop listings
"""
import logging
import requests
from config import TRIPADVISOR_API_KEY
from integrations.google_places import get_region_from_area, search_place
from integrations.external_apis import search_hotpepper, get_tripadvisor_data

logger = logging.getLogger(__name__)

# ========================================
# ショップ情報 拡張ロジック (刷新版)
# ========================================

def enrich_shops_with_photos(shops: list, area: str = '', language: str = 'ja') -> list:
    """
    ショップリストに外部APIデータを追加 (place_id重複排除付き、国コード検証強化版)
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
