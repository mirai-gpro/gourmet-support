from twilio.rest import Client
import os

client = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])

twiml = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="ja-JP" voice="Polly.Mizuki">こんにちは。日本語テストです。成功しました。</Say>
</Response>'''

call = client.calls.create(
    twiml=twiml,
    to='+819025565125',
    from_='+819025565125'
)
print(f'Call SID: {call.sid}')
