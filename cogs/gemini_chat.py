import discord
from discord.ext import commands
import google.generativeai as genai
import os
import asyncio
import json
import re
from discord import app_commands
from discord import ui
from typing import List, Optional
from firestore_db import firestore_db

# Gemini API の初期化
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    print("エラー: GEMINI_API_KEY 環境変数が設定されていません。")
    genai_initialized = False
else:
    genai.configure(api_key=GEMINI_API_KEY)  # type: ignore
    # 2.0 Flashは終了済みのため、現在利用可能な安定モデルを使用する。
    gemini_model = genai.GenerativeModel(os.getenv('GEMINI_MODEL', 'gemini-3.8-flash'))  # type: ignore
    genai_initialized = True

ALWAYS_REPLY_FILE: str = 'always_reply_channels.json'  # type: ignore
REFERENCE_MODE_FILE: str = 'reference_mode_config.json'  # type: ignore
ENGLISH_MODE_FILE: str = 'english_mode_config.json'  # type: ignore
VOICE_MEMO_FILE: str = 'voice_memo_config.json'  # type: ignore

class CommandHelpView(ui.View):
    def __init__(self, commands_table):
        super().__init__(timeout=None)
        for cmd, desc in commands_table:
            self.add_item(CommandButton(cmd, desc))

class CommandButton(ui.Button):
    def __init__(self, command, description):
        super().__init__(label=command, style=discord.ButtonStyle.primary)
        self.command = command
        self.description = description

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"`{self.command}` をチャット欄にコピーしてください。", ephemeral=True)
        # Discordの仕様上、直接ユーザーの入力欄にテキストを自動入力することはできませんが、
        # ボタンを押すとコマンドが表示され、コピーしやすくなります。

