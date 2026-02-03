#!/usr/bin/env python3
"""
ローカル音声会話テストツール (Twilio完全準拠版 - Streaming STT)

Twilioを使わずに、PCのマイク/ヘッドセットで会話をテスト
- マイクから店員発話を録音（リアルタイムストリーミング）
- Google Cloud STT Streaming API で音声認識（is_finalフラグで発話終了検知）
- Gemini で応答生成
- Google Cloud TTS で音声合成
- スピーカーで音声再生

twilio_webhooks.py と完全同一ロジック:
- 店員の第一声を検知したら、事前生成済みのAI挨拶を即答
- 全発話に即答相槌（初回: 丁寧1.5秒、2回目以降: 短0.6秒）
- 復唱モード対応
- Google STT Streaming API の is_final フラグで発話終了検知（VADなし）

使用方法:
    python api/reservation/test_voice_conversation.py [--save-audio]

オプション:
    --save-audio  会話全体を1つのWAVファイルとして保存（output/ディレクトリに保存）
                  ※個別ターンの音声ファイルは保存されません
"""

import os
import sys
import time
import wave
import pyaudio
import argparse
import queue
import threading
from pathlib import Path
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# 音声再生用
try:
    import pygame
    # PyAudioと同じ設定で初期化（16kHz、モノラル、16bit）
    # これによりオーディオデバイスの競合を防ぐ
    pygame.mixer.init(frequency=16000, size=-16, channels=1, buffer=512)
    USE_PYGAME = True
except ImportError:
    USE_PYGAME = False
    print("[警告] pygame がインストールされていません。音声再生機能が制限されます。")
    print("インストール: pip install pygame")

# Google Cloud クライアント
from google.cloud import speech
from google.cloud import texttospeech
import google.generativeai as genai

# 環境変数（GEMINI_API_KEYに統一、互換性のためGOOGLE_API_KEYもフォールバック）
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY', '')

# Google Cloud クライアント初期化
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

# Gemini 初期化
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
else:
    print("[エラー] GEMINI_API_KEY または GOOGLE_API_KEY 環境変数が未設定です")
    print("ヒント: .env ファイルに GEMINI_API_KEY=your-key を設定してください")
    sys.exit(1)

# 予約情報（テスト用）
RESERVATION_INFO = {
    "restaurant_name": "リストランテ鈴木",
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

# 会話履歴
conversation_history = []

# 音声設定
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000  # 16kHz（STT用）

# ストリーミングSTT設定
def get_stt_streaming_config():
    """Google Cloud STT ストリーミング設定"""
    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code="ja-JP",
        enable_automatic_punctuation=True,
        model="default",
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,  # 中間結果も取得
        single_utterance=False,  # 連続認識
    )

    return streaming_config

# 状態管理
is_first_interaction = True
in_recitation_mode = False

# 事前生成音声
acknowledgment_audio = None  # 「はい、かしこまりました。」
quick_hai_audio = None       # 「はい。」
greeting_audio = None        # AI挨拶音声


def synthesize_speech_mp3(text: str) -> bytes:
    """Google Cloud TTS で音声生成（MP3形式）"""
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="ja-JP",
        name="ja-JP-Chirp3-HD-Leda"
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


def synthesize_speech_linear16(text: str) -> bytes:
    """Google Cloud TTS で音声生成（LINEAR16形式、録音用）"""
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="ja-JP",
        name="ja-JP-Chirp3-HD-Leda"
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,  # 16kHz
        speaking_rate=1.0
    )

    response = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    return response.audio_content


