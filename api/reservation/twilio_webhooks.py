"""
Twilio Webhook ハンドラー

仕様書準拠アーキテクチャ:
- Twilio Media Streams (WebSocket) で生音声を受信
- Google Cloud STT でリアルタイム音声認識
- Gemini で会話応答生成
- Google Cloud TTS で音声合成
- WebSocket で音声を送り返す
"""

import os
import json
import base64
import asyncio
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Start, Stream
from google.cloud import texttospeech
from google.cloud import speech
import google.generativeai as genai

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/twilio", tags=["twilio"])

# 環境変数
BASE_URL = os.environ.get('BASE_URL', 'https://your-app.run.app')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')

# Google Cloud クライアント
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

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

# インメモリ状態管理（テスト用）
active_calls = {}


# ========================================
# Google Cloud TTS
# ========================================

def synthesize_speech_google(text: str, voice_name: str = "ja-JP-Chirp3-HD-Leda") -> bytes:
    """
    Google Cloud TTS で音声を生成
    Twilio用に mulaw 8kHz で出力
    """
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="ja-JP",
        name=voice_name
    )

    # Twilio Media Streams 用: mulaw 8kHz
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MULAW,
        sample_rate_hertz=8000
    )

    response = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    return response.audio_content


def synthesize_speech_mp3(text: str, voice_name: str = "ja-JP-Chirp3-HD-Leda") -> bytes:
    """MP3形式で音声生成（<Play>用）"""
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="ja-JP",
        name=voice_name
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.0
    )

    response = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    return response.audio_content


# ========================================
# Google Cloud STT (Streaming)
# ========================================

def get_stt_streaming_config():
    """Google Cloud STT ストリーミング設定"""
    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
        sample_rate_hertz=8000,
        language_code="ja-JP",
        enable_automatic_punctuation=True,
        model="phone_call",  # 電話音声に最適化
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,  # 中間結果も取得
        single_utterance=False,  # 連続認識
    )

    return streaming_config


# ========================================
# Gemini 会話
# ========================================

def get_gemini_response(user_input: str, call_sid: str) -> str:
    """Gemini で会話応答を生成"""
    if not gemini_model:
        return "申し訳ございません。システムエラーが発生しました。"

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


# ========================================
# Webhook エンドポイント
# ========================================

