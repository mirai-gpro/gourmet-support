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
quick_hai_audio = None  # 短い相槌「はい。」
greeting_audio = None  # AI挨拶音声（事前生成）

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
    """即座の相槌音声とAI挨拶音声を事前生成"""
    global acknowledgment_audio, quick_hai_audio, greeting_audio
    try:
        # 初回用の相槌
        acknowledgment_audio = synthesize_speech_mp3("はい、かしこまりました。")
        logger.info(f"[Init] 初回相槌音声生成完了: {len(acknowledgment_audio)} bytes")

        # 2回目以降用の短い相槌
        quick_hai_audio = synthesize_speech_mp3("はい。")
        logger.info(f"[Init] 短い相槌音声生成完了: {len(quick_hai_audio)} bytes")

        # AI挨拶音声
        greeting_text = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。私は{RESERVATION_INFO['reserver_name']}様のAIアシスタントです。{RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']}の{RESERVATION_INFO['time']}から、{RESERVATION_INFO['reserver_name']}様名義で{RESERVATION_INFO['guests']}名、{RESERVATION_INFO['seat_type']}で、予約をお願いできますでしょうか。"
        greeting_audio = synthesize_speech_mp3(greeting_text)
        logger.info(f"[Init] AI挨拶音声生成完了: {len(greeting_audio)} bytes")
    except Exception as e:
        logger.error(f"[Init] 音声生成エラー: {e}")

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
以下の予約情報で予約を取ってください。丁寧な日本語で簡潔に話してください（1-2文）。

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

【あなたの応答】:"""

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
        'transcript': [],
        'is_first_interaction': True  # 最初の応答フラグ
    }

    # TwiMLレスポンス
    response = VoiceResponse()

    # 最初の挨拶は再生せず、店員の発話を待つ

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
    logger.info("[Media Stream] WebSocket接続開始 (Streaming STT)")

    call_sid = None
    stream_sid = None

    # ストリーミングSTT用のキュー
    audio_queue = asyncio.Queue()
    streaming_task = None

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

                # Google STT Streaming セッションを開始
                streaming_task = asyncio.create_task(
                    stt_streaming_session(audio_queue, call_sid, websocket, stream_sid)
                )
                logger.info(f"[STT Streaming] セッション開始")

                # 2.5秒経っても店員が話さない場合、AIから挨拶を開始
                if call_sid:
                    asyncio.create_task(fallback_greeting(call_sid, 2.5))

            elif event == 'media':
                # 音声データ受信
                media = data.get('media', {})
                payload = media.get('payload')  # Base64エンコードされた mulaw 音声
                track = media.get('track')  # inbound or outbound

                # inbound（相手の声）を処理
                if payload and track == 'inbound':
                    # AI音声再生中かチェック
                    if call_sid and call_sid in active_calls:
                        is_playing = active_calls[call_sid].get('is_playing_audio', False)
                        if is_playing:
                            # AI音声再生中はストリーミングをスキップ
                            continue

                    # Base64デコード
                    audio_data = base64.b64decode(payload)

                    # ストリーミングキューに追加（即座に送信）
                    await audio_queue.put(audio_data)

            elif event == 'mark':
                # マーカーイベント
                mark_name = data.get('mark', {}).get('name')
                logger.info(f"[Media Stream] Mark: {mark_name}")

            elif event == 'stop':
                # ストリーム終了
                logger.info(f"[Media Stream] 終了: streamSid={stream_sid}")
                # ストリーミングタスクを終了
                if streaming_task:
                    await audio_queue.put(None)  # 終了シグナル
                break

    except WebSocketDisconnect:
        logger.info(f"[Media Stream] 切断: streamSid={stream_sid}")
    except Exception as e:
        logger.error(f"[Media Stream] エラー: {e}")
    finally:
        # ストリーミングタスクをキャンセル
        if streaming_task and not streaming_task.done():
            streaming_task.cancel()
            try:
                await streaming_task
            except asyncio.CancelledError:
                pass

        # 通話終了処理
        if call_sid and call_sid in active_calls:
            active_calls[call_sid]['status'] = 'completed'
            active_calls[call_sid]['ended_at'] = datetime.now().isoformat()
            logger.info(f"[Media Stream] 通話完了: {active_calls[call_sid]}")
        # ロックをクリーンアップ
        if call_sid and call_sid in audio_locks:
            del audio_locks[call_sid]

async def stt_streaming_session(audio_queue: asyncio.Queue, call_sid: str, websocket: WebSocket, stream_sid: str):
    """
    Google Cloud STT Streaming セッション
    音声チャンクをリアルタイムで送信し、is_finalフラグで発話終了を検知
    """
    import queue
    import threading

    try:
        logger.info(f"[STT Streaming] セッション開始: call_sid={call_sid}")

        # 入力キュー: 非同期→同期（音声チャンク）
        input_queue = queue.Queue()

        # 出力キュー: 同期→非同期（認識結果）
        output_queue = queue.Queue()

        # ストリーミング認識を実行する同期関数
        def run_streaming_recognize():
            """同期スレッドでストリーミング認識を実行"""
            accumulated_audio = []  # スレッド内でローカルに定義

            try:
                # ストリーミング設定
                streaming_config = get_stt_streaming_config()

                # リクエストジェネレーター（同期）
                def request_generator():
                    """音声チャンクを生成（同期）"""
                    while True:
                        chunk = input_queue.get()
                        if chunk is None:  # 終了シグナル
                            break

                        # チャンクを蓄積
                        accumulated_audio.append(chunk)

                        # ストリーミングリクエストを生成
                        yield speech.StreamingRecognizeRequest(audio_content=chunk)

                # ストリーミング認識を実行
                responses = stt_client.streaming_recognize(streaming_config, request_generator())

                # 応答を処理
                for response in responses:
                    if not response.results:
                        continue

                    for result in response.results:
                        if not result.alternatives:
                            continue

                        transcript = result.alternatives[0].transcript
                        confidence = result.alternatives[0].confidence if result.alternatives else 0.0

                        logger.info(f"[STT Streaming] 中間結果: '{transcript}' is_final={result.is_final} confidence={confidence:.2f}")

                        # is_final=True の場合のみ処理
                        if result.is_final and transcript and confidence > 0.5:
                            logger.info(f"[STT Streaming] 発話終了検知: '{transcript}'")

                            # 蓄積された音声データを取得
                            audio_data = b''.join(accumulated_audio)
                            accumulated_audio.clear()

                            # 出力キューに結果を送信
                            output_queue.put(('transcript', transcript, confidence, audio_data))

            except Exception as e:
                import traceback
                logger.error(f"[STT Streaming Thread] エラー: {e}")
                logger.error(f"[STT Streaming Thread] トレースバック: {traceback.format_exc()}")

        # ストリーミング認識をバックグラウンドスレッドで開始
        streaming_thread = threading.Thread(target=run_streaming_recognize, daemon=True)
        streaming_thread.start()
        logger.info(f"[STT Streaming] スレッド開始")

        # 非同期ループ: 音声チャンクを送信 & 結果を受信
        while True:
            # 音声チャンクを取得（ブロッキング）
            chunk = await audio_queue.get()

            if chunk is None:  # 終了シグナル
                input_queue.put(None)
                break

            # チャンクをスレッドに送信
            input_queue.put(chunk)

            # ノンブロッキングで結果をチェック
            try:
                while not output_queue.empty():
                    item = output_queue.get_nowait()
                    if isinstance(item, tuple) and item[0] == 'transcript':
                        _, transcript, confidence, audio_data = item
                        # 音声処理タスクを開始
                        asyncio.create_task(
                            process_audio_chunk_with_transcript(
                                websocket, stream_sid, call_sid, audio_data, transcript, confidence
                            )
                        )
            except queue.Empty:
                pass

        # スレッドの終了を待つ
        await asyncio.to_thread(streaming_thread.join, timeout=5.0)
        logger.info(f"[STT Streaming] セッション終了")

    except asyncio.CancelledError:
        logger.info(f"[STT Streaming] セッション中断: call_sid={call_sid}")
        # 終了シグナルを送信
        try:
            input_queue.put(None)
        except:
            pass
    except Exception as e:
        import traceback
        logger.error(f"[STT Streaming] エラー: {e}")
        logger.error(f"[STT Streaming] トレースバック: {traceback.format_exc()}")


async def process_audio_chunk_with_transcript(websocket: WebSocket, stream_sid: str, call_sid: str, audio_data: bytes, transcript: str, confidence: float):
    """
    音声チャンクを処理（STTの結果を既に持っている版）
    即答相槌 → Gemini → Google TTS → Twilio送信
    """
    try:
        logger.info(f"[Process Audio] 処理開始: transcript='{transcript}', confidence={confidence:.2f}")

        # 最初の応答かチェック
        if call_sid and call_sid in active_calls:
            is_first = active_calls[call_sid].get('is_first_interaction', False)
            if is_first:
                logger.info(f"[First Interaction] 店員の第一声を検知 → AI挨拶を開始")
                active_calls[call_sid]['is_first_interaction'] = False

                # 事前生成済みのAI挨拶を再生
                if greeting_audio:
                    greeting_id = str(uuid.uuid4())
                    audio_cache[greeting_id] = greeting_audio

                    # AI音声再生中フラグを設定
                    active_calls[call_sid]['is_playing_audio'] = True

                    # 挨拶を再生
                    await update_call_with_audio(call_sid, greeting_id)

                    # 推定時間後にフラグを解除（約30-35秒）
                    asyncio.create_task(reset_playing_flag(call_sid, 35.0))

                    logger.info(f"[First Interaction] AI挨拶再生完了")
                return

        # 音声再生中は処理をスキップ
        if call_sid and call_sid in active_calls:
            if active_calls[call_sid].get('is_playing_audio', False):
                logger.info(f"[Process Audio] 音声再生中のためスキップ: {call_sid}")
                return

        # 🔥 会話履歴に追加する前に店員の発話回数をカウント
        staff_count = sum(1 for item in active_calls.get(call_sid, {}).get('transcript', []) if item['role'] == '店員')
        is_first_response = staff_count == 0  # 店員の1回目

        logger.info(f"[Response Check] 店員発話回数={staff_count}, 初回={is_first_response}")

        # 会話履歴に追加
        if call_sid in active_calls:
            active_calls[call_sid]['transcript'].append({
                'role': '店員',
                'text': transcript,
                'timestamp': datetime.now().isoformat()
            })

        # 即答モード（全ての発話に対して相槌を入れる）
        quick_audio = None
        quick_delay = 0
        quick_text = ""

        if is_first_response and acknowledgment_audio:
            # 初回: 丁寧な相槌
            quick_audio = acknowledgment_audio
            quick_delay = 1.5  # やや長め
            quick_text = "はい、かしこまりました。"
            logger.info(f"[Quick Response] 初回応答 → 「{quick_text}」")
        elif quick_hai_audio:
            # 2回目以降: 短い相槌
            quick_audio = quick_hai_audio
            quick_delay = 0.6
            quick_text = "はい。"
            logger.info(f"[Quick Response] 通常応答 → 「{quick_text}」")
        else:
            logger.warning(f"[Quick Response] 相槌音声が利用不可")

        # 即答音声を再生（Gemini処理と並列化）
        if quick_audio:
            logger.info(f"[Quick Response] 即答音声再生開始 + Gemini並列処理")
            quick_id = str(uuid.uuid4())
            audio_cache[quick_id] = quick_audio

            # バックグラウンドで即答音声を再生（待たない）
            asyncio.create_task(update_call_with_audio(call_sid, quick_id))
            logger.info(f"[Quick Response] 即答音声「{quick_text}」バックグラウンド再生開始")
        else:
            logger.warning(f"[Quick Response] 相槌音声が利用不可")

        # 復唱モード検知
        if "復唱" in transcript:
            if call_sid in active_calls:
                active_calls[call_sid]['in_recitation_mode'] = True
                logger.info(f"[Recitation Mode] 復唱モード開始")

        # Gemini で応答生成（非同期化）
        logger.info(f"[LLM] Gemini処理開始")
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

        # 音声の長さを推定（日本語: 1文字あたり0.25秒 + バッファ）
        estimated_duration = len(ai_response) * 0.25 + 2.0

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

        # 復唱モード解除
        if call_sid in active_calls and active_calls.get(call_sid, {}).get('in_recitation_mode', False):
            active_calls[call_sid]['in_recitation_mode'] = False
            logger.info(f"[Recitation Mode] 復唱モード終了")

    except Exception as e:
        import traceback
        logger.error(f"[Process Audio] エラー: {e}")
        logger.error(f"[Process Audio] トレースバック: {traceback.format_exc()}")


async def process_audio_chunk(websocket: WebSocket, stream_sid: str, call_sid: str, audio_data: bytes):
    """
    音声チャンクを処理
    Google STT → 即答相槌 → Gemini → Google TTS → Twilio送信
    """
    try:
        logger.info(f"[Process Audio] 処理開始: {len(audio_data)} bytes")

        # 最初の応答かチェック
        if call_sid and call_sid in active_calls:
            is_first = active_calls[call_sid].get('is_first_interaction', False)
            if is_first:
                logger.info(f"[First Interaction] 店員の第一声を検知 → AI挨拶を開始")
                active_calls[call_sid]['is_first_interaction'] = False

                # 事前生成済みのAI挨拶を再生
                if greeting_audio:
                    greeting_id = str(uuid.uuid4())
                    audio_cache[greeting_id] = greeting_audio

                    # AI音声再生中フラグを設定
                    active_calls[call_sid]['is_playing_audio'] = True

                    # 挨拶を再生
                    await update_call_with_audio(call_sid, greeting_id)

                    # 推定時間後にフラグを解除（約30-35秒）
                    asyncio.create_task(reset_playing_flag(call_sid, 35.0))

                    logger.info(f"[First Interaction] AI挨拶再生完了")
                return

        # 音声再生中は処理をスキップ
        if call_sid and call_sid in active_calls:
            if active_calls[call_sid].get('is_playing_audio', False):
                logger.info(f"[Process Audio] 音声再生中のためスキップ: {call_sid}")
                return

        # Google Cloud STT で音声認識（非同期化）
        audio = speech.RecognitionAudio(content=audio_data)
        
        # フレーズヒントの設定
        phrases = [
            "予約", "空席", "満席", "お取りできます", "承知いたしました",
            "かしこまりました", "少々お待ちください", "確認いたします",
            "テーブル席", "カウンター席", "個室", "お名前", "人数",
            "日時", "時間", "お電話番号"
        ]
        
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
            sample_rate_hertz=8000,
            language_code="ja-JP",
            model="phone_call",
            enable_automatic_punctuation=True,
            speech_contexts=[speech.SpeechContext(phrases=phrases)],
            # VAD（Voice Activity Detection）を有効化
            use_enhanced=True,  # 高精度モデルを使用
        )

        # 同期関数をスレッドプールで実行
        response = await asyncio.to_thread(stt_client.recognize, config=config, audio=audio)
        logger.info(f"[STT] 結果数: {len(response.results)}")

        if response.results:
            transcript = response.results[0].alternatives[0].transcript
            confidence = response.results[0].alternatives[0].confidence

            logger.info(f"[STT] 認識: '{transcript}' (confidence: {confidence:.2f})")

            # 🔥 デバッグ: confidence チェック前
            logger.info(f"[DEBUG] confidence={confidence}, 閾値チェック: {confidence > 0.5}")

            if transcript and confidence > 0.5:
                logger.info(f"[DEBUG] 条件通過: transcript='{transcript}', confidence={confidence}")
                
                # 🔥 会話履歴に追加する前に店員の発話回数をカウント
                staff_count = sum(1 for item in active_calls.get(call_sid, {}).get('transcript', []) if item['role'] == '店員')
                is_first_response = staff_count == 0  # 店員の1回目
                
                logger.info(f"[Response Check] 店員発話回数={staff_count}, 初回={is_first_response}")
                logger.info(f"[DEBUG] acknowledgment_audio存在: {acknowledgment_audio is not None}")
                logger.info(f"[DEBUG] quick_hai_audio存在: {quick_hai_audio is not None}")

                # 会話履歴に追加
                if call_sid in active_calls:
                    active_calls[call_sid]['transcript'].append({
                        'role': '店員',
                        'text': transcript,
                        'timestamp': datetime.now().isoformat()
                    })
                    logger.info(f"[DEBUG] 履歴追加完了")

                # 即答モード（全ての発話に対して相槌を入れる）
                quick_audio = None
                quick_delay = 0
                quick_text = ""
                
                if is_first_response and acknowledgment_audio:
                    # 初回: 丁寧な相槌
                    quick_audio = acknowledgment_audio
                    quick_delay = 1.5  # やや長め
                    quick_text = "はい、かしこまりました。"
                    logger.info(f"[Quick Response] 初回応答 → 「{quick_text}」")
                elif quick_hai_audio:
                    # 2回目以降: 短い相槌
                    quick_audio = quick_hai_audio
                    quick_delay = 0.6
                    quick_text = "はい。"
                    logger.info(f"[Quick Response] 通常応答 → 「{quick_text}」")
                else:
                    logger.warning(f"[Quick Response] 相槌音声が利用不可")

                # 即答音声を再生（Gemini処理と並列化）
                if quick_audio:
                    logger.info(f"[Quick Response] 即答音声再生開始 + Gemini並列処理")
                    quick_id = str(uuid.uuid4())
                    audio_cache[quick_id] = quick_audio

                    # バックグラウンドで即答音声を再生（待たない）
                    asyncio.create_task(update_call_with_audio(call_sid, quick_id))
                    logger.info(f"[Quick Response] 即答音声「{quick_text}」バックグラウンド再生開始")
                else:
                    logger.warning(f"[Quick Response] 相槌音声が利用不可")

                # 復唱モード検知
                if "復唱" in transcript:
                    if call_sid in active_calls:
                        active_calls[call_sid]['in_recitation_mode'] = True
                        logger.info(f"[Recitation Mode] 復唱モード開始")

                # Gemini で応答生成（非同期化）
                logger.info(f"[LLM] Gemini処理開始")
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

                # 音声の長さを推定（日本語: 1文字あたり0.25秒 + バッファ）
                estimated_duration = len(ai_response) * 0.25 + 2.0

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
            else:
                logger.info(f"[DEBUG] 条件不通過: transcript='{transcript}', confidence={confidence}")

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


async def fallback_greeting(call_sid: str, timeout: float):
    """
    タイムアウト時にAIから挨拶を開始（店員が話さない場合）
    """
    await asyncio.sleep(timeout)

    # まだ最初の応答フラグが立っているかチェック
    if call_sid in active_calls and active_calls[call_sid].get('is_first_interaction', False):
        logger.info(f"[Fallback Greeting] {timeout}秒経過、店員が話さないためAIから挨拶を開始")
        active_calls[call_sid]['is_first_interaction'] = False

        # 事前生成済みのAI挨拶を再生
        if greeting_audio:
            greeting_id = str(uuid.uuid4())
            audio_cache[greeting_id] = greeting_audio

            # AI音声再生中フラグを設定
            active_calls[call_sid]['is_playing_audio'] = True

            # 挨拶を再生
            await update_call_with_audio(call_sid, greeting_id)

            # 推定時間後にフラグを解除（約30-35秒）
            asyncio.create_task(reset_playing_flag(call_sid, 35.0))

            logger.info(f"[Fallback Greeting] AI挨拶再生完了")


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
