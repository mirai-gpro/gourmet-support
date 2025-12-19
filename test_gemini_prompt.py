#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Geminiプロンプトテスト - 締め文が出力されるか確認
"""
import os
import json
import google.generativeai as genai
from prompt_manager import PromptManager

# Gemini API初期化
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.0-flash-exp')

# PromptManager初期化
prompt_manager = PromptManager()

def test_new_search():
    """新規検索のテスト"""
    print("=" * 80)
    print("テスト1: 新規検索（日時情報なし）")
    print("=" * 80)

    system_prompt = prompt_manager.get('support_system', language='ja')
    user_message = "新宿でイタリアンを探しています"

    # プロンプト構築
    prompt = f"""システム指示:
{system_prompt}

【ユーザーの発言】
{user_message}
"""

    print("\n[送信するプロンプト]")
    print(prompt[:1000] + "...\n" if len(prompt) > 1000 else prompt + "\n")

    try:
        response = model.generate_content(prompt)
        assistant_text = response.text

        print("[Geminiのレスポンス]")
        print(assistant_text)
        print("\n")

        # JSONパース
        cleaned_text = assistant_text.strip()
        if cleaned_text.startswith('```'):
            lines = cleaned_text.split('\n')
            cleaned_text = '\n'.join(lines[1:])
        if cleaned_text.endswith('```'):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()

        data = json.loads(cleaned_text)
        message = data.get('message', '')
        shops = data.get('shops', [])

        print(f"[パース結果]")
        print(f"shops数: {len(shops)}件")
        print(f"message長: {len(message)}文字")
        print(f"\nmessage内容:\n{message}\n")

        # 締め文チェック
        closing_statement = "ご案内したお店についてのご質問はお気軽にどうぞ。別の条件でお探しの場合は「他で○○」のようにお伝えください。"

        if closing_statement in message:
            print("✅ 締め文が含まれています")
        else:
            print("❌ 締め文が含まれていません")
            print(f"\n期待される締め文:\n{closing_statement}")

    except Exception as e:
        print(f"エラー: {e}")
        import traceback
        traceback.print_exc()


def test_with_datetime():
    """日時情報ありのテスト"""
    print("\n" + "=" * 80)
    print("テスト2: 新規検索（日時情報あり）")
    print("=" * 80)

    system_prompt = prompt_manager.get('support_system', language='ja')
    user_message = "明日19時に新宿でイタリアンを探しています"

    # プロンプト構築（日時情報フラグ付き）
    datetime_notice = "\n\n【重要】セッションに日時情報が保存されています。必ず予約案内（パートC）を締め文の後に追加してください。"

    prompt = f"""システム指示:
{system_prompt}

{datetime_notice}

【ユーザーの発言】
{user_message}
"""

    print("\n[送信するプロンプト（抜粋）]")
    print("...締め文の指示部分あり...")
    print(f"...日時情報通知: {datetime_notice}...\n")

    try:
        response = model.generate_content(prompt)
        assistant_text = response.text

        print("[Geminiのレスポンス]")
        print(assistant_text)
        print("\n")

        # JSONパース
        cleaned_text = assistant_text.strip()
        if cleaned_text.startswith('```'):
            lines = cleaned_text.split('\n')
            cleaned_text = '\n'.join(lines[1:])
        if cleaned_text.endswith('```'):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()

        data = json.loads(cleaned_text)
        message = data.get('message', '')

        print(f"\nmessage内容:\n{message}\n")

        # 締め文チェック
        closing_statement = "ご案内したお店についてのご質問はお気軽にどうぞ。別の条件でお探しの場合は「他で○○」のようにお伝えください。"
        reservation_notice = "なお、ご希望の日時での予約状況については、私が直接お店に電話で確認することもできます。ご希望でしたらお申し付けください。"

        has_closing = closing_statement in message
        has_reservation = reservation_notice in message

        print(f"✅ 締め文: {'含まれています' if has_closing else '❌ 含まれていません'}")
        print(f"✅ 予約案内: {'含まれています' if has_reservation else '❌ 含まれていません'}")

        if not has_closing:
            print(f"\n期待される締め文:\n{closing_statement}")
        if not has_reservation:
            print(f"\n期待される予約案内:\n{reservation_notice}")

    except Exception as e:
        print(f"エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    test_new_search()
    test_with_datetime()
