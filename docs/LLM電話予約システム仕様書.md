# LLM電話予約システム 要件定義・仕様書

**プロジェクト名**: グルメサポート - AI電話予約機能
**作成日**: 2025年11月22日
**バージョン**: 1.0

---

## 1. 概要

### 1.1 目的
ユーザーが選択した飲食店に対して、AIが自動で電話をかけて予約を取得するシステム。

### 1.2 主要機能
- 最大3店舗まで優先順位をつけて予約依頼
- AIが電話で店舗スタッフと会話し予約交渉
- 予約成功時点で処理終了（優先順位①で成功なら②③には電話しない）
- リアルタイムで予約状況をユーザーに通知

---

## 2. 技術スタック

| 役割 | サービス | 備考 |
|------|----------|------|
| 電話発信 | **Plivo Voice** | 発信・音声ストリーム転送のみ使用 |
| 音声認識 (STT) | **Google Cloud Speech-to-Text** | ストリーミング認識、日本語対応 |
| 音声合成 (TTS) | **Google Cloud Text-to-Speech** | Neural2音声、日本語対応 |
| 会話AI | **Gemini API** | Function Calling使用 |
| データベース | **Supabase** | PostgreSQL + Realtime |
| バックエンド | **Cloud Run** | Python (FastAPI) |
| フロントエンド | **Astro** | 既存アプリに統合 |

---

## 3. システム構成図

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
                        │           ┌──────────────┐          │
                        │           │   Supabase   │          │
                        │           │  (PostgreSQL)│          │
                        │           └──────────────┘          │
                        └──────────────────────────────────────┘
```

---

## 4. 音声処理フロー

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

## 5. データベース設計 (Supabase)

### 5.1 テーブル構成

#### reservation_jobs（予約ジョブ）
| カラム名 | 型 | 説明 |
|---------|-----|------|
| id | UUID | 主キー |
| user_id | TEXT | ユーザーID |
| status | TEXT | queued / calling / completed / failed |
| current_shop_index | INT | 現在の店舗インデックス (0-2) |
| created_at | TIMESTAMPTZ | 作成日時 |
| updated_at | TIMESTAMPTZ | 更新日時 |
| result | JSONB | 最終結果 |

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
| transcript | JSONB | 会話ログ |
| reservation_details | JSONB | 予約確定詳細 |

### 5.2 settings JSONB構造
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

### 5.3 reservation_details JSONB構造
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

---

## 6. API設計

### 6.1 予約依頼開始
```
POST /api/reservation/start

Request:
{
  "user_id": "user123",
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
    },
    // ... 最大3店舗
  ]
}

Response:
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

### 6.2 予約状態取得
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
    {
      "shop": "リストランテ○○",
      "result": "in_progress"
    }
  ],
  "result": null
}
```

### 6.3 Plivo Webhooks（内部用）
```
POST /api/plivo/answer      # 発信応答時
WS   /api/plivo/stream/{job_id}  # 音声ストリーム
POST /api/plivo/hangup      # 通話終了時
```

---

## 7. Gemini会話設計

### 7.1 Function Calling定義

#### check_reservation_status
予約交渉の状態を報告する
```json
{
  "name": "check_reservation_status",
  "parameters": {
    "status": "talking | success | failed | need_user",
    "reason": "状態の理由"
  }
}
```

#### confirm_reservation
予約が確定した時に呼び出す
```json
{
  "name": "confirm_reservation",
  "parameters": {
    "confirmed_date": "YYYY-MM-DD",
    "confirmed_time": "HH:MM",
    "party_size": 4,
    "seat_type": "テーブル席",
    "notes": "予約番号: 1234"
  }
}
```

#### reject_alternative
店舗の代替案が許容範囲外の時
```json
{
  "name": "reject_alternative",
  "parameters": {
    "offered_time": "20:00",
    "reason": "希望時間から90分以上離れている"
  }
}
```

### 7.2 システムプロンプト
```
あなたは飲食店予約代行AIです。電話で予約を取ります。

【予約依頼】
- 希望日: {date}
- 希望時間: {time}
- 時間許容: {flexibility_text}
- 人数: {guests}名
- 席希望: {seat_types}
- 備考: {notes}

【行動ルール】
1. 丁寧な敬語で簡潔に（1発話15秒以内）
2. 最初: 「お忙しいところ恐れ入ります。予約のお電話です」
3. 予約可否を明確に確認
4. 代替案は許容範囲内か即判断

【Function Call使用タイミング】
- 予約確定 → confirm_reservation()
- 満席・予約不可 → check_reservation_status(status="failed")
- 代替案が範囲外 → reject_alternative()
- 会話継続 → テキストのみ応答