class EnglishModeGroup(app_commands.Group):
    def __init__(self, cog):
        super().__init__(name="english", description="英語学習モードの管理")
        self.cog = cog

    @app_commands.command(name="mode", description="英語学習モードを切り替え")
    @app_commands.describe(mode="モード選択: friendly, business, slang, off, status")
    @app_commands.choices(mode=[
        app_commands.Choice(name="friendly", value="friendly"),
        app_commands.Choice(name="business", value="business"),
        app_commands.Choice(name="slang", value="slang"),
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="status", value="status")
    ])
    async def mode(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        await self.cog.english_mode_logic(interaction, mode.value)

class GeminiChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.always_reply_channels = set()
        self.conversation_history_limit = 100
        self.context_messages_limit = 100
        self.reference_mode_config = {}  # {channel_id: {"enabled": bool, "file_path": str}}
        self.english_mode_config = {}    # {channel_id: {"enabled": bool, "mode": str}}
        self.voice_memo_config = {}      # {channel_id: {"enabled": bool}}
        # Group登録は__init__の最後で
        self.bot.tree.add_command(EnglishModeGroup(self))

    async def cog_load(self):
        """Cog読み込み時の非同期初期化処理"""
        try:
            print("[GeminiChat] 高速初期化開始...")
            
            # Firestore読み込みを一時的にスキップしてデフォルト値を使用
            print("[GeminiChat] Firestore読み込みをスキップ - デフォルト値で初期化")
            self.always_reply_channels = set()
            self.reference_mode_config = {}
            self.english_mode_config = {}
            self.voice_memo_config = {}
            
            print("[GeminiChat] 高速初期化完了")
        except Exception as e:
            print(f"[GeminiChat] 初期化エラー: {e}")
            # デフォルト値で初期化
            self.always_reply_channels = set()
            self.reference_mode_config = {}
            self.english_mode_config = {}
            self.voice_memo_config = {}
    
    def load_always_reply_channels_from_json(self):
        """JSONファイルから常時返信チャンネル設定を読み込み"""
        try:
            import os
            import json
            always_reply_file = 'always_reply_channels.json'
            if os.path.exists(always_reply_file):
                with open(always_reply_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.always_reply_channels = set(data)
                    else:
                        self.always_reply_channels = set(data.get('channels', []))
                print(f"[GeminiChat] 常時返信チャンネルをJSONから読み込み: {self.always_reply_channels}")
            else:
                self.always_reply_channels = set()
                print("[GeminiChat] JSONファイルが見つからないため空設定")
        except Exception as e:
            print(f"[GeminiChat] JSON読み込みエラー: {e}")
            self.always_reply_channels = set()
    
    def load_reference_mode_config_from_json(self):
        """JSONファイルから参照モード設定を読み込み"""
        try:
            import os
            import json
            reference_file = 'reference_mode_config.json'
            if os.path.exists(reference_file):
                with open(reference_file, 'r', encoding='utf-8') as f:
                    self.reference_mode_config = json.load(f)
                print(f"[GeminiChat] 参照モード設定をJSONから読み込み: {len(self.reference_mode_config)}件")
            else:
                self.reference_mode_config = {}
                print("[GeminiChat] 参照モード設定JSONファイルなし")
        except Exception as e:
            print(f"[GeminiChat] 参照モード設定JSON読み込みエラー: {e}")
            self.reference_mode_config = {}
    
    def load_english_mode_config_from_json(self):
        """JSONファイルから英語学習モード設定を読み込み"""
        try:
            print("[GeminiChat] 英語学習モード設定読み込み開始...")
            import os
            import json
            english_file = 'english_mode_config.json'
            if os.path.exists(english_file):
                print(f"[GeminiChat] {english_file} ファイル発見")
                with open(english_file, 'r', encoding='utf-8') as f:
                    self.english_mode_config = json.load(f)
                print(f"[GeminiChat] 英語学習モード設定をJSONから読み込み: {len(self.english_mode_config)}件")
            else:
                self.english_mode_config = {}
                print("[GeminiChat] 英語学習モード設定JSONファイルなし")
        except Exception as e:
            print(f"[GeminiChat] 英語学習モード設定JSON読み込みエラー: {e}")
            import traceback
            traceback.print_exc()
            self.english_mode_config = {}

    async def load_always_reply_channels(self):
        try:
            try:
                settings = await asyncio.wait_for(firestore_db.get_all_settings('always_reply'), timeout=2.0)
                print(f"[GeminiChat] Firestoreから設定取得: {len(settings)}件")
            except asyncio.TimeoutError:
                print("[GeminiChat] Firestore読み込みタイムアウト - 空設定で続行")
                settings = {}
            except Exception as firestore_error:
                print(f"[GeminiChat] Firestore接続エラー: {firestore_error}")
                settings = {}
            
            self.always_reply_channels = set()
            
            for key, enabled in settings.items():
                if enabled and key.endswith('_enabled'):
                    # チャンネルIDを抽出
                    channel_id = int(key.split('_')[0])
                    self.always_reply_channels.add(channel_id)
            
            print(f"[GeminiChat] 常時返信チャンネルをロード: {self.always_reply_channels}")
        except Exception as e:
            print(f"[GeminiChat] 常時返信チャンネルロード失敗: {e}")
            self.always_reply_channels = set()

    async def save_always_reply_channels(self):
        try:
            # 一時的にFirestore保存をスキップ
            print(f"[GeminiChat] 常時返信チャンネル（メモリのみ）: {self.always_reply_channels}")
            # TODO: Firestore認証修正後に有効化
            # for channel_id in self.always_reply_channels:
            #     await asyncio.wait_for(
            #         firestore_db.set_setting('always_reply', 'enabled', True, channel_id=channel_id), 
            #         timeout=10.0
            #     )
        except Exception as e:
            print(f"[GeminiChat] 常時返信チャンネル保存失敗: {e}")

    async def load_reference_mode_config(self):
        """参照モード設定を読み込み"""
        try:
            try:
                settings = await asyncio.wait_for(firestore_db.get_all_settings('reference_mode'), timeout=2.0)
                print(f"[GeminiChat] 参照モード設定取得: {len(settings)}件")
            except asyncio.TimeoutError:
                print("[GeminiChat] 参照モード読み込みタイムアウト")
                settings = {}
            except Exception as firestore_error:
                print(f"[GeminiChat] 参照モードFirestore接続エラー: {firestore_error}")
                settings = {}
            
            self.reference_mode_config = {}
            
            for key, config in settings.items():
                if key.endswith('_config'):
                    channel_id = int(key.split('_')[0])
                    self.reference_mode_config[str(channel_id)] = config
            
            print(f"[GeminiChat] Firestoreから参照モード設定をロード: {self.reference_mode_config}")
        except Exception as e:
            print(f"[GeminiChat] 参照モード設定ロード失敗: {e}")
            self.reference_mode_config = {}

    def save_reference_mode_config(self):
        """参照モード設定を保存"""
        try:
            with open(REFERENCE_MODE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.reference_mode_config, f, ensure_ascii=False, indent=2)
                print(f"[GeminiChat] 参照モード設定保存: {self.reference_mode_config}")
        except Exception as e:
            print(f"[GeminiChat] 参照モード設定保存失敗: {e}")

    def get_reference_content(self, channel_id: int) -> str:
        """指定されたチャンネルの参照ファイル内容を取得"""
        channel_config = self.reference_mode_config.get(str(channel_id))
        if not channel_config or not channel_config.get("enabled", False):
            return ""
        
        file_path = channel_config.get("file_path", "")
        if not file_path:
            return ""
        
        try:
            # ファイルが存在するかチェック
            if not os.path.exists(file_path):
                return f"⚠️ 参照ファイル '{file_path}' が見つかりません。"
            
            # ファイルサイズチェック（10MB制限）
            file_size = os.path.getsize(file_path)
            if file_size > 10 * 1024 * 1024:  # 10MB
                return f"⚠️ 参照ファイル '{file_path}' が大きすぎます（{file_size // 1024 // 1024}MB）。10MB以下のファイルを使用してください。"
            
            # ファイル読み込み
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 内容が空の場合
            if not content.strip():
                return f"⚠️ 参照ファイル '{file_path}' は空です。"
            
            return content
            
        except PermissionError:
            return f"⚠️ 参照ファイル '{file_path}' の読み込み権限がありません。"
        except UnicodeDecodeError:
            return f"⚠️ 参照ファイル '{file_path}' の文字エンコーディングが対応していません。UTF-8で保存してください。"
        except Exception as e:
            return f"⚠️ 参照ファイル '{file_path}' の読み込み中にエラーが発生しました: {str(e)}"

    async def get_dm_channel(self, user_id: int):
        """ユーザーのDMチャンネルを取得"""
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        return user.dm_channel or await user.create_dm()

    async def save_file_to_dm(self, user_id: int, filename: str, content: str):
        """ファイル内容をDMに保存"""
        try:
            dm_channel = await self.get_dm_channel(user_id)
            
            # 既存の同名ファイルを削除
            deleted_count = 0
            async for msg in dm_channel.history(limit=100):
                if msg.author == self.bot.user and msg.content.startswith(f"REFERENCE_FILE:{filename}:"):
                    try:
                        await msg.delete()
                        deleted_count += 1
                    except discord.NotFound:
                        pass
                    except Exception as delete_error:
                        print(f"ファイル削除エラー: {delete_error}")
            
            # 新しいファイルを保存（分割して送信）
            prefix = f"REFERENCE_FILE:{filename}:"
            max_content_length = 1950  # 安全マージンを多めに取る
            
            if len(content) <= max_content_length:
                # 1メッセージで送信可能
                message_content = f"{prefix}{content}"
                await dm_channel.send(message_content)
            else:
                # 複数メッセージに分割
                parts = []
                for i in range(0, len(content), max_content_length):
                    parts.append(content[i:i + max_content_length])
                
                for i, part in enumerate(parts):
                    part_prefix = f"REFERENCE_FILE:{filename}:PART{i+1}OF{len(parts)}:"
                    message_content = f"{part_prefix}{part}"
                    await dm_channel.send(message_content)
            
            print(f"ファイル '{filename}' をDMに保存完了 (User: {user_id})")
            return True
            
        except Exception as e:
            print(f"ファイルのDM保存中にエラー: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def load_file_from_dm(self, user_id: int, filename: str) -> Optional[str]:
        """DMからファイル内容を読み込み"""
        try:
            dm_channel = await self.get_dm_channel(user_id)
            
            # 単一メッセージファイルを検索
            simple_prefix = f"REFERENCE_FILE:{filename}:"
            
            async for message in dm_channel.history(limit=100):
                if message.author == self.bot.user and message.content.startswith(simple_prefix):
                    if ":PART" not in message.content:
                        # 単一メッセージのファイル
                        content = message.content.replace(simple_prefix, "", 1)
                        return content
            
            # 分割メッセージファイルを検索
            parts = {}
            total_parts = 0
            part_prefix = f"REFERENCE_FILE:{filename}:PART"
            
            async for message in dm_channel.history(limit=100):
                if message.author == self.bot.user and message.content.startswith(part_prefix):
                    # PART番号を抽出
                    try:
                        # メッセージ形式: "REFERENCE_FILE:filename:PART1OF2:内容"
                        colon_parts = message.content.split(":", 3)
                        if len(colon_parts) >= 4:
                            part_info = colon_parts[2]  # "PART1OF2" 形式
                            content = colon_parts[3]    # 実際の内容
                        else:
                            # フォールバック: より柔軟な解析
                            part_start = message.content.find(":PART")
                            if part_start == -1:
                                continue
                            part_end = message.content.find(":", part_start + 1)
                            if part_end == -1:
                                continue
                            part_info = message.content[part_start+1:part_end]
                            content = message.content[part_end+1:]
                        
                        # PART番号を抽出
                        if "PART" in part_info and "OF" in part_info:
                            part_num = int(part_info.split("OF")[0].replace("PART", ""))
                            total_parts = int(part_info.split("OF")[1])
                            parts[part_num] = content
                        else:
                            continue
                        
                    except (ValueError, IndexError) as e:
                        print(f"分割ファイル解析エラー: {e}")
                        print(f"問題のメッセージ: {message.content[:200]}...")
                        continue
            
            # 分割ファイルを結合
            if parts and len(parts) == total_parts:
                content = ""
                for i in range(1, total_parts + 1):
                    content += parts[i]
                return content
            
            return None
            
        except Exception as e:
            print(f"ファイルのDM読み込み中にエラー: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def list_files_in_dm(self, user_id: int) -> List[str]:
        """DMに保存されているファイル一覧を取得"""
        try:
            dm_channel = await self.get_dm_channel(user_id)
            files = set()
            
            async for message in dm_channel.history(limit=100):
                if message.author == self.bot.user and message.content.startswith("REFERENCE_FILE:"):
                    # ファイル名を抽出
                    try:
                        parts = message.content.split(":")
                        if len(parts) >= 3:
                            filename = parts[1]
                            files.add(filename)
                    except Exception as e:
                        print(f"ファイル名抽出エラー: {e}")
                        continue
            
            return sorted(list(files))
            
        except Exception as e:
            print(f"ファイル一覧取得中にエラー: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def delete_file_from_dm(self, user_id: int, filename: str) -> bool:
        """DMからファイルを削除"""
        try:
            dm_channel = await self.get_dm_channel(user_id)
            deleted_count = 0
            
            # 該当するファイルのメッセージを全て削除
            async for msg in dm_channel.history(limit=100):
                if msg.author == self.bot.user and msg.content.startswith(f"REFERENCE_FILE:{filename}:"):
                    try:
                        await msg.delete()
                        deleted_count += 1
                    except discord.NotFound:
                        pass
                    except Exception as delete_error:
                        print(f"メッセージ削除エラー: {delete_error}")
            
            print(f"ファイル '{filename}' を削除しました ({deleted_count}件のメッセージ) (User: {user_id})")
            return deleted_count > 0
            
        except Exception as e:
            print(f"ファイル削除中にエラー: {e}")
            return False

    async def get_reference_content_from_dm(self, channel_id: int, user_id: int) -> str:
        """DMから参照ファイル内容を取得"""
        channel_config = self.reference_mode_config.get(str(channel_id))
        
        if not channel_config or not channel_config.get("enabled", False):
            return ""
        
        filename = channel_config.get("file_path", "")
        if not filename:
            return ""
        
        # 古い形式（フルパス）から新しい形式（拡張子なしファイル名）への変換
        import os
        if os.path.sep in filename or filename.endswith(('.txt', '.md', '.json', '.py', '.js', '.html', '.css')):
            # フルパスまたは拡張子付きの場合、ファイル名のみを抽出し拡張子を除去
            filename = os.path.splitext(os.path.basename(filename))[0]
        
        # DMからファイル内容を読み込み
        content = await self.load_file_from_dm(user_id, filename)
        if content is None:
            return f"⚠️ 参照ファイル '{filename}' がDMに見つかりません。"
        
        if not content.strip():
            return f"⚠️ 参照ファイル '{filename}' は空です。"
        
        return content

    async def handle_file_upload_message(self, message):
        """メッセージ形式でのファイルアップロード処理"""
        try:
            content = message.content
            lines = content.split('\n', 1)
            
            if len(lines) < 2:
                await message.reply("❌ ファイル内容が見つかりません。\n使用方法: `/upload:ファイル名.txt`\n[ファイル内容]")
                return
            
            # ファイル名を抽出
            first_line = lines[0]
            if not first_line.startswith('/upload:'):
                return
            
            filename = first_line.replace('/upload:', '').strip()
            file_content = lines[1].strip()
            
            if not filename:
                await message.reply("❌ ファイル名を指定してください。\n使用方法: `/upload:ファイル名.txt`")
                return
            
            if not file_content:
                await message.reply("❌ ファイル内容を入力してください。")
                return
            
            # 危険な文字の除去と拡張子の除去
            import re
            import os
            filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
            # 拡張子を除去
            filename = os.path.splitext(filename)[0]
            
            # ファイルをDMに保存
            success = await self.save_file_to_dm(message.author.id, filename, file_content)
            
            if success:
                embed = discord.Embed(
                    title="✅ ファイルアップロード完了",
                    description=f"**{filename}** をDMに保存しました。",
                    color=discord.Color.green()
                )
                embed.add_field(name="ファイル名", value=filename, inline=True)
                embed.add_field(name="文字数", value=f"{len(file_content):,}文字", inline=True)
                embed.add_field(
                    name="使用方法", 
                    value=f"`/reference mode on {filename}` で参照モードに設定", 
                    inline=False
                )
                await message.reply(embed=embed)
                
                # 元のメッセージを削除（オプション）
                try:
                    await message.delete()
                except:
                    pass
            else:
                await message.reply("❌ ファイルの保存に失敗しました。")
                
        except Exception as e:
            print(f"ファイルアップロード処理エラー: {e}")
            await message.reply("❌ ファイルアップロード中にエラーが発生しました。")

    async def handle_file_update_message(self, message):
        """メッセージ形式でのファイル更新処理"""
        try:
            content = message.content
            lines = content.split('\n', 1)
            
            if len(lines) < 2:
                await message.reply("❌ ファイル内容が見つかりません。\n使用方法: `/update:ファイル名.txt`\n[新しいファイル内容]")
                return
            
            # ファイル名を抽出
            first_line = lines[0]
            if not first_line.startswith('/update:'):
                return
            
            filename = first_line.replace('/update:', '').strip()
            file_content = lines[1].strip()
            
            if not filename:
                await message.reply("❌ ファイル名を指定してください。\n使用方法: `/update:ファイル名.txt`")
                return
            
            if not file_content:
                await message.reply("❌ ファイル内容を入力してください。")
                return
            
            # 危険な文字の除去と拡張子の除去
            import re
            import os
            filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
            # 拡張子を除去
            filename = os.path.splitext(filename)[0]
            
            # 既存ファイルの確認
            existing_content = await self.load_file_from_dm(message.author.id, filename)
            if existing_content is None:
                await message.reply(f"❌ ファイル '{filename}' が見つかりません。\n`/reference list` で既存ファイルを確認してください。")
                return
            
            # ファイルを更新（上書き保存）
            success = await self.save_file_to_dm(message.author.id, filename, file_content)
            
            if success:
                embed = discord.Embed(
                    title="✅ ファイル更新完了",
                    description=f"**{filename}** の内容を更新しました。",
                    color=discord.Color.green()
                )
                embed.add_field(name="ファイル名", value=filename, inline=True)
                embed.add_field(name="新しい文字数", value=f"{len(file_content):,}文字", inline=True)
                embed.add_field(name="変更", value=f"前: {len(existing_content):,}文字 → 後: {len(file_content):,}文字", inline=True)
                await message.reply(embed=embed)
                
                # 元のメッセージを削除（オプション）
                try:
                    await message.delete()
                except:
                    pass
            else:
                await message.reply("❌ ファイルの更新に失敗しました。")
                
        except Exception as e:
            print(f"ファイル更新処理エラー: {e}")
            await message.reply("❌ ファイル更新中にエラーが発生しました。")

    def load_english_mode_config(self):
        try:
            with open(ENGLISH_MODE_FILE, 'r', encoding='utf-8') as f:
                self.english_mode_config = json.load(f)
                print(f"[GeminiChat] 英語学習モード設定ロード: {self.english_mode_config}")
        except Exception as e:
            print(f"[GeminiChat] 英語学習モード設定ロード失敗: {e}")
            self.english_mode_config = {}

    def save_english_mode_config(self):
        try:
            with open(ENGLISH_MODE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.english_mode_config, f, ensure_ascii=False, indent=2)
                print(f"[GeminiChat] 英語学習モード設定保存: {self.english_mode_config}")
        except Exception as e:
            print(f"[GeminiChat] 英語学習モード設定保存失敗: {e}")

    def load_voice_memo_config(self):
        """ボイスメモ自動文字起こし設定を読み込み"""
        try:
            print("[GeminiChat] ボイスメモ設定読み込み開始...")
            import os
            if os.path.exists(VOICE_MEMO_FILE):
                print(f"[GeminiChat] {VOICE_MEMO_FILE} ファイル発見")
                with open(VOICE_MEMO_FILE, 'r', encoding='utf-8') as f:
                    self.voice_memo_config = json.load(f)
                    print(f"[GeminiChat] ボイスメモ設定ロード: {self.voice_memo_config}")
            else:
                self.voice_memo_config = {}
                print("[GeminiChat] ボイスメモ設定ファイルが見つからないため空設定")
        except Exception as e:
            print(f"[GeminiChat] ボイスメモ設定ロード失敗: {e}")
            import traceback
            traceback.print_exc()
            self.voice_memo_config = {}

    def save_voice_memo_config(self):
        """ボイスメモ自動文字起こし設定を保存"""
        try:
            with open(VOICE_MEMO_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.voice_memo_config, f, ensure_ascii=False, indent=2)
                print(f"[GeminiChat] ボイスメモ設定保存: {self.voice_memo_config}")
        except Exception as e:
            print(f"[GeminiChat] ボイスメモ設定保存失敗: {e}")

    async def transcribe_voice_memo(self, voice_file_url: str) -> str:
        """ボイスメモをGemini APIで文字起こし"""
        try:
            import aiohttp
            import tempfile
            import os
            
            # 音声ファイルをダウンロード
            async with aiohttp.ClientSession() as session:
                async with session.get(voice_file_url) as response:
                    if response.status == 200:
                        # 一時ファイルに保存
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as temp_file:
                            temp_file.write(await response.read())
                            temp_file_path = temp_file.name
                        
                        try:
                            # Gemini APIで音声認識
                            audio_file = genai.upload_file(temp_file_path)
                            prompt = """
この音声ファイルの内容を正確に文字起こししてください。

要件:
- 日本語で出力
- 話者の意図を正確に伝える
- 「えー」「あの」などの言いよどみは適度に除去
- 自然で読みやすい文章にする
- 改行は適切に入れる

音声の内容:
"""
                            response = await asyncio.to_thread(gemini_model.generate_content, [prompt, audio_file])
                            return response.text
                            
                        finally:
                            # 一時ファイルを削除
                            try:
                                os.unlink(temp_file_path)
                            except:
                                pass
                    else:
                        return "❌ 音声ファイルのダウンロードに失敗しました。"
                        
        except Exception as e:
            print(f"音声文字起こしエラー: {e}")
            return f"❌ 音声文字起こし中にエラーが発生しました: {str(e)}"

    @commands.Cog.listener()
    async def on_message(self, message):
        # デバッグ: メッセージ受信ログ
        print(f"📝 メッセージ受信: {message.author}: {message.content}")
        
        if message.author == self.bot.user:
            print("🤖 Bot自身のメッセージのためスキップ")
            return

        # 一時的にFirestore保存を無効化（AI機能を優先）
        # TODO: Firestore認証修正後に有効化
        
        if not genai_initialized:
            return
        
        # ファイルアップロード処理をチェック
        if message.content.startswith('/upload:'):
            await self.handle_file_upload_message(message)
            return
        
        # ファイル更新処理をチェック
        if message.content.startswith('/update:'):
            await self.handle_file_update_message(message)
            return
            
        # ボイスメモ自動文字起こしをチェック
        channel_id = str(message.channel.id)
        voice_memo_config = self.voice_memo_config.get(channel_id, {"enabled": False})
        if voice_memo_config.get("enabled", False) and message.attachments:
            for attachment in message.attachments:
                # 音声ファイルかどうかをチェック
                if any(attachment.filename.lower().endswith(ext) for ext in ['.ogg', '.mp3', '.wav', '.m4a', '.aac', '.flac']):
                    print(f"[VoiceMemo] 音声ファイル検出: {attachment.filename}")
                    
                    # 文字起こしを実行
                    async with message.channel.typing():
                        transcription = await self.transcribe_voice_memo(attachment.url)
                        
                        # 結果を送信
                        embed = discord.Embed(
                            title="🎤 ボイスメモ文字起こし",
                            description=transcription,
                            color=discord.Color.blue()
                        )
                        embed.add_field(name="📁 ファイル名", value=attachment.filename, inline=True)
                        embed.add_field(name="👤 投稿者", value=message.author.display_name, inline=True)
                        await message.reply(embed=embed)
                    return
            
        if message.content.startswith('/'):
            return
        # Bot本体へのメンションは、常時返信設定や英語学習モードに関係なく通常のAI応答へ渡す。
        if any((user is not None and hasattr(user, 'id') and user.id == self.bot.user.id) for user in message.mentions):  # type: ignore
            mention_pattern = rf'<@!?{self.bot.user.id}>'
            content = re.sub(mention_pattern, '', message.content).strip()
            if not content:
                await message.reply("呼びかけありがとう！質問やメッセージも一緒に送ってね。")
            else:
                await self.generate_response(message, content)
            return

        # 英語学習モード判定
        channel_id = str(message.channel.id)
        english_mode = self.english_mode_config.get(channel_id, {"enabled": False})
        if english_mode.get("enabled", False):
            await self.generate_english_learning_response(message, message.content, english_mode.get("mode", "friendly"))
            return
        # AI自動返信チェック（デバッグログ付き）
        print(f"[GeminiChat] チャンネル {message.channel.id} チェック中")
        print(f"[GeminiChat] 常時返信チャンネル: {self.always_reply_channels}")
        if message.channel.id in self.always_reply_channels:
            print(f"[GeminiChat] AI自動返信開始: {message.content}")
            await self.generate_response(message, message.content)
            return
        else:
            print(f"[GeminiChat] AI自動返信対象外チャンネル")

    async def generate_response(self, message, user_input):
        print(f"[GeminiChat] generate_response開始: {user_input}")
        async with message.channel.typing():
            try:
                # アーカイブチェック & 実行（新しい統一システム）
                try:
                    archive_needed = await firestore_db.check_archive_needed_new(message.author.id)
                    if archive_needed:
                        print(f"[GeminiChat] アーカイブ実行開始: ユーザー{message.author.id}")
                        await firestore_db.archive_user_conversations(message.author.id)
                except Exception as archive_error:
                    print(f"[GeminiChat] アーカイブ処理エラー: {archive_error}")

                print("[GeminiChat] 統一会話履歴取得開始...")
                # 統一された会話履歴をユーザーベースで取得
                try:
                    conversation_history = await asyncio.wait_for(
                        firestore_db.get_user_conversation_history(message.author.id, 50),
                        timeout=3.0
                    )
                    print(f"[GeminiChat] 統一会話履歴取得成功: {len(conversation_history)}件")
                except asyncio.TimeoutError:
                    print("[GeminiChat] 統一会話履歴読み込みタイムアウト - 空履歴で続行")
                    conversation_history = []
                except Exception as firestore_error:
                    print(f"[GeminiChat] 統一会話履歴Firestore接続エラー: {firestore_error}")
                    conversation_history = []
                
                # 従来のメッセージ履歴取得は統一システムに移行済みのため削除

                # アーカイブ会話履歴取得（ユーザーベース）
                try:
                    archived_conversations = await asyncio.wait_for(
                        firestore_db.get_archived_conversations_new(message.author.id, 2),
                        timeout=2.0
                    )
                    print(f"[GeminiChat] アーカイブ履歴取得成功: {len(archived_conversations)}件")
                except Exception as archive_error:
                    print(f"[GeminiChat] アーカイブ履歴取得エラー: {archive_error}")
                    archived_conversations = []
                
                # 統一会話履歴を整理して表示用に変換
                display_history = []
                
                # アーカイブデータを背景情報として追加
                if archived_conversations:
                    display_history.append({
                        'author': 'システム',
                        'content': f'[過去の会話要約: {len(archived_conversations)}個のアーカイブあり]',
                        'timestamp': '過去'
                    })
                
                # 統一された会話履歴を処理
                for conv in conversation_history:
                    print(f"[DEBUG] 統一会話データ型: {type(conv)}, 内容: {conv}")
                    if isinstance(conv, dict):
                        # message_typeに基づいて表示
                        message_type = conv.get('message_type', 'user')
                        content = conv.get('content', '')
                        username = conv.get('username', 'Unknown')
                        timestamp = conv.get('timestamp', '')
                        
                        # タイムスタンプの整形
                        if hasattr(timestamp, 'strftime'):
                            timestamp_str = timestamp.strftime('%H:%M')
                        else:
                            timestamp_str = str(timestamp)[:5] if timestamp else ''
                        
                        if message_type == 'user':
                            # ユーザーメッセージ
                            if f'<@{self.bot.user.id}>' in content:
                                content = content.replace(f'<@{self.bot.user.id}>', '').strip()
                            if content and not content.startswith('/'):
                                display_history.append({
                                    'author': username,
                                    'content': content,
                                    'timestamp': timestamp_str
                                })
                        elif message_type == 'ai':
                            # AIメッセージ
                            display_history.append({
                                'author': 'まなち',
                                'content': content,
                                'timestamp': timestamp_str
                            })
                    else:
                        print(f"[WARNING] 統一会話データが辞書型でない: {type(conv)}")
                
                # 参照モードの内容を取得（一時的に無効化）
                reference_content = None  # await self.get_reference_content_from_firestore(message.channel.id, message.author.id)
                
                context_prompt = """あなたはDiscordチャンネルで会話に参加しているAIアシスタントです。\n以下の会話履歴を参考にして、自然で文脈に沿った返答をしてください。\n\n**キャラクター設定:**\n- あなたの名前は「まなち」です。自分のことを「まなち」と呼んでください\n- 女子高校生の後輩のような親しみやすい喋り方\n- 敬語は使わず、友達感覚で話す\n- 現代的な若者言葉を使う（ただし過度にならない）\n- 照れたり嬉しいときなど、適度に「えへへ♡」という言い回しを使う（頻繁ではなく自然なタイミングで）\n\n**絵文字・顔文字の使い方:**\n- 現代的な絵文字を自然に使う（😊、🤔、😆、✨、💕など）\n- 顔文字は控えめに、絵文字を中心に使用\n- おっさん風の古い顔文字（(笑)、( ´Д｀)など）は避ける\n- 絵文字は文の最後や感情表現に自然に配置\n\n**質問応答に関する重要指示:**\n- 質問されたことには必ず明確に答えてください\n- 質問をはぐらかしたり、曖昧な返答を避けてください\n- 知っていることは正確に、知らないことは「知らない」と正直に答えてください\n- 質問の内容を理解できない場合は、確認の質問をしてください\n- 複数の質問がある場合は、順番に一つずつ答えてください\n- 質問に対する回答を優先し、その後で会話を展開してください\n\n重要な指示:\n- 会話の流れを理解し、適切なタイミングで返答してください\n- ユーザーの感情や意図を読み取り、共感的な返答を心がけてください\n- 質問には明確に答え、話題を展開してください\n- 冗談やユーモアにも適切に反応してください\n- 日本語で自然な会話を心がけてください\n\n**会話履歴の参照ルール（最重要）:**\n- 現在のメッセージと過去の会話が明確に関連している場合のみ、過去の会話を参照してください\n- 関連性の判断基準：\n  * 同じ話題やキーワードが含まれている\n  * 同じ人物について話している\n  * 同じ状況や出来事について話している\n  * 感情的な流れが続いている\n- 関連性がない場合は、過去の会話を一切言及せず、現在のメッセージのみに焦点を当てて返答してください\n- 無理に過去の会話を関連付けることは絶対に避けてください\n"""
                
                # 参照ファイルの内容がある場合は追加
                if reference_content:
                    if reference_content.startswith("⚠️"):
                        # エラーメッセージの場合は警告として表示
                        context_prompt += f"\n**参照ファイルエラー:**\n{reference_content}\n\n"
                    else:
                        # 正常な参照ファイル内容
                        context_prompt += f"\n**参照ファイルの内容（以下の情報に基づいて回答してください）:**\n{reference_content}\n\n"
                
                if display_history:
                    context_prompt += "【会話履歴】\n"
                    recent_messages = display_history[-self.context_messages_limit:]
                    for msg in recent_messages:
                        context_prompt += f"[{msg['timestamp']}] {msg['author']}: {msg['content']}\n"
                    context_prompt += "\n"
                context_prompt += f"【現在のメッセージ】\n{message.author.display_name}: {user_input}\n\n"
                context_prompt += """上記の会話の流れを踏まえて、自然で適切な返答をしてください。\n\n返答のガイドライン:\n1. まず現在のメッセージの内容を理解する\n2. 質問がある場合は、必ず明確に答えることを最優先にする\n3. 質問をはぐらかしたり、曖昧な返答を絶対に避ける\n4. 過去の会話履歴と関連性があるかどうかを慎重に判断する\n5. 関連性がある場合のみ、自然な形で過去の会話を参照する\n6. 関連性がない場合は、現在のメッセージのみに焦点を当てて返答する\n7. 過去の会話を無理に引き合いに出すことは絶対に避ける\n8. 自然で自然体な会話を心がける\n9. 女子高校生の後輩のような親しみやすい喋り方で返答する\n10. 現代的な絵文字を自然に使用し、古い顔文字は避ける\n11. 友達感覚で話すが、初対面の人には適度に丁寧に\n12. 質問に対する回答を完了してから、必要に応じて会話を展開する"""
                
                print("[GeminiChat] Gemini AI生成開始...")
                response = await asyncio.to_thread(gemini_model.generate_content, context_prompt)
                ai_response_text = response.text
                print(f"[GeminiChat] Gemini AI生成完了: {len(ai_response_text)}文字")
                
                # レスポンスを送信
                print("[GeminiChat] Discord返信送信中...")
                await message.channel.send(ai_response_text)
                print("[GeminiChat] Discord返信送信完了")
                
                # 統一会話システムにユーザーメッセージとAIレスポンスを保存
                try:
                    # ユーザーメッセージを保存
                    asyncio.create_task(
                        firestore_db.add_conversation_message(
                            user_id=message.author.id,
                            username=message.author.display_name,
                            content=user_input,
                            message_type='user'
                        )
                    )
                    # AIレスポンスを保存
                    asyncio.create_task(
                        firestore_db.add_conversation_message(
                            user_id=message.author.id,
                            username='まなち',
                            content=ai_response_text,
                            message_type='ai'
                        )
                    )
                    print(f"[GeminiChat] 統一会話保存タスク作成: ユーザー{message.author.id}")
                except Exception as save_error:
                    print(f"[GeminiChat] 統一会話保存エラー: {save_error}")
                    
            except Exception as e:
                print(f"Gemini API エラー: {e}")
                await message.channel.send("AI応答の取得中にエラーが発生しました。申し訳ありません。")

    async def generate_english_learning_response(self, message, user_input, mode):
        async with message.channel.typing():
            try:
                # 会話履歴取得（100件まで拡張）
                conversation_history = []
                async for msg in message.channel.history(limit=self.conversation_history_limit, before=message):
                    if msg.author != self.bot.user:
                        conversation_history.append({
                            'author': msg.author.display_name,
                            'content': msg.content,
                            'timestamp': msg.created_at.strftime('%H:%M')
                        })
                conversation_history.reverse()

                # モードごとのプロンプトテンプレート
                if mode == "friendly":
                    style = "カジュアルで親しみやすい友達口調で英語で返答してください。"
                    feedback_rule = (
                        "■ 文法・単語エラーチェック: 明らかな文法ミスや単語の誤用がある場合は日本語で優しく修正・解説してください。\n"
                        "■ 失礼・不適切表現チェック: 親友間でも失礼や不適切な表現がある場合は日本語で注意してください。\n"
                        "■ 返答方針: 問題がない場合は英語のみで返答。問題がある場合は英語で返答後、日本語で修正・注意。\n"
                        "■ スラングや略語: カジュアルなスラングや略語は問題視しない。"
                    )
                elif mode == "business":
                    style = "フォーマルで丁寧なビジネス英語で返答してください。"
                    feedback_rule = (
                        "■ 文法・単語エラーチェック: 明らかな文法ミスや単語の誤用がある場合は日本語で厳密に修正・解説してください。\n"
                        "■ ビジネス表現チェック: ビジネスに不適切な表現や失礼な表現は日本語で指摘し、よりフォーマルで適切な表現を提案してください。\n"
                        "■ 返答方針: 問題がない場合は英語のみで返答。問題がある場合は英語で返答後、日本語で修正・指摘。\n"
                        "■ カジュアル表現: カジュアルすぎる表現はビジネスに適したフォーマルな表現に修正。"
                    )
                elif mode == "slang":
                    style = "アメリカ英語のスラングや口語表現を交えたカジュアルな英語で返答してください。"
                    feedback_rule = (
                        "■ 文法エラーチェック: 明らかな文法ミスや単語の誤用がある場合は日本語で優しく修正・解説してください。\n"
                        "■ スラング許容: スラングや口語表現は積極的に使用し、細かい指摘はしない。\n"
                        "■ 返答方針: 問題がない場合は英語のみで返答。重大な文法エラーのみ日本語で修正。\n"
                        "■ 重大エラーのみ注意: 会話が成立しないほどの重大な誤りや、差別的・攻撃的な表現のみ注意。"
                    )
                else:
                    style = "カジュアルな英語で返答してください。"
                    feedback_rule = (
                        "■ 文法・単語エラーチェック: 明らかな文法ミスや単語の誤用がある場合は日本語で優しく修正・解説してください。\n"
                        "■ 返答方針: 問題がない場合は英語のみで返答。問題がある場合は英語で返答後、日本語で修正・解説。"
                    )

                prompt = f"""
あなたは英語学習をサポートするAIです。
ユーザーと英語で会話してください。

【会話スタイル】
{style}

【フィードバックルール】
{feedback_rule}

【重要な注意】
- まずユーザーの英語を注意深くチェックしてください
- 明らかなエラーがある場合のみ日本語で指摘してください
- エラーがない場合は英語のみで返答してください
- 指摘する場合は英語返答の後に日本語で修正・解説してください

【会話履歴】
"""
                for msg in conversation_history:
                    prompt += f"[{msg['timestamp']}] {msg['author']}: {msg['content']}\n"
                prompt += f"\n【ユーザーの発言】\n{message.author.display_name}: {user_input}\n"
                prompt += """
【処理手順】
1. ユーザーの英語をチェック
2. エラーがあれば「英語返答 + 日本語修正」
3. エラーがなければ「英語のみ返答」
---
"""
                response = await asyncio.to_thread(gemini_model.generate_content, prompt)
                await message.channel.send(response.text)
            except Exception as e:
                print(f"Gemini API 英語学習モードエラー: {e}")
                await message.channel.send("英語学習モード応答中にエラーが発生しました。申し訳ありません。")

    async def english_mode_logic(self, interaction: discord.Interaction, mode: str):
        print(f"[/english mode] 呼び出し: user={interaction.user}, mode={mode}")
        if not interaction.user or not hasattr(interaction.user, 'guild_permissions') or not interaction.user.guild_permissions.administrator:  # type: ignore
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        if not interaction.channel or not hasattr(interaction.channel, 'id'):
            await interaction.response.send_message("❌ チャンネル情報を取得できません。", ephemeral=True)
            return
        channel_id = str(interaction.channel.id)  # type: ignore
        valid_modes = ["friendly", "business", "slang"]
        if mode in valid_modes:
            self.english_mode_config[channel_id] = {"enabled": True, "mode": mode}
            self.save_english_mode_config()
            await interaction.response.send_message(f"✅ 英語学習モードを[{mode}]で有効化しました。", ephemeral=True)
            print(f"[/english mode] 英語学習モード有効化: channel={channel_id}, mode={mode}")
        elif mode == "off":
            if channel_id in self.english_mode_config:
                self.english_mode_config[channel_id]["enabled"] = False
                self.save_english_mode_config()
                await interaction.response.send_message("✅ 英語学習モードを無効化しました。", ephemeral=True)
                print(f"[/english mode] 英語学習モード無効化: channel={channel_id}")
            else:
                await interaction.response.send_message("ℹ️ 英語学習モードは既に無効です。", ephemeral=True)
        elif mode == "status":
            if channel_id in self.english_mode_config and self.english_mode_config[channel_id].get("enabled", False):
                current_mode = self.english_mode_config[channel_id].get("mode", "未設定")
                await interaction.response.send_message(f"✅ 英語学習モード: 有効\n現在のモード: {current_mode}", ephemeral=True)
            else:
                await interaction.response.send_message("❌ 英語学習モード: 無効", ephemeral=True)
        else:
            await interaction.response.send_message("使い方: /english mode <friendly|business|slang|off|status>", ephemeral=True)

    @app_commands.command(name="reference", description="参照モードの管理")
    @app_commands.describe(
        mode="操作モード",
        filename="参照するファイル名（mode=onの場合のみ）"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="reload", value="reload"),
        app_commands.Choice(name="upload", value="upload"),
        app_commands.Choice(name="list", value="list"),
        app_commands.Choice(name="delete", value="delete"),
    ])
    async def reference_mode(self, interaction: discord.Interaction, mode: str, filename: str | None = None):
        """参照モードの管理コマンド"""
        print(f"[/reference] 呼び出し: user={interaction.user}, mode={mode}, filename={filename}")
        
        # 権限チェック（管理者のみ）
        if not interaction.user or not hasattr(interaction.user, 'guild_permissions') or not interaction.user.guild_permissions.administrator:  # type: ignore
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        
        if not interaction.channel or not hasattr(interaction.channel, 'id'):
            await interaction.response.send_message("❌ チャンネル情報を取得できません。", ephemeral=True)
            return
        
        channel_id = str(interaction.channel.id)  # type: ignore
        
        if mode == "on":
            if filename:
                # ファイル名から拡張子を除去
                import os
                filename = os.path.splitext(filename)[0]
                
                # DMからファイルの存在確認
                content = await self.load_file_from_dm(interaction.user.id, filename)
                if content is None:
                    await interaction.response.send_message(f"❌ 指定されたファイル '{filename}' がDMに見つかりません。\n`/reference list` で利用可能なファイルを確認してください。", ephemeral=True)
                    return
                
                # ファイルサイズチェック（おおよその文字数）
                if len(content) > 100000:  # 約100KB相当
                    await interaction.response.send_message(f"❌ ファイル '{filename}' が大きすぎます。より小さなファイルを使用してください。", ephemeral=True)
                    return
                
                # 設定を保存（ファイル名のみ保存）
                self.reference_mode_config[channel_id] = {
                    "enabled": True,
                    "file_path": filename  # DMファイル名として保存
                }
                self.save_reference_mode_config()
                
                await interaction.response.send_message(f"✅ 参照モードを有効化しました。\n参照ファイル: {filename}\n文字数: {len(content):,}文字", ephemeral=True)
                print(f"[/reference] 参照モード有効化: channel={channel_id}, file={filename}")
                
            else:
                # ファイル名が指定されていない場合
                if channel_id in self.reference_mode_config and self.reference_mode_config[channel_id].get("enabled", False):
                    current_file = self.reference_mode_config[channel_id].get("file_path", "未設定")
                    await interaction.response.send_message(f"ℹ️ 参照モードは既に有効です。\n現在の参照ファイル: {current_file if current_file != '未設定' else '未設定'}\n\n新しいファイルを設定する場合は `/reference mode on [ファイル名]` で指定してください。", ephemeral=True)
                else:
                    await interaction.response.send_message("ℹ️ 参照モードをONにしますが、参照するファイルが設定されていません。\n`/reference mode on [ファイル名]` でファイルを指定してください。", ephemeral=True)
                    # ファイルなしで有効化
                    self.reference_mode_config[channel_id] = {
                        "enabled": True,
                        "file_path": ""
                    }
                    self.save_reference_mode_config()
        
        elif mode == "off":
            if channel_id in self.reference_mode_config:
                self.reference_mode_config[channel_id]["enabled"] = False
                self.save_reference_mode_config()
                await interaction.response.send_message("✅ 参照モードを無効化しました。", ephemeral=True)
                print(f"[/reference] 参照モード無効化: channel={channel_id}")
            else:
                await interaction.response.send_message("ℹ️ 参照モードは既に無効です。", ephemeral=True)
        
        elif mode == "status":
            if channel_id in self.reference_mode_config and self.reference_mode_config[channel_id].get("enabled", False):
                filename = self.reference_mode_config[channel_id].get("file_path", "")
                if filename:
                    # DMからファイル内容をチェック
                    content = await self.load_file_from_dm(interaction.user.id, filename)
                    if content is not None:
                        status_msg = f"✅ 参照モード: 有効\n参照ファイル: {filename}\n文字数: {len(content):,}文字"
                    else:
                        status_msg = f"⚠️ 参照モード: 有効\n参照ファイル: {filename} (DMに見つかりません)"
                else:
                    status_msg = "⚠️ 参照モード: 有効\n参照ファイル: 未設定"
            else:
                status_msg = "❌ 参照モード: 無効"
            
            await interaction.response.send_message(status_msg, ephemeral=True)
        
        elif mode == "reload":
            if channel_id in self.reference_mode_config and self.reference_mode_config[channel_id].get("enabled", False):
                filename = self.reference_mode_config[channel_id].get("file_path", "")
                if filename:
                    # DMからファイル内容をチェック
                    content = await self.load_file_from_dm(interaction.user.id, filename)
                    if content is not None:
                        await interaction.response.send_message("✅ 参照ファイルを再読み込みしました。", ephemeral=True)
                        print(f"[/reference] 参照ファイル再読み込み: channel={channel_id}, file={filename}")
                    else:
                        await interaction.response.send_message("⚠️ 参照ファイルがDMに見つかりません。", ephemeral=True)
                else:
                    await interaction.response.send_message("⚠️ 参照ファイルが設定されていません。", ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ 参照モードが無効です。", ephemeral=True)
        
        elif mode == "upload":
            # ファイルアップロード方法の案内
            embed = discord.Embed(
                title="📁 ファイルアップロード方法",
                description="以下の2つの方法でファイルをアップロードできます",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="🔹 方法1: 小さなファイル（4000文字以下）",
                value="下の「テキスト入力」ボタンでモーダルを開いてファイル内容を入力",
                inline=False
            )
            embed.add_field(
                name="🔹 方法2: 大きなファイル（制限なし）",
                value="このチャンネルで以下の形式でメッセージを送信:\n"
                      "```\n/upload:ファイル名.txt\n[ここにファイル内容を記載]\n```",
                inline=False
            )
            embed.add_field(
                name="📝 例：",
                value="```\n/upload:会議議事録.txt\n今日の会議内容:\n1. プロジェクトの進捗...\n2. 来週の予定...\n```",
                inline=False
            )
            
            view = UploadMethodView(self)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        elif mode == "list":
            # ユーザーのDMに保存されているファイル一覧を表示
            files = await self.list_files_in_dm(interaction.user.id)
            if files:
                embed = discord.Embed(
                    title="📁 保存済み参照ファイル一覧",
                    description="ボタンをクリックしてファイルを参照モードに設定できます",
                    color=discord.Color.blue()
                )
                
                # ファイル一覧をembedに追加
                file_list = ""
                for i, filename in enumerate(files, 1):
                    # ファイル内容の文字数を取得
                    content = await self.load_file_from_dm(interaction.user.id, filename)
                    char_count = len(content) if content else 0
                    file_list += f"{i}. **{filename}** ({char_count:,}文字)\n"
                
                embed.add_field(name="ファイル一覧", value=file_list, inline=False)
                embed.add_field(
                    name="📝 操作方法", 
                    value="• 下のボタンでファイルを参照モードに設定\n• `/reference delete <ファイル名>` でファイル削除", 
                    inline=False
                )
                
                # ファイル選択ビューを作成
                view = FileSelectionView(self, files, channel_id)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message("📁 DMに保存されている参照ファイルはありません。\n`/reference upload` でファイルをアップロードしてください。", ephemeral=True)
        
        elif mode == "delete":
            if filename:
                # ファイル名から拡張子を除去
                import os
                filename = os.path.splitext(filename)[0]
                
                # 指定されたファイルを削除
                success = await self.delete_file_from_dm(interaction.user.id, filename)
                if success:
                    await interaction.response.send_message(f"✅ ファイル '{filename}' を削除しました。", ephemeral=True)
                    
                    # 削除したファイルが現在参照中の場合、参照モードを無効化
                    if (channel_id in self.reference_mode_config and 
                        self.reference_mode_config[channel_id].get("file_path") == filename):
                        self.reference_mode_config[channel_id]["enabled"] = False
                        self.save_reference_mode_config()
                        await interaction.followup.send("ℹ️ 削除されたファイルが参照中だったため、参照モードを無効化しました。", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ ファイル '{filename}' が見つからないか、削除に失敗しました。", ephemeral=True)
            else:
                await interaction.response.send_message("❌ 削除するファイル名を指定してください。", ephemeral=True)
        

    @app_commands.command(name="voice_memo", description="ボイスメモ自動文字起こし機能の切り替え")
    @app_commands.describe(mode="on:有効化, off:無効化, status:状態確認")
    @app_commands.choices(mode=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="status", value="status")
    ])
    async def voice_memo(self, interaction: discord.Interaction, mode: str):
        """ボイスメモ自動文字起こし機能の管理"""
        print(f"[/voice_memo] 呼び出し: user={interaction.user}, mode={mode}")
        
        # 権限チェック（管理者のみ）
        if not interaction.user or not hasattr(interaction.user, 'guild_permissions') or not interaction.user.guild_permissions.administrator:  # type: ignore
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        
        if not interaction.channel or not hasattr(interaction.channel, 'id'):
            await interaction.response.send_message("❌ チャンネル情報を取得できません。", ephemeral=True)
            return
        
        channel_id = str(interaction.channel.id)  # type: ignore
        
        if mode == "on":
            self.voice_memo_config[channel_id] = {"enabled": True}
            self.save_voice_memo_config()
            await interaction.response.send_message("✅ ボイスメモ自動文字起こし機能を有効化しました。\n音声ファイルが投稿されると自動的に文字起こしされます。", ephemeral=True)
            print(f"[/voice_memo] ボイスメモ機能有効化: channel={channel_id}")
            
        elif mode == "off":
            if channel_id in self.voice_memo_config:
                self.voice_memo_config[channel_id]["enabled"] = False
                self.save_voice_memo_config()
                await interaction.response.send_message("✅ ボイスメモ自動文字起こし機能を無効化しました。", ephemeral=True)
                print(f"[/voice_memo] ボイスメモ機能無効化: channel={channel_id}")
            else:
                await interaction.response.send_message("ℹ️ ボイスメモ機能は既に無効です。", ephemeral=True)
                
        elif mode == "status":
            if channel_id in self.voice_memo_config and self.voice_memo_config[channel_id].get("enabled", False):
                await interaction.response.send_message("✅ ボイスメモ自動文字起こし機能: 有効\n\n対応形式: .ogg, .mp3, .wav, .m4a, .aac, .flac", ephemeral=True)
            else:
                await interaction.response.send_message("❌ ボイスメモ自動文字起こし機能: 無効", ephemeral=True)
        else:
            await interaction.response.send_message("使い方: /voice_memo <on|off|status>", ephemeral=True)

    @app_commands.command(name="ai_always", description="AI常時返信モード切り替え（always/disable/status）")
    @app_commands.describe(mode="always:有効化, disable:無効化, status:状態確認")
    async def ai_always(self, interaction: discord.Interaction, mode: str):
        print(f"[/ai_always] 呼び出し: user={interaction.user}, mode={mode}")
        
        # 即座に応答を延期（3秒制限回避）
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user or not hasattr(interaction.user, 'guild_permissions') or not interaction.user.guild_permissions.administrator:  # type: ignore
            await interaction.followup.send("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        if not interaction.channel or not hasattr(interaction.channel, 'id'):
            await interaction.followup.send("❌ チャンネル情報を取得できません。", ephemeral=True)
            return
        channel_id = interaction.channel.id  # type: ignore
        
        if mode == "always":
            if channel_id in self.always_reply_channels:
                await interaction.followup.send("ℹ️ このチャンネルは既に常時返信機能が有効です。", ephemeral=True)
                return
            self.always_reply_channels.add(channel_id)
            await self.save_always_reply_channels()
            print(f"[/ai_always] 常時返信を有効化: {channel_id}")
            await interaction.followup.send(f"✅ このチャンネルの常時AI返信モードを有効化しました。", ephemeral=True)
        elif mode == "disable":
            if channel_id not in self.always_reply_channels:
                await interaction.followup.send("ℹ️ このチャンネルは常時返信機能が有効ではありません。", ephemeral=True)
                return
            self.always_reply_channels.remove(channel_id)
            
            # Firestoreからも削除
            try:
                await asyncio.wait_for(
                    firestore_db.set_setting('always_reply', 'enabled', False, channel_id=channel_id), 
                    timeout=10.0
                )
                print(f"[/ai_always] 常時返信を無効化: {channel_id}")
            except Exception as e:
                print(f"[/ai_always] Firestore無効化エラー: {e}")
            
            await interaction.followup.send(f"✅ このチャンネルの常時AI返信モードを無効化しました。", ephemeral=True)
        elif mode == "status":
            status = "有効" if channel_id in self.always_reply_channels else "無効"
            await interaction.followup.send(f"このチャンネルの常時AI返信モード: {status}", ephemeral=True)
        else:
            await interaction.followup.send("使い方: /ai_always <always|disable|status>", ephemeral=True)

    @app_commands.command(name="ai_clear", description="あなたの会話履歴を削除します（統一システム）")
    @app_commands.describe(
        target="all: 全ての会話履歴, archive: アーカイブのみ"
    )
    async def ai_clear(self, interaction: discord.Interaction, target: str):
        print(f"[/ai_clear] 呼び出し: user={interaction.user}, target={target}")
        await interaction.response.defer(ephemeral=True)
        
        try:
            user_id = interaction.user.id
            
            # 統一会話システムの件数を確認
            conversation_history = await firestore_db.get_user_conversation_history(user_id, 1000)
            conversation_count = len(conversation_history)
            archived_conversations = await firestore_db.get_archived_conversations_new(user_id, 100)
            archive_count = len(archived_conversations)
            
            if target.lower() == "all":
                # 全ての会話履歴を削除
                if conversation_count == 0 and archive_count == 0:
                    await interaction.followup.send("📭 削除する会話履歴がありません。", ephemeral=True)
                    return
                
                deleted_conversations = await firestore_db.delete_user_conversations_new(user_id)
                deleted_archives = await firestore_db.delete_user_archives_new(user_id)
                total_deleted = deleted_conversations + deleted_archives
                
                message = f"🗑️ 全ての会話履歴を削除しました！\n"
                message += f"・削除した会話: {deleted_conversations}件\n"
                message += f"・削除したアーカイブ: {deleted_archives}件\n"
                message += f"・合計削除: {total_deleted}件"
                
            elif target.lower() == "archive":
                # アーカイブのみ削除
                if archive_count == 0:
                    await interaction.followup.send("📭 削除するアーカイブがありません。", ephemeral=True)
                    return
                
                deleted_archives = await firestore_db.delete_user_archives_new(user_id)
                
                message = f"🗑️ アーカイブを削除しました！\n"
                message += f"・削除したアーカイブ: {deleted_archives}件"
                
            else:
                await interaction.followup.send("❌ 無効な削除対象です。all, archive のいずれかを指定してください。", ephemeral=True)
                return
            
            await interaction.followup.send(message, ephemeral=True)
            print(f"[/ai_clear] 完了: ユーザー{user_id}, target={target}")
            
        except Exception as e:
            print(f"[/ai_clear] エラー: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send("❌ 会話履歴の削除中にエラーが発生しました。", ephemeral=True)

    @app_commands.command(name="ai_status", description="あなたの会話履歴の状況を確認します（統一システム）")
    async def ai_status(self, interaction: discord.Interaction):
        print(f"[/ai_status] 呼び出し: user={interaction.user}")
        await interaction.response.defer(ephemeral=True)
        
        try:
            user_id = interaction.user.id
            
            # 統一会話システムの件数を確認
            conversation_history = await firestore_db.get_user_conversation_history(user_id, 1000)
            conversation_count = len(conversation_history)
            
            # アーカイブ件数を確認
            archived_conversations = await firestore_db.get_archived_conversations_new(user_id, 100)
            archive_count = len(archived_conversations)
            
            # 最新の会話日時を確認
            last_date_str = "なし"
            if conversation_history:
                last_conversation = conversation_history[-1]
                last_timestamp = last_conversation.get('timestamp')
                if last_timestamp and hasattr(last_timestamp, 'strftime'):
                    last_date_str = last_timestamp.strftime('%Y/%m/%d %H:%M')
            
            # ユーザーとAIのメッセージ数を分別でカウント
            user_messages = len([msg for msg in conversation_history if msg.get('message_type') == 'user'])
            ai_messages = len([msg for msg in conversation_history if msg.get('message_type') == 'ai'])
            
            message = f"📊 **会話履歴の状況（統一システム）**\n\n"
            message += f"💬 **統一会話**: {conversation_count}件\n"
            message += f"・ユーザーメッセージ: {user_messages}件\n"
            message += f"・AIメッセージ: {ai_messages}件\n"
            message += f"📦 **アーカイブ**: {archive_count}件\n"
            message += f"🕐 **最終会話**: {last_date_str}\n\n"
            
            if conversation_history:
                message += "**最新の会話（最大5件）:**\n"
                for i, conv in enumerate(conversation_history[-5:], 1):
                    timestamp = conv.get('timestamp', '')
                    message_type = conv.get('message_type', 'user')
                    username = conv.get('username', 'Unknown')
                    content = conv.get('content', '')[:30]
                    
                    if hasattr(timestamp, 'strftime'):
                        time_str = timestamp.strftime('%m/%d %H:%M')
                    else:
                        time_str = str(timestamp)[:16]
                    
                    type_icon = "🤖" if message_type == 'ai' else "👤"
                    message += f"{i}. {type_icon} [{time_str}] {username}: {content}...\n"
            
            await interaction.followup.send(message, ephemeral=True)
            print(f"[/ai_status] 完了: ユーザー{user_id}, 現在{conversation_count}件, アーカイブ{archive_count}件")
            
        except Exception as e:
            print(f"[/ai_status] エラー: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send("❌ 会話履歴の確認中にエラーが発生しました。", ephemeral=True)

    @commands.command(name='gemini-status')
    async def gemini_status(self, ctx):
        if genai_initialized:
            embed = discord.Embed(
                title="✅ Gemini API 状態",
                description="Gemini API は正常に動作しています。",
                color=discord.Color.green()
            )
            embed.add_field(name="常時返信チャンネル", value=f"{len(self.always_reply_channels)}チャンネル", inline=True)
            
            # 参照モードの統計を追加
            active_reference_channels = sum(1 for config in self.reference_mode_config.values() if config.get("enabled", False))
            embed.add_field(name="参照モード有効チャンネル", value=f"{active_reference_channels}チャンネル", inline=True)
            
            await ctx.send(embed=embed)
        else:
            await ctx.send("❌ Gemini API は利用できません。APIキーを確認してください。")

    @app_commands.command(name="help", description="全コマンドの説明表を機能別に表示")
    async def help_command(self, interaction: discord.Interaction):
        # 基本コマンド
        basic_embed = discord.Embed(
            title="📋 基本コマンド",
            description="基本的な機能とヘルプコマンド",
            color=discord.Color.blue()
        )
        basic_embed.add_field(name="/help", value="全コマンドの説明表を表示", inline=False)
        basic_embed.add_field(name="@Bot名 質問", value="常時返信対象外のチャンネルでも、Botへのメンション後に文章を付けるとAIが返答します", inline=False)

        # AI会話機能
        ai_embed = discord.Embed(
            title="🤖 AI会話機能（管理者のみ）",
            description="常時返信モードの設定",
            color=discord.Color.green()
        )
        ai_embed.add_field(
            name="🔄 常時返信モード",
            value="• `/ai_always always` - 常時AI返信モード有効化\n"
                  "• `/ai_always disable` - 常時AI返信モード無効化\n"
                  "• `/ai_always status` - 常時AI返信モード状態確認",
            inline=False
        )
        
        # その他の機能
        other_embed = discord.Embed(
            title="🛠️ その他の機能",
            description="メッセージ管理やその他のユーティリティ機能",
            color=discord.Color.orange()
        )
        other_embed.add_field(
            name="🗑️ 自動削除機能（管理者のみ）",
            value="• `/delete <時間>` - 指定時間ごとに全メッセージ削除\n"
                  "• `/delete-now` - 即座に全メッセージ削除\n"
                  "• `/cancel-delete` - 自動削除キャンセル\n"
                  "• `/delete-status` - 削除設定確認",
            inline=False
        )
        other_embed.add_field(
            name="🎙️ ボイスコントロール機能",
            value="• `/mute_all` - 通話にいる全員をサーバーミュートします\n"
                  "• `/unmute_all` - 通話にいる全員のサーバーミュートを解除します\n"
                  "• ⚠️ 管理者権限が必要です",
            inline=False
        )
        steam_help = (
                  "• `/link profile_url:<SteamプロフィールURL>` - Steam連携。初回は所持ゲームを取得し、表示対象に設定済みのタグのロールを同期\n"
                  "• `/sync` - 連携済みSteamの所持ゲームと表示対象タグのロールを再同期\n"
                  "• `/recruit` - 「今すぐ」または日時を選択し、ゲームタグ・通知先・参加者を指定して募集作成\n"
                  "• 通知先はタグロールへのメンションまたは `@everyone` から選択\n"
                  "• 募集の「参加 (〇)」「未定 (△)」「不参加 (×)」ボタンで参加状況を登録\n"
                  "• `/game-tag-list` - タグ一覧を表示（非表示タグは管理者のみ確認可能）\n"
                  "• Steam連携は表示対象に設定済みのタグのロールだけを所持ゲームに応じて付与・解除（タグ一覧への追加は行いません）\n"
                  "• Steam所持ゲームの再取得と表示対象ロールの再同期は `/sync` で実行できます"
        )
        other_embed.add_field(name="🎮 Steam連携・ゲーム募集", value=steam_help, inline=False)
        if interaction.guild and (interaction.permissions.manage_roles or interaction.permissions.administrator):
            other_embed.add_field(
                name="🏷️ ゲームタグ管理（ロール管理権限が必要）",
                value="• `/game-tag-add <tag_name>` - 手動タグ（Minecraft、ボードゲーム等）を追加\n"
                      "• `/game-tag-add <tag_name> <match_keyword>` - Steamゲーム名に一致するタグを追加\n"
                      "• `/game-tag-manage` - Steamタグと手動タグの一覧・表示名・表示状態を管理。非表示タグの削除は削除済みフラグで保持されます\n"
                      "• ロール登録はタグパネルで本人が選択するか、Steam同期で所持が確認された場合に行います\n"
                      "• `/game-tag-manage` の「手動タグを追加」からもカスタムタグを登録可能。Steam取得タグの一覧追加は管理者が「Steamキャッシュからタグを追加」を押した場合のみ（初期状態は非表示）\n"
                      "• `/game-tag-manage` をチャット本文に入力しても管理画面を開けます\n"
                      "• `/game-tag-panel` - 実行したチャンネルに「タグ登録・更新」と「Steam連携」のパネルを設置\n"
                      "• タグ登録・更新を押すと個人用のタグ選択画面が開き、1ページ10件から選択。「選択を登録・更新」で反映（Steam以外が先、Steamゲームが続きます。ページ選択で移動）\n"
                      "• 登録済みタグは緑、登録解除を選択中は赤、未登録タグは灰色で表示\n"
                      "• Steam連携フォームに「1. Steamクライアントを起動」「2. 画面上部のアカウント名からプロフィールURLをコピー」の手順を表示",
                inline=False,
            )

        # ヘルプを1回の応答にまとめ、followupの失敗や表示漏れを避ける。
        await interaction.response.send_message(
            embeds=[basic_embed, ai_embed, other_embed],
            ephemeral=True,
        )

class FileUploadModal(discord.ui.Modal, title='📁 参照ファイルのアップロード'):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    filename = discord.ui.TextInput(
        label='ファイル名',
        placeholder='example.txt (拡張子を含めてください)',
        required=True,
        max_length=50
    )

    content = discord.ui.TextInput(
        label='ファイル内容',
        placeholder='参照したいテキスト内容をここに貼り付けてください',
        style=discord.TextStyle.long,
        required=True,
        max_length=4000
    )

    async def on_submit(self, interaction: discord.Interaction):
        filename = self.filename.value.strip()
        content = self.content.value
        
        # ファイル名の検証
        if not filename:
            await interaction.response.send_message("❌ ファイル名を入力してください。", ephemeral=True)
            return
        
        # 危険な文字の除去と拡張子の除去
        import re
        import os
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # 拡張子を除去
        filename = os.path.splitext(filename)[0]
        
        if not content.strip():
            await interaction.response.send_message("❌ ファイル内容を入力してください。", ephemeral=True)
            return
        
        # ファイルをDMに保存
        success = await self.cog.save_file_to_dm(interaction.user.id, filename, content)
        
        if success:
            embed = discord.Embed(
                title="✅ ファイルアップロード完了",
                description=f"**{filename}** をDMに保存しました。",
                color=discord.Color.green()
            )
            embed.add_field(name="ファイル名", value=filename, inline=True)
            embed.add_field(name="文字数", value=f"{len(content):,}文字", inline=True)
            embed.add_field(
                name="使用方法", 
                value=f"`/reference mode on {filename}` で参照モードに設定", 
                inline=False
            )
            embed.add_field(
                name="管理コマンド",
                value="• `/reference list` - 保存済みファイル一覧\n• `/reference delete <ファイル名>` - ファイル削除",
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("❌ ファイルの保存に失敗しました。", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"FileUploadModal エラー: {error}")
        await interaction.response.send_message("❌ ファイルアップロード中にエラーが発生しました。", ephemeral=True)

class UploadMethodView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)  # 5分でタイムアウト
        self.cog = cog
    
    @discord.ui.button(label='テキスト入力（4000文字以下）', style=discord.ButtonStyle.primary, emoji='📝')
    async def small_file_upload(self, interaction: discord.Interaction, button: discord.ui.Button):
        """小さなファイル用のモーダルを開く"""
        modal = FileUploadModal(self.cog)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label='使用方法を再表示', style=discord.ButtonStyle.secondary, emoji='❓')
    async def show_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        """使用方法を再表示"""
        embed = discord.Embed(
            title="📋 大きなファイルのアップロード方法",
            description="制限なしでファイルをアップロードできます",
            color=discord.Color.green()
        )
        embed.add_field(
            name="📝 手順",
            value="1. このチャンネルに以下の形式でメッセージを送信\n"
                  "2. 最初の行: `/upload:ファイル名.txt`\n"
                  "3. 2行目以降: ファイルの内容\n"
                  "4. メッセージ送信後、自動的にファイルが保存されます",
            inline=False
        )
        embed.add_field(
            name="📄 例：",
            value="```\n/upload:プロジェクト仕様書.txt\n# プロジェクト仕様書\n\n## 概要\nこのプロジェクトは...\n\n## 要件\n1. 機能A\n2. 機能B\n```",
            inline=False
        )
        embed.add_field(
            name="✏️ ファイル更新方法",
            value="既存ファイルを更新するには `/update:ファイル名.txt` を使用",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class FileSelectionView(discord.ui.View):
    def __init__(self, cog, files: List[str], channel_id: str):
        super().__init__(timeout=300)  # 5分でタイムアウト
        self.cog = cog
        self.files = files
        self.channel_id = channel_id
        
        # ファイルが5個以下の場合はボタン、それ以上の場合はセレクトメニューを使用
        if len(files) <= 5:
            self.create_buttons()
        else:
            self.create_select_menu()
    
    def create_buttons(self):
        """5個以下のファイル用ボタンを作成"""
        for filename in self.files:
            button = discord.ui.Button(
                label=filename[:80],  # ラベル長制限
                style=discord.ButtonStyle.primary,
                emoji="📄"
            )
            button.callback = self.create_button_callback(filename)
            self.add_item(button)
    
    def create_select_menu(self):
        """6個以上のファイル用セレクトメニューを作成"""
        options = []
        for filename in self.files:
            options.append(discord.SelectOption(
                label=filename[:100],  # ラベル長制限
                value=filename,
                emoji="📄"
            ))
        
        select = discord.ui.Select(
            placeholder="参照モードに設定するファイルを選択...",
            options=options
        )
        select.callback = self.select_callback
        self.add_item(select)
    
    def create_button_callback(self, filename: str):
        """ボタン用のコールバック関数を作成"""
        async def button_callback(interaction: discord.Interaction):
            await self.set_reference_file(interaction, filename)
        return button_callback
    
    async def select_callback(self, interaction: discord.Interaction):
        """セレクトメニューのコールバック"""
        filename = interaction.data['values'][0]
        print(f"[DEBUG] select_callback: selected filename='{filename}'")
        await self.set_reference_file(interaction, filename)
    
    async def set_reference_file(self, interaction: discord.Interaction, filename: str):
        """選択されたファイルを参照モードに設定"""
        try:
            print(f"[DEBUG] set_reference_file開始: filename='{filename}', user_id={interaction.user.id}")
            
            # DMからファイル内容の存在確認
            content = await self.cog.load_file_from_dm(interaction.user.id, filename)
            if content is None:
                print(f"[DEBUG] ファイル '{filename}' が見つかりませんでした")
                await interaction.response.send_message(f"❌ ファイル '{filename}' が見つかりません。", ephemeral=True)
                return
            
            # ファイルサイズチェック
            if len(content) > 100000:  # 約100KB相当
                await interaction.response.send_message(f"❌ ファイル '{filename}' が大きすぎます。より小さなファイルを使用してください。", ephemeral=True)
                return
            
            # 参照モード設定を保存
            self.cog.reference_mode_config[self.channel_id] = {
                "enabled": True,
                "file_path": filename
            }
            self.cog.save_reference_mode_config()
            
            # 成功メッセージ
            embed = discord.Embed(
                title="✅ 参照モード設定完了",
                description=f"**{filename}** を参照モードに設定しました。",
                color=discord.Color.green()
            )
            embed.add_field(name="ファイル名", value=filename, inline=True)
            embed.add_field(name="文字数", value=f"{len(content):,}文字", inline=True)
            embed.add_field(name="チャンネル", value=f"<#{interaction.channel.id}>", inline=True)
            embed.add_field(
                name="📝 使用方法",
                value="このチャンネルでAIと会話すると、自動的にファイル内容が文脈に含まれます。",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # 元のメッセージのビューを無効化
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(view=self)
            
            print(f"[FileSelection] 参照モード設定: channel={self.channel_id}, file={filename}, user={interaction.user.id}")
            
        except Exception as e:
            print(f"FileSelection エラー: {e}")
            await interaction.response.send_message("❌ ファイル設定中にエラーが発生しました。", ephemeral=True)
    
    async def on_timeout(self):
        """タイムアウト時にボタンを無効化"""
        for item in self.children:
            item.disabled = True

    # === Firestoreベースのファイル処理メソッド ===
    async def handle_file_upload_message(self, message):
        """Firestoreベースのファイルアップロード処理"""
        try:
            content = message.content
            lines = content.split('\n', 1)
            
            if len(lines) < 2:
                await message.reply("❌ ファイル内容が指定されていません。\n使用方法: `/upload:ファイル名.txt`\n（次の行からファイル内容を入力）")
                return
            
            # ファイル名を取得
            filename_line = lines[0]
            if not filename_line.startswith('/upload:'):
                await message.reply("❌ 正しい形式で入力してください。\n使用方法: `/upload:ファイル名.txt`")
                return
            
            filename = filename_line[8:].strip()  # '/upload:' を除去
            if not filename:
                await message.reply("❌ ファイル名を指定してください。")
                return
            
            file_content = lines[1]
            
            # Firestoreにファイルを保存
            file_id = await firestore_db.save_user_file(
                user_id=message.author.id,
                filename=filename,
                content=file_content,
                channel_id=message.channel.id
            )
            
            if file_id:
                embed = discord.Embed(
                    title="📁 ファイルアップロード完了",
                    description=f"**{filename}** をFirestoreに保存しました。",
                    color=discord.Color.green()
                )
                embed.add_field(name="ファイルID", value=file_id, inline=True)
                embed.add_field(name="ファイル名", value=filename, inline=True)
                embed.add_field(name="文字数", value=f"{len(file_content):,}文字", inline=True)
                embed.add_field(name="投稿者", value=message.author.display_name, inline=True)
                
                # ファイル内容のプレビュー（最初の200文字）
                preview = file_content[:200]
                if len(file_content) > 200:
                    preview += "..."
                embed.add_field(
                    name="📝 内容プレビュー",
                    value=f"```\n{preview}\n```",
                    inline=False
                )
                
                await message.reply(embed=embed)
                
                # 元のメッセージを削除（オプション）
                try:
                    await message.delete()
                except:
                    pass  # 削除に失敗しても無視
                    
            else:
                await message.reply("❌ ファイルの保存に失敗しました。")
                
        except Exception as e:
            print(f"[ファイルアップロード] エラー: {e}")
            await message.reply("❌ ファイルアップロード中にエラーが発生しました。")
    
    async def handle_file_update_message(self, message):
        """Firestoreベースのファイル更新処理"""
        try:
            content = message.content
            lines = content.split('\n', 1)
            
            if len(lines) < 2:
                await message.reply("❌ ファイル内容が指定されていません。\n使用方法: `/update:ファイル名.txt`\n（次の行からファイル内容を入力）")
                return
            
            # ファイル名を取得
            filename_line = lines[0]
            if not filename_line.startswith('/update:'):
                await message.reply("❌ 正しい形式で入力してください。\n使用方法: `/update:ファイル名.txt`")
                return
            
            filename = filename_line[8:].strip()  # '/update:' を除去
            if not filename:
                await message.reply("❌ ファイル名を指定してください。")
                return
            
            file_content = lines[1]
            
            # 既存のファイルを検索
            user_files = await firestore_db.get_user_files(user_id=message.author.id)
            existing_file = None
            
            for file_data in user_files:
                if file_data['filename'] == filename:
                    existing_file = file_data
                    break
            
            if existing_file:
                # 既存ファイルを削除して新しいものを作成
                await firestore_db.delete_user_file(existing_file['id'])
                
                # 新しいファイルを保存
                file_id = await firestore_db.save_user_file(
                    user_id=message.author.id,
                    filename=filename,
                    content=file_content,
                    channel_id=message.channel.id
                )
                
                if file_id:
                    embed = discord.Embed(
                        title="🔄 ファイル更新完了",
                        description=f"**{filename}** を更新しました。",
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="新ファイルID", value=file_id, inline=True)
                    embed.add_field(name="ファイル名", value=filename, inline=True)
                    embed.add_field(name="文字数", value=f"{len(file_content):,}文字", inline=True)
                    
                    await message.reply(embed=embed)
                else:
                    await message.reply("❌ ファイルの更新に失敗しました。")
            else:
                await message.reply(f"❌ **{filename}** というファイルが見つかりません。\n新しいファイルとして作成する場合は `/upload:{filename}` を使用してください。")
                
        except Exception as e:
            print(f"[ファイル更新] エラー: {e}")
            await message.reply("❌ ファイル更新中にエラーが発生しました。")

    # === Firestoreベースの参照ファイル取得 ===
    async def get_reference_content_from_firestore(self, channel_id: int, user_id: int):
        """Firestoreから参照モード用のファイル内容を取得"""
        try:
            # チャンネル設定から参照ファイル情報を取得
            config = await firestore_db.get_channel_config(channel_id, 'reference_mode')
            
            if not config or not config.get('enabled', False):
                return None
            
            file_id = config.get('file_id')
            if not file_id:
                return "⚠️ 参照ファイルIDが設定されていません。"
            
            # Firestoreからファイル内容を取得
            content = await firestore_db.get_file_content(file_id)
            
            if content is None:
                return "⚠️ 参照ファイルが見つかりませんでした。ファイルが削除されている可能性があります。"
            
            return content
            
        except Exception as e:
            print(f"[参照ファイル取得] エラー: {e}")
            return f"⚠️ 参照ファイルの取得中にエラーが発生しました: {str(e)}"

async def setup(bot):
    await bot.add_cog(GeminiChat(bot)) 
