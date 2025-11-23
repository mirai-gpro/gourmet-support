# LLM電話予約システム 要件定義・仕様書

**プロジェクト名**: グルメサポート - AI電話予約機能
**作成日**: 2025年11月23日
**バージョン**: 1.1

---

## 1. 概要

### 1.1 目的
Gemini LLMとGoogle Cloudサービスを活用し、**人間と遜色ない自然な会話**で飲食店に電話をかけ、予約を自動で取得・管理する。

### 1.2 主要機能
- 最大3店舗まで優先順位をつけて予約依頼
- AIが電話で店舗スタッフと会話し予約交渉
- 予約成功時点で処理終了（優先順位①で成功なら②③には電話しない）
- リアルタイムで予約状況をユーザーに通知
- 通話録音・ログによる品質管理

---

## 2. システムアーキテクチャ

### 2.1 技術スタック一覧

| 要素 | サービス | 役割 |
|------|----------|------|
| アプリケーションホスティング | **Cloud Run** | サーバーレスでアプリケーションロジックをホスト。PlivoからのWebhookを受信し、LLMとAIサービスを制御するハブ。 |
| LLM / 会話AI | **Gemini (Flash)** | リアルタイムな会話の意思決定、応答テキスト生成、Function Callingによる予約情報抽出・管理。 |
| 電話API (CPaaS) | **Plivo** | 電話の発信・通話制御。音声データをCloud Runにストリーム配信し、Webhookでイベント通知を行う。 |
| 音声認識 (STT) | **Google Cloud Speech-to-Text** | 飲食店スタッフの音声をリアルタイムで高精度にテキスト化。※CPaaS付属機能は使用しない（単価最適化） |
| 音声合成 (TTS) | **Google Cloud Text-to-Speech** | Geminiの応答テキストを自然な日本語音声（Neural2）に変換。※CPaaS付属機能は使用しない（単価最適化） |
| データ永続化 | **Supabase (PostgreSQL)** | 予約情報、ユーザー情報、通話ログ、ステータスの管理。Realtimeで状態変更を通知。 |
| 大容量ストレージ | **Google Cloud Storage (GCS)** | 通話録音ファイル、詳細対話ログの保存先。 |
| フロントエンド | **Astro** | 既存アプリに統合 |

### 2.2 システム構成図

```
┌─────────────────┐     ┌──────────────────────────────────────┐
│  フロントエンド   │◀───▶│           Cloud Run (Python)          │
│  (Astro)        │ WS  │                                      │
└─────────────────┘     │  ┌─────────┐ ┌────────┐ ┌─────────┐ │
                        │  │Google   │ │Gemini  │ │Google   │ │
        ┌──────────┐    │  │STT      │▶│+ FC    │▶│TTS      │ │
        │  Plivo   │◀──▶│  └─────────┘ └────────┘ └─────────┘ │
        │ (発信)   │ WS │                   │                  │
        └──────────┘    │                   ▼                  │
                        │     ┌──────────┐ ┌──────────┐       │
                        │     │ Supabase │ │   GCS    │       │
                        │     │(PostgreSQL)│ │ (録音)   │       │
                        │     └──────────┘ └──────────┘       │
                        └──────────────────────────────────────┘
```

### 2.3 音声処理フロー

```
【リアルタイム双方向音声処理】

店員の声 ──▶ Plivo Audio Stream ──▶ Google STT ──▶ テキスト
                                                      │
                                                      ▼
                                               Gemini API
                                             (Function Calling)
                                                      │
                                                      ▼
LLMの声 ◀── Plivo Audio Stream ◀── Google TTS ◀── テキスト
```

---

## 3. 機能要件とデータ設計

### 3.1 発信と対話のロジック

| 項目 | 仕様 |
|------|------|
| 発信元番号 | Plivoから取得した番号を使用。ユーザー個人の携帯番号は使用しない。 |
| 連絡先の伝達 | LLMは会話の中で、予約情報の一部としてユーザー本人の携帯番号を飲食店に伝える。 |
| 会話の主導権 | GeminiがFunction Callingを用いて、予約確認・実行などの内部処理を制御する。 |
| 人間介入 | 対話が袋小路に入った場合、人間スタッフが**途中介入（バージイン）**できる仕組みをPlivoのMulti-Party Call機能で実装する。 |

### 3.2 予約識別キーの定義