def initialize_audio():
    """即答音声とAI挨拶音声を事前生成"""
    global acknowledgment_audio, quick_hai_audio, greeting_audio
    
    try:
        print("[初期化] 相槌音声を生成中...")
        
        # 初回用の相槌
        acknowledgment_audio = synthesize_speech_mp3("はい、かしこまりました。")
        print(f"  ✓ 初回相槌: {len(acknowledgment_audio)} bytes")
        
        # 2回目以降用の短い相槌
        quick_hai_audio = synthesize_speech_mp3("はい。わかりました。")
        print(f"  ✓ 短い相槌: {len(quick_hai_audio)} bytes")
        
        # AI挨拶音声
        greeting_text = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。私は{RESERVATION_INFO['reserver_name']}様のAIアシスタントです。{RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']}の{RESERVATION_INFO['time']}から、{RESERVATION_INFO['reserver_name']}様名義で{RESERVATION_INFO['guests']}名、{RESERVATION_INFO['seat_type']}で、予約をお願いできますでしょうか。"
        greeting_audio = synthesize_speech_mp3(greeting_text)
        print(f"  ✓ AI挨拶: {len(greeting_audio)} bytes")
        
        print("[初期化] 完了\n")
        
    except Exception as e:
        print(f"[エラー] 音声生成失敗: {e}")
        sys.exit(1)


def play_audio_mp3_simple(audio_bytes: bytes):
    """MP3音声を再生（中断なし）"""
    if not USE_PYGAME:
        print("[スキップ] pygame未インストールのため音声再生をスキップします")
        return

    tmp_path = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

        # pygame がファイルをリリースするまで待機
        pygame.mixer.music.unload()
        time.sleep(0.1)

    except Exception as e:
        print(f"[エラー] 音声再生エラー: {e}")
    finally:
        # ファイル削除（エラーは無視）
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass  # Windows でのファイルロックエラーは無視


def play_audio_mp3_with_stt_interruption(audio_bytes: bytes, audio_interface: pyaudio.PyAudio):
    """
    MP3音声を再生し、Google STTで店員の発話を検知したら即座に中断

    Args:
        audio_bytes: MP3音声データ
        audio_interface: PyAudioインターフェース

    Returns:
        tuple[bool, str, float, list]: (中断されたか, トランスクリプト, 信頼度, 録音フレーム)
            - 中断された場合: (True, transcript, confidence, frames)
            - 完了まで再生: (False, "", 0.0, [])
    """
    if not USE_PYGAME:
        print("[スキップ] pygame未インストールのため音声再生をスキップします")
        return False, "", 0.0, []

    # 中断フラグとSTT結果を格納
    interruption_detected = threading.Event()
    stt_result = {'transcript': '', 'confidence': 0.0}
    recorded_chunks = []

    # ★★★ 新規追加: 最後の中間結果を保存 ★★★
    last_interim_result = {'transcript': '', 'confidence': 0.0}

    # 入力キュー: 音声チャンク
    input_queue = queue.Queue()
    # 出力キュー: 認識結果
    output_queue = queue.Queue()

    # ストリーミング認識スレッド
    def run_streaming_recognize():
        """Google STT Streaming APIで中断検知"""
        nonlocal last_interim_result  # ★★★ 追加 ★★★
        accumulated_audio = []

        try:
            streaming_config = get_stt_streaming_config()

            def request_generator():
                while True:
                    chunk = input_queue.get()
                    if chunk is None:
                        break
                    accumulated_audio.append(chunk)
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)

            responses = stt_client.streaming_recognize(streaming_config, request_generator())

            for response in responses:
                if not response.results:
                    continue

                for result in response.results:
                    if not result.alternatives:
                        continue

                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence if result.alternatives else 0.0

                    # 中間結果でも検知（店員が話し始めた瞬間に反応）
                    if transcript.strip():
                        print(f"\n  [STT中断検知] {'[確定]' if result.is_final else '[中間]'} {transcript}")

                        # ★★★ 最後の中間結果を常に更新 ★★★
                        last_interim_result['transcript'] = transcript
                        last_interim_result['confidence'] = confidence if result.is_final else 0.8

                        # トランスクリプトが来た時点でAI音声を停止
                        interruption_detected.set()

                        # is_finalまで待って確定結果を取得
                        if result.is_final:
                            audio_data = b''.join(accumulated_audio)
                            output_queue.put(('transcript', transcript, confidence, audio_data))
                            break

        except Exception as e:
            import traceback
            print(f"\n[STT中断検知エラー] {e}")
            print(traceback.format_exc())
            output_queue.put(('error', str(e), 0.0, b''))

    # 録音スレッド
    def record_audio():
        """マイク入力を録音し、STTに送信"""
        stream = audio_interface.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )

        try:
            # AI音声再生中 + 中断後も録音継続（is_finalまで、最大10秒）
            start_time = time.time()
            max_wait = 10.0  # ★★★ 最大10秒まで待つ ★★★

            while pygame.mixer.music.get_busy() or (
                interruption_detected.is_set() and
                output_queue.empty() and
                (time.time() - start_time) < max_wait  # ★★★ タイムアウト追加 ★★★
            ):
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                recorded_chunks.append(data)
                input_queue.put(data)
                time.sleep(0.01)  # 10ms間隔

        finally:
            input_queue.put(None)  # 終了シグナル
            stream.stop_stream()
            stream.close()

    tmp_path = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        # AI音声再生開始
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        print("[AI音声] 再生開始（STT監視中...）")

        # STTスレッドと録音スレッドを開始
        stt_thread = threading.Thread(target=run_streaming_recognize, daemon=True)
        record_thread = threading.Thread(target=record_audio, daemon=True)

        stt_thread.start()
        record_thread.start()

        # 中断検知を待つ
        while pygame.mixer.music.get_busy():
            if interruption_detected.is_set():
                # 店員の発話を検知 → AI音声を即座に停止
                pygame.mixer.music.stop()
                print("[中断] 店員の発話を検知 → AI音声を即座に停止")
                break
            time.sleep(0.01)  # 10ms間隔でチェック

        # スレッドの終了を待つ
        record_thread.join(timeout=15.0)  # ★★★ 15秒に延長 ★★★
        stt_thread.join(timeout=10.0)     # ★★★ 10秒に延長 ★★★

        # pygame がファイルをリリースするまで待機
        if not interruption_detected.is_set():
            pygame.mixer.music.unload()
            time.sleep(0.1)

        # 結果を取得（ファイル削除前に実行）
        result = None
        if interruption_detected.is_set():
            try:
                result_type, transcript, confidence, _ = output_queue.get(timeout=8.0)  # ★★★ 8秒に延長 ★★★
                if result_type == 'transcript':
                    result = (True, transcript, confidence, recorded_chunks)
                else:
                    result = (True, "", 0.0, recorded_chunks)
            except queue.Empty:
                # ★★★ is_finalが来なかった場合、最後の中間結果を使用 ★★★
                print(f"[フォールバック] is_finalタイムアウト → 最後の中間結果を使用: '{last_interim_result['transcript']}'")
                result = (True, last_interim_result['transcript'], last_interim_result['confidence'], recorded_chunks)
        else:
            result = (False, "", 0.0, [])

        return result

    except Exception as e:
        print(f"[エラー] 音声再生・中断検知エラー: {e}")
        import traceback
        print(traceback.format_exc())
        # エラーが起きても中断検知結果は返す
        if interruption_detected.is_set():
            return True, last_interim_result['transcript'], last_interim_result['confidence'], recorded_chunks
        return False, "", 0.0, []
    finally:
        # ファイル削除（エラーは無視）
        if tmp_path:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
            try:
                os.remove(tmp_path)
            except Exception:
                pass  # Windows でのファイルロックエラーは無視


