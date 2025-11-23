#!/usr/bin/env python3
"""
音声AI連携テスト（Twilio不要）

Google Cloud STT/TTS + Gemini の連携をテスト
マイク入力 → STT → Gemini → TTS → スピーカー出力

必要な環境変数:
- GOOGLE_APPLICATION_CREDENTIALS: サービスアカウントキーのパス
- GOOGLE_API_KEY: Gemini API キー
"""

import os
import io
import wave
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Cloud クライアント
try:
    from google.cloud import speech_v1, texttospeech_v1
    import google.generativeai as genai
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("Google Cloud SDKがインストールされていません")


# ========================================
# Gemini 予約エージェント
# ========================================

class ReservationAgent:
    """予約交渉を行うGeminiエージェント"""

    def __init__(self, reservation_info: dict):
        self.reservation = reservation_info
        self.conversation_history = []
        self.state = "talking"
        self.result = None

        # Gemini初期化
        api_key = os.environ.get('GOOGLE_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-2.0-flash')
            self.chat = self.model.start_chat(history=[])

            # システムプロンプトを送信
            self.chat.send_message(self._get_system_prompt())
        else:
            self.model = None
            logger.warning("GOOGLE_API_KEY が設定されていません")

    def _get_system_prompt(self) -> str:
        r = self.reservation
        flexibility_map = {
            'exact': '指定時間のみ',
            '30': '指定時間から+30分まで',
            '60': '指定時間から+60分まで',
            '90': '指定時間から+90分まで'
        }
        flexibility_text = flexibility_map.get(r.get('flexibility', 'exact'), '指定時間のみ')

        return f"""あなたは飲食店予約代行AIです。電話で予約を取ります。

【予約依頼】
- 予約者名: {r.get('reserver_name', '山田')}
- 連絡先: {r.get('contact_phone', '090-1234-5678')}
- 希望日: {r.get('date', '明日')}
- 希望時間: {r.get('time', '19:00')}
- 時間許容: {flexibility_text}
- 人数: {r.get('guests', 2)}名
- 席希望: {', '.join(r.get('seat_types', ['指定なし']))}
- 備考: {r.get('notes', 'なし')}

【行動ルール】
1. 丁寧な敬語で簡潔に（1発話15秒以内）
2. 予約名と連絡先を伝える
3. 予約可否を明確に確認
4. 代替案は許容範囲内か即判断
5. 予約確定時は復唱確認
6. 応答の末尾に状態タグを付ける

【状態タグ】
[TALKING] - 会話継続
[SUCCESS] - 予約確定
[FAILED] - 予約不可
[NEED_USER] - ユーザー確認必要

最初の発話から始めてください。"""

    def process(self, shop_utterance: str) -> tuple:
        """
        店員の発話を処理して応答を生成

        Returns:
            (response_text, state)
        """
        if not self.model:
            return "申し訳ございません、システムエラーです。", "failed"

        try:
            self.conversation_history.append({
                "role": "shop",
                "text": shop_utterance,
                "at": datetime.now().isoformat()
            })

            response = self.chat.send_message(f"[店員] {shop_utterance}")
            response_text = response.text.strip()

            # 状態タグを抽出
            state = "talking"
            for tag in ["[SUCCESS]", "[FAILED]", "[NEED_USER]", "[TALKING]"]:
                if tag in response_text:
                    state = tag.strip("[]").lower()
                    response_text = response_text.replace(tag, "").strip()
                    break

            self.state = state
            self.conversation_history.append({
                "role": "agent",
                "text": response_text,
                "at": datetime.now().isoformat()
            })

            return response_text, state

        except Exception as e:
            logger.error(f"Gemini エラー: {e}")
            return "申し訳ございません、もう一度お願いできますか。", "talking"

    def get_opening(self) -> str:
        """最初の発話を取得"""
        if not self.model:
            return "お忙しいところ恐れ入ります。予約のお電話です。"

        try:
            response = self.chat.send_message("最初の発話を生成してください。")
            return response.text.strip().replace("[TALKING]", "").strip()
        except:
            return "お忙しいところ恐れ入ります。予約のお電話です。"


# ========================================
# Google Cloud STT
# ========================================

def transcribe_audio(audio_content: bytes, sample_rate: int = 16000) -> str:
    """
    音声をテキストに変換

    Args:
        audio_content: 音声データ（LINEAR16）
        sample_rate: サンプルレート

    Returns:
        認識されたテキスト
    """
    if not GOOGLE_AVAILABLE:
        return "[STT未対応]"

    try:
        client = speech_v1.SpeechClient()

        audio = speech_v1.RecognitionAudio(content=audio_content)
        config = speech_v1.RecognitionConfig(
            encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            language_code="ja-JP",
            enable_automatic_punctuation=True,
        )

        response = client.recognize(config=config, audio=audio)

        transcript = ""
        for result in response.results:
            transcript += result.alternatives[0].transcript

        return transcript

    except Exception as e:
        logger.error(f"STT エラー: {e}")
        return ""


# ========================================
# Google Cloud TTS
# ========================================

