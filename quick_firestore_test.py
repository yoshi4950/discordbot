#!/usr/bin/env python3
"""
簡単なFirestore接続テスト
"""
import os
from dotenv import load_dotenv
load_dotenv()

print("環境変数確認...")
print(f"GOOGLE_CLOUD_PROJECT_ID: {os.getenv('GOOGLE_CLOUD_PROJECT_ID')}")
print(f"GOOGLE_APPLICATION_CREDENTIALS_JSON: {'設定済み' if os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON') else '未設定'}")

try:
    print("Firestore初期化テスト...")
    from firestore_db import firestore_db
    print("✅ Firestore初期化成功")
    
    print("✅ すべてのテスト完了")
    
except Exception as e:
    print(f"❌ エラー: {e}")
    import traceback
    traceback.print_exc()