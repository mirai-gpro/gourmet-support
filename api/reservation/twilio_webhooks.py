"""
Twilio Webhook ハンドラー

エンドポイント:
- POST /api/twilio/answer    - 発信応答時
- POST /api/twilio/status    - 通話ステータス更新
- WS   /api/twilio/stream    - 音声ストリーム（WebSocket）
"""

import os
import json
import base64
import logging
from datetime import datetime
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Stream, Say

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/twilio", tags=["twilio"])

# 環境変数
BASE_URL = os.environ.get('BASE_URL', 'https://your-app.run.app')


# ========================================
# インメモリ状態管理（テスト用）
# 本番ではSupabaseに置き換え
# ========================================
active_calls = {}


# ========================================
# Webhook エンドポイント
# ========================================

@router.post("/answer")
async def handle_answer(request: Request):
    """
    Twilioが通話に応答した時に呼ばれる
    シンプル版：日本語音声テスト用
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

    # TwiMLレスポンスを生成（シンプル版 - Streamなし）
    response = VoiceResponse()
    response.say(
        'お忙しいところ恐れ入ります。グルメサポートの予約システムです。'
        'このメッセージが聞こえていれば、日本語音声テストは成功です。'
        'ありがとうございました。',
        language='ja-JP',
        voice='Polly.Mizuki'
    )

    return Response(
        content=str(response),
        media_type="application/xml"
    )


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