def synthesize_speech(text: str, output_format: str = "LINEAR16") -> bytes:
    """
    テキストを音声に変換

    Args:
        text: 読み上げるテキスト
        output_format: 出力形式（LINEAR16, MULAW）

    Returns:
        音声データ
    """
    if not GOOGLE_AVAILABLE:
        return b""

    try:
        client = texttospeech_v1.TextToSpeechClient()

        synthesis_input = texttospeech_v1.SynthesisInput(text=text)

        voice = texttospeech_v1.VoiceSelectionParams(
            language_code="ja-JP",
            name="ja-JP-Neural2-B",  # 女性の自然な声
        )

        if output_format == "MULAW":
            # Twilio用（μ-law 8kHz）
            audio_config = texttospeech_v1.AudioConfig(
                audio_encoding=texttospeech_v1.AudioEncoding.MULAW,
                sample_rate_hertz=8000,
            )
        else:
            # 標準（LINEAR16 16kHz）
            audio_config = texttospeech_v1.AudioConfig(
                audio_encoding=texttospeech_v1.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
            )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        return response.audio_content

    except Exception as e:
        logger.error(f"TTS エラー: {e}")
        return b""


# ========================================
# テスト実行
# ========================================

def test_gemini_conversation():
    """Gemini会話テスト"""
    print("\n" + "=" * 50)
    print("1. Gemini 予約会話テスト")
    print("=" * 50)

    reservation = {
        "reserver_name": "山田太郎",
        "contact_phone": "090-1234-5678",
        "date": "12月25日",
        "time": "19:00",
        "flexibility": "30",
        "guests": 4,
        "seat_types": ["テーブル席"],
        "notes": "誕生日のお祝いです"
    }

    agent = ReservationAgent(reservation)

    # 最初の発話
    opening = agent.get_opening()
    print(f"\n🤖 AI: {opening}")

    # 模擬会話
    shop_responses = [
        "はい、お電話ありがとうございます。ご予約ですね。",
        "12月25日の19時、4名様ですね。少々お待ちください。",
        "申し訳ございません、19時は満席なのですが、19時30分でしたらお席をご用意できます。",
        "はい、では19時30分で4名様、山田様でお取りしておきます。当日お待ちしております。"
    ]

    for shop_text in shop_responses:
        print(f"\n👤 店員: {shop_text}")
        response, state = agent.process(shop_text)
        print(f"🤖 AI: {response}")
        print(f"   [状態: {state}]")

        if state in ["success", "failed"]:
            break

    print("\n✅ 会話テスト完了")
    return True


def test_tts():
    """TTS テスト"""
    print("\n" + "=" * 50)
    print("2. Google Cloud TTS テスト")
    print("=" * 50)

    text = "お忙しいところ恐れ入ります。予約のお電話です。"
    print(f"\n入力テキスト: {text}")

    audio = synthesize_speech(text)

    if audio:
        print(f"✅ 音声生成成功: {len(audio)} bytes")

        # WAVファイルとして保存（クロスプラットフォーム対応）
        import tempfile
        output_path = os.path.join(tempfile.gettempdir(), "test_tts_output.wav")
        with wave.open(output_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(16000)
            wf.writeframes(audio)

        print(f"   保存先: {output_path}")
        return True
    else:
        print("❌ 音声生成失敗")
        return False


def test_full_flow():
    """フルフローテスト（テキスト入力で擬似的にテスト）"""
    print("\n" + "=" * 50)
    print("3. フルフロー テスト（テキスト入力）")
    print("=" * 50)

    reservation = {
        "reserver_name": "山田太郎",
        "contact_phone": "090-1234-5678",
        "date": "明日",
        "time": "18:00",
        "flexibility": "exact",
        "guests": 2,
        "seat_types": ["カウンター席"],
        "notes": ""
    }

    agent = ReservationAgent(reservation)

    print("\n📞 予約電話シミュレーション開始")
    print("   （'quit'で終了）\n")

    # 最初の発話
    opening = agent.get_opening()
    print(f"🤖 AI: {opening}")

    # TTS で音声化
    audio = synthesize_speech(opening)
    if audio:
        print(f"   [TTS: {len(audio)} bytes]")

    while True:
        shop_input = input("\n👤 店員 > ").strip()
        if shop_input.lower() == 'quit':
            break

        response, state = agent.process(shop_input)
        print(f"🤖 AI: {response}")
        print(f"   [状態: {state}]")

        # TTS で音声化
        audio = synthesize_speech(response)
        if audio:
            print(f"   [TTS: {len(audio)} bytes]")

        if state in ["success", "failed"]:
            print("\n📞 通話終了")
            break

    return True


def main():
    """メイン"""
    print("\n🎙️ 音声AI連携テスト")
    print("=" * 50)

    # 環境チェック
    print("\n環境チェック:")
    print(f"  GOOGLE_APPLICATION_CREDENTIALS: {'✅' if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') else '❌'}")
    print(f"  GOOGLE_API_KEY: {'✅' if os.environ.get('GOOGLE_API_KEY') else '❌'}")
    print(f"  Google Cloud SDK: {'✅' if GOOGLE_AVAILABLE else '❌'}")

    # テスト選択
    print("\nテストを選択:")
    print("  1. Gemini会話テスト（自動）")
    print("  2. TTS音声生成テスト")
    print("  3. フルフローテスト（対話形式）")
    print("  4. 全テスト実行")

    choice = input("\n選択 (1-4): ").strip()

    if choice == "1":
        test_gemini_conversation()
    elif choice == "2":
        test_tts()
    elif choice == "3":
        test_full_flow()
    elif choice == "4":
        test_gemini_conversation()
        test_tts()
    else:
        print("無効な選択です")


if __name__ == '__main__':
    main()
