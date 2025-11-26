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
    --save-audio  会話を音声ファイルとして保存（output/ディレクトリに保存）
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

# 音声再生用
try:
    import pygame
    pygame.mixer.init()
    USE_PYGAME = True
except ImportError:
    USE_PYGAME = False
    print("[警告] pygame がインストールされていません。音声再生機能が制限されます。")
    print("インストール: pip install pygame")

# Google Cloud クライアント
from google.cloud import speech
from google.cloud import texttospeech
import google.generativeai as genai

# 環境変数
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', '')

# Google Cloud クライアント初期化
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

# Gemini 初期化
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
else:
    print("[エラー] GOOGLE_API_KEY 環境変数が未設定です")
    sys.exit(1)

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


def initialize_audio():
    """即答音声とAI挨拶音声を事前生成"""
    global acknowledgment_audio, quick_hai_audio, greeting_audio
    
    try:
        print("[初期化] 相槌音声を生成中...")
        
        # 初回用の相槌
        acknowledgment_audio = synthesize_speech_mp3("はい、かしこまりました。")
        print(f"  ✓ 初回相槌: {len(acknowledgment_audio)} bytes")
        
        # 2回目以降用の短い相槌
        quick_hai_audio = synthesize_speech_mp3("はい。")
        print(f"  ✓ 短い相槌: {len(quick_hai_audio)} bytes")
        
        # AI挨拶音声
        greeting_text = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。私は{RESERVATION_INFO['reserver_name']}様のAIアシスタントです。{RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']}の{RESERVATION_INFO['time']}から、{RESERVATION_INFO['reserver_name']}様名義で{RESERVATION_INFO['guests']}名、{RESERVATION_INFO['seat_type']}で、予約をお願いできますでしょうか。"
        greeting_audio = synthesize_speech_mp3(greeting_text)
        print(f"  ✓ AI挨拶: {len(greeting_audio)} bytes")
        
        print("[初期化] 完了\n")
        
    except Exception as e:
        print(f"[エラー] 音声生成失敗: {e}")
        sys.exit(1)


def play_audio_mp3(audio_bytes: bytes):
    """MP3音声を再生"""
    if not USE_PYGAME:
        print("[スキップ] pygame未インストールのため音声再生をスキップします")
        return

    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

        os.remove(tmp_path)
    except Exception as e:
        print(f"[エラー] 音声再生エラー: {e}")


def transcribe_audio_streaming(audio_interface: pyaudio.PyAudio) -> tuple[str, float, bytes]:
    """
    Google Cloud STT Streaming API で音声認識
    リアルタイムで音声を送信し、is_final フラグで発話終了を検知
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
            return "", 0.0, b''

        # 録音データをWAV形式で返す
        wav_buffer = BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(audio_interface.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(recorded_chunks))

        return transcript, confidence, wav_buffer.getvalue()

    except queue.Empty:
        print("[エラー] 認識結果を取得できませんでした")
        return "", 0.0, b''


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
        print(f"[Gemini エラー] {e}")
        return "少々お待ちください。"




def main():
    """メインループ"""
    global is_first_interaction, in_recitation_mode
    
    # コマンドライン引数のパース
    parser = argparse.ArgumentParser(description="ローカル音声会話テストツール (Twilio完全準拠版)")
    parser.add_argument('--save-audio', action='store_true', help='会話を音声ファイルとして保存')
    args = parser.parse_args()

    # 保存ディレクトリの設定
    save_dir = None
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

    try:
        while True:
            turn += 1
            print(f"\n{'='*60}")
            print(f"ターン {turn}")
            print(f"{'='*60}")

            # ストリーミングSTT（録音+認識を同時実行）
            transcript, confidence, audio_data = transcribe_audio_streaming(audio)

            if not transcript:
                print("[STT] 認識できませんでした。もう一度お願いします。")
                continue

            # confidence チェック
            if confidence <= 0.5:
                print(f"[STT] 信頼度が低いためスキップ: {confidence:.2f}")
                continue

            # 録音を保存
            if save_dir and audio_data:
                staff_audio_path = save_dir / f"turn_{turn:02d}_staff.wav"
                with open(staff_audio_path, 'wb') as f:
                    f.write(audio_data)
                print(f"[保存] 店員発話: {staff_audio_path.name}")

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
                    print(f"[AI挨拶] 再生中...")
                    play_audio_mp3(greeting_audio)

                    # AI挨拶を保存
                    if save_dir:
                        ai_audio_path = save_dir / f"turn_{turn:02d}_ai_greeting.mp3"
                        with open(ai_audio_path, 'wb') as f:
                            f.write(greeting_audio)
                        print(f"[保存] AI挨拶: {ai_audio_path.name}")

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
            # 即答相槌（全発話に対して）
            # ============================================
            quick_audio = None
            quick_delay = 0
            quick_text = ""

            if is_first_response and acknowledgment_audio:
                # 初回: 丁寧な相槌
                quick_audio = acknowledgment_audio
                quick_delay = 1.5
                quick_text = "はい、かしこまりました。"
                print(f"[即答相槌] 初回 → 「{quick_text}」")
            elif quick_hai_audio:
                # 2回目以降: 短い相槌
                quick_audio = quick_hai_audio
                quick_delay = 0.6
                quick_text = "はい。"
                print(f"[即答相槌] 通常 → 「{quick_text}」")

            if quick_audio:
                print(f"[TTS] 即答再生中...")
                play_audio_mp3(quick_audio)

                # 即答相槌を保存
                if save_dir:
                    ai_audio_path = save_dir / f"turn_{turn:02d}_ai_quick.mp3"
                    with open(ai_audio_path, 'wb') as f:
                        f.write(quick_audio)
                    print(f"[保存] AI即答: {ai_audio_path.name}")

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

            # TTS + 再生
            print("[TTS] 音声生成・再生中...")
            tts_audio = synthesize_speech_mp3(ai_response)

            # Gemini応答を保存
            if save_dir:
                ai_audio_path = save_dir / f"turn_{turn:02d}_ai_gemini.mp3"
                with open(ai_audio_path, 'wb') as f:
                    f.write(tts_audio)
                print(f"[保存] AIGemini応答: {ai_audio_path.name}")

            play_audio_mp3(tts_audio)

    except KeyboardInterrupt:
        print("\n\n[終了] 会話を終了します。")
    finally:
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

        print(f"\n[保存] 会話履歴: {transcript_path}")
        print(f"[保存] すべての音声ファイル: {save_dir}")
        print("=" * 60)


if __name__ == "__main__":
    main()