def transcribe_audio_streaming(audio_interface: pyaudio.PyAudio) -> tuple[str, float, bytes, list]:
    """
    Google Cloud STT Streaming API で音声認識
    リアルタイムで音声を送信し、is_final フラグで発話終了を検知

    Returns:
        tuple[str, float, bytes, list]: (transcript, confidence, wav_audio, raw_frames)
            - transcript: 認識テキスト
            - confidence: 信頼度
            - wav_audio: WAV形式の音声データ（個別ターン用）
            - raw_frames: 生の音声フレーム（全編録音用）
    """
    print("\n[録音開始] 話してください... (発話が終わると自動的に停止します)")

    # 入力キュー: 音声チャンク
    input_queue = queue.Queue()

    # 出力キュー: 認識結果
    output_queue = queue.Queue()

    # 録音済み音声（保存用）
    recorded_chunks = []

    # 録音終了フラグ
    stop_recording = threading.Event()

    # ストリーミング認識スレッド
    def run_streaming_recognize():
        """同期スレッドでストリーミング認識を実行"""
        accumulated_audio = []

        try:
            streaming_config = get_stt_streaming_config()

            # リクエストジェネレーター
            def request_generator():
                """音声チャンクを生成"""
                while True:
                    chunk = input_queue.get()
                    if chunk is None:  # 終了シグナル
                        break

                    accumulated_audio.append(chunk)
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

                    print(f"  [STT] {'[確定]' if result.is_final else '[中間]'} {transcript}", end='\r' if not result.is_final else '\n')

                    # is_final=True の場合のみ処理
                    if result.is_final:
                        print(f"  [STT] 発話終了検知: '{transcript}' (confidence: {confidence:.2f})")

                        # 蓄積された音声データを取得
                        audio_data = b''.join(accumulated_audio)

                        # 出力キューに結果を送信
                        output_queue.put(('transcript', transcript, confidence, audio_data))

                        # 録音停止
                        stop_recording.set()
                        break

        except Exception as e:
            import traceback
            print(f"\n[STT エラー] {e}")
            print(traceback.format_exc())
            output_queue.put(('error', str(e), 0.0, b''))
            stop_recording.set()

    # ストリーミング認識スレッドを開始
    streaming_thread = threading.Thread(target=run_streaming_recognize, daemon=True)
    streaming_thread.start()

    # 録音ストリーム
    stream = audio_interface.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )

    try:
        # 録音ループ
        while not stop_recording.is_set():
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            recorded_chunks.append(data)
            input_queue.put(data)

    finally:
        # 終了シグナルを送信
        input_queue.put(None)
        stream.stop_stream()
        stream.close()

    # スレッドの終了を待つ
    streaming_thread.join(timeout=5.0)

    # 結果を取得
    try:
        result_type, transcript, confidence, _ = output_queue.get_nowait()
        if result_type == 'error':
            print(f"[エラー] STTエラー: {transcript}")
            return "", 0.0, b'', []

        # 録音データをWAV形式で返す
        wav_buffer = BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(audio_interface.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(recorded_chunks))

        return transcript, confidence, wav_buffer.getvalue(), recorded_chunks

    except queue.Empty:
        print("[エラー] 認識結果を取得できませんでした")
        return "", 0.0, b'', []


