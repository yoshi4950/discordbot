#!/usr/bin/env python3
"""
超シンプルなデバッグボット
"""
import os
from dotenv import load_dotenv
load_dotenv()
import discord
from discord.ext import commands

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot起動完了: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"コマンド同期完了: {len(synced)}個")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

@bot.tree.command(name="test", description="テストコマンド")
async def test_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("✅ テスト成功！")

if __name__ == "__main__":
    print("シンプルボット開始...")
    bot.run(DISCORD_BOT_TOKEN)