# -*- coding: utf-8 -*-
"""
汎用カスタマーサポートシステム (Gemini API版) - 改善版
モジュール分割版（3ファイル構成）

分割構成:
- api_integrations.py: 外部API連携
- support_core.py: ビジネスロジック・コアクラス
- app_customer_support.py: Webアプリケーション層（本ファイル）
"""
import os
import re
import json
import base64
import logging
import threading
import queue
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from google import genai
from google.genai import types
from google.cloud import texttospeech
from google.cloud import speech

# 新しいモジュールからインポート
from api_integrations import (
    enrich_shops_with_photos,
    extract_area_from_text,
    GOOGLE_PLACES_API_KEY
)
from support_core import (
    load_system_prompts,
    INITIAL_GREETINGS,
    SYSTEM_PROMPTS,
    SupportSession,
    SupportAssistant
)

# ãƒ­ã‚®ãƒ³ã‚°è¨­å®š
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # UTF-8エンコーディングを有効化

# ========================================
# CORS & SocketIO è¨­å®š (Claudeã‚¢ãƒ‰ãƒã‚¤ã‚¹é©ç”¨ç‰ˆ)
# ========================================

# è¨±å¯ã™ã‚‹ã‚ªãƒªã‚¸ãƒ³(æœ«å°¾ã®ã‚¹ãƒ©ãƒƒã‚·ãƒ¥ãªã—)
allowed_origins = [
    "https://gourmet-sp-two.vercel.app",
    "https://gourmet-sp.vercel.app",
    "http://localhost:4321"
]

# SocketIOåˆæœŸåŒ– (cors_allowed_originsã‚’æ˜Žç¤ºçš„ã«æŒ‡å®š)
socketio = SocketIO(
    app,
    cors_allowed_origins=allowed_origins,
    async_mode='threading',
    logger=False,
    engineio_logger=False
)

