-- ========================================
-- 長期記憶システム データベーススキーマ
-- Supabase (PostgreSQL)
-- ========================================

-- ========================================
-- テーブル1: user_profiles
-- ユーザーの基本プロファイル
-- ========================================
CREATE TABLE IF NOT EXISTS user_profiles (
    -- 識別子
    session_id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255),  -- 将来のGoogle認証用（現在はNULL）

    -- 呼び方設定
    preferred_name VARCHAR(100),           -- 「ちゃっぴー」
    name_honorific VARCHAR(20) DEFAULT '', -- 「様」「さん」「くん」「ちゃん」または空文字

    -- 基本情報
    default_language VARCHAR(10) DEFAULT 'ja',  -- 'ja', 'en', 'zh', 'ko'
    preferred_mode VARCHAR(20) DEFAULT 'chat',  -- 'chat', 'concierge'

    -- タイムスタンプ
    first_visit_at TIMESTAMPTZ DEFAULT NOW(),
    last_visit_at TIMESTAMPTZ DEFAULT NOW(),
    visit_count INTEGER DEFAULT 1,

    -- メタデータ
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_last_visit ON user_profiles(last_visit_at);

-- ========================================
-- テーブル2: user_preferences
-- 食の好み・傾向
-- ========================================
CREATE TABLE IF NOT EXISTS user_preferences (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(255) REFERENCES user_profiles(session_id) ON DELETE CASCADE,

    -- 好みカテゴリ
    category VARCHAR(50) NOT NULL,  -- 'food_type', 'allergy', 'area', 'budget', 'atmosphere', 'other'
    preference_key VARCHAR(100) NOT NULL,  -- 'spicy_food', 'peanut_allergy', 'shibuya_area' など
    preference_value TEXT,  -- 具体的な値やメモ

    -- 重要度・信頼度
    confidence_score FLOAT DEFAULT 1.0,  -- 0.0-1.0: AIが判断した確信度
    importance_level VARCHAR(20) DEFAULT 'medium',  -- 'low', 'medium', 'high', 'critical'

    -- メタデータ
    detected_at TIMESTAMPTZ DEFAULT NOW(),  -- 初めて検出された日時
    last_mentioned_at TIMESTAMPTZ DEFAULT NOW(),  -- 最後に言及された日時
    mention_count INTEGER DEFAULT 1,  -- 言及された回数

    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- ユニーク制約（同じカテゴリ・キーの重複を防ぐ）
    UNIQUE(session_id, category, preference_key)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_user_preferences_session ON user_preferences(session_id);
CREATE INDEX IF NOT EXISTS idx_user_preferences_category ON user_preferences(category);
CREATE INDEX IF NOT EXISTS idx_user_preferences_importance ON user_preferences(importance_level);

-- ========================================
-- テーブル3: user_interaction_history
-- 訪問履歴・行動ログ（分析用）
-- ========================================
CREATE TABLE IF NOT EXISTS user_interaction_history (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(255) REFERENCES user_profiles(session_id) ON DELETE CASCADE,

    -- セッション情報
    actual_session_id VARCHAR(255),  -- その時のセッションID
    language VARCHAR(10),
    mode VARCHAR(20),

    -- 会話サマリー
    conversation_summary TEXT,  -- AI生成のサマリー
    inquiry_type VARCHAR(50),   -- 'restaurant_search', 'recommendation', 'reservation' など

    -- 検索・推薦結果
    searched_areas JSONB,       -- ['渋谷', '新宿']
    searched_cuisines JSONB,    -- ['イタリアン', '寿司']
    recommended_shops JSONB,    -- [{ shop_id, name, ... }]

    -- タイムスタンプ
    session_started_at TIMESTAMPTZ,
    session_ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_interaction_history_session ON user_interaction_history(session_id);
CREATE INDEX IF NOT EXISTS idx_interaction_history_started ON user_interaction_history(session_started_at);

-- ========================================
-- 自動更新トリガー (updated_at)
-- ========================================

-- user_profiles用のトリガー関数
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- user_profiles トリガー
DROP TRIGGER IF EXISTS update_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER update_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- user_preferences トリガー
DROP TRIGGER IF EXISTS update_user_preferences_updated_at ON user_preferences;
CREATE TRIGGER update_user_preferences_updated_at
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ========================================
-- Row Level Security (RLS) 設定
-- 注意: 実運用前に適切に設定してください
-- ========================================

-- RLSを有効化（必要に応じてコメント解除）
-- ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE user_interaction_history ENABLE ROW LEVEL SECURITY;

-- ========================================
-- サンプルデータ挿入（テスト用）
-- ========================================

-- サンプルユーザー1
INSERT INTO user_profiles (session_id, preferred_name, name_honorific, default_language, visit_count)
VALUES ('test-session-001', 'ちゃっぴー', '', 'ja', 3)
ON CONFLICT (session_id) DO NOTHING;

INSERT INTO user_preferences (session_id, category, preference_key, preference_value, confidence_score, importance_level, mention_count)
VALUES
    ('test-session-001', 'food_type', 'spicy_food', '辛い料理が好き', 0.9, 'high', 5),
    ('test-session-001', 'allergy', 'peanut_allergy', 'ピーナッツアレルギー', 1.0, 'critical', 2),
    ('test-session-001', 'area', 'shibuya_area', '渋谷エリアをよく利用', 0.8, 'medium', 7)
ON CONFLICT (session_id, category, preference_key) DO NOTHING;

-- サンプルユーザー2（英語ユーザー）
INSERT INTO user_profiles (session_id, preferred_name, name_honorific, default_language, visit_count)
VALUES ('test-session-002', 'John', '-san', 'en', 2)
ON CONFLICT (session_id) DO NOTHING;

INSERT INTO user_preferences (session_id, category, preference_key, preference_value, confidence_score, importance_level, mention_count)
VALUES
    ('test-session-002', 'food_type', 'vegetarian', 'Vegetarian diet', 1.0, 'critical', 3),
    ('test-session-002', 'area', 'shinjuku_area', 'Frequently visits Shinjuku', 0.7, 'medium', 4)
ON CONFLICT (session_id, category, preference_key) DO NOTHING;

-- ========================================
-- 便利なクエリ（テスト・デバッグ用）
-- ========================================

-- 全ユーザープロファイル表示
-- SELECT * FROM user_profiles ORDER BY last_visit_at DESC;

-- 特定ユーザーの全好み表示
-- SELECT * FROM user_preferences WHERE session_id = 'test-session-001' ORDER BY importance_level DESC, mention_count DESC;

-- アレルギー情報のみ表示
-- SELECT up.preferred_name, pref.preference_key, pref.preference_value
-- FROM user_profiles up
-- JOIN user_preferences pref ON up.session_id = pref.session_id
-- WHERE pref.category = 'allergy' AND pref.importance_level = 'critical';

-- 訪問回数が多いユーザー
-- SELECT session_id, preferred_name, visit_count, last_visit_at
-- FROM user_profiles
-- WHERE visit_count > 2
-- ORDER BY visit_count DESC;

-- 非アクティブユーザー（90日以上訪問なし）
-- SELECT session_id, preferred_name, last_visit_at
-- FROM user_profiles
-- WHERE last_visit_at < NOW() - INTERVAL '90 days'
-- ORDER BY last_visit_at;
