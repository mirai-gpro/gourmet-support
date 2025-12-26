# 長期記憶実装 詳細仕様書

## 1. 概要

セッションを跨いでユーザーの好み・傾向を記憶し、次回訪問時にパーソナライズされた体験を提供する。

### 技術スタック
- **データベース**: Supabase (PostgreSQL)
- **保存方法**: セッション終了時 + リアルタイム重要情報検出時
- **読み込み**: セッション開始時に1回のみ
- **識別子**: session_id (将来的にGoogle認証でuser_idに移行)

---

## 2. データベース設計 (Supabase)

### テーブル1: `user_profiles`
ユーザーの基本プロファイル

```sql
CREATE TABLE user_profiles (
    -- 識別子
    session_id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255),  -- 将来のGoogle認証用（現在はNULL）

    -- 呼び方設定
    preferred_name VARCHAR(100),           -- 「ちゃっぴー」
    name_honorific VARCHAR(20),            -- 「様」「さん」「くん」「ちゃん」または空文字

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
CREATE INDEX idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX idx_user_profiles_last_visit ON user_profiles(last_visit_at);
```

### テーブル2: `user_preferences`
食の好み・傾向

```sql
CREATE TABLE user_preferences (
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
CREATE INDEX idx_user_preferences_session ON user_preferences(session_id);
CREATE INDEX idx_user_preferences_category ON user_preferences(category);
CREATE INDEX idx_user_preferences_importance ON user_preferences(importance_level);
```

### テーブル3: `user_interaction_history`
訪問履歴・行動ログ（分析用）

```sql
CREATE TABLE user_interaction_history (
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
CREATE INDEX idx_interaction_history_session ON user_interaction_history(session_id);
CREATE INDEX idx_interaction_history_started ON user_interaction_history(session_started_at);
```

---

## 3. 実装アーキテクチャ

### 3.1 新規ファイル: `long_term_memory.py`

```python
# 長期記憶管理モジュール
# - Supabaseとの接続
# - プロファイルのCRUD操作
# - 好みの自動抽出・更新
```

**主要クラス**:
- `LongTermMemory`: Supabaseクライアント、CRUD操作
- `PreferenceExtractor`: AI応答から重要情報を抽出

**主要関数**:
- `get_or_create_profile(session_id)`: プロファイル取得/作成
- `update_profile(session_id, data)`: プロファイル更新
- `get_all_preferences(session_id)`: 全ての好み取得
- `upsert_preference(session_id, category, key, value, ...)`: 好み保存
- `extract_preferences_from_conversation(messages)`: 会話から好み抽出
- `generate_system_prompt_context(session_id)`: システムプロンプト用コンテキスト生成

### 3.2 既存ファイルの修正

#### `support_core.py`
- `SupportSession.__init__()`: 長期記憶を読み込む
- `SupportSession.initialize()`: 初回訪問チェック、名前取得フロー
- セッション終了時: 長期記憶を保存

#### `app_customer_support.py`
- `/api/session/start`: 長期記憶読み込み、初回訪問フラグ設定
- `/api/finalize`: 長期記憶保存

---

## 4. 実装フロー

### 4.1 初回訪問時のフロー

```
[ユーザー] セッション開始
    ↓
[システム] Supabaseでsession_id検索
    ↓
[結果] プロファイルが存在しない
    ↓
[システム] 新規プロファイル作成（preferred_name=NULL）
    ↓
[AI初回挨拶]
「初めまして、AIコンシェルジュです。
宜しければ、あなたを何とお呼びすればいいか、教えて頂けますか？」
    ↓
[ユーザー] 「ちゃっぴー」または「ちゃっぴー、と呼んで」
    ↓
[AI] 名前抽出: "ちゃっぴー"
    ↓
[AI確認]
「ちゃっぴー"様"とお呼びすればよろしいですか？」
    ↓
[ユーザー選択肢]
  - 「はい」→ name_honorific='様'
  - 「様はいらない」→ name_honorific=''
  - 「さん付けで」→ name_honorific='さん'
    ↓
[システム] Supabaseに保存
  - preferred_name='ちゃっぴー'
  - name_honorific='様' (または空文字、'さん'等)
    ↓
[AI]
「承知いたしました、ちゃっぴー様。
それでは、どのようなレストランをお探しでしょうか？」
```

**実装上のポイント**:
- 名前抽出は正規表現 + AI判定の組み合わせ
- 敬称の確認は選択肢形式またはフリーテキスト対応
- 保存は非同期（await）で即座に実行

### 4.2 2回目以降のフロー