| 識別子 | 定義と役割 | 紐付け |
|--------|-----------|--------|
| ユーザーID | 予約代行を依頼したユーザーを一意に識別する内部キー。ユーザーの携帯番号と一対一で紐づく。 | usersテーブルのPrimary Key |
| 予約ID (job_id) | 個々の予約案件を一意に識別するキー。 | reservation_jobsテーブルのPrimary Key |
| 確定連絡先 | 予約台帳に記録される、ユーザー本人の携帯番号。飲食店との緊急連絡用キーとなる。 | reservation_jobsテーブルに記録 |

---

## 4. データベース設計 (Supabase)

### 4.1 ER図

```
users
  │
  │ 1:N
  ▼
reservation_jobs ──────┬─────────────────┐
  │                    │                 │
  │ 1:N                │ 1:N             │
  ▼                    ▼                 ▼
reservation_shops   call_attempts    call_recordings
```

### 4.2 テーブル定義

#### users（ユーザー）
| カラム名 | 型 | 説明 |
|---------|-----|------|
| id | UUID | 主キー |
| phone_number | TEXT | 携帯番号（暗号化） |
| display_name | TEXT | 表示名 |
| created_at | TIMESTAMPTZ | 作成日時 |

#### reservation_jobs（予約ジョブ）
| カラム名 | 型 | 説明 |
|---------|-----|------|
| id | UUID | 主キー |
| user_id | UUID | 外部キー (users) |
| status | TEXT | queued / calling / completed / failed |
| current_shop_index | INT | 現在の店舗インデックス (0-2) |
| contact_phone | TEXT | 店舗に伝える連絡先（暗号化） |
| created_at | TIMESTAMPTZ | 作成日時 |
| updated_at | TIMESTAMPTZ | 更新日時 |
| result | JSONB | 最終結果 |
| summary | TEXT | 通話要約（Gemini生成） |

#### reservation_shops（予約対象店舗）
| カラム名 | 型 | 説明 |
|---------|-----|------|
| id | UUID | 主キー |
| job_id | UUID | 外部キー (reservation_jobs) |
| priority | INT | 優先順位 (1, 2, 3) |
| shop_name | TEXT | 店舗名 |
| phone_number | TEXT | 電話番号 |
| place_id | TEXT | Google Place ID |
| settings | JSONB | 予約設定 |

#### call_attempts（通話履歴）
| カラム名 | 型 | 説明 |
|---------|-----|------|
| id | UUID | 主キー |
| job_id | UUID | 外部キー (reservation_jobs) |
| shop_id | UUID | 外部キー (reservation_shops) |
| call_uuid | TEXT | Plivo通話UUID |
| status | TEXT | initiated / ringing / answered / completed / failed |
| result | TEXT | success / failed / no_answer / busy |
| started_at | TIMESTAMPTZ | 通話開始日時 |
| ended_at | TIMESTAMPTZ | 通話終了日時 |
| transcript | JSONB | 会話ログ（サマリー） |
| transcript_gcs_path | TEXT | 詳細ログのGCSパス |
| recording_gcs_path | TEXT | 録音ファイルのGCSパス |
| reservation_details | JSONB | 予約確定詳細 |

### 4.3 JSONB構造

#### settings
```json
{
  "guests": 4,
  "date": "2024-12-25",
  "time": "18:00",
  "flexibility": "30",
  "seat_types": ["table", "private"],
  "notes": "誕生日ケーキ希望"
}
```

#### reservation_details
```json
{
  "confirmed_date": "2024-12-25",
  "confirmed_time": "18:30",
  "party_size": 4,
  "seat_type": "テーブル席",
  "confirmation_number": "1234",
  "notes": "18:30に変更"
}
```

#### transcript（サマリー）
```json
[
  {"speaker": "agent", "text": "お忙しいところ恐れ入ります。予約のお電話です。", "at": "2024-12-20T10:00:00Z"},
  {"speaker": "shop", "text": "はい、お電話ありがとうございます。", "at": "2024-12-20T10:00:05Z"}
]
```

### 4.4 SQL定義

