#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot接続成功: {bot.user} (ID: {bot.user.id})")
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ スラッシュコマンド同期: {len(synced)}個")
        for cmd in synced:
            print(f"  - /{cmd.name}: {cmd.description}")
    except Exception as e:
        print(f"❌ スラッシュコマンド同期エラー: {e}")

@bot.tree.command(name="test", description="最小テストコマンド")
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message("🔧 最小Bot正常動作！")

if __name__ == "__main__":
    print("🚀 最小Bot起動中...")
    bot.run(DISCORD_BOT_TOKEN)