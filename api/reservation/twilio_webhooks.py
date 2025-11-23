"""
Twilio Webhook ハンドラー

エンドポイント:
- POST /api/twilio/answer    - 発信応答時
- POST /api/twilio/gather    - 音声入力処理
- POST /api/twilio/status    - 通話ステータス更新
"""

import os
import json
import base64
import logging
from datetime import datetime
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from google.cloud import texttospeech
import google.generativeai as genai

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/twilio", tags=["twilio"])

# 環境変数
BASE_URL = os.environ.get('BASE_URL', 'https://your-app.run.app')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')

# Google Cloud TTS クライアント
tts_client = texttospeech.TextToSpeechClient()

# Gemini 初期化
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
else:
    gemini_model = None
    logger.warning("[Gemini] GOOGLE_API_KEY が未設定")


# 予約情報（テスト用）
RESERVATION_INFO = {
    "reserver_name": "山田太郎",
    "contact_phone": "090-1234-5678",
    "date": "12月25日",
    "time": "19時",
    "guests": 4,
    "seat_type": "テーブル席",
    "flexibility": "30分程度なら前後可能",
    "notes": "誕生日のお祝い"
}


def synthesize_speech_google(text: str, voice_name: str = "ja-JP-Chirp3-HD-Leda") -> bytes:
    """
    Google Cloud TTS で音声を生成
    既存の app_customer_support.py と同じ設定を使用
    """
    synthesis_input = texttospeech.SynthesisInput(text=text)

    try:
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name=voice_name  # ja-JP-Chirp3-HD-Leda（高品質）
        )
    except Exception as e:
        logger.warning(f"[TTS] 指定音声が無効、デフォルトに変更: {e}")
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name="ja-JP-Neural2-B"
        )

    # Twilio電話用: MP3形式（Twilioが対応）
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.0,
        pitch=0.0
    )

    response = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    return response.audio_content


# ========================================
# インメモリ状態管理（テスト用）
# 本番ではSupabaseに置き換え
# ========================================
active_calls = {}


# ========================================
# Webhook エンドポイント
# ========================================

def get_gemini_response(user_input: str, call_sid: str) -> str:
    """Gemini で会話応答を生成"""
    if not gemini_model:
        return "申し訳ございません。システムエラーが発生しました。"

    # 会話履歴を取得
    history = active_calls.get(call_sid, {}).get('transcript', [])
    history_text = "\n".join([f"{h['role']}: {h['text']}" for h in history[-5:]])

    prompt = f"""あなたはレストラン予約の電話をかけている予約代行AIです。
以下の予約情報で予約を取ってください。丁寧な日本語で簡潔に話してください。

【予約情報】
- 予約者名: {RESERVATION_INFO['reserver_name']}
- 連絡先: {RESERVATION_INFO['contact_phone']}
- 希望日: {RESERVATION_INFO['date']}
- 希望時間: {RESERVATION_INFO['time']}
- 人数: {RESERVATION_INFO['guests']}名
- 席種: {RESERVATION_INFO['seat_type']}
- 時間の融通: {RESERVATION_INFO['flexibility']}
- 備考: {RESERVATION_INFO['notes']}

【これまでの会話】
{history_text}

【店員の発言】
{user_input}

【あなたの応答】（1-2文で簡潔に）:"""

    try:
        response = gemini_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"[Gemini] エラー: {e}")
        return "少々お待ちください。"


