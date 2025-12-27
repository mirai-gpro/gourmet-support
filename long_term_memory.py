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

    def get_profile(self, session_id: str) -> Optional[Dict[str, Any]]:
        """プロファイル取得"""
        try:
            response = self.client.table('user_profiles').select('*').eq('session_id', session_id).execute()

            if response.data and len(response.data) > 0:
                logger.info(f"[LTM] プロファイル取得成功: {session_id}")
                return response.data[0]
            else:
                logger.info(f"[LTM] プロファイル未登録: {session_id}")
                return None
        except Exception as e:
            logger.error(f"[LTM] プロファイル取得エラー: {e}")
            return None

    def create_profile(self, session_id: str, data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """新規プロファイル作成"""
        try:
            profile_data = {
                'session_id': session_id,
                'user_id': session_id,  # user_idカラムにも同じ値を設定
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
                logger.info(f"[LTM] プロファイル作成成功: {session_id}")
                return response.data[0]
            else:
                logger.error(f"[LTM] プロファイル作成失敗: {session_id}")
                return None
        except Exception as e:
            logger.error(f"[LTM] プロファイル作成エラー: {e}")
            return None

    def update_profile(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """プロファイル更新"""
        try:
            # last_visit_atは常に更新
            updates['last_visit_at'] = datetime.now().isoformat()

            response = self.client.table('user_profiles').update(updates).eq('session_id', session_id).execute()

            if response.data:
                logger.info(f"[LTM] プロファイル更新成功: {session_id}")
                return True
            else:
                logger.error(f"[LTM] プロファイル更新失敗: {session_id}")
                return False
        except Exception as e:
            logger.error(f"[LTM] プロファイル更新エラー: {e}")
            return False

    def increment_visit_count(self, session_id: str) -> bool:
        """訪問回数をインクリメント"""
        try:
            profile = self.get_profile(session_id)
            if not profile:
                return False

            new_count = profile.get('visit_count', 0) + 1
            return self.update_profile(session_id, {'visit_count': new_count})
        except Exception as e:
            logger.error(f"[LTM] 訪問回数更新エラー: {e}")
            return False

    def get_or_create_profile(self, session_id: str, initial_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """プロファイル取得または作成"""
        profile = self.get_profile(session_id)

        if profile:
            # 既存プロファイルの場合、訪問回数を更新
            self.increment_visit_count(session_id)
            # 最新情報を再取得
            return self.get_profile(session_id)
        else:
            # 新規作成
            return self.create_profile(session_id, initial_data)

    def is_first_visit(self, session_id: str) -> bool:
        """初回訪問かどうか判定"""
        profile = self.get_profile(session_id)
        return profile is None or profile.get('preferred_name') is None

    # ----------------------------------------
    # 好み・傾向の管理
    # ----------------------------------------

    def get_all_preferences(self, session_id: str) -> List[Dict[str, Any]]:
        """全ての好みを取得"""
        try:
            response = self.client.table('user_preferences').select('*').eq('session_id', session_id).execute()

            if response.data:
                logger.info(f"[LTM] 好み取得成功: {session_id}, {len(response.data)}件")
                return response.data
            else:
                logger.info(f"[LTM] 好みデータなし: {session_id}")
                return []
        except Exception as e:
            logger.error(f"[LTM] 好み取得エラー: {e}")
            return []

    def upsert_preference(
        self,
        session_id: str,
        category: str,
        preference_key: str,
        preference_value: str,
        confidence_score: float = 1.0,
        importance_level: str = 'medium'
    ) -> bool:
        """好みを追加または更新（UPSERT）"""
        try:
            # 既存のデータを確認
            existing = self.client.table('user_preferences').select('*').eq(
                'session_id', session_id
            ).eq('category', category).eq('preference_key', preference_key).execute()

            if existing.data and len(existing.data) > 0:
                # 既存データがある場合は更新
                old_data = existing.data[0]
                new_mention_count = old_data.get('mention_count', 1) + 1

                # confidence_scoreは平均を取る
                old_confidence = old_data.get('confidence_score', 1.0)
                new_confidence = (old_confidence + confidence_score) / 2

                update_data = {
                    'preference_value': preference_value,
                    'confidence_score': new_confidence,
                    'importance_level': importance_level,
                    'last_mentioned_at': datetime.now().isoformat(),
                    'mention_count': new_mention_count
                }

                response = self.client.table('user_preferences').update(update_data).eq(
                    'session_id', session_id
                ).eq('category', category).eq('preference_key', preference_key).execute()

                logger.info(f"[LTM] 好み更新: {session_id}, {category}/{preference_key}, 言及{new_mention_count}回")
            else:
                # 新規追加
                insert_data = {
                    'session_id': session_id,
                    'category': category,
                    'preference_key': preference_key,
                    'preference_value': preference_value,
                    'confidence_score': confidence_score,
                    'importance_level': importance_level,
                    'detected_at': datetime.now().isoformat(),
                    'last_mentioned_at': datetime.now().isoformat(),
                    'mention_count': 1
                }

                response = self.client.table('user_preferences').insert(insert_data).execute()
                logger.info(f"[LTM] 好み追加: {session_id}, {category}/{preference_key}")

            return True
        except Exception as e:
            logger.error(f"[LTM] 好みUPSERTエラー: {e}")
            return False

    def get_critical_preferences(self, session_id: str) -> List[Dict[str, Any]]:
        """重要度がcriticalの好みのみ取得（アレルギー等）"""
        try:
            response = self.client.table('user_preferences').select('*').eq(
                'session_id', session_id
            ).eq('importance_level', 'critical').execute()

            return response.data if response.data else []
        except Exception as e:
            logger.error(f"[LTM] 重要な好み取得エラー: {e}")
            return []

    # ----------------------------------------
    # 訪問履歴の管理
    # ----------------------------------------

    def save_interaction_history(
        self,
        session_id: str,
        actual_session_id: str,
        language: str,
        mode: str,
        conversation_summary: str = None,
        searched_areas: List[str] = None,
        recommended_shops: List[Dict] = None,
        session_started_at: datetime = None,
        session_ended_at: datetime = None
    ) -> bool:
        """訪問履歴を保存"""
        try:
            duration = None
            if session_started_at and session_ended_at:
                duration = int((session_ended_at - session_started_at).total_seconds())

            history_data = {
                'session_id': session_id,
                'actual_session_id': actual_session_id,
                'language': language,
                'mode': mode,
                'conversation_summary': conversation_summary,
                'searched_areas': json.dumps(searched_areas) if searched_areas else None,
                'recommended_shops': json.dumps(recommended_shops) if recommended_shops else None,
                'session_started_at': session_started_at.isoformat() if session_started_at else None,
                'session_ended_at': session_ended_at.isoformat() if session_ended_at else None,
                'duration_seconds': duration
            }

            response = self.client.table('user_interaction_history').insert(history_data).execute()

            if response.data:
                logger.info(f"[LTM] 訪問履歴保存成功: {session_id}")
                return True
            else:
                logger.error(f"[LTM] 訪問履歴保存失敗: {session_id}")
                return False
        except Exception as e:
            logger.error(f"[LTM] 訪問履歴保存エラー: {e}")
            return False

    # ----------------------------------------
    # システムプロンプト生成
    # ----------------------------------------

    def generate_system_prompt_context(self, session_id: str, language: str = 'ja') -> str:
        """システムプロンプトに注入するコンテキストを生成"""
        profile = self.get_profile(session_id)
        if not profile:
            return ""

        preferences = self.get_all_preferences(session_id)

        # 言語別のテンプレート
        if language == 'ja':
            return self._generate_context_ja(profile, preferences)
        elif language == 'en':
            return self._generate_context_en(profile, preferences)
        elif language == 'zh':
            return self._generate_context_zh(profile, preferences)
        elif language == 'ko':
            return self._generate_context_ko(profile, preferences)
        else:
            return self._generate_context_ja(profile, preferences)

    def _generate_context_ja(self, profile: Dict, preferences: List[Dict]) -> str:
        """日本語コンテキスト生成"""
        context_parts = []

        # ユーザー情報
        context_parts.append("【ユーザー情報】")
        preferred_name = profile.get('preferred_name', '')
        name_honorific = profile.get('name_honorific', '')
        if preferred_name:
            context_parts.append(f"- 呼び方: {preferred_name}{name_honorific}")
        context_parts.append(f"- 訪問回数: {profile.get('visit_count', 1)}回目")

        # アレルギー・重要な制約
        critical_prefs = [p for p in preferences if p.get('importance_level') == 'critical']
        if critical_prefs:
            context_parts.append("\n【重要な制約・アレルギー】")
            for pref in critical_prefs:
                context_parts.append(f"⚠️ {pref.get('preference_value', '')}（必ず回避すること）")

        # 食の好み
        food_prefs = [p for p in preferences if p.get('category') == 'food_type']
        if food_prefs:
            context_parts.append("\n【食の好み】")
            for pref in sorted(food_prefs, key=lambda x: x.get('mention_count', 0), reverse=True)[:5]:
                mention = pref.get('mention_count', 1)
                value = pref.get('preference_value', '')
                context_parts.append(f"✓ {value}（言及回数: {mention}回）")

        # よく使うエリア
        area_prefs = [p for p in preferences if p.get('category') == 'area']
        if area_prefs:
            context_parts.append("\n【よく使うエリア】")
            for pref in sorted(area_prefs, key=lambda x: x.get('mention_count', 0), reverse=True)[:3]:
                mention = pref.get('mention_count', 1)
                value = pref.get('preference_value', '')
                context_parts.append(f"- {value}（{mention}回）")

        # その他の傾向
        other_prefs = [p for p in preferences if p.get('category') in ['budget', 'atmosphere', 'other']]
        if other_prefs:
            context_parts.append("\n【その他の傾向】")
            for pref in other_prefs[:3]:
                context_parts.append(f"- {pref.get('preference_value', '')}")

        return "\n".join(context_parts)

    def _generate_context_en(self, profile: Dict, preferences: List[Dict]) -> str:
        """英語コンテキスト生成"""
        context_parts = []

        context_parts.append("【User Information】")
        preferred_name = profile.get('preferred_name', '')
        name_honorific = profile.get('name_honorific', '')
        if preferred_name:
            context_parts.append(f"- Address as: {preferred_name}{name_honorific}")
        context_parts.append(f"- Visit count: {profile.get('visit_count', 1)} visit(s)")

        critical_prefs = [p for p in preferences if p.get('importance_level') == 'critical']
        if critical_prefs:
            context_parts.append("\n【Critical Constraints & Allergies】")
            for pref in critical_prefs:
                context_parts.append(f"⚠️ {pref.get('preference_value', '')} (MUST avoid)")

        food_prefs = [p for p in preferences if p.get('category') == 'food_type']
        if food_prefs:
            context_parts.append("\n【Food Preferences】")
            for pref in sorted(food_prefs, key=lambda x: x.get('mention_count', 0), reverse=True)[:5]:
                mention = pref.get('mention_count', 1)
                value = pref.get('preference_value', '')
                context_parts.append(f"✓ {value} (mentioned {mention} time(s))")

        return "\n".join(context_parts)

    def _generate_context_zh(self, profile: Dict, preferences: List[Dict]) -> str:
        """中国語コンテキスト生成"""
        # 簡体字中国語向け
        context_parts = []
        context_parts.append("【用户信息】")
        preferred_name = profile.get('preferred_name', '')
        name_honorific = profile.get('name_honorific', '')
        if preferred_name:
            context_parts.append(f"- 称呼: {preferred_name}{name_honorific}")
        context_parts.append(f"- 访问次数: 第{profile.get('visit_count', 1)}次")

        critical_prefs = [p for p in preferences if p.get('importance_level') == 'critical']
        if critical_prefs:
            context_parts.append("\n【重要限制・过敏】")
            for pref in critical_prefs:
                context_parts.append(f"⚠️ {pref.get('preference_value', '')}（必须避免）")

        return "\n".join(context_parts)

    def _generate_context_ko(self, profile: Dict, preferences: List[Dict]) -> str:
        """韓国語コンテキスト生成"""
        context_parts = []
        context_parts.append("【사용자 정보】")
        preferred_name = profile.get('preferred_name', '')
        name_honorific = profile.get('name_honorific', '')
        if preferred_name:
            context_parts.append(f"- 호칭: {preferred_name}{name_honorific}")
        context_parts.append(f"- 방문 횟수: {profile.get('visit_count', 1)}회")

        critical_prefs = [p for p in preferences if p.get('importance_level') == 'critical']
        if critical_prefs:
            context_parts.append("\n【중요 제약・알레르기】")
            for pref in critical_prefs:
                context_parts.append(f"⚠️ {pref.get('preference_value', '')} (반드시 회피)")

        return "\n".join(context_parts)


# ========================================
# 好み自動抽出ロジック
# ========================================

class PreferenceExtractor:
    """会話から好み・傾向を自動抽出"""

    # 正規表現パターン（日本語）
    PATTERNS_JA = {
        'food_like': r'([\w]+)(?:が|は)(好き|大好き|気に入って|お気に入り)',
        'food_dislike': r'([\w]+)(?:が|は)(苦手|嫌い|ダメ|食べられない)',
        'allergy': r'([\w]+)(?:の)?アレルギー',
        'area': r'([\w]+)(?:に|で|の)(住んで|よく行く|いつも|通って)',
    }

    @staticmethod
    def extract_from_text(text: str, language: str = 'ja') -> List[Dict[str, Any]]:
        """テキストから好みを抽出（正規表現ベース）"""
        results = []

        if language == 'ja':
            # 好きな食べ物
            for match in re.finditer(PreferenceExtractor.PATTERNS_JA['food_like'], text):
                food_item = match.group(1)
                results.append({
                    'category': 'food_type',
                    'preference_key': f'{food_item}_like',
                    'preference_value': f'{food_item}が好き',
                    'confidence_score': 0.8,
                    'importance_level': 'medium'
                })

            # 苦手な食べ物
            for match in re.finditer(PreferenceExtractor.PATTERNS_JA['food_dislike'], text):
                food_item = match.group(1)
                results.append({
                    'category': 'food_type',
                    'preference_key': f'{food_item}_dislike',
                    'preference_value': f'{food_item}が苦手',
                    'confidence_score': 0.8,
                    'importance_level': 'medium'
                })

            # アレルギー
            for match in re.finditer(PreferenceExtractor.PATTERNS_JA['allergy'], text):
                allergen = match.group(1)
                results.append({
                    'category': 'allergy',
                    'preference_key': f'{allergen}_allergy',
                    'preference_value': f'{allergen}アレルギー',
                    'confidence_score': 1.0,
                    'importance_level': 'critical'
                })

            # エリア
            for match in re.finditer(PreferenceExtractor.PATTERNS_JA['area'], text):
                area = match.group(1)
                results.append({
                    'category': 'area',
                    'preference_key': f'{area}_area',
                    'preference_value': f'{area}エリアをよく利用',
                    'confidence_score': 0.7,
                    'importance_level': 'medium'
                })

        return results

    @staticmethod
    def extract_and_save(session_id: str, text: str, language: str = 'ja') -> int:
        """抽出して自動保存"""
        ltm = LongTermMemory()
        preferences = PreferenceExtractor.extract_from_text(text, language)

        saved_count = 0
        for pref in preferences:
            success = ltm.upsert_preference(
                session_id=session_id,
                category=pref['category'],
                preference_key=pref['preference_key'],
                preference_value=pref['preference_value'],
                confidence_score=pref['confidence_score'],
                importance_level=pref['importance_level']
            )
            if success:
                saved_count += 1

        if saved_count > 0:
            logger.info(f"[Extractor] {saved_count}件の好みを自動保存: {session_id}")

        return saved_count


# ========================================
# ユーティリティ関数
# ========================================

def extract_name_from_text(text: str) -> Optional[str]:
    """テキストから名前を抽出"""
    # パターン1: 「名前を〜に変更」「登録名を〜に変えて」（名前変更）
    match = re.search(r'(?:名前|登録名)(?:を)?([ぁ-んァ-ヶー\u4e00-\u9fafA-Za-z\s]+?)(?:に)?(?:変更|変えて)', text)
    if match:
        name = match.group(1).strip()
        if 2 <= len(name) <= 20:
            return name

    # パターン2: 「〜と申します」「〜と言います」（フォーマルな自己紹介）
    match = re.search(r'^?([ぁ-んァ-ヶー\u4e00-\u9fafA-Za-z\s]+?)(?:と申します|と言います|といいます|と言う|という)', text)
    if match:
        name = match.group(1).strip()
        if 2 <= len(name) <= 20:
            return name

    # パターン3: 「〜と呼んで」
    match = re.search(r'([^\s、。]+)(?:と|って)(?:呼んで|呼ばれ)', text)
    if match:
        return match.group(1)

    # パターン4: 「名前は〜」（名前部分のみ抽出）
    match = re.search(r'名前は([ぁ-んァ-ヶー\u4e00-\u9fafA-Za-z\s]+?)(?:です|と申します|と言います|$|。)', text)
    if match:
        name = match.group(1).strip()
        if 2 <= len(name) <= 20:
            return name

    # パターン5: 「〜です」「〜だよ」などの文末パターン
    match = re.search(r'^([ぁ-んァ-ヶー\u4e00-\u9fafA-Za-z]{2,10})(?:です|だよ|っす|やで)?[。!！]*$', text.strip())
    if match:
        return match.group(1)

    # パターン6: 単独の名前（ひらがな・カタカナ・漢字・アルファベット2-10文字）
    match = re.search(r'^([ぁ-んァ-ヶー\u4e00-\u9fafA-Za-z]{2,10})$', text.strip())
    if match:
        return match.group(1)

    return None
