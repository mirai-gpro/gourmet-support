# -*- coding: utf-8 -*-
"""
Session management module for Gourmet Customer Support System
Handles RAM-based session storage and management
"""
import uuid
import logging
from datetime import datetime
from google.genai import types

logger = logging.getLogger(__name__)

# ========================================
# RAMベースのセッション管理 (Firestore完全廃止)
# ========================================
_SESSION_CACHE = {}


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
        """メッセージを追加(役割(Role)別の構造で保存)"""
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
        """SDKにそのまま渡せる形式のリストを返す(types.Contentオブジェクトのリスト)"""
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
        """メッセージ履歴を取得(互換性のため残す)"""
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
