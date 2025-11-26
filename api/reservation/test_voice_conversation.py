#!/usr/bin/env python3
"""
ローカル音声会話テストツール

Twilioを使わずに、PCのマイク/ヘッドセットで会話をテスト
- マイクから店員発話を録音
- Google Cloud STT で音声認識
- Gemini で応答生成（即答キーワード検知含む）
- Google Cloud TTS で音声合成
- スピーカーで音声再生

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
from pathlib import Path
from io import BytesIO
from datetime import datetime

# 音声再生用
try:
    # pygame をインポート（MP3再生用）
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

# VAD設定
SILENCE_THRESHOLD = 500  # 音声エネルギー閾値
SILENCE_DURATION = 1.0  # 無音検知時間（秒）


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


def play_audio_mp3(audio_bytes: bytes):
    """MP3音声を再生"""
    if not USE_PYGAME:
        print("[スキップ] pygame未インストールのため音声再生をスキップします")
        return

    try:
        # 一時ファイルに保存
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        # pygameで再生
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()

        # 再生が終わるまで待機
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

        # 一時ファイル削除
        os.remove(tmp_path)
    except Exception as e:
        print(f"[エラー] 音声再生エラー: {e}")


def transcribe_audio(audio_data: bytes) -> tuple[str, float]:
    """Google Cloud STT で音声認識"""
    audio = speech.RecognitionAudio(content=audio_data)

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code="ja-JP",
        model="default",
        enable_automatic_punctuation=True,
    )

    response = stt_client.recognize(config=config, audio=audio)

    if response.results:
        transcript = response.results[0].alternatives[0].transcript
        confidence = response.results[0].alternatives[0].confidence
        return transcript, confidence
    else:
        return "", 0.0


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


def record_audio_with_vad(audio_interface: pyaudio.PyAudio) -> bytes:
    """
    マイクから音声を録音（VAD付き）
    発話終了を検知したら録音を停止
    """
    stream = audio_interface.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )

    print("\n[録音開始] 話してください...")

    frames = []
    silence_chunks = 0
    silence_threshold_chunks = int(SILENCE_DURATION * RATE / CHUNK_SIZE)
    is_speaking = False

    try:
        while True:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            frames.append(data)

            # 音声エネルギー計算
            audio_data = list(data)
            energy = sum(abs(b) for b in audio_data) / len(audio_data)

            # 発話検知
            if energy > SILENCE_THRESHOLD:
                silence_chunks = 0
                if not is_speaking:
                    is_speaking = True
                    print("[発話検知]")
            else:
                silence_chunks += 1

            # 発話終了検知
            if is_speaking and silence_chunks >= silence_threshold_chunks:
                print("[発話終了]")
                break

            # タイムアウト（30秒）
            if len(frames) > RATE / CHUNK_SIZE * 30:
                print("[タイムアウト]")
                break

    finally:
        stream.stop_stream()
        stream.close()

    # WAV形式でバイト列を返す
    wav_buffer = BytesIO()
    with wave.open(wav_buffer, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio_interface.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))

    return wav_buffer.getvalue()


def main():
    """メインループ"""
    # コマンドライン引数のパース
    parser = argparse.ArgumentParser(description="ローカル音声会話テストツール")
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
    print("ローカル音声会話テストツール")
    print("=" * 60)
    print(f"予約情報: {RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']} {RESERVATION_INFO['time']} / {RESERVATION_INFO['guests']}名")
    print(f"レストラン: {RESERVATION_INFO['restaurant_name']}")
    if save_dir:
        print(f"録音保存先: {save_dir}")
    print("=" * 60)
    print("\n操作方法:")
    print("  - マイクに向かって店員役として話してください")
    print("  - 発話が終わると自動的に認識されます")
    print("  - Ctrl+C で終了")
    print("=" * 60)

    # PyAudio初期化
    audio = pyaudio.PyAudio()

    # 最初のAI挨拶を事前生成
    print("\n[初期化] AI挨拶音声を生成中...")
    greeting_text = f"お忙しいところ恐れ入ります。{RESERVATION_INFO['restaurant_name']}様へ、{RESERVATION_INFO['reserver_name']}様の予約をお願いしたく、お電話しております。私は{RESERVATION_INFO['reserver_name']}様のAIアシスタントです。{RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']}の{RESERVATION_INFO['time']}から、{RESERVATION_INFO['reserver_name']}様名義で{RESERVATION_INFO['guests']}名、{RESERVATION_INFO['seat_type']}で、予約をお願いできますでしょうか。"

    # 即答相槌音声を事前生成
    print("[初期化] 即答相槌音声を生成中...")
    acknowledgment_audio = synthesize_speech_mp3("はい、かしこまりました。")

    print("[初期化] 完了\n")

    # 会話ループ
    turn = 0
    is_first_interaction = True

    try:
        while True:
            turn += 1
            print(f"\n{'='*60}")
            print(f"ターン {turn}")
            print(f"{'='*60}")

            # 音声録音
            audio_data = record_audio_with_vad(audio)

            # 録音を保存
            if save_dir:
                staff_audio_path = save_dir / f"turn_{turn:02d}_staff.wav"
                with open(staff_audio_path, 'wb') as f:
                    f.write(audio_data)
                print(f"[保存] 店員発話: {staff_audio_path.name}")

            # STT
            print("[STT] 音声認識中...")
            transcript, confidence = transcribe_audio(audio_data)

            if not transcript:
                print("[STT] 認識できませんでした。もう一度お願いします。")
                continue

            print(f"[STT] 認識: '{transcript}' (confidence: {confidence:.2f})")

            # 会話履歴に追加
            conversation_history.append({
                'role': '店員',
                'text': transcript,
                'timestamp': datetime.now().isoformat()
            })

            # 最初の応答の場合、AI挨拶を返す
            if is_first_interaction:
                print("[AI] 店員の第一声を検知 → AI挨拶を開始")
                is_first_interaction = False

                print(f"[AI] {greeting_text}")
                print("[TTS] 音声再生中...")
                greeting_audio = synthesize_speech_mp3(greeting_text)

                # AI挨拶を保存
                if save_dir:
                    ai_audio_path = save_dir / f"turn_{turn:02d}_ai_greeting.mp3"
                    with open(ai_audio_path, 'wb') as f:
                        f.write(greeting_audio)
                    print(f"[保存] AI挨拶: {ai_audio_path.name}")

                play_audio_mp3(greeting_audio)

                conversation_history.append({
                    'role': 'AI',
                    'text': greeting_text,
                    'timestamp': datetime.now().isoformat()
                })
                continue

            # 即答キーワード検知
            quick_response_keywords = ["確認", "待って", "お待ち", "少々"]
            needs_quick_response = any(keyword in transcript for keyword in quick_response_keywords)

            # 復唱モード検知（情報のみ）
            if "復唱" in transcript:
                print("[検知] 復唱モード（次の発話は長めに待ちます）")

            # 即答相槌
            if needs_quick_response:
                print("[AI] 即答相槌: 'はい、かしこまりました。'")
                print("[TTS] 音声再生中...")

                # 即答相槌を保存
                if save_dir:
                    ai_audio_path = save_dir / f"turn_{turn:02d}_ai_quick.mp3"
                    with open(ai_audio_path, 'wb') as f:
                        f.write(acknowledgment_audio)
                    print(f"[保存] AI即答: {ai_audio_path.name}")

                play_audio_mp3(acknowledgment_audio)

                conversation_history.append({
                    'role': 'AI',
                    'text': 'かしこまりました。',
                    'timestamp': datetime.now().isoformat()
                })
                continue

            # Gemini応答
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
            f.write("会話履歴\n")
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
