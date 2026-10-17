import os
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
import asyncio
from functools import wraps


def _parse_service_account_json(value: str) -> Dict[str, Any]:
    """環境変数からサービスアカウントJSONを読み込む。秘密値は例外に含めない。"""
    raw = value.strip()

    # .envの1行をそのままKoyebの値に貼り付けたケースも受け付ける。
    raw = re.sub(
        r"^(?:export\s+)?GOOGLE_APPLICATION_CREDENTIALS_JSON\s*=\s*",
        "",
        raw,
        count=1,
    ).strip()
    if raw[:5].casefold() == "json:":
        raw = raw[5:].strip()
    fence_match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    if fence_match:
        raw = fence_match.group(1).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        raw = raw[1:-1].strip()
    # JSONの外側の開始波括弧だけを付け忘れた場合を補正する。
    # 添付ログの「Extra data at column 7」は `"type":` から始まる値で発生する。
    if raw.startswith('"type"') and raw.rstrip().endswith("}"):
        raw = "{" + raw

    try:
        parsed = json.loads(raw)
        # JSON文字列として二重にエンコードされている場合を許容する。
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSONの値が有効なJSONではありません "
            f"（{exc.lineno}行目・{exc.colno}列目）。Koyebの値にはサービスアカウントJSON本体だけを設定してください。"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSONはJSONオブジェクトである必要があります。")
    required_fields = ("type", "project_id", "private_key", "client_email", "token_uri")
    missing_fields = [field for field in required_fields if not parsed.get(field)]
    if missing_fields:
        raise ValueError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSONにサービスアカウントJSONの必須項目がありません: "
            + ", ".join(missing_fields)
        )
    if parsed.get("type") != "service_account":
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSONのtypeはservice_accountである必要があります。")
    return parsed

