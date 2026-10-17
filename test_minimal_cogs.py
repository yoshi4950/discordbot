#!/usr/bin/env python3
"""
最小テストボット - cogsが読み込まれて応答するかテスト
"""
import os
from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands
import asyncio

# 必要最小限のimport
from cogs.gemini_chat import GeminiChat

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is ready. Logged in as {bot.user.name} (ID: {bot.user.id})")
    
    # スラッシュコマンドの同期
    try:
        synced = await bot.tree.sync()
        print(f"[テストボット] スラッシュコマンドを同期しました: {len(synced)}個")
        for command in synced:
            print(f"  - /{command.name}: {command.description}")
    except Exception as e:
        print(f"❌ コマンド同期エラー: {e}")

async def main():
    print("🔍 最小テストボット開始...")
    
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN環境変数が設定されていません")
        return
        
    print("✅ Discord BOT TOKEN確認済み")
    
    # GeminiChatのみ読み込み
    print("📦 GeminiChatを読み込み中...")
    try:
        await bot.add_cog(GeminiChat(bot))
        print("✅ GeminiChat cog読み込み完了")
    except Exception as e:
        print(f"❌ GeminiChat cog読み込みエラー: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("🚀 Discord接続開始...")
    
    try:
        await bot.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        print(f"❌ Bot起動エラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())