```
[ユーザー] セッション開始
    ↓
[システム] Supabaseでsession_id検索
    ↓
[結果] プロファイルが存在
    ↓
[システム]
  - preferred_name='ちゃっぴー'
  - name_honorific='様'
  - visit_count=3
  - 好みデータ読み込み
    ↓
[システムプロンプトに注入]
「このユーザーは"ちゃっぴー様"と呼ぶこと。
過去の好み:
- 辛い料理が好き (confidence: 0.9)
- ピーナッツアレルギー (confidence: 1.0, importance: critical)
- よく渋谷エリアで検索 (mention_count: 5)」
    ↓
[AI挨拶]
「お帰りなさいませ、ちゃっぴー様。
今日はどちらのエリアでレストランをお探しでしょうか？」
```

**実装上のポイント**:
- visit_count を更新（+1）
- last_visit_at を更新
- 重要度が高い好み（アレルギー等）を優先的にプロンプトに含める

### 4.3 会話中の好み抽出フロー

```
[会話例]
ユーザー: 「辛い料理が大好きなんです」
    ↓
[AI応答生成後]
    ↓
[PreferenceExtractor実行]
  - パターンマッチング: 「〜が好き」「〜が苦手」「アレルギー」
  - AIによる判定: 重要度スコアリング
    ↓
[検出結果]
{
  category: 'food_type',
  preference_key: 'spicy_food',
  preference_value: '辛い料理が好き',
  confidence_score: 0.9,
  importance_level: 'high'
}
    ↓
[Supabaseに非同期保存]
  - UPSERT (session_id, category, preference_key)
  - mention_count++ (既存の場合)
```

**抽出対象の例**:

| カテゴリ | preference_key | 例 |
|---------|----------------|-----|
| food_type | spicy_food | 辛い料理が好き |
| food_type | sweet_preference | 甘いものが苦手 |
| allergy | peanut_allergy | ピーナッツアレルギー |
| allergy | shellfish_allergy | 甲殻類アレルギー |
| area | shibuya_area | 渋谷エリアをよく利用 |
| area | shinjuku_area | 新宿エリアをよく利用 |
| budget | high_end | 高級志向 |
| budget | casual | カジュアル志向 |
| atmosphere | quiet_preference | 静かな雰囲気を好む |
| atmosphere | lively_preference | 賑やかな雰囲気を好む |
| other | vegetarian | ベジタリアン |
| other | no_smoking | 完全禁煙を希望 |

### 4.4 セッション終了時の保存フロー

```
[ユーザー] セッション終了
    ↓
[システム] /api/finalize 実行
    ↓
[長期記憶保存処理]
  1. 会話履歴からサマリー生成（AI）
  2. 未保存の好み情報を抽出
  3. user_interaction_history に記録
     - conversation_summary
     - searched_areas
     - recommended_shops
  4. user_preferences を更新
     - last_mentioned_at 更新
  5. user_profiles 更新
     - visit_count++
     - last_visit_at 更新
    ↓
[完了]
```

---

## 5. システムプロンプトへの注入フォーマット

### 生成例（日本語の場合）

```
【ユーザー情報】
- 呼び方: ちゃっぴー様
- 訪問回数: 3回目
- 前回訪問: 2025年12月20日

【重要な制約・アレルギー】
⚠️ ピーナッツアレルギー（必ず回避すること）
⚠️ 甲殻類アレルギー（必ず回避すること）

【食の好み】
✓ 辛い料理が好き（高確信度）
✓ イタリアンをよく選ぶ（言及回数: 5回）
× 甘いものは苦手

【よく使うエリア】
- 渋谷（言及回数: 7回）
- 新宿（言及回数: 3回）

【その他の傾向】
- 静かな雰囲気を好む
- 予算: カジュアル〜中価格帯
```

### 英語の場合

```
【User Information】
- Address as: Chappy-sama
- Visit count: 3rd visit
- Last visit: December 20, 2025

【Critical Constraints & Allergies】
⚠️ Peanut allergy (MUST avoid)
⚠️ Shellfish allergy (MUST avoid)

【Food Preferences】
✓ Loves spicy food (high confidence)
✓ Often chooses Italian (mentioned 5 times)
× Dislikes sweets

【Frequently Used Areas】
- Shibuya (mentioned 7 times)
- Shinjuku (mentioned 3 times)

【Other Tendencies】
- Prefers quiet atmosphere
- Budget: Casual to mid-range
```

---

## 6. AI自動抽出ロジック

### 6.1 抽出タイミング
- **リアルタイム**: 各メッセージ送信後
- **バッチ**: セッション終了時に全会話を再分析

### 6.2 抽出方法

#### パターン1: 正規表現ベース（高速・確実）
```python
PREFERENCE_PATTERNS = {
    'food_like': r'(.*?)が好き|大好き|気に入っ',
    'food_dislike': r'(.*?)が苦手|嫌い|ダメ',
    'allergy': r'(.*?)アレルギー|(.*?)は食べられない',
    'area': r'(.*?)(に|で)(住んでいる|よく行く|いつも)',
}
```