class FirestoreDatabase:
    def __init__(self):
        """Firestore データベースクライアントを初期化"""
        # 環境変数からプロジェクトIDとサービスアカウントキーを取得
        self.project_id = os.getenv('GOOGLE_CLOUD_PROJECT_ID')
        self.credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        self.credentials_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
        
        if not self.project_id:
            raise ValueError("環境変数 GOOGLE_CLOUD_PROJECT_ID が設定されていません")
        
        print(f"[Firestore] 使用プロジェクトID: {self.project_id}")
        
        # KoyebなどではサービスアカウントJSONを環境変数から直接読み込む。
        # ローカルではJSON環境変数、認証ファイル、ADCの順で既存設定を利用する。
        auth_email = None
        credential_project_id = None
        try:
            if self.credentials_json:
                credentials_info = _parse_service_account_json(self.credentials_json)
                auth_email = credentials_info.get('client_email')
                credential_project_id = credentials_info.get('project_id')
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info
                )
                self.db = firestore.Client(
                    project=self.project_id,
                    credentials=credentials,
                )
            elif self.credentials_path:
                credentials = service_account.Credentials.from_service_account_file(
                    self.credentials_path
                )
                auth_email = credentials.service_account_email
                self.db = firestore.Client(
                    project=self.project_id,
                    credentials=credentials,
                )
            else:
                # Cloud Run等の実行環境やgcloud ADCを利用する場合
                self.db = firestore.Client(project=self.project_id)
            print(f"[Firestore] クライアント初期化成功 (データベース: default)")
            if auth_email:
                print(f"[Firestore] 認証サービスアカウント: {auth_email}")
            if credential_project_id and credential_project_id != self.project_id:
                print(
                    f"[Firestore] 注意: 認証JSONのproject_id ({credential_project_id}) と "
                    f"接続先プロジェクト ({self.project_id}) が異なります。"
                )
        except Exception as e:
            # 認証JSONや秘密鍵そのものをログへ出さない。
            print(f"[Firestore] クライアント初期化失敗: {type(e).__name__}: {e}")
            raise RuntimeError(
                "Firestoreの認証に失敗しました。GOOGLE_CLOUD_PROJECT_IDと"
                "GOOGLE_APPLICATION_CREDENTIALS_JSON（または認証ファイル設定）を確認してください。"
            ) from e
        
        # コレクション名
        self.CONVERSATIONS = 'conversations'
        self.SETTINGS = 'bot_settings'
        self.REFERENCE_FILES = 'reference_files'
        self.USER_FILES = 'user_files'
        self.CHAT_HISTORY = 'chat_history'
        self.AI_CONVERSATIONS = 'ai_conversations'  # 廃止予定
        self.CHANNEL_CONFIGS = 'channel_configs'
        self.AI_CONVERSATION_ARCHIVES = 'ai_conversation_archives'  # 廃止予定
        self.CONVERSATION_ARCHIVES = 'conversation_archives'

    def diagnose_collection_read(self, collection_name: str) -> Dict[str, Any]:
        """gRPCの403時にRESTで読み取り権限を確認し、Google側の詳細理由を得る。"""
        url = (
            f"https://firestore.googleapis.com/v1/projects/{self.project_id}"
            f"/databases/(default)/documents/{collection_name}"
        )
        session = AuthorizedSession(self.db._credentials)
        try:
            response = session.get(url, params={"pageSize": 1}, timeout=15)
            if response.ok:
                # ドキュメント本文やFirestoreデータは返さず、成功状態だけ返す。
                return {"http_status": response.status_code, "ok": True}
            try:
                error = response.json().get("error", {})
            except (ValueError, AttributeError):
                error = {}
            return {
                "http_status": response.status_code,
                "ok": False,
                "status": error.get("status"),
                "message": error.get("message", "詳細メッセージなし"),
            }
        finally:
            session.close()
    
    def async_to_sync(func):
        """非同期関数を同期的に実行するデコレータ"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(func(*args, **kwargs))
        return wrapper
    
    async def add_message(self, channel_id: int, user_id: int, username: str, content: str, message_id: int):
        """メッセージを会話履歴に追加（旧形式・互換性用）"""
        try:
            message_data = {
                'channel_id': channel_id,
                'user_id': user_id,
                'username': username,
                'content': content,
                'message_id': message_id,
                'timestamp': datetime.now(timezone.utc)
            }
            
            # ドキュメントIDを固定形式で設定
            doc_id = f"msg_{message_id}"
            doc_ref = self.db.collection(self.CONVERSATIONS).document(doc_id)
            doc_ref.set(message_data)
            
        except Exception as e:
            print(f"[Firestore] メッセージ保存エラー: {e}")
    
    async def add_conversation_message(self, user_id: int, username: str, content: str, message_type: str, conversation_id: str = None):
        """新形式：会話メッセージを追加（チャンネル情報なし）"""
        try:
            if not conversation_id:
                conversation_id = f"conv_{user_id}_{int(datetime.now(timezone.utc).timestamp())}"
            
            message_data = {
                'user_id': user_id,
                'username': username,
                'content': content,
                'message_type': message_type,  # 'user' or 'ai'
                'conversation_id': conversation_id,
                'timestamp': datetime.now(timezone.utc)
            }
            
            doc_ref = self.db.collection(self.CONVERSATIONS).document()
            doc_ref.set(message_data)
            
            print(f"[Firestore] 会話メッセージ保存: {message_type} - {username}")
            return conversation_id
            
        except Exception as e:
            print(f"[Firestore] 会話メッセージ保存エラー: {e}")
            return None
    
    async def get_conversation_history(self, channel_id: int, limit: int = 100) -> List[Dict]:
        """チャンネルの会話履歴を取得"""
        try:
            query = (self.db.collection(self.CONVERSATIONS)
                    .where(filter=FieldFilter('channel_id', '==', channel_id))
                    .order_by('timestamp', direction=firestore.Query.DESCENDING)
                    .limit(limit))
            
            docs = query.stream()
            messages = []
            
            for doc in docs:
                data = doc.to_dict()
                messages.append({
                    'user_id': data.get('user_id'),
                    'username': data.get('username'),
                    'content': data.get('content'),
                    'timestamp': data.get('timestamp'),
                    'message_id': data.get('message_id')
                })
            
            # 古い順に並び替え
            return list(reversed(messages))
            
        except Exception as e:
            print(f"[Firestore] 会話履歴取得エラー: {e}")
            return []
    
    async def get_user_conversation_history(self, user_id: int, limit: int = 100) -> List[Dict]:
        """ユーザーの会話履歴を取得（新形式・チャンネル問わず）"""
        try:
            # インデックスエラーを回避するため、シンプルなクエリに変更
            query = (self.db.collection(self.CONVERSATIONS)
                    .where(filter=FieldFilter('user_id', '==', user_id))
                    .limit(limit * 2))  # フィルタ後に制限を適用するため多めに取得
            
            docs = query.stream()
            messages = []
            
            for doc in docs:
                data = doc.to_dict()
                # クライアント側でmessage_typeをフィルタ
                if data.get('message_type') in ['user', 'ai']:
                    messages.append({
                        'user_id': data.get('user_id'),
                        'username': data.get('username'),
                        'content': data.get('content'),
                        'message_type': data.get('message_type'),
                        'conversation_id': data.get('conversation_id'),
                        'timestamp': data.get('timestamp')
                    })
            
            # タイムスタンプでソート（新しい順）
            messages.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
            
            # 制限を適用して古い順に並び替え
            messages = messages[:limit]
            messages.reverse()
            
            print(f"[Firestore] ユーザー会話履歴取得: {len(messages)}件")
            return messages
            
        except Exception as e:
            print(f"[Firestore] ユーザー会話履歴取得エラー: {e}")
            return []
    
    async def get_user_message_count_new(self, user_id: int) -> int:
        """ユーザーの会話件数を取得（新形式）"""
        try:
            # message_typeとの複合インデックスを要求しないよう、ユーザーで絞ってからPython側で数える。
            query = self.db.collection(self.CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            )

            return sum(
                1 for doc in query.stream()
                if (doc.to_dict() or {}).get('message_type') in ['user', 'ai']
            )
            
        except Exception as e:
            print(f"[Firestore] ユーザー会話件数取得エラー: {e}")
            return 0
    
    async def set_setting(self, setting_type: str, setting_key: str, setting_value: Any, 
                         channel_id: Optional[int] = None, user_id: Optional[int] = None):
        """設定を保存"""
        try:
            # ドキュメントIDを生成 (設定の一意性を保証)
            doc_id = f"{setting_type}_{setting_key}"
            if channel_id:
                doc_id += f"_ch{channel_id}"
            if user_id:
                doc_id += f"_usr{user_id}"
            
            setting_data = {
                'setting_type': setting_type,
                'setting_key': setting_key,
                'setting_value': setting_value,
                'channel_id': channel_id,
                'user_id': user_id,
                'updated_at': datetime.now(timezone.utc)
            }
            
            doc_ref = self.db.collection(self.SETTINGS).document(doc_id)
            doc_ref.set(setting_data)
            
        except Exception as e:
            print(f"[Firestore] 設定保存エラー: {e}")
    
    async def get_setting(self, setting_type: str, setting_key: str, 
                         channel_id: Optional[int] = None, user_id: Optional[int] = None) -> Optional[Any]:
        """設定を取得"""
        try:
            # ドキュメントIDを生成
            doc_id = f"{setting_type}_{setting_key}"
            if channel_id:
                doc_id += f"_ch{channel_id}"
            if user_id:
                doc_id += f"_usr{user_id}"
            
            doc_ref = self.db.collection(self.SETTINGS).document(doc_id)
            doc = doc_ref.get()
            
            if doc.exists:
                return doc.to_dict().get('setting_value')
            return None
            
        except Exception as e:
            print(f"[Firestore] 設定取得エラー: {e}")
            return None
    
    async def get_all_settings(self, setting_type: str) -> Dict[str, Any]:
        """指定タイプの全設定を取得"""
        try:
            query = self.db.collection(self.SETTINGS).where(filter=FieldFilter('setting_type', '==', setting_type))
            docs = query.stream()
            
            settings = {}
            for doc in docs:
                data = doc.to_dict()
                key = data.get('setting_key')
                
                # チャンネルまたはユーザー固有の設定の場合、キーを調整
                if data.get('channel_id'):
                    key = f"{data.get('channel_id')}_{key}"
                elif data.get('user_id'):
                    key = f"user_{data.get('user_id')}_{key}"
                
                settings[key] = data.get('setting_value')
            
            return settings
            
        except Exception as e:
            print(f"[Firestore] 全設定取得エラー: {e}")
            return {}

    async def delete_old_messages(self, days: int = 30):
        """古いメッセージを削除"""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            query = self.db.collection(self.CONVERSATIONS).where(filter=FieldFilter('timestamp', '<', cutoff_date))
            docs = query.stream()
            
            batch = self.db.batch()
            count = 0
            
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
                
                # バッチサイズ制限（500件）に対応
                if count >= 500:
                    batch.commit()
                    batch = self.db.batch()
                    count = 0
            
            if count > 0:
                batch.commit()
            
            print(f"[Firestore] {count}件の古いメッセージを削除しました")
            
        except Exception as e:
            print(f"[Firestore] 古いメッセージ削除エラー: {e}")
    
    async def migrate_json_settings_to_firestore(self):
        """既存のJSONファイルからFirestoreへの移行"""
        print("[Firestore] 移行処理開始")
        try:
            # always_reply_channels.json の移行
            always_reply_file = 'always_reply_channels.json'
            print(f"[Firestore] {always_reply_file} 移行処理開始")
            if os.path.exists(always_reply_file):
                with open(always_reply_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # データが配列の場合とオブジェクトの場合を両方対応
                    if isinstance(data, list):
                        channels = data
                    else:
                        channels = data.get('channels', [])
                    
                    for channel_id in channels:
                        await self.set_setting('always_reply', 'enabled', True, channel_id=int(channel_id))
                print(f"[Firestore] {always_reply_file} の移行完了")
            
            # reference_mode_config.json の移行
            reference_mode_file = 'reference_mode_config.json'
            if os.path.exists(reference_mode_file):
                with open(reference_mode_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for channel_id, config in data.items():
                        await self.set_setting('reference_mode', 'config', config, channel_id=int(channel_id))
                print(f"[Firestore] {reference_mode_file} の移行完了")
            
            # english_mode_config.json の移行
            english_mode_file = 'english_mode_config.json'
            if os.path.exists(english_mode_file):
                with open(english_mode_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for channel_id, config in data.items():
                        await self.set_setting('english_mode', 'config', config, channel_id=int(channel_id))
                print(f"[Firestore] {english_mode_file} の移行完了")

            # voice_memo_config.json の移行
            voice_memo_file = 'voice_memo_config.json'
            if os.path.exists(voice_memo_file):
                with open(voice_memo_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for channel_id, config in data.items():
                        await self.set_setting('voice_memo', 'config', config, channel_id=int(channel_id))
                print(f"[Firestore] {voice_memo_file} の移行完了")
                        
        except Exception as e:
            print(f"[Firestore] 設定ファイル移行エラー: {e}")
    
    # === ファイル管理システム ===
    async def save_user_file(self, user_id: int, filename: str, content: str, channel_id: int = None):
        """ユーザーがアップロードしたファイルをFirestoreに保存"""
        try:
            file_data = {
                'user_id': user_id,
                'filename': filename,
                'content': content,
                'channel_id': channel_id,
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc)
            }
            
            doc_ref = self.db.collection(self.USER_FILES).document()
            doc_ref.set(file_data)
            
            print(f"[Firestore] ユーザーファイル保存: {filename} (ユーザー: {user_id})")
            return doc_ref.id
            
        except Exception as e:
            print(f"[Firestore] ユーザーファイル保存エラー: {e}")
            return None
    
    async def get_user_files(self, user_id: int = None, channel_id: int = None):
        """ユーザーのファイル一覧を取得"""
        try:
            query = self.db.collection(self.USER_FILES)
            
            if user_id:
                query = query.where(filter=FieldFilter('user_id', '==', user_id))
            if channel_id:
                query = query.where(filter=FieldFilter('channel_id', '==', channel_id))
            
            query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
            docs = query.stream()
            
            files = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                files.append(data)
            
            print(f"[Firestore] ユーザーファイル取得: {len(files)}件")
            return files
            
        except Exception as e:
            print(f"[Firestore] ユーザーファイル取得エラー: {e}")
            return []
    
    async def get_file_content(self, file_id: str):
        """ファイルIDから内容を取得"""
        try:
            doc_ref = self.db.collection(self.USER_FILES).document(file_id)
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                return data.get('content', '')
            return None
            
        except Exception as e:
            print(f"[Firestore] ファイル内容取得エラー: {e}")
            return None
    
    async def delete_user_file(self, file_id: str):
        """ユーザーファイルを削除"""
        try:
            doc_ref = self.db.collection(self.USER_FILES).document(file_id)
            doc_ref.delete()
            print(f"[Firestore] ユーザーファイル削除: {file_id}")
            return True
            
        except Exception as e:
            print(f"[Firestore] ユーザーファイル削除エラー: {e}")
            return False
    
    # === AI会話履歴管理 ===
    async def save_ai_conversation(self, channel_id: int, user_message: str, ai_response: str, user_id: int):
        """AI会話を保存"""
        try:
            conversation_data = {
                'channel_id': channel_id,
                'user_id': user_id,
                'user_message': user_message,
                'ai_response': ai_response,
                'timestamp': datetime.now(timezone.utc)
            }
            
            doc_ref = self.db.collection(self.AI_CONVERSATIONS).document()
            doc_ref.set(conversation_data)
            
            print(f"[Firestore] AI会話保存: チャンネル{channel_id}")
            return doc_ref.id
            
        except Exception as e:
            print(f"[Firestore] AI会話保存エラー: {e}")
            return None
    
    async def get_ai_conversation_history(self, channel_id: int, limit: int = 50):
        """AI会話履歴を取得"""
        try:
            query = self.db.collection(self.AI_CONVERSATIONS).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            ).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
            
            docs = query.stream()
            conversations = []
            
            for doc in docs:
                data = doc.to_dict()
                conversations.append(data)
            
            # 時系列順にソート（古い順）
            conversations.reverse()
            
            print(f"[Firestore] AI会話履歴取得: {len(conversations)}件")
            return conversations
            
        except Exception as e:
            print(f"[Firestore] AI会話履歴取得エラー: {e}")
            return []
    
    # === チャンネル設定管理 ===
    async def save_channel_config(self, channel_id: int, config_type: str, config_data: dict):
        """チャンネル固有の設定を保存"""
        try:
            doc_id = f"{channel_id}_{config_type}"
            config_doc = {
                'channel_id': channel_id,
                'config_type': config_type,
                'config_data': config_data,
                'updated_at': datetime.now(timezone.utc)
            }
            
            doc_ref = self.db.collection(self.CHANNEL_CONFIGS).document(doc_id)
            doc_ref.set(config_doc)
            
            print(f"[Firestore] チャンネル設定保存: {config_type} (チャンネル: {channel_id})")
            return True
            
        except Exception as e:
            print(f"[Firestore] チャンネル設定保存エラー: {e}")
            return False
    
    async def get_channel_config(self, channel_id: int, config_type: str):
        """チャンネル設定を取得"""
        try:
            doc_id = f"{channel_id}_{config_type}"
            doc_ref = self.db.collection(self.CHANNEL_CONFIGS).document(doc_id)
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                return data.get('config_data', {})
            return {}
            
        except Exception as e:
            print(f"[Firestore] チャンネル設定取得エラー: {e}")
            return {}

    # === AI会話アーカイブ機能 ===
    async def archive_ai_conversations(self, user_id: int, channel_id: int):
        """ユーザーのAI会話をアーカイブに移動"""
        try:
            # 対象ユーザーの会話を取得
            query = self.db.collection(self.AI_CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            ).order_by('timestamp', direction=firestore.Query.ASCENDING)
            
            docs = query.stream()
            conversations = []
            doc_refs = []
            
            for doc in docs:
                conversations.append(doc.to_dict())
                doc_refs.append(doc.reference)
            
            if len(conversations) == 0:
                print(f"[Firestore] アーカイブ対象の会話なし: ユーザー{user_id}")
                return False
                
            # アーカイブデータを作成
            archive_data = {
                'user_id': user_id,
                'channel_id': channel_id,
                'conversation_count': len(conversations),
                'conversations': conversations,
                'created_at': datetime.now(timezone.utc),
                'archived_at': datetime.now(timezone.utc)
            }
            
            # アーカイブコレクションに保存
            archive_ref = self.db.collection(self.AI_CONVERSATION_ARCHIVES).document()
            archive_ref.set(archive_data)
            
            # 元の会話を削除
            batch = self.db.batch()
            for doc_ref in doc_refs:
                batch.delete(doc_ref)
            batch.commit()
            
            print(f"[Firestore] AI会話アーカイブ完了: ユーザー{user_id}, {len(conversations)}件")
            return True
            
        except Exception as e:
            print(f"[Firestore] AI会話アーカイブエラー: {e}")
            return False
    
    async def get_user_conversation_count(self, user_id: int, channel_id: int) -> int:
        """ユーザーの現在の会話件数を取得"""
        try:
            query = self.db.collection(self.AI_CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            )
            
            docs = list(query.stream())
            return len(docs)
            
        except Exception as e:
            print(f"[Firestore] 会話件数取得エラー: {e}")
            return 0
    
    async def get_user_last_conversation_date(self, user_id: int, channel_id: int) -> Optional[datetime]:
        """ユーザーの最後の会話日時を取得"""
        try:
            query = self.db.collection(self.AI_CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            ).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(1)
            
            docs = list(query.stream())
            if docs:
                return docs[0].to_dict().get('timestamp')
            return None
            
        except Exception as e:
            print(f"[Firestore] 最終会話日時取得エラー: {e}")
            return None
    
    async def get_archived_conversations(self, user_id: int, channel_id: int, limit: int = 3) -> List[Dict]:
        """ユーザーのアーカイブ会話を取得"""
        try:
            query = self.db.collection(self.AI_CONVERSATION_ARCHIVES).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            ).order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit)
            
            docs = query.stream()
            archives = []
            
            for doc in docs:
                archives.append(doc.to_dict())
            
            print(f"[Firestore] アーカイブ会話取得: ユーザー{user_id}, {len(archives)}件")
            return archives
            
        except Exception as e:
            print(f"[Firestore] アーカイブ会話取得エラー: {e}")
            return []
    
    async def check_archive_needed(self, user_id: int, channel_id: int) -> bool:
        """アーカイブが必要かチェック"""
        try:
            # 会話件数をチェック
            count = await self.get_user_conversation_count(user_id, channel_id)
            if count >= 100:
                return True
            
            # 最終会話日時をチェック
            last_date = await self.get_user_last_conversation_date(user_id, channel_id)
            if last_date:
                days_passed = (datetime.now(timezone.utc) - last_date).days
                if days_passed >= 7:
                    return True
            
            return False
            
        except Exception as e:
            print(f"[Firestore] アーカイブチェックエラー: {e}")
            return False
    
    async def delete_user_ai_conversations(self, user_id: int, channel_id: int) -> int:
        """ユーザーのAI会話履歴を全削除"""
        try:
            # 対象ユーザーの会話を取得
            query = self.db.collection(self.AI_CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            )
            
            docs = list(query.stream())
            deleted_count = len(docs)
            
            if deleted_count == 0:
                print(f"[Firestore] 削除対象の会話なし: ユーザー{user_id}")
                return 0
            
            # バッチで削除
            batch = self.db.batch()
            for doc in docs:
                batch.delete(doc.reference)
            batch.commit()
            
            print(f"[Firestore] AI会話削除完了: ユーザー{user_id}, {deleted_count}件")
            return deleted_count
            
        except Exception as e:
            print(f"[Firestore] AI会話削除エラー: {e}")
            return 0
    
    async def delete_user_ai_archives(self, user_id: int, channel_id: int) -> int:
        """ユーザーのAI会話アーカイブを全削除"""
        try:
            # 対象ユーザーのアーカイブを取得
            query = self.db.collection(self.AI_CONVERSATION_ARCHIVES).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            )
            
            docs = list(query.stream())
            deleted_count = len(docs)
            
            if deleted_count == 0:
                print(f"[Firestore] 削除対象のアーカイブなし: ユーザー{user_id}")
                return 0
            
            # バッチで削除
            batch = self.db.batch()
            for doc in docs:
                batch.delete(doc.reference)
            batch.commit()
            
            print(f"[Firestore] AIアーカイブ削除完了: ユーザー{user_id}, {deleted_count}件")
            return deleted_count
            
        except Exception as e:
            print(f"[Firestore] AIアーカイブ削除エラー: {e}")
            return 0

    async def delete_user_conversations(self, user_id: int, channel_id: int) -> int:
        """ユーザーの通常メッセージ履歴を削除"""
        try:
            # 対象ユーザーのメッセージを取得
            query = self.db.collection(self.CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            )
            
            docs = list(query.stream())
            deleted_count = len(docs)
            
            if deleted_count == 0:
                print(f"[Firestore] 削除対象のメッセージなし: ユーザー{user_id}")
                return 0
            
            # バッチで削除
            batch = self.db.batch()
            for doc in docs:
                batch.delete(doc.reference)
            batch.commit()
            
            print(f"[Firestore] メッセージ削除完了: ユーザー{user_id}, {deleted_count}件")
            return deleted_count
            
        except Exception as e:
            print(f"[Firestore] メッセージ削除エラー: {e}")
            return 0

    async def get_user_message_count(self, user_id: int, channel_id: int) -> int:
        """ユーザーの通常メッセージ件数を取得"""
        try:
            query = self.db.collection(self.CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).where(
                filter=FieldFilter('channel_id', '==', channel_id)
            )
            
            docs = list(query.stream())
            return len(docs)
            
        except Exception as e:
            print(f"[Firestore] メッセージ件数取得エラー: {e}")
            return 0
    
    # === 新アーカイブ機能（conversationsベース） ===
    async def archive_user_conversations(self, user_id: int):
        """ユーザーの会話をアーカイブに移動（チャンネル問わず）"""
        try:
            # 複合インデックスが未作成でも動くよう、ユーザー単独で検索してから絞り込む。
            docs = list(self.db.collection(self.CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).stream())
            target_docs = [
                doc for doc in docs
                if (doc.to_dict() or {}).get('message_type') in ['user', 'ai']
            ]
            target_docs.sort(key=lambda doc: (doc.to_dict() or {}).get('timestamp') or datetime.min.replace(tzinfo=timezone.utc))
            conversations = [doc.to_dict() for doc in target_docs]
            doc_refs = [doc.reference for doc in target_docs]
            
            if len(conversations) == 0:
                print(f"[Firestore] アーカイブ対象の会話なし: ユーザー{user_id}")
                return False
                
            # アーカイブデータを作成
            archive_data = {
                'user_id': user_id,
                'conversation_count': len(conversations),
                'conversations': conversations,
                'created_at': datetime.now(timezone.utc),
                'archived_at': datetime.now(timezone.utc)
            }
            
            # アーカイブコレクションに保存
            archive_ref = self.db.collection(self.CONVERSATION_ARCHIVES).document()
            archive_ref.set(archive_data)
            
            # 元の会話を削除
            for offset in range(0, len(doc_refs), 450):
                batch = self.db.batch()
                for doc_ref in doc_refs[offset:offset + 450]:
                    batch.delete(doc_ref)
                batch.commit()
            
            print(f"[Firestore] 会話アーカイブ完了: ユーザー{user_id}, {len(conversations)}件")
            return True
            
        except Exception as e:
            print(f"[Firestore] 会話アーカイブエラー: {e}")
            return False
    
    async def check_archive_needed_new(self, user_id: int) -> bool:
        """アーカイブが必要かチェック（新形式）"""
        try:
            # 会話件数をチェック
            count = await self.get_user_message_count_new(user_id)
            if count >= 100:
                return True
            
            # インデックスエラーを回避するため、シンプルなクエリで最初のメッセージ日時をチェック
            query = self.db.collection(self.CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            ).limit(10)  # 少数取得してクライアント側でソート
            
            docs = list(query.stream())
            if docs:
                # クライアント側でソートして最古のメッセージを見つける
                valid_docs = []
                for doc in docs:
                    data = doc.to_dict()
                    if data.get('message_type') in ['user', 'ai'] and data.get('timestamp'):
                        valid_docs.append(data)
                
                if valid_docs:
                    # 最古のメッセージを取得
                    valid_docs.sort(key=lambda x: x.get('timestamp'))
                    first_date = valid_docs[0].get('timestamp')
                    if first_date:
                        days_passed = (datetime.now(timezone.utc) - first_date).days
                        if days_passed >= 7:
                            return True
            
            return False
            
        except Exception as e:
            print(f"[Firestore] アーカイブチェックエラー: {e}")
            return False

    # === 統合検索システム ===
    async def search_user_data(self, user_id: int, search_term: str, limit: int = 10):
        """ユーザーの全データを横断検索"""
        try:
            results = {
                'files': [],
                'conversations': [],
                'messages': []
            }
            
            # ファイル検索
            files_query = self.db.collection(self.USER_FILES).where(
                filter=FieldFilter('user_id', '==', user_id)
            )
            files_docs = files_query.stream()
            
            for doc in files_docs:
                data = doc.to_dict()
                if search_term.lower() in data.get('content', '').lower() or search_term.lower() in data.get('filename', '').lower():
                    data['id'] = doc.id
                    results['files'].append(data)
            
            # AI会話検索
            conv_query = self.db.collection(self.AI_CONVERSATIONS).where(
                filter=FieldFilter('user_id', '==', user_id)
            )
            conv_docs = conv_query.stream()
            
            for doc in conv_docs:
                data = doc.to_dict()
                if (search_term.lower() in data.get('user_message', '').lower() or 
                    search_term.lower() in data.get('ai_response', '').lower()):
                    results['conversations'].append(data)
            
            print(f"[Firestore] ユーザーデータ検索完了: {len(results['files'])}ファイル, {len(results['conversations'])}会話")
            return results
            
        except Exception as e:
            print(f"[Firestore] ユーザーデータ検索エラー: {e}")
            return {'files': [], 'conversations': [], 'messages': []}

    # === 新しい統一システム用のメソッド ===
    async def get_archived_conversations_new(self, user_id: int, limit: int = 10) -> List[Dict]:
        """新しい統一システム用: ユーザーのアーカイブ会話を取得"""
        try:
            # インデックスエラーを回避するため、シンプルなクエリに変更
            docs = (
                self.db.collection(self.CONVERSATION_ARCHIVES)
                .where(filter=FieldFilter('user_id', '==', user_id))
                .limit(limit * 2)  # ソート後に制限を適用するため多めに取得
                .stream()
            )
            
            archived_conversations = []
            for doc in docs:
                data = doc.to_dict()
                archived_conversations.append(data)
            
            # archived_atでソート（新しい順）してから制限を適用
            archived_conversations.sort(key=lambda x: x.get('archived_at', datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
            return archived_conversations[:limit]
        except Exception as e:
            print(f"[Firestore] ユーザーアーカイブ取得エラー: {e}")
            return []

    async def delete_user_conversations_new(self, user_id: int) -> int:
        """新しい統一システム用: ユーザーの全会話を削除"""
        try:
            docs = (
                self.db.collection(self.CONVERSATIONS)
                .where(filter=FieldFilter('user_id', '==', user_id))
                .stream()
            )
            
            batch = self.db.batch()
            count = 0
            
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
                
                # バッチサイズ制限対応
                if count % 500 == 0:
                    batch.commit()
                    batch = self.db.batch()
            
            if count % 500 != 0:
                batch.commit()
            
            print(f"[Firestore] ユーザー{user_id}の会話{count}件を削除")
            return count
        except Exception as e:
            print(f"[Firestore] ユーザー会話削除エラー: {e}")
            return 0

    async def delete_user_archives_new(self, user_id: int) -> int:
        """新しい統一システム用: ユーザーのアーカイブを削除"""
        try:
            docs = (
                self.db.collection(self.CONVERSATION_ARCHIVES)
                .where(filter=FieldFilter('user_id', '==', user_id))
                .stream()
            )
            
            batch = self.db.batch()
            count = 0
            
            for doc in docs:
                batch.delete(doc.reference)
                count += 1
                
                # バッチサイズ制限対応
                if count % 500 == 0:
                    batch.commit()
                    batch = self.db.batch()
            
            if count % 500 != 0:
                batch.commit()
            
            print(f"[Firestore] ユーザー{user_id}のアーカイブ{count}件を削除")
            return count
        except Exception as e:
            print(f"[Firestore] ユーザーアーカイブ削除エラー: {e}")
            return 0
    
# グローバルなデータベースインスタンス
firestore_db = FirestoreDatabase()