def select_smart_acknowledgment(staff_utterance: str) -> tuple:
    """
    店員の発言内容に応じて適切な相槌を選択（キーワードトリガー＋文脈パターン）

    Args:
        staff_utterance: 店員の発言テキスト

    Returns:
        tuple[str, str]: (音声テキスト, ログ表示用テキスト)
    """
    utterance_lower = staff_utterance.strip()

    # 質問形式（明確な疑問文）
    if any(kw in utterance_lower for kw in ['ございますか', 'でしょうか', 'いかがですか']):
        return "確認しますので、少々お待ち下さい。", "質問形式 → 「確認しますので、少々お待ち下さい。」"

    # 待機要求（店員が作業する）
    if any(kw in utterance_lower for kw in ['お待ちください', '確認します', '代わります', '変わります']):
        return "承知いたしました。", "待機要求 → 「承知いたしました。」"

    # 確認・復唱（店員が情報を確認）
    if '復唱' in utterance_lower or 'かしこまりました' in utterance_lower:
        return "はい。", "確認 → 「はい。」"

    # デフォルト: シンプルな「はい」
    return "はい。", "デフォルト → 「はい。」"


def get_gemini_response(user_input: str) -> str:
    """Gemini で応答生成"""
    history_text = "\n".join([f"{h['role']}: {h['text']}" for h in conversation_history[-10:]])

    prompt = f"""あなたはレストラン予約の電話をかけている予約代行AIです。
以下の予約情報で予約を取ってください。丁寧な日本語で簡潔に話してください（1-2文）。

【予約情報】
- 予約者名: {RESERVATION_INFO['reserver_name']}
- 連絡先: {RESERVATION_INFO['contact_phone']}
- 希望日: {RESERVATION_INFO['date']}
- 希望時間: {RESERVATION_INFO['time']}
- 人数: {RESERVATION_INFO['guests']}名
- 席種: {RESERVATION_INFO['seat_type']}
- 時間の融通: {RESERVATION_INFO['flexibility']} （※店から聞かれた場合のみ伝える）
- 備考: {RESERVATION_INFO['notes']}

【重要な指示】
- 電話番号を伝える際は、必ず1桁ずつ区切って伝えてください。
  例: 「090-1234-5678」→「ゼロキュウゼロ、イチニーサンヨン、ゴーロクナナハチ」
- 「6千7百」や「8じゅう9」のような表現は絶対に使わないでください。
- 時間の融通（30分程度前後可能）は、店から「その時間は難しい」などと聞かれた場合のみ伝えてください。聞かれていないのに自分から言わないでください。
- 「当日はよろしくお願いいたします」は使わないでください（これは店側が客に言うセリフです）。
- 店員が「お待ちしております」「ご来店をお待ちしております」などと予約確定を告げた場合は、「ありがとうございました。それでは失礼いたします。」などで締めくくってください。

【絶対に禁止】
- ト書きや括弧書きの説明は絶対に含めないでください。
  ❌ 悪い例: 「（保留音の後、店員に代わる）」「（少々お待ちください）」
  ✅ 良い例: 「承知いたしました。」「はい、お待ちしております。」
- 音声で読み上げられない記号や説明文は一切書かないでください。
- 実際に声に出して話す内容のみを出力してください。

【これまでの会話】
{history_text}

【店員の発言】
{user_input}

【あなたの応答】:"""

    try:
        response = gemini_model.generate_content(prompt)
        response_text = response.text.strip()

        # Geminiが会話履歴の形式を真似して "AI: " を出力することがあるため除去
        if response_text.startswith("AI: ") or response_text.startswith("AI:"):
            response_text = response_text.replace("AI: ", "", 1).replace("AI:", "", 1).strip()

        return response_text
    except Exception as e:
        print(f"[Gemini エラー] {e}")
        return "少々お待ちください。"


