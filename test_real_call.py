#!/usr/bin/env python3
"""
実通話テスト - Twilio トライアルアカウント用
認証済み携帯番号への発信テスト
"""

import os
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Say, Gather

# Twilio認証情報
ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')

# 電話番号設定
# FROM: 購入した番号、または Verified Caller ID
FROM_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
# TO: 認証済みの携帯番号
TO_NUMBER = os.environ.get('TEST_PHONE_NUMBER')


def make_simple_call():
    """シンプルなTTS通話テスト"""
    print("\n" + "=" * 50)
    print("📞 実通話テスト（TTS）")
    print("=" * 50)

    if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER, TO_NUMBER]):
        print("❌ 環境変数が不足しています:")
        print(f"   TWILIO_ACCOUNT_SID: {'✅' if ACCOUNT_SID else '❌'}")
        print(f"   TWILIO_AUTH_TOKEN: {'✅' if AUTH_TOKEN else '❌'}")
        print(f"   TWILIO_PHONE_NUMBER: {'✅' if FROM_NUMBER else '❌'}")
        print(f"   TEST_PHONE_NUMBER: {'✅' if TO_NUMBER else '❌'}")
        return False

    print(f"\n発信元: {FROM_NUMBER}")
    print(f"発信先: {TO_NUMBER}")

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    # TwiML URL（Twilio のデモ用）または インラインTwiML
    twiml = VoiceResponse()
    twiml.say(
        "こんにちは。これはグルメサポートの予約システムからのテスト通話です。"
        "音声合成が正常に動作しています。"
        "このメッセージが聞こえていれば、テストは成功です。"
        "ありがとうございました。",
        language="ja-JP",
        voice="Google.ja-JP-Neural2-B"  # Google Neural voice
    )

    try:
        print("\n📱 発信中...")
        call = client.calls.create(
            twiml=str(twiml),
            to=TO_NUMBER,
            from_=FROM_NUMBER
        )

        print(f"✅ 発信成功!")
        print(f"   Call SID: {call.sid}")
        print(f"   Status: {call.status}")
        print(f"\n📞 電話に出てください！")

        return True

    except Exception as e:
        print(f"❌ 発信失敗: {e}")
        return False


def make_interactive_call(webhook_url: str):
    """Webhook連携の対話型通話テスト"""
    print("\n" + "=" * 50)
    print("📞 対話型通話テスト（Webhook）")
    print("=" * 50)

    if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER, TO_NUMBER]):
        print("❌ 環境変数が不足しています")
        return False

    print(f"\n発信元: {FROM_NUMBER}")
    print(f"発信先: {TO_NUMBER}")
    print(f"Webhook: {webhook_url}")

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    try:
        print("\n📱 発信中...")
        call = client.calls.create(
            url=webhook_url,
            to=TO_NUMBER,
            from_=FROM_NUMBER,
            status_callback=webhook_url.replace('/answer', '/status'),
            status_callback_event=['initiated', 'ringing', 'answered', 'completed']
        )

        print(f"✅ 発信成功!")
        print(f"   Call SID: {call.sid}")
        print(f"   Status: {call.status}")

        return True

    except Exception as e:
        print(f"❌ 発信失敗: {e}")
        return False


def check_call_status(call_sid: str):
    """通話ステータス確認"""
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    call = client.calls(call_sid).fetch()

    print(f"\n📊 通話ステータス:")
    print(f"   SID: {call.sid}")
    print(f"   Status: {call.status}")
    print(f"   Duration: {call.duration}秒")
    print(f"   Direction: {call.direction}")

    return call


def list_verified_numbers():
    """認証済み番号一覧"""
    print("\n" + "=" * 50)
    print("📋 認証済み電話番号一覧")
    print("=" * 50)

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    try:
        # Outgoing Caller IDs (Verified Numbers)
        caller_ids = client.outgoing_caller_ids.list()

        if caller_ids:
            print(f"\n✅ {len(caller_ids)}件の認証済み番号:")
            for cid in caller_ids:
                print(f"   {cid.phone_number} ({cid.friendly_name})")
        else:
            print("\n⚠️ 認証済み番号がありません")
            print("   Twilio Console → Phone Numbers → Verified Caller IDs で追加してください")

        return caller_ids

    except Exception as e:
        print(f"❌ エラー: {e}")
        return []


def main():
    print("=" * 50)
    print("📞 Twilio 実通話テスト")
    print("=" * 50)

    print("\n環境変数:")
    print(f"  TWILIO_ACCOUNT_SID: {ACCOUNT_SID[:10]}..." if ACCOUNT_SID else "  TWILIO_ACCOUNT_SID: ❌ 未設定")
    print(f"  TWILIO_AUTH_TOKEN: {'✅ 設定済' if AUTH_TOKEN else '❌ 未設定'}")
    print(f"  TWILIO_PHONE_NUMBER: {FROM_NUMBER or '❌ 未設定'}")
    print(f"  TEST_PHONE_NUMBER: {TO_NUMBER or '❌ 未設定'}")

    print("\nテストを選択:")
    print("  1. 認証済み番号一覧を表示")
    print("  2. シンプル通話テスト（TTS）")
    print("  3. 対話型通話テスト（要Webhook URL）")
    print("  4. 通話ステータス確認")

    choice = input("\n選択 (1-4): ").strip()

    if choice == '1':
        list_verified_numbers()
    elif choice == '2':
        make_simple_call()
    elif choice == '3':
        webhook_url = input("Webhook URL (例: https://xxx.ngrok.io/api/twilio/answer): ").strip()
        if webhook_url:
            make_interactive_call(webhook_url)
        else:
            print("❌ Webhook URLが必要です")
    elif choice == '4':
        call_sid = input("Call SID: ").strip()
        if call_sid:
            check_call_status(call_sid)
        else:
            print("❌ Call SIDが必要です")
    else:
        print("❌ 無効な選択")


if __name__ == "__main__":
    main()
