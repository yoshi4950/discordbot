#!/usr/bin/env python3
"""
デバッグ用 - 完全最小構成Discord Bot
"""
import os
from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands
import asyncio

print("🔍 デバッグbot開始")

# 環境変数取得
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
print(f"✅ Token確認: {'有効' if DISCORD_BOT_TOKEN else '無効'}")

# Botセットアップ
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
print("✅ Bot作成完了")

@bot.event
async def on_ready():
    print(f"🎉 Bot起動成功! {bot.user} (ID: {bot.user.id})")
    
    # スラッシュコマンド同期
    try:
        await bot.tree.sync()
        print("✅ スラッシュコマンド同期完了")
    except Exception as e:
        print(f"❌ スラッシュコマンド同期エラー: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    print(f"📝 メッセージ受信: {message.author}: {message.content}")
    
    if message.content == "ping":
        await message.channel.send("pong!")
    elif message.content == "test":
        await message.channel.send("🔧 デバッグbot動作中!")

@bot.tree.command(name="test", description="デバッグ用テストコマンド")
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message("🔧 デバッグbot正常動作!")

async def main():
    print("🚀 Discord接続開始...")
    
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKENが設定されていません")
        return
    
    try:
        await bot.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        print(f"❌ Bot起動エラー: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())