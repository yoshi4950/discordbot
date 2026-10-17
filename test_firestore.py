#!/usr/bin/env python3
"""
Firestore接続テスト
"""

import asyncio
from firestore_db import firestore_db
from datetime import datetime

async def test_firestore():
    try:
        print("🔧 Firestore接続テスト開始...")
        
        # テストメッセージの保存
        test_message_id = 999999999
        await firestore_db.add_message(
            channel_id=123456789,
            user_id=987654321,
            username="TestUser",
            content="テストメッセージ",
            message_id=test_message_id
        )
        print("✅ メッセージ保存テスト成功")
        
        # メッセージ取得テスト
        history = await firestore_db.get_conversation_history(123456789, 10)
        print(f"✅ 履歴取得テスト成功: {len(history)}件のメッセージ")
        
        # 設定保存テスト
        await firestore_db.set_setting("test", "test_key", "test_value", channel_id=123456789)
        print("✅ 設定保存テスト成功")
        
        # 設定取得テスト
        value = await firestore_db.get_setting("test", "test_key", channel_id=123456789)
        print(f"✅ 設定取得テスト成功: {value}")
        
        print("🎉 すべてのテストが成功しました！")
        
    except Exception as e:
        print(f"❌ テスト失敗: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_firestore())