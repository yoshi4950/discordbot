"""
Firestore無効化版 - ローカル開発・テスト用
メッセージはメモリ内に保存され、Firestoreエラーを回避します
"""
import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

class FirestoreDatabase:
    def __init__(self):
        """Firestore無効化版 - メモリ内保存"""
        print("[Firestore] 無効化モード: メモリ内保存を使用")
        
        # メモリ内ストレージ
        self.conversations = []
        self.settings = {}
        self.spotify_tokens = {}
        self.executable_configs = {}
        self.user_files = []
        self.ai_conversations = []
        self.channel_configs = {}
        
        print("[Firestore] メモリ内データベース初期化完了")
    
    async def add_message(self, channel_id: int, user_id: int, username: str, content: str, message_id: int):
        """メッセージをメモリに追加"""
        message_data = {
            'channel_id': channel_id,
            'user_id': user_id,
            'username': username,
            'content': content,
            'message_id': message_id,
            'timestamp': datetime.now(timezone.utc)
        }
        self.conversations.append(message_data)
        
        # 最新100件のみ保持
        if len(self.conversations) > 100:
            self.conversations = self.conversations[-100:]
        
        print(f"[Firestore] メッセージ保存（メモリ）: {username} - {content[:50]}...")
    
    async def get_conversation_history(self, channel_id: int, limit: int = 100) -> List[Dict]:
        """チャンネルの会話履歴をメモリから取得"""
        messages = [
            msg for msg in self.conversations 
            if msg['channel_id'] == channel_id
        ]
        
        # 最新順でソート
        messages.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # 制限を適用して古い順に並び替え
        limited_messages = messages[:limit]
        limited_messages.reverse()
        
        print(f"[Firestore] 会話履歴取得（メモリ）: {len(limited_messages)}件")
        return limited_messages
    
    async def set_setting(self, setting_type: str, setting_key: str, setting_value: Any, 
                         channel_id: Optional[int] = None, user_id: Optional[int] = None):
        """設定をメモリに保存"""
        key = f"{setting_type}_{setting_key}"
        if channel_id:
            key += f"_ch{channel_id}"
        if user_id:
            key += f"_usr{user_id}"
        
        self.settings[key] = {
            'setting_type': setting_type,
            'setting_key': setting_key,
            'setting_value': setting_value,
            'channel_id': channel_id,
            'user_id': user_id,
            'updated_at': datetime.now(timezone.utc)
        }
        print(f"[Firestore] 設定保存（メモリ）: {key}")
    
    async def get_setting(self, setting_type: str, setting_key: str, 
                         channel_id: Optional[int] = None, user_id: Optional[int] = None) -> Optional[Any]:
        """設定をメモリから取得"""
        key = f"{setting_type}_{setting_key}"
        if channel_id:
            key += f"_ch{channel_id}"
        if user_id:
            key += f"_usr{user_id}"
        
        setting = self.settings.get(key)
        if setting:
            return setting.get('setting_value')
        return None
    
    async def get_all_settings(self, setting_type: str) -> Dict[str, Any]:
        """指定タイプの全設定をメモリから取得"""
        result = {}
        for key, setting in self.settings.items():
            if setting['setting_type'] == setting_type:
                result_key = setting['setting_key']
                if setting.get('channel_id'):
                    result_key = f"{setting['channel_id']}_{result_key}"
                elif setting.get('user_id'):
                    result_key = f"user_{setting['user_id']}_{result_key}"
                result[result_key] = setting['setting_value']
        
        return result
    
    async def save_spotify_token(self, user_id: int, access_token: str, refresh_token: str, expires_at: datetime):
        """Spotifyトークンをメモリに保存"""
        self.spotify_tokens[user_id] = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_at': expires_at,
            'updated_at': datetime.now(timezone.utc)
        }
        print(f"[Firestore] Spotifyトークン保存（メモリ）: ユーザー{user_id}")
    
    async def get_spotify_token(self, user_id: int) -> Optional[Dict]:
        """Spotifyトークンをメモリから取得"""
        token_data = self.spotify_tokens.get(user_id)
        if token_data:
            return {
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token'),
                'expires_at': token_data.get('expires_at')
            }
        return None
    
    async def save_executable_config(self, command_name: str, file_path: str, description: str = ""):
        """実行可能ファイル設定をメモリに保存"""
        self.executable_configs[command_name] = {
            'command_name': command_name,
            'file_path': file_path,
            'description': description,
            'updated_at': datetime.now(timezone.utc)
        }
        print(f"[Firestore] 実行設定保存（メモリ）: {command_name}")
    
    async def get_executable_configs(self) -> Dict[str, Dict]:
        """全ての実行可能ファイル設定をメモリから取得"""
        configs = {}
        for cmd_name, config in self.executable_configs.items():
            configs[cmd_name] = {
                'path': config.get('file_path'),
                'description': config.get('description', '')
            }
        
        print(f"[Firestore] 実行可能ファイル設定取得（メモリ）: {len(configs)}件")
        return configs
    
    async def delete_old_messages(self, days: int = 30):
        """古いメッセージ削除（メモリ版では何もしない）"""
        print("[Firestore] メモリ版: 古いメッセージ削除はスキップ")
    
    async def migrate_json_settings_to_firestore(self):
        """JSONファイル移行（メモリ版では既存ファイルから読み込み）"""
        print("[Firestore] メモリ版: 既存JSONファイルから設定を読み込み")
        
        try:
            # always_reply_channels.json
            if os.path.exists('always_reply_channels.json'):
                with open('always_reply_channels.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    channels = data if isinstance(data, list) else data.get('channels', [])
                    for channel_id in channels:
                        await self.set_setting('always_reply', 'enabled', True, channel_id=int(channel_id))
            
            # reference_mode_config.json
            if os.path.exists('reference_mode_config.json'):
                with open('reference_mode_config.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for channel_id, config in data.items():
                        await self.set_setting('reference_mode', 'config', config, channel_id=int(channel_id))
            
            # english_mode_config.json
            if os.path.exists('english_mode_config.json'):
                with open('english_mode_config.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for channel_id, config in data.items():
                        await self.set_setting('english_mode', 'config', config, channel_id=int(channel_id))
            
            # exe_launcher_config.json
            if os.path.exists('exe_launcher_config.json'):
                with open('exe_launcher_config.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'commands' in data and isinstance(data['commands'], dict):
                        for cmd_name, config in data['commands'].items():
                            await self.save_executable_config(
                                cmd_name, 
                                config.get('path', ''), 
                                config.get('description', '')
                            )
                    if 'config_channel_id' in data:
                        await self.set_setting('exe_launcher', 'config_channel_id', data['config_channel_id'])
            
            print("[Firestore] メモリ版: 設定ファイル読み込み完了")
                        
        except Exception as e:
            print(f"[Firestore] メモリ版: 設定ファイル読み込みエラー: {e}")
    
    # === その他のメソッドもダミー実装 ===
    async def save_user_file(self, user_id: int, filename: str, content: str, channel_id: int = None):
        file_data = {
            'user_id': user_id,
            'filename': filename,
            'content': content,
            'channel_id': channel_id,
            'created_at': datetime.now(timezone.utc),
            'id': len(self.user_files) + 1
        }
        self.user_files.append(file_data)
        return str(file_data['id'])
    
    async def get_user_files(self, user_id: int = None, channel_id: int = None):
        files = self.user_files
        if user_id:
            files = [f for f in files if f['user_id'] == user_id]
        if channel_id:
            files = [f for f in files if f['channel_id'] == channel_id]
        return files
    
    async def get_file_content(self, file_id: str):
        for file_data in self.user_files:
            if str(file_data['id']) == file_id:
                return file_data.get('content', '')
        return None
    
    async def delete_user_file(self, file_id: str):
        self.user_files = [f for f in self.user_files if str(f['id']) != file_id]
        return True
    
    async def save_ai_conversation(self, channel_id: int, user_message: str, ai_response: str, user_id: int):
        conversation_data = {
            'channel_id': channel_id,
            'user_id': user_id,
            'user_message': user_message,
            'ai_response': ai_response,
            'timestamp': datetime.now(timezone.utc),
            'id': len(self.ai_conversations) + 1
        }
        self.ai_conversations.append(conversation_data)
        return str(conversation_data['id'])
    
    async def get_ai_conversation_history(self, channel_id: int, limit: int = 50):
        conversations = [c for c in self.ai_conversations if c['channel_id'] == channel_id]
        conversations.sort(key=lambda x: x['timestamp'])
        return conversations[-limit:]
    
    async def save_channel_config(self, channel_id: int, config_type: str, config_data: dict):
        key = f"{channel_id}_{config_type}"
        self.channel_configs[key] = {
            'channel_id': channel_id,
            'config_type': config_type,
            'config_data': config_data,
            'updated_at': datetime.now(timezone.utc)
        }
        return True
    
    async def get_channel_config(self, channel_id: int, config_type: str):
        key = f"{channel_id}_{config_type}"
        config = self.channel_configs.get(key)
        return config.get('config_data', {}) if config else {}
    
    async def save_spotify_playlist(self, user_id: int, playlist_data: dict):
        return "dummy_playlist_id"
    
    async def get_user_spotify_playlists(self, user_id: int):
        return []
    
    async def search_user_data(self, user_id: int, search_term: str, limit: int = 10):
        return {'files': [], 'conversations': [], 'messages': []}

# グローバルなデータベースインスタンス
firestore_db = FirestoreDatabase()