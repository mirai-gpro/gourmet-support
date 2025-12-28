-- ========================================
-- 長期記憶システム マイグレーション
-- session_id → user_id への移行
-- ========================================

-- 既存テーブルを削除
DROP TABLE IF EXISTS user_interaction_history CASCADE;
DROP TABLE IF EXISTS user_preferences CASCADE;
DROP TABLE IF EXISTS user_profiles CASCADE;

-- 新しいuser_profilesテーブルを作成
CREATE TABLE user_profiles (
    user_id VARCHAR(255) PRIMARY KEY,
    preferred_name VARCHAR(100),
    name_honorific VARCHAR(20) DEFAULT '',
    default_language VARCHAR(10) DEFAULT 'ja',
    preferred_mode VARCHAR(20) DEFAULT 'chat',
    first_visit_at TIMESTAMPTZ DEFAULT NOW(),
    last_visit_at TIMESTAMPTZ DEFAULT NOW(),
    visit_count INTEGER DEFAULT 1,
    conversation_summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_profiles_last_visit ON user_profiles(last_visit_at);

-- 自動更新トリガー
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER update_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