@router.post("/answer")
async def handle_answer(request: Request):
    """
    Twilioが通話に応答した時に呼ばれる
    Media Streams (WebSocket) を開始する
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
    from urllib.parse import quote
    greeting = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['reserver_name']}と申します。{RESERVATION_INFO['date']}の{RESERVATION_INFO['time']}から{RESERVATION_INFO['guests']}名で予約をお願いしたいのですが、よろしいでしょうか。"

    # TwiMLレスポンス
    response = VoiceResponse()

    # 挨拶を再生（日本語をURLエンコード）
    encoded_greeting = quote(greeting, safe='')
    audio_url = f"{BASE_URL}/api/twilio/audio/dynamic?text={encoded_greeting}"
    logger.info(f"[Twilio Answer] Audio URL: {audio_url}")
    response.play(audio_url)

    # Media Streams (WebSocket) を開始
    start = Start()
    stream = Stream(
        url=f"wss://{BASE_URL.replace('https://', '').replace('http://', '')}/api/twilio/media-stream",
        track="both_tracks"  # 送受信両方
    )
    stream.parameter(name="call_sid", value=call_sid)
    start.append(stream)
    response.append(start)

    # 会話を継続（Streamが処理）
    response.pause(length=120)  # 最大2分

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """
    Twilio Media Streams (WebSocket)

    仕様書準拠:
    店員の声 → Twilio Audio Stream → Google STT → テキスト
                                                      ↓
                                                  Gemini API
                                                      ↓
    AIの声 ← Twilio Audio Stream ← Google TTS ← テキスト
    """
    await websocket.accept()
    logger.info("[Media Stream] WebSocket接続開始")

    call_sid = None
    stream_sid = None
    audio_buffer = []

    # STT ストリーミングセッション
    streaming_config = get_stt_streaming_config()

    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            event = data.get('event')

            if event == 'start':
                # ストリーム開始
                stream_sid = data.get('streamSid')
                start_data = data.get('start', {})
                custom_params = start_data.get('customParameters', {})
                call_sid = custom_params.get('call_sid')

                logger.info(f"[Media Stream] 開始: streamSid={stream_sid}, callSid={call_sid}")

            elif event == 'media':
                # 音声データ受信
                media = data.get('media', {})
                payload = media.get('payload')  # Base64エンコードされた mulaw 音声
                track = media.get('track')  # inbound or outbound

                # デバッグ: 最初の数回だけtrackをログ出力
                if len(audio_buffer) < 3:
                    logger.info(f"[Media Stream] 受信 track={track}, payload_len={len(payload) if payload else 0}")

                # inbound（相手の声）を処理
                if payload and track == 'inbound':
                    # Base64デコード
                    audio_data = base64.b64decode(payload)
                    audio_buffer.append(audio_data)

                    # 一定量たまったらSTT処理（50チャンク = 約1秒分）
                    if len(audio_buffer) >= 50:
                        logger.info(f"[Media Stream] STT処理開始: {len(audio_buffer)} chunks")
                        # バックグラウンドタスクとして実行（ブロッキングを避ける）
                        audio_to_process = b''.join(audio_buffer)
                        audio_buffer.clear()
                        asyncio.create_task(
                            process_audio_chunk(websocket, stream_sid, call_sid, audio_to_process)
                        )

            elif event == 'mark':
                # マーカーイベント
                mark_name = data.get('mark', {}).get('name')
                logger.info(f"[Media Stream] Mark: {mark_name}")

            elif event == 'stop':
                # ストリーム終了
                logger.info(f"[Media Stream] 終了: streamSid={stream_sid}")
                break

    except WebSocketDisconnect:
        logger.info(f"[Media Stream] 切断: streamSid={stream_sid}")
    except Exception as e:
        logger.error(f"[Media Stream] エラー: {e}")
    finally:
        # 通話終了処理
        if call_sid and call_sid in active_calls:
            active_calls[call_sid]['status'] = 'completed'
            active_calls[call_sid]['ended_at'] = datetime.now().isoformat()
            logger.info(f"[Media Stream] 通話完了: {active_calls[call_sid]}")


async def process_audio_chunk(websocket: WebSocket, stream_sid: str, call_sid: str, audio_data: bytes):
    """
    音声チャンクを処理
    Google STT → Gemini → Google TTS → Twilio送信
    """
    try:
        logger.info(f"[Process Audio] 処理開始: {len(audio_data)} bytes")

        # Google Cloud STT で音声認識（非同期化）
        audio = speech.RecognitionAudio(content=audio_data)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
            sample_rate_hertz=8000,
            language_code="ja-JP",
            model="phone_call",
        )

        # 同期関数をスレッドプールで実行
        response = await asyncio.to_thread(stt_client.recognize, config=config, audio=audio)
        logger.info(f"[STT] 結果数: {len(response.results)}")

        if response.results:
            transcript = response.results[0].alternatives[0].transcript
            confidence = response.results[0].alternatives[0].confidence

            logger.info(f"[STT] 認識: '{transcript}' (confidence: {confidence:.2f})")

            if transcript and confidence > 0.5:
                # 会話履歴に追加
                if call_sid in active_calls:
                    active_calls[call_sid]['transcript'].append({
                        'role': '店員',
                        'text': transcript,
                        'timestamp': datetime.now().isoformat()
                    })

                # Gemini で応答生成（非同期化）
                ai_response = await asyncio.to_thread(get_gemini_response, transcript, call_sid)
                logger.info(f"[Gemini] 応答: {ai_response}")

                # 会話履歴に追加
                if call_sid in active_calls:
                    active_calls[call_sid]['transcript'].append({
                        'role': 'AI',
                        'text': ai_response,
                        'timestamp': datetime.now().isoformat()
                    })

                # Google TTS で音声生成（非同期化）
                logger.info(f"[TTS] 音声生成開始: {len(ai_response)}文字")
                tts_audio = await asyncio.to_thread(synthesize_speech_google, ai_response)
                logger.info(f"[TTS] 音声生成完了: {len(tts_audio)} bytes")

                # Twilioに音声送信
                await send_audio_to_twilio(websocket, stream_sid, tts_audio)

    except Exception as e:
        import traceback
        logger.error(f"[Process Audio] エラー: {e}")
        logger.error(f"[Process Audio] トレースバック: {traceback.format_exc()}")


async def send_audio_to_twilio(websocket: WebSocket, stream_sid: str, audio_data: bytes):
    """
    Twilioに音声データを送信
    mulaw 8kHz 形式
    """
    logger.info(f"[Send Audio] 送信開始: {len(audio_data)} bytes")

    # チャンクに分割して送信（20msごと = 160バイト）
    chunk_size = 160
    chunks_sent = 0

    try:
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i:i + chunk_size]
            payload = base64.b64encode(chunk).decode('utf-8')

            message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {
                    "payload": payload
                }
            }

            await websocket.send_text(json.dumps(message))
            chunks_sent += 1

            # 送信間隔を短縮（5ms）
            await asyncio.sleep(0.005)

        # マーカー送信（音声再生完了通知）
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {
                "name": "audio_complete"
            }
        }
        await websocket.send_text(json.dumps(mark_message))
        logger.info(f"[Send Audio] 送信完了: {chunks_sent} chunks")

    except Exception as e:
        logger.warning(f"[Send Audio] 接続切断（{chunks_sent} chunks送信済）: {e}")


@router.post("/status")
async def handle_status(request: Request):
    """通話ステータス更新"""
    form_data = await request.form()
    call_sid = form_data.get('CallSid')
    call_status = form_data.get('CallStatus')
    call_duration = form_data.get('CallDuration')

    logger.info(f"[Twilio Status] CallSid={call_sid}, Status={call_status}, Duration={call_duration}")

    if call_sid in active_calls:
        active_calls[call_sid]['status'] = call_status
        if call_duration:
            active_calls[call_sid]['duration'] = call_duration

        if call_status in ['completed', 'failed', 'busy', 'no-answer']:
            active_calls[call_sid]['ended_at'] = datetime.now().isoformat()

    return Response(status_code=200)


@router.get("/audio/dynamic")
async def get_dynamic_audio(text: str = ""):
    """動的にテキストから音声を生成（MP3形式）"""
    if not text:
        return Response(status_code=400)

    try:
        from urllib.parse import unquote
        text = unquote(text)

        audio_content = synthesize_speech_mp3(text)
        logger.info(f"[Google TTS] 動的音声生成: {len(text)}文字 → {len(audio_content)} bytes")

        return Response(
            content=audio_content,
            media_type="audio/mpeg"
        )
    except Exception as e:
        logger.error(f"[Google TTS] エラー: {e}")
        return Response(status_code=500)


@router.get("/test")
async def test_endpoint():
    """Webhook疎通確認用"""
    return {
        "status": "ok",
        "message": "Twilio webhook endpoint is ready (Google Cloud STT/TTS)",
        "active_calls": len(active_calls),
        "architecture": {
            "stt": "Google Cloud Speech-to-Text",
            "llm": "Gemini 2.0 Flash",
            "tts": "Google Cloud Text-to-Speech"
        }
    }


@router.get("/calls")
async def get_active_calls():
    """アクティブな通話一覧（デバッグ用）"""
    return {
        "calls": list(active_calls.values())
    }
