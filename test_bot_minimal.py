#!/usr/bin/env python3
"""
最小構成でのDiscord Bot起動テスト
"""
import os
from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands
import asyncio

# 環境変数取得
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Botセットアップ
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot起動成功! {bot.user} (ID: {bot.user.id})")
    print("🎉 最小構成テスト完了")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    if message.content == "ping":
        await message.channel.send("pong!")

async def main():
    if not DISCORD_BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKENが設定されていません")
        return
    
    print("🚀 最小構成Bot起動中...")
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())