#### パターン2: AI判定（柔軟・高精度）
```python
# Gemini Flash (軽量モデル)に問い合わせ
prompt = f"""
以下の会話から、ユーザーの食の好み・アレルギー・エリア情報を抽出してください。

会話:
{conversation_text}

抽出形式（JSON）:
{{
  "preferences": [
    {{
      "category": "food_type|allergy|area|budget|atmosphere|other",
      "key": "具体的なキー",
      "value": "詳細な説明",
      "confidence": 0.0-1.0,
      "importance": "low|medium|high|critical"
    }}
  ]
}}
"""
```

### 6.3 重複排除・マージロジック
- 同じ `(session_id, category, preference_key)` が既に存在する場合:
  - `mention_count++`
  - `last_mentioned_at` 更新
  - `confidence_score` を平均化
  - `preference_value` を最新に更新

---

## 7. セキュリティ・プライバシー考慮

### 7.1 データ保持期間
- アクティブユーザー（90日以内に訪問）: 無期限保持
- 非アクティブユーザー（90日以上訪問なし）: 警告通知
- 完全非アクティブ（180日以上）: データ自動削除

### 7.2 データ削除機能
- ユーザーからの削除リクエストに対応
- `/api/profile/delete` エンドポイント実装

### 7.3 機密情報の取り扱い
- アレルギー情報は `importance_level='critical'` で管理
- Supabase Row Level Security (RLS) 設定
- API通信はHTTPS必須

---

## 8. 実装優先順位

### Phase 1: 基礎実装（必須）
1. ✅ Supabaseテーブル作成
2. ✅ `long_term_memory.py` 基本CRUD
3. ✅ 初回訪問時の名前取得フロー
4. ✅ 2回目以降の挨拶カスタマイズ

### Phase 2: 自動抽出（推奨）
5. ⚠️ 正規表現ベースの好み抽出
6. ⚠️ セッション終了時の一括保存

### Phase 3: 高度な機能（オプション）
7. 🔲 AI判定による高精度抽出
8. 🔲 訪問履歴分析
9. 🔲 トレンド分析（人気エリア・料理タイプ）

---

## 9. 必要な依存関係

```txt
# Supabase Client
supabase>=2.0.0

# 既存の依存関係は維持
Flask==3.0.0
google-genai>=0.3.0
...
```

---

## 10. 環境変数

```.env
# Supabase接続情報
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=your-anon-key-here
SUPABASE_SERVICE_KEY=your-service-role-key-here  # サーバーサイド専用

# 既存の環境変数
GEMINI_API_KEY=...
GOOGLE_PLACES_API_KEY=...
```

---

## 11. テストシナリオ

### シナリオ1: 初回訪問
1. 新規session_idでセッション開始
2. 「何とお呼びすればいいですか？」の質問表示
3. 名前入力: 「太郎」
4. 敬称確認: 「太郎様とお呼びすればよろしいですか？」
5. 応答: 「様はいらない」
6. 保存確認: Supabaseで `preferred_name='太郎', name_honorific=''`

### シナリオ2: 2回目訪問
1. 同じsession_idでセッション開始
2. 挨拶: 「お帰りなさいませ、太郎。」
3. visit_count=2 に更新

### シナリオ3: 好み抽出
1. ユーザー入力: 「辛い料理が大好きなんです」
2. AI応答後、好み抽出実行
3. Supabaseに保存: `category='food_type', key='spicy_food'`
4. 次回セッションで「辛い料理がお好きでしたね」と反映

### シナリオ4: アレルギー検出
1. ユーザー入力: 「ピーナッツアレルギーがあります」
2. 即座に保存: `importance_level='critical'`
3. 以降のレストラン推薦で自動除外

---

## 12. 実装スケジュール案

| フェーズ | タスク | 推定時間 |
|---------|-------|---------|
| Phase 1 | Supabaseセットアップ・テーブル作成 | 30分 |
| Phase 1 | `long_term_memory.py` 実装 | 2時間 |
| Phase 1 | 名前取得フロー実装 | 1時間 |
| Phase 1 | 挨拶カスタマイズ実装 | 30分 |
| Phase 2 | 好み抽出ロジック実装 | 1.5時間 |
| Phase 2 | セッション終了時保存 | 1時間 |
| Phase 2 | テスト・デバッグ | 1時間 |
| **合計** | | **約7.5時間** |

---

## 13. 次のステップへの展望

### Google認証導入後（将来）
- `session_id` → `user_id` へのマイグレーション
- 複数デバイス対応（同一user_idで統合）
- OAuth2.0フロー実装

### 高度な分析機能
- ユーザーセグメンテーション
- レコメンデーションエンジン強化
- A/Bテスト基盤

---

以上が長期記憶実装の詳細仕様です。
Phase 1の実装から開始することを推奨します。