def generate_conversation_summary(conversation_history: list, start_time: datetime, end_time: datetime) -> str:
    """
    会話内容から予約サマリーを生成（パターンB：簡潔版）

    Args:
        conversation_history: 会話履歴
        start_time: 会話開始時刻
        end_time: 会話終了時刻

    Returns:
        str: フォーマットされたサマリーテキスト
    """
    # 通話時間を計算
    duration = end_time - start_time
    duration_minutes = int(duration.total_seconds() // 60)
    duration_seconds = int(duration.total_seconds() % 60)
    duration_str = f"{duration_minutes}分{duration_seconds}秒"

    # ターン数（店員とAIの発話数）
    turn_count = len(conversation_history)

    # 会話内容から特別リクエストを抽出
    special_requests = []
    cake_info = None
    cake_message = None
    allergies = None

    # 会話履歴から情報を抽出
    conversation_text = " ".join([entry['text'] for entry in conversation_history])

    # 誕生日の確認
    if "誕生日" in conversation_text or "お祝い" in conversation_text:
        special_requests.append("誕生日のお祝い")

    # ケーキ情報の抽出
    if "5000円" in conversation_text or "5,000円" in conversation_text:
        cake_info = "ケーキ 5,000円"
    elif "3000円" in conversation_text or "3,000円" in conversation_text:
        cake_info = "ケーキ 3,000円"
    elif "7000円" in conversation_text or "7,000円" in conversation_text:
        cake_info = "ケーキ 7,000円"

    # メッセージの抽出
    for entry in conversation_history:
        if "お誕生日おめでとう" in entry['text']:
            # メッセージ部分を抽出
            text = entry['text']
            if "太郎" in text:
                cake_message = "お誕生日おめでとう　太郎"
            else:
                cake_message = "お誕生日おめでとう"
            break

    # アレルギー情報の抽出
    for entry in conversation_history:
        if "アレルギー" in entry['text'] and entry['role'] == "AI":
            if "ありません" in entry['text'] or "ない" in entry['text']:
                allergies = "なし"
            else:
                allergies = "あり（詳細は会話履歴を参照）"
            break

    # 確認事項
    confirmation = []
    for entry in conversation_history:
        if "復唱" in entry['text']:
            confirmation.append("店舗にて予約内容を復唱確認済み")
        if "お待ちしております" in entry['text'] and entry['role'] == "店員":
            confirmation.append("当日のご来店をお待ちしております")

    # サマリーテキストを生成
    summary = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    summary += "予約完了レポート\n"
    summary += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    summary += "【予約状況】 ✅ 予約完了\n\n"

    summary += f"【店舗】 {RESERVATION_INFO['restaurant_name']}\n"
    summary += f"【日時】 {RESERVATION_INFO['date']}（{RESERVATION_INFO['day_of_week']}） {RESERVATION_INFO['time']}\n"
    summary += f"【人数】 {RESERVATION_INFO['guests']}名様（{RESERVATION_INFO['seat_type']}）\n"
    summary += f"【予約者】 {RESERVATION_INFO['reserver_name']} 様（{RESERVATION_INFO['contact_phone']}）\n\n"

    # 特別リクエストセクション
    if special_requests or cake_info or allergies:
        summary += "【特別リクエスト】\n"
        for req in special_requests:
            summary += f"- {req}\n"
        if cake_info:
            if cake_message:
                summary += f"- {cake_info}（メッセージ: {cake_message}）\n"
            else:
                summary += f"- {cake_info}\n"
        if allergies:
            summary += f"- アレルギー: {allergies}\n"
        summary += "\n"

    # 確認事項セクション
    if confirmation:
        summary += "【確認事項】\n"
        summary += "\n".join(confirmation) + "\n\n"

    summary += f"通話時間: {duration_str} / ターン数: {turn_count}\n"
    summary += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    return summary


def main():
    """メインループ"""
    global is_first_interaction, in_recitation_mode

    # コマンドライン引数のパース
    parser = argparse.ArgumentParser(description="ローカル音声会話テストツール (Twilio完全準拠版)")
    parser.add_argument('--save-audio', action='store_true', help='会話全体を1つのWAVファイルとして保存（個別ファイルは保存しない）')
    args = parser.parse_args()

    # 保存ディレクトリの設定
    save_dir = None
    # 全編録音用バッファ
    full_recording_frames = []

    if args.save_audio:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = Path(f"output/conversation_{timestamp}")
        save_dir.mkdir(parents=True, exist_ok=True)
        print(f"[録音] 音声ファイルを保存します: {save_dir}")

    print("=" * 60)
    print("ローカル音声会話テストツール (Twilio完全準拠版 - Streaming STT)")
    print("=" * 60)
    print(f"予約情報: {RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']} {RESERVATION_INFO['time']} / {RESERVATION_INFO['guests']}名")
    print(f"レストラン: {RESERVATION_INFO['restaurant_name']}")
    if save_dir:
        print(f"録音保存先: {save_dir}")
    print("=" * 60)
    print("\n仕様:")
    print("  - Google STT Streaming API (is_final フラグで発話終了検知)")
    print("  - 店員の第一声を検知 → 事前生成済みAI挨拶を即答")
    print("  - 全発話に即答相槌（初回: 丁寧1.5秒、2回目以降: 短0.6秒）")
    print("  - 復唱モード対応")
    print("  - twilio_webhooks.py と完全同一ロジック")
    print("=" * 60)
    print("\n操作方法:")
    print("  - マイクに向かって店員役として話してください")
    print("  - 発話が終わると自動的に認識されます（is_final検知）")
    print("  - Ctrl+C で終了")
    print("=" * 60)

    # 事前音声生成
    initialize_audio()

    # PyAudio初期化
    audio = pyaudio.PyAudio()

    # 会話ループ
    turn = 0
    conversation_start_time = datetime.now()  # 会話開始時刻を記録

    try:
        while True:
            turn += 1
            print(f"\n{'='*60}")
            print(f"ターン {turn}")
            print(f"{'='*60}")

            # ストリーミングSTT（録音+認識を同時実行）
            transcript, confidence, audio_data, raw_frames = transcribe_audio_streaming(audio)

            # 全編録音に追加
            if save_dir and raw_frames:
                full_recording_frames.extend(raw_frames)

            if not transcript:
                print("[STT] 認識できませんでした。もう一度お願いします。")
                continue

            # confidence チェック
            if confidence <= 0.5:
                print(f"[STT] 信頼度が低いためスキップ: {confidence:.2f}")
                continue


            # 会話履歴に追加（店員発話回数カウント用）
            staff_count = sum(1 for item in conversation_history if item['role'] == '店員')
            is_first_response = staff_count == 0

            conversation_history.append({
                'role': '店員',
                'text': transcript,
                'timestamp': datetime.now().isoformat()
            })

            # ============================================
            # 初回: 店員の第一声を検知 → AI挨拶を即答
            # ============================================
            if is_first_interaction:
                print("[初回検知] 店員の第一声を検知 → AI挨拶を即答")
                is_first_interaction = False

                if greeting_audio:
                    print(f"[AI挨拶] 再生中（中断検知あり）...")
                    interrupted, staff_transcript, staff_confidence, staff_audio_chunks = play_audio_mp3_with_stt_interruption(greeting_audio, audio)

                    # 中断された場合の処理
                    if interrupted:
                        print(f"[中断] AI挨拶が中断されました")
                        staff_audio_data = b''.join(staff_audio_chunks)

                        # 中断時の店員発話を全編録音に追加
                        if save_dir and staff_audio_data:
                            # LINEAR16フォーマット（ヘッダーなし）として追加
                            for i in range(0, len(staff_audio_data), CHUNK_SIZE * 2):
                                chunk = staff_audio_data[i:i + CHUNK_SIZE * 2]
                                if chunk:
                                    full_recording_frames.append(chunk)
                            print(f"[録音] 中断時の店員発話を全編録音に追加: {len(staff_audio_data)} bytes")

                        # AI挨拶（中断された部分まで）を会話履歴に追加
                        greeting_text = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約を... [店員により中断]"
                        conversation_history.append({
                            'role': 'AI',
                            'text': greeting_text,
                            'timestamp': datetime.now().isoformat()
                        })

                        # 中断時の店員発話を会話履歴に追加して、次のターンへ
                        if staff_transcript and staff_confidence > 0.5:
                            print(f"[中断時の店員発話] {staff_transcript} (信頼度: {staff_confidence:.2f})")
                            conversation_history.append({
                                'role': '店員',
                                'text': staff_transcript,
                                'timestamp': datetime.now().isoformat()
                            })
                            print("[中断後] 次のターンで応答します")
                    else:
                        # 中断されなかった場合（挨拶を最後まで再生）
                        # 全編録音用にLINEAR16版を生成して追加
                        if save_dir:
                            greeting_text_full = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。私は{RESERVATION_INFO['reserver_name']}様のAIアシスタントです。{RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']}の{RESERVATION_INFO['time']}から、{RESERVATION_INFO['reserver_name']}様名義で{RESERVATION_INFO['guests']}名、{RESERVATION_INFO['seat_type']}で、予約をお願いできますでしょうか。"
                            greeting_linear16 = synthesize_speech_linear16(greeting_text_full)
                            # LINEAR16はヘッダーなしのRAWデータなので、そのまま追加
                            # 1サンプル = 2バイト（int16）、CHUNK_SIZE分ずつ分割
                            for i in range(0, len(greeting_linear16), CHUNK_SIZE * 2):
                                chunk = greeting_linear16[i:i + CHUNK_SIZE * 2]
                                if chunk:
                                    full_recording_frames.append(chunk)
                            print(f"[録音] AI挨拶を全編録音に追加: {len(greeting_linear16)} bytes")

                        # 会話履歴に追加
                        greeting_text = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。..."
                        conversation_history.append({
                            'role': 'AI',
                            'text': greeting_text,
                            'timestamp': datetime.now().isoformat()
                        })

                        print("[AI挨拶] 完了")
                continue

            # ============================================
            # 即答相槌（スマート選択）
            # ============================================
            # ★★★ スマート相槌選択を使用 ★★★
            quick_text, log_text = select_smart_acknowledgment(transcript)
            print(f"[即答相槌] {log_text}")

            # 音声生成（必要に応じてその場で生成）
            if quick_text in ["はい。", "はい。わかりました。"] and quick_hai_audio:
                # 事前生成済み音声を使用
                quick_audio = quick_hai_audio
                quick_delay = 0.5
            else:
                # その場で生成（特殊な相槌の場合）
                quick_audio = synthesize_speech_mp3(quick_text)
                quick_delay = 0.8

            print(f"[TTS] 即答再生中...")
            play_audio_mp3_simple(quick_audio)

            # 全編録音用にLINEAR16版を生成して追加
            if save_dir:
                quick_linear16 = synthesize_speech_linear16(quick_text)
                for i in range(0, len(quick_linear16), CHUNK_SIZE * 2):
                    chunk = quick_linear16[i:i + CHUNK_SIZE * 2]
                    if chunk:
                        full_recording_frames.append(chunk)
                print(f"[録音] 即答相槌を全編録音に追加: {len(quick_linear16)} bytes")

            conversation_history.append({
                'role': 'AI',
                'text': quick_text,
                'timestamp': datetime.now().isoformat()
            })

            # 相槌の長さ分だけ待機
            time.sleep(quick_delay)

            # ============================================
            # 復唱モード検知（ストリーミングSTTでは自動検知するのでログのみ）
            # ============================================
            if "復唱" in transcript:
                in_recitation_mode = True
                print(f"[復唱モード] 検知（ストリーミングSTTが自動的に発話終了を待ちます）")

            # ============================================
            # Gemini応答
            # ============================================
            print("[Gemini] 応答生成中...")
            ai_response = get_gemini_response(transcript)
            print(f"[AI] {ai_response}")

            # 会話履歴に追加
            conversation_history.append({
                'role': 'AI',
                'text': ai_response,
                'timestamp': datetime.now().isoformat()
            })

            # TTS + 再生（STT中断検知を有効化）
            print("[TTS] 音声生成・再生中... (Google STT監視中)")
            tts_audio = synthesize_speech_mp3(ai_response)
            was_interrupted, staff_transcript, staff_confidence, staff_frames = play_audio_mp3_with_stt_interruption(tts_audio, audio)

            if was_interrupted:
                print(f"[中断完了] AI応答が途中で停止されました → 店員: 「{staff_transcript}」")

                # 中断された場合、AI音声は録音に追加しない
                # 店員の発話を録音に追加
                if save_dir and staff_frames:
                    full_recording_frames.extend(staff_frames)
                    print(f"[録音] 中断時の店員発話を全編録音に追加: {len(staff_frames)} chunks")

                # 店員の発話を会話履歴に追加して、次のターンへ
                if staff_transcript and staff_confidence > 0.5:
                    conversation_history.append({
                        'role': '店員',
                        'text': staff_transcript,
                        'timestamp': datetime.now().isoformat()
                    })

                    # ★★★ 中断後の即答相槌を追加 ★★★
                    interrupt_ack_text, interrupt_log_text = select_smart_acknowledgment(staff_transcript)
                    print(f"[中断後即答] {interrupt_log_text}")

                    # 音声生成
                    if interrupt_ack_text in ["はい。", "はい。わかりました。"] and quick_hai_audio:
                        interrupt_ack_audio = quick_hai_audio
                    else:
                        interrupt_ack_audio = synthesize_speech_mp3(interrupt_ack_text)

                    play_audio_mp3_simple(interrupt_ack_audio)

                    # 全編録音に追加
                    if save_dir:
                        interrupt_linear16 = synthesize_speech_linear16(interrupt_ack_text)
                        for i in range(0, len(interrupt_linear16), CHUNK_SIZE * 2):
                            chunk = interrupt_linear16[i:i + CHUNK_SIZE * 2]
                            if chunk:
                                full_recording_frames.append(chunk)
                        print(f"[録音] 中断後即答相槌を全編録音に追加: {len(interrupt_linear16)} bytes")

                    conversation_history.append({
                        'role': 'AI',
                        'text': interrupt_ack_text,
                        'timestamp': datetime.now().isoformat()
                    })

                    time.sleep(0.5)
                    # ★★★ ここまで ★★★

                    # 次のターンのGemini応答を生成
                    print("\n[Gemini] 応答生成中... (中断後の返答)")
                    ai_response = get_gemini_response(staff_transcript)
                    print(f"[AI] {ai_response}")

                    conversation_history.append({
                        'role': 'AI',
                        'text': ai_response,
                        'timestamp': datetime.now().isoformat()
                    })

                    # 新しい応答を再生（また中断検知あり）
                    print("[TTS] 音声生成・再生中... (Google STT監視中)")
                    tts_audio = synthesize_speech_mp3(ai_response)
                    was_interrupted2, _, _, _ = play_audio_mp3_with_stt_interruption(tts_audio, audio)

                    if not was_interrupted2:
                        # 完了まで再生された場合のみ、全編録音に追加
                        if save_dir:
                            ai_linear16 = synthesize_speech_linear16(ai_response)
                            for i in range(0, len(ai_linear16), CHUNK_SIZE * 2):
                                chunk = ai_linear16[i:i + CHUNK_SIZE * 2]
                                if chunk:
                                    full_recording_frames.append(chunk)
                            print(f"[録音] Gemini応答を全編録音に追加: {len(ai_linear16)} bytes")

            else:
                # 完了まで再生された場合のみ、全編録音に追加
                if save_dir:
                    ai_linear16 = synthesize_speech_linear16(ai_response)
                    for i in range(0, len(ai_linear16), CHUNK_SIZE * 2):
                        chunk = ai_linear16[i:i + CHUNK_SIZE * 2]
                        if chunk:
                            full_recording_frames.append(chunk)
                    print(f"[録音] Gemini応答を全編録音に追加: {len(ai_linear16)} bytes")

    except KeyboardInterrupt:
        print("\n\n[終了] 会話を終了します。")
    finally:
        # 全編録音を保存（audio.terminate()の前に実行）
        if save_dir and full_recording_frames:
            full_recording_path = save_dir / "full_conversation.wav"
            with wave.open(str(full_recording_path), 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(audio.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(full_recording_frames))
            print(f"\n[保存] 全編録音: {full_recording_path.name} ({len(full_recording_frames)} chunks)")

        audio.terminate()

    # 会話履歴を表示
    print("\n" + "=" * 60)
    print("会話履歴")
    print("=" * 60)
    for i, entry in enumerate(conversation_history, 1):
        print(f"{i}. [{entry['role']}] {entry['text']}")
    print("=" * 60)

    # 会話履歴をテキストファイルで保存
    if save_dir:
        transcript_path = save_dir / "conversation_transcript.txt"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write("会話履歴 (Twilio完全準拠版)\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"予約情報:\n")
            f.write(f"  レストラン: {RESERVATION_INFO['restaurant_name']}\n")
            f.write(f"  予約者: {RESERVATION_INFO['reserver_name']}\n")
            f.write(f"  日時: {RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']} {RESERVATION_INFO['time']}\n")
            f.write(f"  人数: {RESERVATION_INFO['guests']}名\n")
            f.write(f"  席種: {RESERVATION_INFO['seat_type']}\n\n")
            f.write("=" * 60 + "\n\n")

            for i, entry in enumerate(conversation_history, 1):
                f.write(f"{i}. [{entry['role']}] {entry['text']}\n")
                f.write(f"   時刻: {entry['timestamp']}\n\n")

        # 会話サマリーを生成して保存
        conversation_end_time = datetime.now()
        summary_text = generate_conversation_summary(conversation_history, conversation_start_time, conversation_end_time)

        summary_path = save_dir / "reservation_summary.txt"
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(summary_text)

        # サマリーをコンソールにも表示
        print("\n" + summary_text)

        print(f"\n[保存完了] 会話履歴: {transcript_path}")
        print(f"[保存完了] 予約サマリー: {summary_path}")
        print(f"[保存完了] 保存先: {save_dir}")
        print(f"  - full_conversation.wav (全編録音)")
        print(f"  - conversation_transcript.txt (テキスト履歴)")
        print(f"  - reservation_summary.txt (予約サマリー)")
        print("=" * 60)


if __name__ == "__main__":
    main()
