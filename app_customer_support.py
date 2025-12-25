# -*- coding: utf-8 -*-
"""
汎用カスタマーサポートシステム (Gemini API版) - リファクタリング版
モジュール分割により保守性を向上
"""
import os
import base64
import logging
import threading
import queue
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from google.cloud import texttospeech, speech

# 新しいモジュールからインポート
from config import (
    ALLOWED_ORIGINS,
    tts_client,
    stt_client,
    logger
)
from prompts import load_system_prompts
from session import SupportSession
from assistant import SupportAssistant
from utils import extract_area_from_text
from integrations.enrichment import enrich_shops_with_photos

# ========================================
# Flask & SocketIO 初期化
# ========================================

app = Flask(__name__)

# SocketIO初期化 (cors_allowed_originsを明示的に指定)
socketio = SocketIO(
    app,
    cors_allowed_origins=ALLOWED_ORIGINS,
    async_mode='threading',
    logger=False,
    engineio_logger=False
)

# Flask-CORS初期化 (supports_credentials=True)
CORS(app, resources={
    r"/*": {
        "origins": ALLOWED_ORIGINS,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# 【重要】全レスポンスに強制的にCORSヘッダーを注入するフック
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# ========================================
# プロンプト読み込み
# ========================================

SYSTEM_PROMPTS = load_system_prompts()

# ========================================
# API Endpoints
# ========================================

@app.route('/api/session/start', methods=['POST', 'OPTIONS'])
def start_session():
    """セッション開始 - モード対応版"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        language = data.get('language', 'ja')
        mode = data.get('mode', 'chat')  # ★ モードを取得 (デフォルト: chat)
        user_info = data.get('user_info', {})

        session = SupportSession()
        session.initialize(
            user_info=user_info,
            language=language,
            mode=mode  # ★ モードを渡す
        )

        # ★★★ モード対応のアシスタントを作成 ★★★
        assistant = SupportAssistant(session, SYSTEM_PROMPTS)
        initial_message = assistant.get_initial_message()

        logger.info(f"[Session Start] ID: {session.session_id}, Mode: {mode}, Language: {language}")

        return jsonify({
            'session_id': session.session_id,
            'initial_message': initial_message,
            'mode': mode,
            'language': language
        })

    except Exception as e:
        logger.error(f"[API] Session start error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    """
    チャット処理 - 改善版
    
    【重要】改善されたフロー(順序を厳守):
    1. 状態確定 (State First): モード・言語を更新
    2. ユーザー入力を記録: メッセージを履歴に追加
    3. 知能生成 (Assistant作成): 最新の状態でアシスタントを作成
    4. 推論開始: Gemini APIを呼び出し
    5. アシスタント応答を記録: 履歴に追加
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
            return jsonify({'error': 'session_idとmessageが必要です'}), 400

        session = SupportSession(session_id)
        session_data = session.get_data()

        if not session_data:
            return jsonify({'error': 'セッションが見つかりません'}), 404

        logger.info(f"[Chat] セッション: {session_id}, モード: {mode}, 言語: {language}")

        # 1. 状態確定 (State First)
        session.update_language(language)
        session.update_mode(mode)

        # 2. ユーザー入力を記録
        session.add_message('user', user_message, 'chat')

        # 3. 知能生成 (Assistant作成)
        assistant = SupportAssistant(session, SYSTEM_PROMPTS)
        
        # 4. 推論開始
        result = assistant.process_user_message(user_message, stage)
        
        # 5. アシスタント応答を記録
        session.add_message('model', result['response'], 'chat')

        if result['summary']:
            session.add_message('model', result['summary'], 'summary')

        # ショップデータ処理
        shops = result.get('shops', [])
        response_text = result['response']
        is_followup = result.get('is_followup', False)

        # 多言語メッセージ辞書
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
            logger.info(f"[Chat] 抽出エリア: '{area}' from '{user_message}'")

            # Places APIで写真を取得
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
                logger.info(f"[Chat] {len(shops)}件のショップデータを返却(元: {original_count}件, 言語: {language})")
            else:
                response_text = current_messages['not_found']
                logger.warning(f"[Chat] 全店舗が除外されました(元: {original_count}件)")

        elif is_followup:
            logger.info(f"[Chat] 深掘り質問への回答: {response_text[:100]}...")

        return jsonify({
            'response': response_text,
            'summary': result['summary'],
            'shops': shops,
            'should_confirm': result['should_confirm'],
            'is_followup': is_followup
        })

    except Exception as e:
        logger.error(f"[API] チャットエラー: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/finalize', methods=['POST', 'OPTIONS'])
def finalize_session():
    """セッション終了と最終要約生成"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'error': 'session_idが必要です'}), 400

        session = SupportSession(session_id)

        if not session.get_data():
            return jsonify({'error': 'セッションが見つかりません'}), 404

        assistant = SupportAssistant(session, SYSTEM_PROMPTS)
        summary = assistant.generate_final_summary()

        logger.info(f"[Finalize] Session: {session_id}")

        return jsonify({
            'summary': summary,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"[API] Finalize error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel', methods=['POST', 'OPTIONS'])
def cancel_session():
    """セッションキャンセル"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'error': 'session_idが必要です'}), 400

        session = SupportSession(session_id)
        if not session.get_data():
            return jsonify({'error': 'セッションが見つかりません'}), 404

        session.update_status('cancelled')

        logger.info(f"[Cancel] Session: {session_id}")

        return jsonify({
            'success': True,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"[API] Cancel error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/tts/synthesize', methods=['POST', 'OPTIONS'])
def synthesize_speech():
    """音声合成"""
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
            return jsonify({'success': False, 'error': 'テキストが必要です'}), 400

        MAX_CHARS = 1000
        if len(text) > MAX_CHARS:
            logger.warning(f"[TTS] テキストが長すぎるため切り詰めます: {len(text)} → {MAX_CHARS} 文字")
            text = text[:MAX_CHARS] + '...'

        logger.info(f"[TTS] 合成開始: {len(text)} 文字")

        synthesis_input = texttospeech.SynthesisInput(text=text)

        try:
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )
        except Exception as voice_error:
            logger.warning(f"[TTS] 指定音声が無効、デフォルトに変更: {voice_error}")
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

        logger.info(f"[TTS] 合成成功: {len(audio_base64)} bytes (base64)")

        return jsonify({
            'success': True,
            'audio': audio_base64
        })

    except Exception as e:
        logger.error(f"[TTS] エラー: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stt/transcribe', methods=['POST', 'OPTIONS'])
def transcribe_audio():
    """音声認識"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.json
        audio_base64 = data.get('audio', '')
        language_code = data.get('language_code', 'ja-JP')

        if not audio_base64:
            return jsonify({'success': False, 'error': '音声データが必要です'}), 400

        logger.info(f"[STT] 認識開始: {len(audio_base64)} bytes (base64)")

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
            logger.info(f"[STT] 認識成功: '{transcript}' (信頼度: {confidence:.2f})")
        else:
            logger.warning("[STT] 音声が認識されませんでした")

        return jsonify({
            'success': True,
            'transcript': transcript
        })

    except Exception as e:
        logger.error(f"[STT] エラー: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/session/<session_id>', methods=['GET'])
def get_session(session_id):
    """セッション情報取得"""
    try:
        session = SupportSession(session_id)
        data = session.get_data()

        if not data:
            return jsonify({'error': 'セッションが見つかりません'}), 404

        return jsonify(data)

    except Exception as e:
        logger.error(f"[API] Session get error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """ヘルスチェック"""
    return jsonify({
        'status': 'healthy',
        'service': 'gourmet-support',
        'timestamp': datetime.now().isoformat()
    })


# ========================================
# WebSocket Streaming (簡略版 - 必要に応じて拡張)
# ========================================

active_streams = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f"[WebSocket] Client connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"[WebSocket] Client disconnected: {request.sid}")
    if request.sid in active_streams:
        stream_data = active_streams[request.sid]
        stream_data['stop_event'].set()
        del active_streams[request.sid]


# ========================================
# Main Entry Point
# ========================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
