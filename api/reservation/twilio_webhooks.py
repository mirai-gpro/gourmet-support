"""
Twilio Webhook ハンドラー

仕様書準拠アーキテクチャ:
- Twilio Media Streams (WebSocket) で生音声を受信
- Google Cloud STT でリアルタイム音声認識
- Gemini で会話応答生成
- Google Cloud TTS で音声合成
- HTTP経由で音声を再生（Twilio REST API使用）
"""

import os
import json
import base64
import asyncio
import logging
import uuid
import httpx
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Start, Stream, Pause
from google.cloud import texttospeech
from google.cloud import speech
import google.generativeai as genai

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/twilio", tags=["twilio"])

# 環境変数
BASE_URL = os.environ.get('BASE_URL', 'https://your-app.run.app')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')

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
    "restaurant_name": "レストラン岡部",
    "reserver_name": "山田太郎",
    "contact_phone": "090-1234-5678",
    "date": "12月25日",
    "day_of_week": "日曜日",
    "time": "19時",
    "guests": 4,
    "seat_type": "テーブル席",
    "flexibility": "30分程度なら前後可能",
    "notes": "誕生日のお祝い"
}

# インメモリ状態管理（テスト用）
active_calls = {}

# 音声送信用のロック（通話ごと）
audio_locks = {}

# 音声キャッシュ（audio_id -> MP3バイナリ）
audio_cache = {}

# 即座の相槌音声（事前生成）
acknowledgment_audio = None


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


def initialize_acknowledgment_audio():
    """即座の相槌音声を事前生成"""
    global acknowledgment_audio
    try:
        acknowledgment_audio = synthesize_speech_mp3("はい、かしこまりました。")
        logger.info(f"[Init] 相槌音声生成完了: {len(acknowledgment_audio)} bytes")
    except Exception as e:
        logger.error(f"[Init] 相槌音声生成エラー: {e}")

