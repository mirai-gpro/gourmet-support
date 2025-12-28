-- ========================================
-- 長期記憶システム データベーススキーマ（新設計版）
-- Supabase (PostgreSQL)
-- ========================================

-- ========================================
-- テーブル: user_profiles
-- ユーザーの基本プロファイル（user_id がPRIMARY KEY）
-- ========================================

-- 既存テーブルを削除（開発環境のみ）
-- DROP TABLE IF EXISTS user_interaction_history CASCADE;
-- DROP TABLE IF EXISTS user_preferences CASCADE;
-- DROP TABLE IF EXISTS user_profiles CASCADE;

CREATE TABLE IF NOT EXISTS user_profiles (
    -- 識別子
    user_id VARCHAR(255) PRIMARY KEY,  -- ブラウザごとのユニークキー（将来Google認証IDに置換可能）

    -- 呼び方設定
    preferred_name VARCHAR(100),           -- 「山田太郎」
    name_honorific VARCHAR(20) DEFAULT '', -- 「様」「さん」「くん」「ちゃん」または空文字

    -- 会話サマリー（LLMが生成した統合サマリー）
    conversation_summary TEXT,             -- 食の好み、アレルギー、エリア、全て含む

    -- 基本情報
    default_language VARCHAR(10) DEFAULT 'ja',  -- 'ja', 'en', 'zh', 'ko'
    preferred_mode VARCHAR(20) DEFAULT 'chat',  -- 'chat', 'concierge'

    -- タイムスタンプ
    first_visit_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    last_visit_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    visit_count INTEGER DEFAULT 1,

    -- メタデータ
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_user_profiles_last_visit ON user_profiles(last_visit_at);

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

-- ========================================
-- Row Level Security (RLS) 設定
-- 注意: 実運用前に適切に設定してください
-- ========================================

-- RLSを有効化（必要に応じてコメント解除）
-- ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

-- ========================================
-- マイグレーションスクリプト（既存データの移行）
-- ========================================

-- 既存の session_id ベースのテーブルから user_id ベースに移行する場合:
--
-- 1. 既存テーブルをバックアップ
-- CREATE TABLE user_profiles_backup AS SELECT * FROM user_profiles;
--
-- 2. 既存データを移行（session_id を user_id として使用）
-- INSERT INTO user_profiles_new (user_id, preferred_name, name_honorific, ...)
-- SELECT session_id, preferred_name, name_honorific, ...
-- FROM user_profiles_backup
-- WHERE session_id IS NOT NULL;
--
-- 3. 古いテーブルを削除して新しいテーブルをリネーム
-- DROP TABLE user_profiles;
-- ALTER TABLE user_profiles_new RENAME TO user_profiles;

-- ========================================
-- 削除するテーブル（新設計では不要）
-- ========================================

-- user_preferences → conversation_summary に統合
-- DROP TABLE IF EXISTS user_preferences CASCADE;

-- user_interaction_history → 不要（ログ目的なら別途検討）
-- DROP TABLE IF EXISTS user_interaction_history CASCADE;

-- ========================================
-- サンプルデータ挿入（テスト用）
-- ========================================

-- サンプルユーザー1
INSERT INTO user_profiles (user_id, preferred_name, name_honorific, default_language, visit_count, conversation_summary)
VALUES (
    'test-user-001',
    'ちゃっぴー',
    '様',
    'ja',
    3,
    '【ユーザー情報】
- 名前: ちゃっぴー様
- 訪問回数: 3回

【食の好み】
- 辛い料理が好き（3回言及）
- 寿司が好き

【アレルギー・制約】
⚠️ ピーナッツアレルギー（必ず回避）

【よく利用するエリア】
- 渋谷（2回）
- 新宿'
)
ON CONFLICT (user_id) DO NOTHING;

-- サンプルユーザー2（英語ユーザー）
INSERT INTO user_profiles (user_id, preferred_name, name_honorific, default_language, visit_count, conversation_summary)
VALUES (
    'test-user-002',
    'John',
    '-san',
    'en',
    2,
    '【User Information】
- Name: John-san
- Visit count: 2

【Food Preferences】
- Vegetarian diet (mentioned 3 times)
- Likes Italian food

【Frequently Visited Areas】
- Shinjuku (4 times)'
)
ON CONFLICT (user_id) DO NOTHING;

-- ========================================
-- 便利なクエリ（テスト・デバッグ用）
-- ========================================

-- 全ユーザープロファイル表示
-- SELECT * FROM user_profiles ORDER BY last_visit_at DESC;

-- 特定ユーザーの情報表示
-- SELECT user_id, preferred_name, name_honorific, visit_count, conversation_summary
-- FROM user_profiles
-- WHERE user_id = 'test-user-001';

-- 訪問回数が多いユーザー
-- SELECT user_id, preferred_name, visit_count, last_visit_at
-- FROM user_profiles
-- WHERE visit_count > 2
-- ORDER BY visit_count DESC;

-- 非アクティブユーザー（90日以上訪問なし）
-- SELECT user_id, preferred_name, last_visit_at
-- FROM user_profiles
-- WHERE last_visit_at < NOW() - INTERVAL '90 days'
-- ORDER BY last_visit_at;