```sql
-- ユーザー
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  phone_number TEXT NOT NULL UNIQUE,
  display_name TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 予約ジョブ
CREATE TABLE reservation_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued',
  current_shop_index INT DEFAULT 0,
  contact_phone TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  result JSONB,
  summary TEXT
);

-- 予約対象店舗
CREATE TABLE reservation_shops (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID REFERENCES reservation_jobs(id) ON DELETE CASCADE,
  priority INT NOT NULL,
  shop_name TEXT NOT NULL,
  phone_number TEXT NOT NULL,
  place_id TEXT,
  settings JSONB NOT NULL
);

-- 通話履歴
CREATE TABLE call_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID REFERENCES reservation_jobs(id) ON DELETE CASCADE,
  shop_id UUID REFERENCES reservation_shops(id),
  call_uuid TEXT,
  status TEXT DEFAULT 'initiated',
  result TEXT,
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  transcript JSONB DEFAULT '[]',
  transcript_gcs_path TEXT,
  recording_gcs_path TEXT,
  reservation_details JSONB
);

-- インデックス
CREATE INDEX idx_jobs_user ON reservation_jobs(user_id);
CREATE INDEX idx_jobs_status ON reservation_jobs(status);
CREATE INDEX idx_shops_job ON reservation_shops(job_id);
CREATE INDEX idx_calls_job ON call_attempts(job_id);

-- Realtime有効化
ALTER PUBLICATION supabase_realtime ADD TABLE reservation_jobs;
ALTER PUBLICATION supabase_realtime ADD TABLE call_attempts;
```

---

## 5. API設計

### 5.1 予約依頼開始
```
POST /api/reservation/start

Request:
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "contact_phone": "090-1234-5678",
  "shops": [
    {
      "name": "リストランテ○○",
      "phone": "03-1234-5678",
      "priority": 1,
      "place_id": "ChIJ...",
      "settings": {
        "guests": 4,
        "date": "2024-12-25",
        "time": "18:00",
        "flexibility": "30",
        "seat_types": ["table"],
        "notes": "誕生日ケーキ希望"
      }
    }
  ]
}

Response:
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

### 5.2 予約状態取得
```
GET /api/reservation/{job_id}/status

Response:
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "calling",
  "current_shop": {
    "name": "リストランテ○○",
    "priority": 1,
    "call_status": "talking"
  },
  "attempts": [
    {"shop": "リストランテ○○", "result": "in_progress"}
  ],
  "result": null
}
```

### 5.3 予約キャンセル
```
POST /api/reservation/{job_id}/cancel

Response:
{
  "success": true,
  "message": "予約依頼をキャンセルしました"
}
```

### 5.4 Plivo Webhooks（内部用）
```
POST /api/plivo/answer        # 発信応答時
WS   /api/plivo/stream/{job_id}   # 音声ストリーム
POST /api/plivo/hangup        # 通話終了時
POST /api/plivo/recording     # 録音完了時
```

---

## 6. Gemini会話設計

### 6.1 Function Calling定義

#### check_reservation_status
予約交渉の状態を報告する
```json
{
  "name": "check_reservation_status",
  "description": "予約交渉の現在状態を報告する",
  "parameters": {
    "type": "object",
    "properties": {
      "status": {
        "type": "string",
        "enum": ["talking", "success", "failed", "need_user"],
        "description": "会話の状態"
      },
      "reason": {
        "type": "string",
        "description": "状態の理由"
      }
    },
    "required": ["status"]
  }
}
```

#### confirm_reservation
予約が確定した時に呼び出す
```json
{
  "name": "confirm_reservation",
  "description": "予約が確定した時に呼び出す",
  "parameters": {
    "type": "object",
    "properties": {
      "confirmed_date": {"type": "string", "description": "確定日 (YYYY-MM-DD)"},
      "confirmed_time": {"type": "string", "description": "確定時間 (HH:MM)"},
      "party_size": {"type": "integer", "description": "人数"},
      "seat_type": {"type": "string", "description": "席タイプ"},
      "confirmation_number": {"type": "string", "description": "予約番号"},
      "notes": {"type": "string", "description": "備考"}
    },
    "required": ["confirmed_date", "confirmed_time", "party_size"]
  }
}
```

#### reject_alternative
店舗の代替案が許容範囲外の時
```json
{
  "name": "reject_alternative",
  "description": "店舗から提案された代替案が許容範囲外の時に呼び出す",
  "parameters": {
    "type": "object",
    "properties": {
      "offered_time": {"type": "string", "description": "提案された時間"},
      "reason": {"type": "string", "description": "断る理由"}
    },
    "required": ["offered_time", "reason"]
  }
}
```

### 6.2 システムプロンプト

```
あなたは飲食店予約代行AIです。電話で予約を取ります。

【予約依頼】
- 予約者名: {customer_name}
- 連絡先: {contact_phone}
- 希望日: {date}
- 希望時間: {time}
- 時間許容: {flexibility_text}
- 人数: {guests}名
- 席希望: {seat_types}
- 備考: {notes}

【行動ルール】
1. 丁寧な敬語で簡潔に（1発話15秒以内で読める長さ）
2. 最初: 「お忙しいところ恐れ入ります。予約のお電話です」
3. 予約名と連絡先を伝える
4. 予約可否を明確に確認
5. 代替案は許容範囲内か即判断
6. 予約確定時は必ず復唱確認

