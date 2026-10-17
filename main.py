import os
from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands
import asyncio
from cogs.gemini_chat import GeminiChat
from cogs.auto_delete import AutoDelete
from cogs.voice_control import VoiceControl
from cogs.game_schedule import GameSchedule  # 追加：今回作成するゲーム・スケジュール管理Cog
from flask import Flask, jsonify
import threading
import time
import sys
from datetime import datetime
import aiohttp

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

flask_app = Flask(__name__)

bot_start_time = time.time()
last_heartbeat = time.time()


@flask_app.route('/')
def health_check():
    """Koyeb用のヘルスチェックエンドポイント"""
    global last_heartbeat
    last_heartbeat = time.time()

    uptime = time.time() - bot_start_time
    bot_status = "online" if bot.is_ready() else "offline"

    health_data = {
        "status": "ok",
        "message": "Discord Bot is running",
        "bot_status": bot_status,
        "uptime_seconds": round(uptime, 2),
        "python_version": sys.version,
        "timestamp": datetime.now().isoformat(),
        "last_heartbeat": datetime.fromtimestamp(last_heartbeat).isoformat()
    }

    return jsonify(health_data)


def run_flask():
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 8000))
    flask_app.run(host=host, port=port)


@bot.event
async def on_ready():
    global last_heartbeat
    last_heartbeat = time.time()

    if bot.user is not None:
        print(f"✅ Bot is ready. Logged in as {bot.user} (ID: {bot.user.id})")
    else:
        print("Bot is ready. (bot.user is None)")

    try:
        synced = await bot.tree.sync()
        print(f"[main.py] スラッシュコマンドを同期しました: {len(synced)}個")
        for cmd in synced:
            print(f"  - /{cmd.name}: {cmd.description}")
    except Exception as e:
        print(f"[main.py] スラッシュコマンド同期エラー: {e}")
        import traceback
        traceback.print_exc()

    if not any(t.get_name() == "periodic_health_check" for t in asyncio.all_tasks() if t.get_name()):
        asyncio.create_task(periodic_health_check(), name="periodic_health_check")
        print("✅ ヘルスチェックタスク開始")


@bot.tree.command(name="test", description="デバッグ用テストコマンド")
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message("🔧 main.py デバッグ正常動作!")


async def periodic_health_check():
    """5分ごとに自分自身のヘルスチェックエンドポイントにリクエストを送信"""
    await bot.wait_until_ready()

    port = os.getenv('PORT', 8000)
    health_url = f"http://localhost:{port}/"

    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(health_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        print(f"[ヘルスチェック] OK - Bot状態: {data.get('bot_status')}, 稼働時間: {data.get('uptime_seconds')}秒")
                    else:
                        print(f"[ヘルスチェック] WARNING - HTTPステータス: {response.status}")
        except asyncio.TimeoutError:
            print("[ヘルスチェック] ERROR - タイムアウト")
        except Exception as e:
            print(f"[ヘルスチェック] ERROR - {e}")

        await asyncio.sleep(300)


async def main():
    print("🔍 main()関数開始")

    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKENが設定されていません。")
        raise SystemExit(1)
    print("✅ Discord BOT TOKEN確認済み")

    print("📦 Cogを読み込み中...")

    try:
        print("🔄 GeminiChat cog初期化中...")
        await asyncio.wait_for(bot.add_cog(GeminiChat(bot)), timeout=10.0)
        print("✅ GeminiChat cog読み込み完了")
    except Exception as e:
        print(f"❌ GeminiChat cog読み込みエラー: {e}")

    try:
        print("🔄 AutoDelete cog初期化中...")
        await asyncio.wait_for(bot.add_cog(AutoDelete(bot)), timeout=5.0)
        print("✅ AutoDelete cog読み込み完了")
    except Exception as e:
        print(f"❌ AutoDelete cog読み込みエラー: {e}")

    try:
        print("🔄 VoiceControl cog初期化中...")
        await asyncio.wait_for(bot.add_cog(VoiceControl(bot)), timeout=5.0)
        print("✅ VoiceControl cog読み込み完了")
    except Exception as e:
        print(f"❌ VoiceControl cog読み込みエラー: {e}")

    try:
        print("🔄 GameSchedule cog初期化中...")
        await asyncio.wait_for(bot.add_cog(GameSchedule(bot)), timeout=5.0)
        print("✅ GameSchedule cog読み込み完了")
    except Exception as e:
        print(f"❌ GameSchedule cog読み込みエラー: {e}")
        import traceback
        traceback.print_exc()

    print("🚀 Discord接続開始...")

    try:
        await bot.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        print(f"❌ Bot起動エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flaskサーバー開始")

    asyncio.run(main())