【重要】
- 許容時間を超える代替案は断る
- 予約番号があれば notes に含める
```

### 7.3 時間許容範囲の判定ロジック
| flexibility値 | 許容範囲 |
|--------------|---------|
| exact | 指定時間のみ |
| 30 | 指定時間 〜 +30分 |
| 60 | 指定時間 〜 +60分 |
| 90 | 指定時間 〜 +90分 |

例: 希望18:00、flexibility=30 の場合
- 18:00 → OK
- 18:30 → OK
- 19:00 → NG（reject_alternative）

---

## 8. 予約フロー

### 8.1 正常系フロー
```
1. ユーザーが予約依頼を送信
2. システムがジョブをSupabaseに登録（status: queued）
3. 優先順位①の店舗に電話発信
4. Plivo WebSocketで音声ストリーム確立
5. AI: 「お忙しいところ恐れ入ります。予約のお電話です」
6. 店員の応答をSTTでテキスト化
7. Geminiが応答を生成
8. TTSで音声化してPlivoに送信
9. （会話継続...）
10. 予約確定 → confirm_reservation() 呼び出し
11. AI: 「ありがとうございます。○月○日○時で承知しました」
12. 通話終了
13. Supabaseに結果を保存（status: completed）
14. フロントエンドにRealtime通知
```

### 8.2 店舗切り替えフロー
```
1. 優先順位①に電話
2. 満席で予約不可 → check_reservation_status(status="failed")
3. AI: 「承知いたしました。また改めてご連絡いたします」
4. 通話終了
5. current_shop_index を 1 に更新
6. 優先順位②の店舗に電話
7. （以降同様）
```

### 8.3 全店舗失敗フロー
```
1. 優先順位①②③すべて失敗
2. status: failed に更新
3. result に失敗理由を記録
4. フロントエンドに通知
5. ユーザーに「予約が取れませんでした」と表示
```

---

## 9. フロントエンド連携

### 9.1 Supabase Realtime購読
```typescript
// ジョブ状態の変更を監視
supabase
  .channel(`job-${jobId}`)
  .on('postgres_changes', {
    event: 'UPDATE',
    schema: 'public',
    table: 'reservation_jobs',
    filter: `id=eq.${jobId}`
  }, callback)
  .subscribe()
```

### 9.2 UI状態表示
| status | 表示内容 |
|--------|---------|
| queued | 「予約依頼を受け付けました」 |
| calling | 「① ○○に電話中...」 |
| completed | 「予約が取れました！」+ 詳細表示 |
| failed | 「予約が取れませんでした」+ 理由表示 |

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
| Plivo API エラー | リトライ（指数バックオフ） |
| Gemini API エラー | 固定フレーズで応答継続 |
| Supabase エラー | メモリにバッファ、後で再送 |

---

## 11. セキュリティ

### 11.1 認証・認可
- ユーザー認証: 既存のSupabase Auth使用
- API認証: Bearer Token
- Webhook認証: Plivo署名検証

### 11.2 データ保護
- 電話番号: 暗号化して保存
- 会話ログ: 90日後に自動削除
- 個人情報: GDPR/個人情報保護法準拠

---

## 12. 環境変数

```env
# Plivo
PLIVO_AUTH_ID=xxx
PLIVO_AUTH_TOKEN=xxx
PLIVO_PHONE_NUMBER=+81xxxxxxxxxx

# Google Cloud
GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
GOOGLE_API_KEY=xxx  # Gemini用

# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=xxx
SUPABASE_ANON_KEY=xxx

# アプリケーション
BASE_URL=https://your-app.run.app
```

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

### Phase 4: フロントエンド連携
- [ ] 予約依頼API呼び出し
- [ ] Realtime状態監視
- [ ] 結果表示UI

### Phase 5: 品質向上
- [ ] エラーハンドリング強化
- [ ] リトライロジック
- [ ] ログ・モニタリング

---

## 14. 参考リンク

- [Plivo Voice API Documentation](https://www.plivo.com/docs/voice/)
- [Google Cloud Speech-to-Text](https://cloud.google.com/speech-to-text/docs)
- [Google Cloud Text-to-Speech](https://cloud.google.com/text-to-speech/docs)
- [Gemini API - Function Calling](https://ai.google.dev/docs/function_calling)
- [Supabase Documentation](https://supabase.com/docs)

---

**ドキュメント履歴**
| バージョン | 日付 | 変更内容 |
|-----------|------|---------|
| 1.0 | 2025-11-22 | 初版作成 |