【Function Call使用タイミング】
- 予約確定 → confirm_reservation()
- 満席・予約不可 → check_reservation_status(status="failed")
- 代替案が範囲外 → reject_alternative()
- 会話継続 → テキストのみ応答

【重要】
- 許容時間を超える代替案は断る
- 予約番号があれば必ず記録
- 不明点があればユーザー確認が必要と判断（status="need_user"）
```

### 6.3 時間許容範囲の判定ロジック

| flexibility値 | 許容範囲 |
|--------------|---------|
| exact | 指定時間のみ |
| 30 | 指定時間 〜 +30分 |
| 60 | 指定時間 〜 +60分 |
| 90 | 指定時間 〜 +90分 |

**例**: 希望18:00、flexibility=30 の場合
- 18:00 → OK
- 18:30 → OK
- 19:00 → NG（reject_alternative）

---

## 7. 予約フロー

### 7.1 正常系フロー

```
1. ユーザーが予約依頼を送信
2. システムがジョブをSupabaseに登録（status: queued）
3. 優先順位①の店舗に電話発信
4. Plivo WebSocketで音声ストリーム確立
5. 録音開始
6. AI: 「お忙しいところ恐れ入ります。予約のお電話です」
7. 店員の応答をSTTでテキスト化
8. Geminiが応答を生成
9. TTSで音声化してPlivoに送信
10. （会話継続...）
11. 予約確定 → confirm_reservation() 呼び出し
12. AI: 「ありがとうございます。○月○日○時で承知しました」
13. 通話終了
14. 録音をGCSにアップロード
15. Geminiで通話要約を生成
16. Supabaseに結果を保存（status: completed）
17. フロントエンドにRealtime通知
```

### 7.2 店舗切り替えフロー

```
1. 優先順位①に電話
2. 満席で予約不可 → check_reservation_status(status="failed")
3. AI: 「承知いたしました。また改めてご連絡いたします」
4. 通話終了、録音保存
5. current_shop_index を 1 に更新
6. 優先順位②の店舗に電話
7. （以降同様）
```

### 7.3 全店舗失敗フロー

```
1. 優先順位①②③すべて失敗
2. status: failed に更新
3. result に失敗理由を記録
4. 全通話の要約を生成
5. フロントエンドに通知
6. ユーザーに「予約が取れませんでした」と表示
```

### 7.4 人間介入（バージイン）フロー

```
1. AIが対話中に複雑な状況に遭遇
2. check_reservation_status(status="need_user") を呼び出し
3. システムがユーザーに通知
4. ユーザーが「介入」ボタンをクリック
5. Plivo Multi-Party Callでユーザーを通話に追加
6. ユーザーが直接店舗と会話
7. 通話終了後、結果を手動入力
```

---

## 8. テスト・監査機能

### 8.1 機能一覧

| 機能 | 実装方法 | 保存先 |
|------|----------|--------|
| リアルタイムモニタリング | Plivo Multi-Party Call機能。テスターの電話をミュート状態で通話に接続。 | - |
| 通話録音 | Plivoの録音機能を使用し、ファイルをGCSに自動アップロード。 | GCS |
| 対話ログ | STT出力、Gemini応答の全文。容量が大きい場合はGCSにJSONファイルとして保存。 | GCS (全文)、Supabase (サマリー) |
| 通話要約 | 通話終了後、Geminiに依頼して会話の要約と最終ステータスを生成し、Supabaseに記録。 | Supabase |

### 8.2 GCSバケット構造

```
gs://gourmet-support-recordings/
├── recordings/
│   └── {job_id}/
│       └── {call_attempt_id}.wav
└── transcripts/
    └── {job_id}/
        └── {call_attempt_id}.json
```

---

## 9. フロントエンド連携

### 9.1 Supabase Realtime購読

```typescript
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