# Flask-CORSåˆæœŸåŒ– (supports_credentials=True)
CORS(app, resources={
    r"/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# ã€é‡è¦ã€‘å…¨ãƒ¬ã‚¹ãƒãƒ³ã‚¹ã«å¼·åˆ¶çš„ã«CORSãƒ˜ãƒƒãƒ€ãƒ¼ã‚’æ³¨å…¥ã™ã‚‹ãƒ•ãƒƒã‚¯
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    # UTF-8エンコーディングを明示
    if response.content_type and 'application/json' in response.content_type:
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response

# Google Cloud TTS/STTåˆæœŸåŒ–
tts_client = texttospeech.TextToSpeechClient()
stt_client = speech.SpeechClient()

# プロンプト読み込み
SYSTEM_PROMPTS = load_system_prompts()

@app.route('/')
def index():
    """ãƒ•ãƒ­ãƒ³ãƒˆã‚¨ãƒ³ãƒ‰è¡¨ç¤º"""
    return render_template('support.html')


@app.route('/api/session/start', methods=['POST', 'OPTIONS'])
def start_session():
    """
    ã‚»ãƒƒã‚·ãƒ§ãƒ³é–‹å§‹ - ãƒ¢ãƒ¼ãƒ‰å¯¾å¿œ
    
    ã€é‡è¦ã€‘æ”¹å–„ã•ã‚ŒãŸãƒ•ãƒ­ãƒ¼:
    1. ã‚»ãƒƒã‚·ãƒ§ãƒ³åˆæœŸåŒ–ï¼ˆãƒ¢ãƒ¼ãƒ‰ãƒ»è¨€èªžè¨­å®šï¼‰
    2. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆä½œæˆï¼ˆæœ€æ–°ã®çŠ¶æ…‹ã§ï¼‰
    3. åˆå›žãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ç”Ÿæˆ
    4. å±¥æ­´ã«è¿½åŠ 
    """
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json or {}
        user_info = data.get('user_info', {})
        language = data.get('language', 'ja')
        mode = data.get('mode', 'chat')

        # 1. ã‚»ãƒƒã‚·ãƒ§ãƒ³åˆæœŸåŒ–
        session = SupportSession()
        session.initialize(user_info, language=language, mode=mode)
        logger.info(f"[Start Session] æ–°è¦ã‚»ãƒƒã‚·ãƒ§ãƒ³ä½œæˆ: {session.session_id}")

        # 2. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆä½œæˆï¼ˆæœ€æ–°ã®çŠ¶æ…‹ã§ï¼‰
        assistant = SupportAssistant(session, SYSTEM_PROMPTS)
        
        # 3. åˆå›žãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ç”Ÿæˆ
        initial_message = assistant.get_initial_message()

        # 4. å±¥æ­´ã«è¿½åŠ ï¼ˆroleã¯'model'ï¼‰
        session.add_message('model', initial_message, 'chat')

        logger.info(f"[API] ã‚»ãƒƒã‚·ãƒ§ãƒ³é–‹å§‹: {session.session_id}, è¨€èªž: {language}, ãƒ¢ãƒ¼ãƒ‰: {mode}")

        return jsonify({
            'session_id': session.session_id,
            'initial_message': initial_message
        })

    except Exception as e:
        logger.error(f"[API] ã‚»ãƒƒã‚·ãƒ§ãƒ³é–‹å§‹ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    """
    ãƒãƒ£ãƒƒãƒˆå‡¦ç† - æ”¹å–„ç‰ˆ
    
    ã€é‡è¦ã€‘æ”¹å–„ã•ã‚ŒãŸãƒ•ãƒ­ãƒ¼ï¼ˆé †åºã‚’åŽ³å®ˆï¼‰:
    1. çŠ¶æ…‹ç¢ºå®š (State First): ãƒ¢ãƒ¼ãƒ‰ãƒ»è¨€èªžã‚’æ›´æ–°
    2. ãƒ¦ãƒ¼ã‚¶ãƒ¼å…¥åŠ›ã‚’è¨˜éŒ²: ãƒ¡ãƒƒã‚»ãƒ¼ã‚¸ã‚’å±¥æ­´ã«è¿½åŠ 
    3. çŸ¥èƒ½ç”Ÿæˆ (Assistantä½œæˆ): æœ€æ–°ã®çŠ¶æ…‹ã§ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆã‚’ä½œæˆ
    4. æŽ¨è«–é–‹å§‹: Gemini APIã‚’å‘¼ã³å‡ºã—
    5. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆå¿œç­”ã‚’è¨˜éŒ²: å±¥æ­´ã«è¿½åŠ 
    """
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')
        user_message = data.get('message')
        stage = data.get('stage', 'conversation')
        language = data.get('language', 'ja')
        mode = data.get('mode', 'chat')

        if not session_id or not user_message:
            return jsonify({'error': 'session_idã¨messageãŒå¿…è¦ã§ã™'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'ã‚»ãƒƒã‚·ãƒ§ãƒ³ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“'}), 404

        logger.info(f"[Chat] ã‚»ãƒƒã‚·ãƒ§ãƒ³: {session_id}, ãƒ¢ãƒ¼ãƒ‰: {mode}, è¨€èªž: {language}")

        # 1. çŠ¶æ…‹ç¢ºå®š (State First)
        session.update_language(language)
        session.update_mode(mode)

        # 2. ãƒ¦ãƒ¼ã‚¶ãƒ¼å…¥åŠ›ã‚’è¨˜éŒ²
        session.add_message('user', user_message, 'chat')

        # 3. çŸ¥èƒ½ç”Ÿæˆ (Assistantä½œæˆ)
        assistant = SupportAssistant(session, SYSTEM_PROMPTS)
        
        # 4. æŽ¨è«–é–‹å§‹
        result = assistant.process_user_message(user_message, stage)
        
        # 5. ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆå¿œç­”ã‚’è¨˜éŒ²
        session.add_message('model', result['response'], 'chat')

        if result['summary']:
            session.add_message('model', result['summary'], 'summary')

        # ã‚·ãƒ§ãƒƒãƒ—ãƒ‡ãƒ¼ã‚¿å‡¦ç†
        shops = result.get('shops', [])
        response_text = result['response']
        is_followup = result.get('is_followup', False)

        # å¤šè¨€èªžãƒ¡ãƒƒã‚»ãƒ¼ã‚¸è¾žæ›¸
        shop_messages = {
            'ja': {
                'intro': lambda count: f"ご希望に合うお店を{count}件ご紹介します。\n\n",
                'not_found': "申し訳ございません。条件に合うお店が見つかりませんでした。別の条件でお探しいただけますか?"
            },
            'en': {
                'intro': lambda count: f"Here are {count} restaurant recommendations for you.\n\n",
                'not_found': "Sorry, we couldn't find any restaurants matching your criteria. Would you like to search with different conditions?"
            },
            'zh': {
                'intro': lambda count: f"为您推荐{count}家餐厅。\n\n",
                'not_found': "很抱歉,没有找到符合条件的餐厅。要用其他条件搜索吗?"
            },
            'ko': {
                'intro': lambda count: f"고객님께 {count}개의 식당을 추천합니다.\n\n",
                'not_found': "죄송합니다. 조건에 맞는 식당을 찾을 수 없었습니다. 다른 조건으로 찾으시겠습니까?"
            }
        }

        current_messages = shop_messages.get(language, shop_messages['ja'])

        if shops and not is_followup:
            original_count = len(shops)
            area = extract_area_from_text(user_message, language)
            logger.info(f"[Chat] æŠ½å‡ºã‚¨ãƒªã‚¢: '{area}' from '{user_message}'")

            # Places APIã§å†™çœŸã‚’å–å¾—
            shops = enrich_shops_with_photos(shops, area, language)

            if shops:
                shop_list = []
                for i, shop in enumerate(shops, 1):
                    name = shop.get('name', '')
                    shop_area = shop.get('area', '')
                    description = shop.get('description', '')
                    if shop_area:
                        shop_list.append(f"{i}. **{name}**({shop_area}): {description}")
                    else:
                        shop_list.append(f"{i}. **{name}**: {description}")

                response_text = current_messages['intro'](len(shops)) + "\n\n".join(shop_list)
                logger.info(f"[Chat] {len(shops)}ä»¶ã®ã‚·ãƒ§ãƒƒãƒ—ãƒ‡ãƒ¼ã‚¿ã‚’è¿”å´(å…ƒ: {original_count}ä»¶, è¨€èªž: {language})")
            else:
                response_text = current_messages['not_found']
                logger.warning(f"[Chat] å…¨åº—èˆ—ãŒé™¤å¤–ã•ã‚Œã¾ã—ãŸ(å…ƒ: {original_count}ä»¶)")

        elif is_followup:
            logger.info(f"[Chat] æ·±æŽ˜ã‚Šè³ªå•ã¸ã®å›žç­”: {response_text[:100]}...")

        # 【デバッグ】最終的なshopsの内容を確認
        logger.info(f"[Chat] 最終shops配列: {len(shops)}件")
        if shops:
            logger.info(f"[Chat] shops[0] keys: {list(shops[0].keys())}")
        return jsonify({
            'response': response_text,
            'summary': result['summary'],
            'shops': shops,
            'should_confirm': result['should_confirm'],
            'is_followup': is_followup
        })

    except Exception as e:
        logger.error(f"[API] ãƒãƒ£ãƒƒãƒˆã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/finalize', methods=['POST', 'OPTIONS'])
def finalize_session():
    """ã‚»ãƒƒã‚·ãƒ§ãƒ³å®Œäº†"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'error': 'session_idãŒå¿…è¦ã§ã™'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'ã‚»ãƒƒã‚·ãƒ§ãƒ³ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“'}), 404

        assistant = SupportAssistant(session, SYSTEM_PROMPTS)
        final_summary = assistant.generate_final_summary()

        return jsonify({
            'summary': final_summary,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"[API] å®Œäº†å‡¦ç†ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel', methods=['POST', 'OPTIONS'])
def cancel_processing():
    """å‡¦ç†ä¸­æ­¢"""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'session_idãŒå¿…è¦ã§ã™'}), 400
        
        logger.info(f"[API] å‡¦ç†ä¸­æ­¢ãƒªã‚¯ã‚¨ã‚¹ãƒˆ: {session_id}")
        
        # ã‚»ãƒƒã‚·ãƒ§ãƒ³ã®ã‚¹ãƒ†ãƒ¼ã‚¿ã‚¹ã‚’æ›´æ–°
        session = SupportSession(session_id)
        session_data = session.get_data()
        
        if session_data:
            session.update_status('cancelled')
        
        return jsonify({
            'success': True,
            'message': 'å‡¦ç†ã‚’ä¸­æ­¢ã—ã¾ã—ãŸ'
        })
        
    except Exception as e:
        logger.error(f"[API] ä¸­æ­¢å‡¦ç†ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tts/synthesize', methods=['POST', 'OPTIONS'])
def synthesize_speech():
    """éŸ³å£°åˆæˆ"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        text = data.get('text', '')
        language_code = data.get('language_code', 'ja-JP')
        voice_name = data.get('voice_name', 'ja-JP-Chirp3-HD-Leda')
        speaking_rate = data.get('speaking_rate', 1.0)
        pitch = data.get('pitch', 0.0)

        if not text:
            return jsonify({'success': False, 'error': 'ãƒ†ã‚­ã‚¹ãƒˆãŒå¿…è¦ã§ã™'}), 400

        MAX_CHARS = 1000
        if len(text) > MAX_CHARS:
            logger.warning(f"[TTS] ãƒ†ã‚­ã‚¹ãƒˆãŒé•·ã™ãŽã‚‹ãŸã‚åˆ‡ã‚Šè©°ã‚ã¾ã™: {len(text)} â†’ {MAX_CHARS} æ–‡å­—")
            text = text[:MAX_CHARS] + '...'

        logger.info(f"[TTS] åˆæˆé–‹å§‹: {len(text)} æ–‡å­—")

        synthesis_input = texttospeech.SynthesisInput(text=text)

        try:
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )
        except Exception as voice_error:
            logger.warning(f"[TTS] æŒ‡å®šéŸ³å£°ãŒç„¡åŠ¹ã€ãƒ‡ãƒ•ã‚©ãƒ«ãƒˆã«å¤‰æ›´: {voice_error}")
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name='ja-JP-Neural2-B'
            )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
            pitch=pitch
        )

        response = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        audio_base64 = base64.b64encode(response.audio_content).decode('utf-8')

        logger.info(f"[TTS] åˆæˆæˆåŠŸ: {len(audio_base64)} bytes (base64)")

        return jsonify({
            'success': True,
            'audio': audio_base64
        })

    except Exception as e:
        logger.error(f"[TTS] ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/transcribe', methods=['POST', 'OPTIONS'])
def transcribe_audio():
    """éŸ³å£°èªè­˜"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': 'éŸ³å£°ãƒ‡ãƒ¼ã‚¿ãŒå¿…è¦ã§ã™'}), 400

        logger.info(f"[STT] èªè­˜é–‹å§‹: {len(audio_base64)} bytes (base64)")

        audio_content = base64.b64decode(audio_base64)
        audio = speech.RecognitionAudio(content=audio_content)

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code=language_code,
            enable_automatic_punctuation=True,
            model='default'
        )

        response = stt_client.recognize(config=config, audio=audio)

        transcript = ''
        if response.results:
            transcript = response.results[0].alternatives[0].transcript
            confidence = response.results[0].alternatives[0].confidence
            logger.info(f"[STT] èªè­˜æˆåŠŸ: '{transcript}' (ä¿¡é ¼åº¦: {confidence:.2f})")
        else:
            logger.warning("[STT] éŸ³å£°ãŒèªè­˜ã•ã‚Œã¾ã›ã‚“ã§ã—ãŸ")

        return jsonify({
            'success': True,
            'transcript': transcript
        })

    except Exception as e:
        logger.error(f"[STT] ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/stream', methods=['POST', 'OPTIONS'])
def transcribe_audio_streaming():
    """éŸ³å£°èªè­˜ (Streaming)"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': 'éŸ³å£°ãƒ‡ãƒ¼ã‚¿ãŒå¿…è¦ã§ã™'}), 400

        logger.info(f"[STT Streaming] èªè­˜é–‹å§‹: {len(audio_base64)} bytes (base64)")

        audio_content = base64.b64decode(audio_base64)

        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code=language_code,
            enable_automatic_punctuation=True,
            model='default'
        )

        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=False,
            single_utterance=True
        )

        CHUNK_SIZE = 1024 * 16

        def audio_generator():
            for i in range(0, len(audio_content), CHUNK_SIZE):
                chunk = audio_content[i:i + CHUNK_SIZE]
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        responses = stt_client.streaming_recognize(streaming_config, audio_generator())

        transcript = ''
        confidence = 0.0

        for response in responses:
            if not response.results:
                continue

            for result in response.results:
                if result.is_final and result.alternatives:
                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence
                    logger.info(f"[STT Streaming] èªè­˜æˆåŠŸ: '{transcript}' (ä¿¡é ¼åº¦: {confidence:.2f})")
                    break

            if transcript:
                break

        if not transcript:
            logger.warning("[STT Streaming] éŸ³å£°ãŒèªè­˜ã•ã‚Œã¾ã›ã‚“ã§ã—ãŸ")

        return jsonify({
            'success': True,
            'transcript': transcript,
            'confidence': confidence
        })

    except Exception as e:
        logger.error(f"[STT Streaming] ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/session/<session_id>', methods=['GET', 'OPTIONS'])
def get_session(session_id):
    """ã‚»ãƒƒã‚·ãƒ§ãƒ³æƒ…å ±å–å¾—"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        session = SupportSession(session_id)
        data = session.get_data()

        if not data:
            return jsonify({'error': 'ã‚»ãƒƒã‚·ãƒ§ãƒ³ãŒè¦‹ã¤ã‹ã‚Šã¾ã›ã‚“'}), 404

        return jsonify(data)

    except Exception as e:
        logger.error(f"[API] ã‚»ãƒƒã‚·ãƒ§ãƒ³å–å¾—ã‚¨ãƒ©ãƒ¼: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET', 'OPTIONS'])
def health_check():
    """ãƒ˜ãƒ«ã‚¹ãƒã‚§ãƒƒã‚¯"""
    if request.method == 'OPTIONS':
        return '', 204

    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'gemini': 'ok',
            'ram_session': 'ok',
            'tts': 'ok',
            'stt': 'ok',
            'places_api': 'ok' if GOOGLE_PLACES_API_KEY else 'not configured'
        }
    })


# ========================================
# WebSocket Streaming STT
# ========================================

active_streams = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f"[WebSocket STT] ã‚¯ãƒ©ã‚¤ã‚¢ãƒ³ãƒˆæŽ¥ç¶š: {request.sid}")
    emit('connected', {'status': 'ready'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"[WebSocket STT] ã‚¯ãƒ©ã‚¤ã‚¢ãƒ³ãƒˆåˆ‡æ–­: {request.sid}")
    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        if 'stop_event' in stream_data:
            stream_data['stop_event'].set()
        del active_streams[request.sid]

@socketio.on('start_stream')
def handle_start_stream(data):
    language_code = data.get('language_code', 'ja-JP')
    sample_rate = data.get('sample_rate', 16000)  # ãƒ•ãƒ­ãƒ³ãƒˆã‚¨ãƒ³ãƒ‰ã‹ã‚‰å—ã‘å–ã‚‹
    client_sid = request.sid
    logger.info(f"[WebSocket STT] ã‚¹ãƒˆãƒªãƒ¼ãƒ é–‹å§‹: {client_sid}, è¨€èªž: {language_code}, ã‚µãƒ³ãƒ—ãƒ«ãƒ¬ãƒ¼ãƒˆ: {sample_rate}Hz")

    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,  # å‹•çš„ã«è¨­å®š
        language_code=language_code,
        enable_automatic_punctuation=True,
        model='latest_long'  # ã‚ˆã‚Šé«˜ç²¾åº¦ãªãƒ¢ãƒ‡ãƒ«ã«å¤‰æ›´
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=True,
        single_utterance=False
    )

    audio_queue = queue.Queue()
    stop_event = threading.Event()

    active_streams[client_sid] = {
        'audio_queue': audio_queue,
        'stop_event': stop_event,
        'streaming_config': streaming_config
    }

    def audio_generator():
        while not stop_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.5)
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)
            except queue.Empty:
                continue

    def recognition_thread():
        try:
            logger.info(f"[WebSocket STT] èªè­˜ã‚¹ãƒ¬ãƒƒãƒ‰é–‹å§‹: {client_sid}")
            responses = stt_client.streaming_recognize(streaming_config, audio_generator())

            for response in responses:
                if stop_event.is_set():
                    break

                if not response.results:
                    continue

                result = response.results[0]

                if result.alternatives:
                    transcript = result.alternatives[0].transcript
                    confidence = result.alternatives[0].confidence if result.is_final else 0.0

                    socketio.emit('transcript', {
                        'text': transcript,
                        'is_final': result.is_final,
                        'confidence': confidence
                    }, room=client_sid)

                    if result.is_final:
                        logger.info(f"[WebSocket STT] æœ€çµ‚èªè­˜: '{transcript}' (ä¿¡é ¼åº¦: {confidence:.2f})")
                    else:
                        logger.debug(f"[WebSocket STT] é€”ä¸­èªè­˜: '{transcript}'")

        except Exception as e:
            logger.error(f"[WebSocket STT] èªè­˜ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)
            socketio.emit('error', {'message': str(e)}, room=client_sid)

    thread = threading.Thread(target=recognition_thread, daemon=True)
    thread.start()

    emit('stream_started', {'status': 'streaming'})

@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    if request.sid not in active_streams:
        logger.warning(f"[WebSocket STT] æœªåˆæœŸåŒ–ã®ã‚¹ãƒˆãƒªãƒ¼ãƒ : {request.sid}")
        return

    try:
        chunk_base64 = data.get('chunk', '')
        if not chunk_base64:
            return

        # â˜…â˜…â˜… sample_rateã‚’å–å¾—(16kHzã§å—ä¿¡) â˜…â˜…â˜…
        sample_rate = data.get('sample_rate', 16000)
        
        # â˜…â˜…â˜… çµ±è¨ˆæƒ…å ±ã‚’å–å¾—ã—ã¦ãƒ­ã‚°å‡ºåŠ›(å¿…ãšå‡ºåŠ›) â˜…â˜…â˜…
        stats = data.get('stats')
        logger.info(f"[audio_chunkå—ä¿¡] sample_rate: {sample_rate}Hz, stats: {stats}")
        
        if stats:
            logger.info(f"[AudioWorkletçµ±è¨ˆ] ã‚µãƒ³ãƒ—ãƒ«ãƒ¬ãƒ¼ãƒˆ: {sample_rate}Hz, "
                       f"ã‚µãƒ³ãƒ—ãƒ«ç·æ•°: {stats.get('totalSamples')}, "
                       f"é€ä¿¡ãƒãƒ£ãƒ³ã‚¯æ•°: {stats.get('chunksSent')}, "
                       f"ç©ºå…¥åŠ›å›žæ•°: {stats.get('emptyInputCount')}, "
                       f"processå‘¼ã³å‡ºã—å›žæ•°: {stats.get('processCalls')}, "
                       f"ã‚ªãƒ¼ãƒãƒ¼ãƒ•ãƒ­ãƒ¼å›žæ•°: {stats.get('overflowCount', 0)}")  # â˜… ã‚ªãƒ¼ãƒãƒ¼ãƒ•ãƒ­ãƒ¼è¿½åŠ 

        audio_chunk = base64.b64decode(chunk_base64)
        
        # â˜…â˜…â˜… 16kHzãã®ã¾ã¾Google STTã«é€ã‚‹ â˜…â˜…â˜…
        stream_data = active_streams[request.sid]
        stream_data['audio_queue'].put(audio_chunk)

    except Exception as e:
        logger.error(f"[WebSocket STT] ãƒãƒ£ãƒ³ã‚¯å‡¦ç†ã‚¨ãƒ©ãƒ¼: {e}", exc_info=True)

@socketio.on('stop_stream')
def handle_stop_stream():
    logger.info(f"[WebSocket STT] ã‚¹ãƒˆãƒªãƒ¼ãƒ åœæ­¢: {request.sid}")

    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        stream_data['audio_queue'].put(None)
        stream_data['stop_event'].set()
        del active_streams[request.sid]

    emit('stream_stopped', {'status': 'stopped'})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