@router.post("/answer")
async def handle_answer(request: Request):
    """
    Twilioが通話に応答した時に呼ばれる
    挨拶を再生し、音声入力を待つ
    """
    form_data = await request.form()
    call_sid = form_data.get('CallSid')
    from_number = form_data.get('From')
    to_number = form_data.get('To')

    logger.info(f"[Twilio Answer] CallSid={call_sid}, From={from_number}, To={to_number}")

    # 通話情報を保存
    active_calls[call_sid] = {
        'call_sid': call_sid,
        'from': from_number,
        'to': to_number,
        'status': 'answered',
        'started_at': datetime.now().isoformat(),
        'transcript': []
    }

    # 最初の挨拶
    greeting = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['reserver_name']}と申します。{RESERVATION_INFO['date']}の{RESERVATION_INFO['time']}から{RESERVATION_INFO['guests']}名で予約をお願いしたいのですが、よろしいでしょうか。"

    # TwiMLレスポンス
    response = VoiceResponse()

    # 挨拶を再生して、音声入力を待つ
    gather = Gather(
        input='speech',
        language='ja-JP',
        timeout=5,
        speech_timeout='auto',
        action=f'{BASE_URL}/api/twilio/gather'
    )
    gather.play(f"{BASE_URL}/api/twilio/audio/dynamic?text={greeting}")
    response.append(gather)

    # タイムアウト時
    response.redirect(f'{BASE_URL}/api/twilio/timeout')

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.post("/gather")
async def handle_gather(request: Request):
    """
    音声入力を受け取り、Geminiで応答を生成
    """
    form_data = await request.form()
    call_sid = form_data.get('CallSid')
    speech_result = form_data.get('SpeechResult', '')

    logger.info(f"[Twilio Gather] CallSid={call_sid}, Speech={speech_result}")

    # 会話履歴に追加
    if call_sid in active_calls:
        active_calls[call_sid]['transcript'].append({
            'role': '店員',
            'text': speech_result,
            'timestamp': datetime.now().isoformat()
        })

    # Gemini で応答生成
    ai_response = get_gemini_response(speech_result, call_sid)
    logger.info(f"[Gemini] 応答: {ai_response}")

    # 会話履歴に追加
    if call_sid in active_calls:
        active_calls[call_sid]['transcript'].append({
            'role': 'AI',
            'text': ai_response,
            'timestamp': datetime.now().isoformat()
        })

    # TwiMLレスポンス
    response = VoiceResponse()

    # 予約完了判定（簡易）
    if "ありがとうございました" in ai_response or "失礼します" in ai_response:
        # 通話終了
        response.play(f"{BASE_URL}/api/twilio/audio/dynamic?text={ai_response}")
        response.hangup()
    else:
        # 会話継続
        gather = Gather(
            input='speech',
            language='ja-JP',
            timeout=5,
            speech_timeout='auto',
            action=f'{BASE_URL}/api/twilio/gather'
        )
        gather.play(f"{BASE_URL}/api/twilio/audio/dynamic?text={ai_response}")
        response.append(gather)
        response.redirect(f'{BASE_URL}/api/twilio/timeout')

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.post("/timeout")
async def handle_timeout(request: Request):
    """タイムアウト時の処理"""
    form_data = await request.form()
    call_sid = form_data.get('CallSid')

    logger.info(f"[Twilio Timeout] CallSid={call_sid}")

    response = VoiceResponse()
    response.play(f"{BASE_URL}/api/twilio/audio/dynamic?text=もしもし、聞こえていますでしょうか。")

    gather = Gather(
        input='speech',
        language='ja-JP',
        timeout=5,
        speech_timeout='auto',
        action=f'{BASE_URL}/api/twilio/gather'
    )
    response.append(gather)
    response.hangup()

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.get("/audio/dynamic")
async def get_dynamic_audio(text: str = ""):
    """
    動的にテキストから音声を生成
    """
    if not text:
        return Response(status_code=400)

    try:
        # URL デコード
        from urllib.parse import unquote
        text = unquote(text)

        audio_content = synthesize_speech_google(text)
        logger.info(f"[Google TTS] 動的音声生成: {len(text)}文字 → {len(audio_content)} bytes")

        return Response(
            content=audio_content,
            media_type="audio/mpeg"
        )
    except Exception as e:
        logger.error(f"[Google TTS] エラー: {e}")
        return Response(status_code=500)


@router.get("/audio/greeting")
async def get_greeting_audio():
    """
    Google Cloud TTS で挨拶音声を生成して返す（テスト用）
    """
    text = "お忙しいところ恐れ入ります。グルメサポートの予約システムです。このメッセージが聞こえていれば、日本語音声テストは成功です。ありがとうございました。"

    try:
        audio_content = synthesize_speech_google(text)
        logger.info(f"[Google TTS] 音声生成成功: {len(audio_content)} bytes")

        return Response(
            content=audio_content,
            media_type="audio/mpeg"
        )
    except Exception as e:
        logger.error(f"[Google TTS] エラー: {e}")
        return Response(status_code=500)