function subscribeToJob(jobId: string, onUpdate: (data: any) => void) {
  const channel = supabase
    .channel(`job-${jobId}`)
    .on('postgres_changes', {
      event: 'UPDATE',
      schema: 'public',
      table: 'reservation_jobs',
      filter: `id=eq.${jobId}`
    }, (payload) => onUpdate(payload.new))
    .subscribe()

  return () => supabase.removeChannel(channel)
}
```

### 9.2 UI状態表示

| status | 表示内容 |
|--------|---------|
| queued | 「予約依頼を受け付けました」 |
| calling | 「① ○○に電話中...」 |
| completed | 「予約が取れました！」+ 詳細表示 |
| failed | 「予約が取れませんでした」+ 理由表示 |
| need_user | 「確認が必要です」+ 介入ボタン |

---

## 10. エラーハンドリング

### 10.1 通話エラー

| エラー | 対応 |
|--------|------|
| 話中 (busy) | 30秒後にリトライ（最大2回） |
| 応答なし (no_answer) | 次の店舗へ |
| 通話切断 | 次の店舗へ |
| STT/TTS エラー | ログ記録、次の店舗へ |

### 10.2 API エラー

| エラー | 対応 |
|--------|------|
| Plivo API エラー | リトライ（指数バックオフ: 2s, 4s, 8s） |
| Gemini API エラー | 固定フレーズで応答継続 |
| Supabase エラー | メモリにバッファ、後で再送 |
| GCS エラー | ローカル保存、後でアップロード |

---

## 11. 外部サービス連携

### 11.1 認証情報（Secret Manager管理）

| サービス | 連携方法 | 認証情報 |
|----------|----------|----------|
| Plivo | Cloud RunがWebhook URLを提供し、Plivoからのイベントを受信。発信はPlivo REST API経由。 | Auth ID, Auth Token |
| Gemini | Cloud RunからGemini APIを呼び出し（REST or SDK） | Gemini API Key |
| GCP AI Services | Cloud Runサービスアカウントに STT/TTS のAPI利用権限を付与 | Service Account Key (または Workload Identity) |
| Supabase | Cloud RunからPostgreSQL接続 | DB URL, Service Key |
| GCS | Cloud Runサービスアカウントに Storage権限を付与 | Service Account Key |

### 11.2 環境変数

```env
# Plivo
PLIVO_AUTH_ID=xxx
PLIVO_AUTH_TOKEN=xxx
PLIVO_PHONE_NUMBER=+81xxxxxxxxxx

# Google Cloud
GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
GOOGLE_API_KEY=xxx  # Gemini用
GCS_BUCKET_NAME=gourmet-support-recordings

# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=xxx
SUPABASE_ANON_KEY=xxx

# アプリケーション
BASE_URL=https://your-app.run.app
```

---

## 12. セキュリティ

### 12.1 認証・認可
- ユーザー認証: Supabase Auth使用
- API認証: Bearer Token
- Webhook認証: Plivo署名検証

### 12.2 データ保護
- 電話番号: 暗号化して保存
- 通話録音: 90日後に自動削除（GCS Lifecycle）
- 会話ログ: 90日後に自動削除
- 個人情報: GDPR/個人情報保護法準拠

### 12.3 アクセス制御
- Cloud Run: IAMによるアクセス制御
- Supabase: Row Level Security (RLS) 有効化
- GCS: バケットポリシーで制限

---

## 13. 実装フェーズ

### Phase 1: 基盤構築
- [ ] Plivo発信 + WebSocket接続
- [ ] Google STT ストリーミング認識
- [ ] Google TTS 音声合成
- [ ] 基本的な音声ループバック確認

### Phase 2: AI会話実装
- [ ] Gemini Function Calling統合
- [ ] 予約交渉ロジック
- [ ] 状態管理・判定ロジック

### Phase 3: データ永続化
- [ ] Supabaseテーブル作成
- [ ] 予約ジョブ管理
- [ ] 通話履歴記録
- [ ] GCS録音保存

### Phase 4: フロントエンド連携
- [ ] 予約依頼API呼び出し
- [ ] Realtime状態監視
- [ ] 結果表示UI
- [ ] 人間介入UI

### Phase 5: 品質向上
- [ ] エラーハンドリング強化
- [ ] リトライロジック
- [ ] ログ・モニタリング
- [ ] 通話要約生成

---

## 14. 参考リンク

- [Plivo Voice API Documentation](https://www.plivo.com/docs/voice/)
- [Plivo Audio Streaming](https://www.plivo.com/docs/voice/api/call/stream/)
- [Google Cloud Speech-to-Text](https://cloud.google.com/speech-to-text/docs)
- [Google Cloud Text-to-Speech](https://cloud.google.com/text-to-speech/docs)
- [Gemini API - Function Calling](https://ai.google.dev/docs/function_calling)
- [Supabase Documentation](https://supabase.com/docs)
- [Supabase Realtime](https://supabase.com/docs/guides/realtime)

---

## ドキュメント履歴

| バージョン | 日付 | 変更内容 |
|-----------|------|---------|
| 1.0 | 2025-11-22 | 初版作成 |
| 1.1 | 2025-11-23 | Googleドキュメント内容を統合。テスト・監査機能、GCS連携、人間介入フロー、外部サービス連携を追加。 |
