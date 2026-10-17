#!/usr/bin/env python3
"""
Firestore統合テストスクリプト
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from firestore_db import firestore_db

async def test_firestore_integration():
    """Firestore統合機能のテスト"""
    print("🔥 Firestore統合機能テスト開始...")
    
    test_user_id = 123456789
    test_channel_id = 987654321
    
    try:
        # 1. AI会話保存テスト
        print("1. AI会話保存テスト...")
        conversation_id = await firestore_db.save_ai_conversation(
            channel_id=test_channel_id,
            user_message="テストメッセージです",
            ai_response="これはテスト応答です",
            user_id=test_user_id
        )
        print(f"   ✅ AI会話保存成功: {conversation_id}")
        
        # 2. AI会話履歴取得テスト
        print("2. AI会話履歴取得テスト...")
        conversations = await firestore_db.get_ai_conversation_history(test_channel_id, 10)
        print(f"   ✅ AI会話履歴取得成功: {len(conversations)}件")
        
        # 3. ユーザーファイル保存テスト
        print("3. ユーザーファイル保存テスト...")
        file_id = await firestore_db.save_user_file(
            user_id=test_user_id,
            filename="test_file.txt",
            content="これはテストファイルの内容です。",
            channel_id=test_channel_id
        )
        print(f"   ✅ ユーザーファイル保存成功: {file_id}")
        
        # 4. ユーザーファイル一覧取得テスト
        print("4. ユーザーファイル一覧取得テスト...")
        files = await firestore_db.get_user_files(user_id=test_user_id)
        print(f"   ✅ ユーザーファイル一覧取得成功: {len(files)}件")
        
        # 5. ファイル内容取得テスト
        if file_id and files:
            print("5. ファイル内容取得テスト...")
            content = await firestore_db.get_file_content(file_id)
            print(f"   ✅ ファイル内容取得成功: {len(content) if content else 0}文字")
        
        # 6. チャンネル設定保存テスト
        print("6. チャンネル設定保存テスト...")
        config_saved = await firestore_db.save_channel_config(
            channel_id=test_channel_id,
            config_type="reference_mode",
            config_data={"enabled": True, "file_id": file_id}
        )
        print(f"   ✅ チャンネル設定保存: {config_saved}")
        
        # 7. チャンネル設定取得テスト
        print("7. チャンネル設定取得テスト...")
        config = await firestore_db.get_channel_config(test_channel_id, "reference_mode")
        print(f"   ✅ チャンネル設定取得成功: {config}")
        
        # 8. 統合検索テスト
        print("8. 統合検索テスト...")
        search_results = await firestore_db.search_user_data(test_user_id, "テスト", 10)
        print(f"   ✅ 統合検索成功: ファイル{len(search_results['files'])}件, 会話{len(search_results['conversations'])}件")
        
        # 9. 設定保存・取得テスト
        print("9. 設定保存・取得テスト...")
        await firestore_db.set_setting('test_settings', 'test_key', {'value': 'test'}, user_id=test_user_id)
        setting = await firestore_db.get_setting('test_settings', 'test_key', user_id=test_user_id)
        print(f"   ✅ 設定保存・取得成功: {setting}")
        
        print("\n🎉 すべてのFirestore統合テストが成功しました！")
        
        # クリーンアップ
        print("\n🧹 テストデータクリーンアップ...")
        if file_id:
            await firestore_db.delete_user_file(file_id)
            print("   ✅ テストファイル削除完了")
        
        print("✅ Firestore統合テスト完了")
        
    except Exception as e:
        print(f"❌ Firestore統合テストエラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_firestore_integration())