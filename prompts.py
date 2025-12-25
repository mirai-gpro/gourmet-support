# -*- coding: utf-8 -*-
"""
Prompt loading module for Gourmet Customer Support System
Handles loading system prompts from GCS or local files
"""
import json
import logging
from google.cloud import storage
from config import PROMPTS_BUCKET_NAME

logger = logging.getLogger(__name__)

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
        if not PROMPTS_BUCKET_NAME:
            logger.warning("[Prompt] PROMPTS_BUCKET_NAME が設定されていません。ローカルファイルを使用します。")
            return None

        client = storage.Client()
        bucket = client.bucket(PROMPTS_BUCKET_NAME)
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

def load_system_prompts():
    """
    システムプロンプトを読み込み (GCS優先、ローカルフォールバック)
    """
    logger.info("[Prompt] プロンプト読み込み開始...")
    prompts = load_prompts_from_gcs()
    if not prompts:
        logger.info("[Prompt] GCSから読み込めませんでした。ローカルファイルを使用します。")
        prompts = load_prompts_from_local()

    if not prompts or (not prompts.get('chat') and not prompts.get('concierge')):
        logger.error("[Prompt] プロンプトの読み込みに失敗しました!")
        prompts = {
            'chat': {'ja': 'エラー: チャットモードプロンプトが読み込めませんでした。'},
            'concierge': {'ja': 'エラー: コンシェルジュモードプロンプトが読み込めませんでした。'}
        }
    else:
        logger.info(f"[Prompt] プロンプト読み込み完了:")
        logger.info(f"  - チャットモード: {list(prompts.get('chat', {}).keys())}")
        logger.info(f"  - コンシェルジュモード: {list(prompts.get('concierge', {}).keys())}")

    return prompts
