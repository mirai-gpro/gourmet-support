# -*- coding: utf-8 -*-
"""
長期記憶管理モジュール
- Supabaseとの接続・CRUD操作
- ユーザープロファイル管理
- 好み・傾向の自動抽出・保存
"""
import os
import re
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List, Any
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# ========================================
# Supabaseクライアント初期化
# ========================================

_supabase_client: Optional[Client] = None

def get_supabase_client() -> Client:
    """Supabaseクライアントを取得（シングルトン）"""
    global _supabase_client

    if _supabase_client is None:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")

        if not supabase_url or not supabase_key:
            logger.error("[LTM] SUPABASE_URL または SUPABASE_KEY が設定されていません")
            raise ValueError("Supabase credentials not configured")

        _supabase_client = create_client(supabase_url, supabase_key)
        logger.info("[LTM] Supabaseクライアント初期化完了")

    return _supabase_client


# ========================================
# ユーザープロファイル管理
# ========================================

class LongTermMemory:
    """長期記憶管理クラス"""

    def __init__(self):
        self.client = get_supabase_client()

    # ----------------------------------------
    # プロファイル操作
    # ----------------------------------------

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """プロファイル取得"""
        try:
            response = self.client.table('user_profiles').select('*').eq('user_id', user_id).execute()

            if response.data and len(response.data) > 0:
                logger.info(f"[LTM] プロファイル取得成功: {user_id}")
                return response.data[0]
            else:
                logger.info(f"[LTM] プロファイル未登録: {user_id}")
                return None
        except Exception as e:
            logger.error(f"[LTM] プロファイル取得エラー: {e}")
            return None

    def create_profile(self, user_id: str, data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """新規プロファイル作成"""
        try:
            profile_data = {
                'user_id': user_id,
                'preferred_name': data.get('preferred_name') if data else None,
                'name_honorific': data.get('name_honorific', '') if data else '',
                'default_language': data.get('language', 'ja') if data else 'ja',
                'preferred_mode': data.get('mode', 'chat') if data else 'chat',
                'first_visit_at': datetime.now().isoformat(),
                'last_visit_at': datetime.now().isoformat(),
                'visit_count': 1
            }

            response = self.client.table('user_profiles').insert(profile_data).execute()

            if response.data and len(response.data) > 0:
                logger.info(f"[LTM] プロファイル作成成功: {user_id}")
                return response.data[0]
            else:
                logger.error(f"[LTM] プロファイル作成失敗: {user_id}")
                return None
        except Exception as e:
            logger.error(f"[LTM] プロファイル作成エラー: {e}")
            return None

    def update_profile(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """プロファイル更新"""
        try:
            # last_visit_atは常に更新
            updates['last_visit_at'] = datetime.now().isoformat()

            response = self.client.table('user_profiles').update(updates).eq('user_id', user_id).execute()

            if response.data:
                logger.info(f"[LTM] プロファイル更新成功: {user_id}")
                return True
            else:
                logger.error(f"[LTM] プロファイル更新失敗: {user_id}")
                return False
        except Exception as e:
            logger.error(f"[LTM] プロファイル更新エラー: {e}")
            return False

    def increment_visit_count(self, user_id: str) -> bool:
        """訪問回数をインクリメント"""
        try:
            profile = self.get_profile(user_id)
            if not profile:
                return False

            new_count = profile.get('visit_count', 0) + 1
            return self.update_profile(user_id, {'visit_count': new_count})
        except Exception as e:
            logger.error(f"[LTM] 訪問回数更新エラー: {e}")
            return False

    def get_or_create_profile(self, user_id: str, initial_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """プロファイル取得または作成"""
        profile = self.get_profile(user_id)

        if profile:
            # 既存プロファイルの場合、訪問回数を更新
            self.increment_visit_count(user_id)
            # 最新情報を再取得
            return self.get_profile(user_id)
        else:
            # 新規作成
            return self.create_profile(user_id, initial_data)

    def is_first_visit(self, user_id: str) -> bool:
        """初回訪問かどうか判定（レコードの存在のみで判定）"""
        profile = self.get_profile(user_id)
        return profile is None

    # ----------------------------------------
    # サマリー管理（新規実装）
    # ----------------------------------------

    def generate_current_summary(self, conversation_history: List[Dict[str, Any]]) -> str:
        """今回の会話ログからサマリーを生成（LLM使用）"""
        # TODO: Gemini API を使用してサマリー生成
        # 現在は簡易実装（後で実装）
        logger.info("[LTM] generate_current_summary は未実装（TODO）")
        return ""

    def merge_summaries(self, previous_summary: str, current_summary: str) -> str:
        """前回サマリーと今回サマリーをマージ（LLM使用、重複除去）"""
        # TODO: Gemini API を使用してマージ
        # 現在は簡易実装（後で実装）
        logger.info("[LTM] merge_summaries は未実装（TODO）")
        if previous_summary and current_summary:
            return f"{previous_summary}\n\n{current_summary}"
        return previous_summary or current_summary
