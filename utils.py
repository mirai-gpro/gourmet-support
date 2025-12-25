# -*- coding: utf-8 -*-
"""
Utility functions for text processing and data extraction
"""
import re
import logging

logger = logging.getLogger(__name__)

def extract_area_from_text(text: str, language: str = 'ja') -> str:
    """
    テキストからエリア名を抽出(Geocoding APIで動的に検証)
    """
    # Avoid circular import - import here
    from integrations.google_places import get_region_from_area

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
