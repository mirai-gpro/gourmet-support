#!/usr/bin/env python3
"""
ローカル音声会話テストツール (Twilio完全準拠版)

Twilioを使わずに、PCのマイク/ヘッドセットで会話をテスト
- マイクから店員発話を録音（バッファサイズのみで判定、VADなし）
- Google Cloud STT で音声認識
- Gemini で応答生成
- Google Cloud TTS で音声合成
- スピーカーで音声再生

twilio_webhooks.py (index 10) と完全同一ロジック:
- 店員の第一声を検知したら、事前生成済みのAI挨拶を即答
- 全発話に即答相槌（初回: 丁寧1.5秒、2回目以降: 短0.6秒）
- 復唱モードでバッファ5倍
- VAD（音声エネルギー計算）なし

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

# バッファ設定（Twilio準拠）
# 16kHz の場合、1チャンク = 1024サンプル = 約64ms
# 150チャンク = 約9.6秒、250チャンク = 約16秒
# Twilioは8kHzなので、16kHzの場合は倍速になる点に注意
NORMAL_BUFFER_SIZE = 75  # 約4.8秒（16kHzで調整）
RECITATION_BUFFER_SIZE = 125  # 約8秒（復唱モード）

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


def transcribe_audio(audio_data: bytes) -> tuple[str, float]:
    """Google Cloud STT で音声認識"""
    audio = speech.RecognitionAudio(content=audio_data)

    # フレーズヒント
    phrases = [
        "予約", "空席", "満席", "お取りできます", "承知いたしました",
        "かしこまりました", "少々お待ちください", "確認いたします",
        "テーブル席", "カウンター席", "個室", "お名前", "人数",
        "日時", "時間", "お電話番号", "復唱"
    ]

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code="ja-JP",
        model="default",
        enable_automatic_punctuation=True,
        speech_contexts=[speech.SpeechContext(phrases=phrases)],
        use_enhanced=True,  # VADを含む高精度モデル
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


def record_audio_buffered(audio_interface: pyaudio.PyAudio) -> bytes:
    """
    マイクから音声を録音（バッファサイズのみで判定、VADなし）
    Twilio版と同じロジック: バッファが一定量溜まったら処理
    """
    global in_recitation_mode
    
    stream = audio_interface.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )

    print("\n[録音開始] 話してください...")

    frames = []
    max_buffer_size = RECITATION_BUFFER_SIZE if in_recitation_mode else NORMAL_BUFFER_SIZE
    
    print(f"[バッファ設定] {'復唱モード' if in_recitation_mode else '通常モード'}: {max_buffer_size} chunks")

    try:
        while len(frames) < max_buffer_size:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            frames.append(data)
            
            # プログレス表示
            if len(frames) % 10 == 0:
                print(f"  バッファ: {len(frames)}/{max_buffer_size} chunks", end='\r')

        print(f"\n[バッファ到達] {len(frames)} chunks 処理開始")

    finally:
        stream.stop_stream()
        stream.close()

    # 復唱モード解除
    if in_recitation_mode:
        in_recitation_mode = False
        print("[復唱モード] 終了")

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
    print("ローカル音声会話テストツール (Twilio完全準拠版)")
    print("=" * 60)
    print(f"予約情報: {RESERVATION_INFO['date']}{RESERVATION_INFO['day_of_week']} {RESERVATION_INFO['time']} / {RESERVATION_INFO['guests']}名")
    print(f"レストラン: {RESERVATION_INFO['restaurant_name']}")
    if save_dir:
        print(f"録音保存先: {save_dir}")
    print("=" * 60)
    print("\n仕様:")
    print("  - バッファサイズのみで判定（VADなし、Twilio準拠）")
    print("  - 店員の第一声を検知 → 事前生成済みAI挨拶を即答")
    print("  - 全発話に即答相槌（初回: 丁寧1.5秒、2回目以降: 短0.6秒）")
    print("  - 復唱モード: バッファ5倍拡大")
    print("=" * 60)
    print("\n操作方法:")
    print("  - マイクに向かって店員役として話してください")
    print("  - バッファが溜まると自動的に認識されます")
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

            # 音声録音
            audio_data = record_audio_buffered(audio)

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
            # 復唱モード検知
            # ============================================
            if "復唱" in transcript:
                in_recitation_mode = True
                print(f"[復唱モード] 開始 → 次回バッファ: {RECITATION_BUFFER_SIZE} chunks")

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