# アプリ起動時に相槌音声を生成
initialize_acknowledgment_audio()


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
    history_text = "\n".join([f"{h['role']}: {h['text']}" for h in history[-10:]])

    prompt = f"""あなたはレストラン予約の電話をかけている予約代行AIです。
以下の予約情報で予約を取ってください。

【重要な指示】
- 簡潔に応答してください（1-2文、30文字以内）
- 既に伝えた情報は繰り返さないでください
- 店員が「それでは」「失礼します」など終了を示唆したら、「ありがとうございました」で終了してください
- 店員の発言が不明瞭や誤認識の可能性がある場合は、会話の文脈から適切に推測して応答してください
- 店員が確認している内容には「はい」「そうです」など肯定で答えてください

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

【あなたの応答】（1-2文、30文字以内）:"""

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
    greeting = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。私は{RESERVATION_INFO['reserver_name']}様のAIアシスタントです。{RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']}の{RESERVATION_INFO['time']}から、{RESERVATION_INFO['reserver_name']}様名義で{RESERVATION_INFO['guests']}名、{RESERVATION_INFO['seat_type']}で、予約をお願いできますでしょうか。"

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

                # 音声送信用ロックを初期化
                if call_sid:
                    audio_locks[call_sid] = asyncio.Lock()

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
                # AI音声再生中はバッファリングをスキップ
                if payload and track == 'inbound':
                    # AI音声再生中かチェック
                    if call_sid and call_sid in active_calls:
                        is_playing = active_calls[call_sid].get('is_playing_audio', False)
                        if is_playing:
                            # AI音声再生中はバッファリングしない
                            continue

                    # Base64デコード
                    audio_data = base64.b64decode(payload)
                    audio_buffer.append(audio_data)

                    # 音声エネルギーで発話検知（mulaw: 128が無音、差が大きいほど音声あり）
                    energy = sum(abs(b - 128) for b in audio_data) / len(audio_data)

                    # 発話中かどうかを判定（通話ごとの無音カウンター）
                    if call_sid and call_sid in active_calls:
                        if energy > 3:  # 閾値: 発話中（より敏感に検知）
                            active_calls[call_sid]['silence_chunks'] = 0
                        else:
                            active_calls[call_sid]['silence_chunks'] = active_calls[call_sid].get('silence_chunks', 0) + 1
                        silence_chunks = active_calls[call_sid].get('silence_chunks', 0)
                    else:
                        silence_chunks = 0

                    # 発話終了を検知（無音が30チャンク=約600ms続いた場合）
                    # かつ、バッファに十分なデータがある場合（最低0.5秒）
                    if len(audio_buffer) >= 25 and silence_chunks >= 30:
                        logger.info(f"[Media Stream] 発話終了検知: {len(audio_buffer)} chunks, silence={silence_chunks}")
                        # バックグラウンドタスクとして実行
                        audio_to_process = b''.join(audio_buffer)
                        audio_buffer.clear()
                        if call_sid in active_calls:
                            active_calls[call_sid]['silence_chunks'] = 0
                        asyncio.create_task(
                            process_audio_chunk(websocket, stream_sid, call_sid, audio_to_process)
                        )
                    # 最大バッファサイズに達した場合も処理（10秒分）
                    elif len(audio_buffer) >= 500:
                        logger.info(f"[Media Stream] 最大バッファ: {len(audio_buffer)} chunks")
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
        # ロックをクリーンアップ
        if call_sid and call_sid in audio_locks:
            del audio_locks[call_sid]


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

                # Google TTS で音声生成（MP3形式）
                logger.info(f"[TTS] 音声生成開始: {len(ai_response)}文字")
                tts_audio = await asyncio.to_thread(synthesize_speech_mp3, ai_response)
                logger.info(f"[TTS] 音声生成完了: {len(tts_audio)} bytes")

                # 音声をキャッシュに保存
                audio_id = str(uuid.uuid4())
                audio_cache[audio_id] = tts_audio
                logger.info(f"[Audio Cache] 保存: {audio_id}")

                # 音声の長さを推定（日本語: 1文字あたり0.2秒）
                estimated_duration = len(ai_response) * 0.2 + 1.0  # +1秒バッファ

                # AI音声再生中フラグを設定
                if call_sid in active_calls:
                    active_calls[call_sid]['is_playing_audio'] = True
                    logger.info(f"[Audio Playback] 開始フラグ設定: {estimated_duration:.1f}秒")

                # Twilio REST API で通話を更新して音声再生
                lock = audio_locks.get(call_sid)
                if lock:
                    async with lock:
                        logger.info(f"[Twilio API] 通話更新開始: {call_sid}")
                        await update_call_with_audio(call_sid, audio_id)
                else:
                    await update_call_with_audio(call_sid, audio_id)

                # 推定時間後にフラグを解除
                asyncio.create_task(reset_playing_flag(call_sid, estimated_duration))

    except Exception as e:
        import traceback
        logger.error(f"[Process Audio] エラー: {e}")
        logger.error(f"[Process Audio] トレースバック: {traceback.format_exc()}")


async def reset_playing_flag(call_sid: str, delay: float):
    """音声再生完了後にフラグを解除"""
    await asyncio.sleep(delay)
    if call_sid in active_calls:
        active_calls[call_sid]['is_playing_audio'] = False
        logger.info(f"[Audio Playback] 終了フラグ解除: {call_sid}")


async def update_call_with_audio(call_sid: str, audio_id: str):
    """
    Twilio REST API を使って通話を更新し、音声を再生する
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logger.error("[Twilio API] 認証情報が未設定")
        return

    # TwiML URL を生成
    twiml_url = f"{BASE_URL}/api/twilio/play-audio/{audio_id}"
    logger.info(f"[Twilio API] TwiML URL: {twiml_url}")

    # Twilio REST API で通話を更新
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{call_sid}.json"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"Url": twiml_url}
            )
            if response.status_code == 200:
                logger.info(f"[Twilio API] 通話更新成功: {call_sid}")
            else:
                logger.error(f"[Twilio API] 通話更新失敗: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"[Twilio API] エラー: {e}")


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


@router.post("/play-audio/{audio_id}")
async def play_audio_twiml(audio_id: str, request: Request):
    """
    音声を再生してMedia Streamを再開するTwiML
    Twilio REST API から呼ばれる
    """
    form_data = await request.form()
    call_sid = form_data.get('CallSid')
    logger.info(f"[Play Audio] TwiML生成: audio_id={audio_id}, call_sid={call_sid}")

    response = VoiceResponse()

    # キャッシュから音声があるか確認
    if audio_id in audio_cache:
        # 音声を再生
        audio_url = f"{BASE_URL}/api/twilio/audio/{audio_id}"
        response.play(audio_url)
        logger.info(f"[Play Audio] 音声再生: {audio_url}")
    else:
        logger.warning(f"[Play Audio] 音声キャッシュなし: {audio_id}")

    # Media Streamを再開
    start = Start()
    stream = Stream(
        url=f"wss://{BASE_URL.replace('https://', '').replace('http://', '')}/api/twilio/media-stream",
        track="both_tracks"
    )
    if call_sid:
        stream.parameter(name="call_sid", value=call_sid)
    start.append(stream)
    response.append(start)

    # 通話を継続
    response.pause(length=120)

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.get("/audio/{audio_id}")
async def get_cached_audio(audio_id: str):
    """キャッシュされた音声を返す"""
    if audio_id in audio_cache:
        audio_content = audio_cache[audio_id]
        # 使用後にキャッシュから削除（メモリ節約）
        del audio_cache[audio_id]
        logger.info(f"[Audio Cache] 取得・削除: {audio_id}, {len(audio_content)} bytes")
        return Response(
            content=audio_content,
            media_type="audio/mpeg"
        )
    else:
        logger.warning(f"[Audio Cache] 見つからない: {audio_id}")
        return Response(status_code=404)


@router.get("/test")
async def test_endpoint():
    """Webhook疎通確認用"""
    return {
        "status": "ok",
        "message": "Twilio webhook endpoint is ready (HTTP Audio Playback)",
        "active_calls": len(active_calls),
        "audio_cache_size": len(audio_cache),
        "architecture": {
            "stt": "Google Cloud Speech-to-Text",
            "llm": "Gemini 2.0 Flash",
            "tts": "Google Cloud Text-to-Speech",
            "audio_playback": "HTTP via Twilio REST API"
        }
    }


@router.get("/calls")
async def get_active_calls():
    """アクティブな通話一覧（デバッグ用）"""
    return {
        "calls": list(active_calls.values())
    }