@router.post("/status")
async def handle_status(request: Request):
    """
    通話ステータス更新時に呼ばれる
    """
    form_data = await request.form()
    call_sid = form_data.get('CallSid')
    call_status = form_data.get('CallStatus')
    call_duration = form_data.get('CallDuration')

    logger.info(f"[Twilio Status] CallSid={call_sid}, Status={call_status}, Duration={call_duration}")

    # 通話情報を更新
    if call_sid in active_calls:
        active_calls[call_sid]['status'] = call_status
        if call_duration:
            active_calls[call_sid]['duration'] = call_duration

        if call_status in ['completed', 'failed', 'busy', 'no-answer']:
            active_calls[call_sid]['ended_at'] = datetime.now().isoformat()
            logger.info(f"[Twilio] 通話終了: {active_calls[call_sid]}")

    return Response(status_code=200)


@router.websocket("/stream")
async def handle_stream(websocket: WebSocket):
    """
    双方向音声ストリーム（WebSocket）

    Twilioからの音声データを受信し、
    Google STT → Gemini → Google TTS で応答を生成して返す
    """
    await websocket.accept()
    logger.info("[Twilio Stream] WebSocket接続開始")

    call_sid = None
    stream_sid = None

    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            event = data.get('event')

            if event == 'start':
                # ストリーム開始
                stream_sid = data.get('streamSid')
                start_data = data.get('start', {})
                call_sid = start_data.get('customParameters', {}).get('call_sid')

                logger.info(f"[Twilio Stream] 開始: streamSid={stream_sid}, callSid={call_sid}")

            elif event == 'media':
                # 音声データ受信
                media = data.get('media', {})
                payload = media.get('payload')  # Base64エンコードされた音声

                if payload:
                    # TODO: Google STTに送信してテキスト化
                    # audio_data = base64.b64decode(payload)
                    # transcript = await transcribe_audio(audio_data)
                    pass

            elif event == 'mark':
                # マーカーイベント（音声再生完了など）
                mark_name = data.get('mark', {}).get('name')
                logger.info(f"[Twilio Stream] Mark: {mark_name}")

            elif event == 'stop':
                # ストリーム終了
                logger.info(f"[Twilio Stream] 終了: streamSid={stream_sid}")
                break

    except WebSocketDisconnect:
        logger.info(f"[Twilio Stream] 切断: streamSid={stream_sid}")
    except Exception as e:
        logger.error(f"[Twilio Stream] エラー: {e}")
    finally:
        await websocket.close()


# ========================================
# ヘルパー関数
# ========================================

async def send_audio_to_twilio(websocket: WebSocket, stream_sid: str, audio_base64: str):
    """
    Twilioに音声データを送信

    Args:
        websocket: WebSocket接続
        stream_sid: Twilioストリーム ID
        audio_base64: Base64エンコードされた音声データ（mulaw 8kHz）
    """
    message = {
        "event": "media",
        "streamSid": stream_sid,
        "media": {
            "payload": audio_base64
        }
    }
    await websocket.send_text(json.dumps(message))


async def send_mark_to_twilio(websocket: WebSocket, stream_sid: str, mark_name: str):
    """
    Twilioにマーカーを送信（音声再生完了の検知用）
    """
    message = {
        "event": "mark",
        "streamSid": stream_sid,
        "mark": {
            "name": mark_name
        }
    }
    await websocket.send_text(json.dumps(message))


# ========================================
# テスト用エンドポイント
# ========================================

@router.get("/test")
async def test_endpoint():
    """Webhook疎通確認用"""
    return {
        "status": "ok",
        "message": "Twilio webhook endpoint is ready",
        "active_calls": len(active_calls)
    }


@router.get("/calls")
async def get_active_calls():
    """アクティブな通話一覧（デバッグ用）"""
    return {
        "calls": list(active_calls.values())
    }
