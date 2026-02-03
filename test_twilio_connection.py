#!/usr/bin/env python3
"""
Twilio接続テスト（バンドル承認待ち中のテスト用）

テスト方法:
1. 環境変数を設定
2. python test_twilio_connection.py を実行
"""

import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

# 環境変数から取得（または直接設定）
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', 'ACc07a248bcd2855feb014c36ee0773d79')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '4e6decf149836342498be1dc272a4ecd')

# Twilioのテスト用マジックナンバー
# https://www.twilio.com/docs/iam/test-credentials
TEST_NUMBERS = {
    'valid': '+15005550006',      # 正常な番号（テスト用）
    'invalid': '+15005550001',    # 無効な番号
    'unavailable': '+15005550000' # 利用不可の番号
}


def test_account_connection():
    """アカウント接続テスト"""
    print("=" * 50)
    print("1. Twilioアカウント接続テスト")
    print("=" * 50)

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        account = client.api.accounts(TWILIO_ACCOUNT_SID).fetch()

        print(f"$2705 接続成功!")
        print(f"   Account SID: {account.sid}")
        print(f"   Account Name: {account.friendly_name}")
        print(f"   Status: {account.status}")
        print(f"   Type: {account.type}")
        return True

    except TwilioRestException as e:
        print(f"$274C 接続失敗: {e.msg}")
        return False
    except Exception as e:
        print(f"$274C エラー: {e}")
        return False


def test_phone_number_lookup():
    """電話番号ルックアップテスト"""
    print("\n" + "=" * 50)
    print("2. 電話番号ルックアップテスト")
    print("=" * 50)

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # 利用可能な番号を検索（日本）
        print("\n日本の利用可能な番号を検索中...")
        available_numbers = client.available_phone_numbers('JP').local.list(limit=3)

        if available_numbers:
            print(f"$2705 {len(available_numbers)}件の番号が利用可能:")
            for number in available_numbers:
                print(f"   {number.phone_number} ({number.locality})")
        else:
            print("$26A0$FE0F 利用可能な番号がありません（規制バンドル未承認の可能性）")

        return True

    except TwilioRestException as e:
        if 'regulatory compliance' in str(e.msg).lower() or 'bundle' in str(e.msg).lower():
            print(f"$26A0$FE0F 規制バンドル未承認のため番号検索不可: {e.msg}")
        else:
            print(f"$274C エラー: {e.msg}")
        return False
    except Exception as e:
        print(f"$274C エラー: {e}")
        return False


def test_twiml_generation():
    """TwiML生成テスト"""
    print("\n" + "=" * 50)
    print("3. TwiML生成テスト")
    print("=" * 50)

    from twilio.twiml.voice_response import VoiceResponse, Stream

    # 基本的な音声応答
    response = VoiceResponse()
    response.say('お忙しいところ恐れ入ります。予約のお電話です。', language='ja-JP')

    print("$2705 基本TwiML生成成功:")
    print(str(response))

    # WebSocketストリーム付きTwiML
    response2 = VoiceResponse()
    response2.say('予約システムに接続します。', language='ja-JP')

    # 双方向音声ストリーム
    stream = Stream(url='wss://your-server.com/stream')
    stream.parameter(name='reservation_id', value='test123')
    response2.append(stream)

    print("\n$2705 WebSocketストリームTwiML生成成功:")
    print(str(response2))

    return True


def test_webhook_simulation():
    """Webhookシミュレーションテスト"""
    print("\n" + "=" * 50)
    print("4. Webhook処理シミュレーション")
    print("=" * 50)

    # Twilioから送られてくるWebhookデータの例
    sample_webhook_data = {
        'CallSid': 'CA1234567890abcdef',
        'AccountSid': TWILIO_ACCOUNT_SID,
        'From': '+819012345678',
        'To': '+81312345678',
        'CallStatus': 'ringing',
        'Direction': 'outbound-api',
        'ApiVersion': '2010-04-01'
    }

    print("$2705 サンプルWebhookデータ:")
    for key, value in sample_webhook_data.items():
        print(f"   {key}: {value}")

    # 処理ロジックのテスト
    call_status = sample_webhook_data['CallStatus']
    if call_status == 'ringing':
        print("\n→ 処理: 呼び出し中...")
    elif call_status == 'in-progress':
        print("\n→ 処理: 通話中、音声ストリーム開始")
    elif call_status == 'completed':
        print("\n→ 処理: 通話完了、結果を保存")

    return True


def test_call_with_test_credentials():
    """テスト用クレデンシャルでの発信テスト"""
    print("\n" + "=" * 50)
    print("5. テスト発信（マジックナンバー使用）")
    print("=" * 50)

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # テスト用マジックナンバーで発信
        # 注意: 実際の通話は発生しない
        print(f"\nテスト番号 {TEST_NUMBERS['valid']} への発信をシミュレート...")

        # TwiML URLを使用した発信テスト
        # 実際にはテスト用番号なので課金されない
        call = client.calls.create(
            to=TEST_NUMBERS['valid'],
            from_=TEST_NUMBERS['valid'],  # テスト用FROM番号
            url='http://demo.twilio.com/docs/voice.xml'
        )

        print(f"$2705 発信テスト成功!")
        print(f"   Call SID: {call.sid}")
        print(f"   Status: {call.status}")

        return True

    except TwilioRestException as e:
        # テスト用番号でも規制により失敗する場合がある
        print(f"$26A0$FE0F テスト発信: {e.msg}")
        print("   (規制バンドル承認後に実番号でテスト可能)")
        return False
    except Exception as e:
        print(f"$274C エラー: {e}")
        return False


def main():
    """メインテスト実行"""
    print("\n" + "$D83D$DD27 Twilio接続テスト開始 " + "=" * 35)
    print(f"Account SID: {TWILIO_ACCOUNT_SID[:10]}...")

    results = []

    # テスト実行
    results.append(('アカウント接続', test_account_connection()))
    results.append(('電話番号検索', test_phone_number_lookup()))
    results.append(('TwiML生成', test_twiml_generation()))
    results.append(('Webhookシミュレーション', test_webhook_simulation()))
    results.append(('テスト発信', test_call_with_test_credentials()))

    # 結果サマリー
    print("\n" + "=" * 50)
    print("$D83D$DCCA テスト結果サマリー")
    print("=" * 50)

    for name, result in results:
        status = "$2705 PASS" if result else "$26A0$FE0F SKIP/FAIL"
        print(f"   {name}: {status}")

    print("\n$D83D$DCA1 次のステップ:")
    print("   1. 規制バンドルの承認を待つ")
    print("   2. 承認後、実番号を取得")
    print("   3. Cloud RunのWebhookエンドポイントを設定")
    print("   4. 実番号でのテスト発信")


if __name__ == '__main__':
    main()
