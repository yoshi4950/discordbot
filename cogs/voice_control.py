import discord
from discord.ext import commands
from discord import app_commands


class VoiceControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="mute_all", description="通話にいる全員をサーバーミュートします")
    async def mute_all(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ ボイスチャンネルに参加してからこのコマンドを使用してください。", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        
        if not voice_channel:
            await interaction.response.send_message("❌ ボイスチャンネルが見つかりませんでした。", ephemeral=True)
            return
        
        members_muted = []
        members_already_muted = []
        errors = []
        
        for member in voice_channel.members:
            if member.bot:
                continue
                
            try:
                # member.voiceがNoneの場合やmute状態が不明な場合の処理を追加
                if member.voice is None:
                    errors.append(f"{member.display_name}（ボイス状態が取得できません）")
                    continue
                
                if not member.voice.mute:
                    await member.edit(mute=True, reason=f"{interaction.user}によって全体ミュートが実行されました")
                    members_muted.append(member.display_name)
                else:
                    members_already_muted.append(member.display_name)
            except discord.Forbidden:
                errors.append(f"{member.display_name}（権限不足）")
            except discord.HTTPException as e:
                errors.append(f"{member.display_name}（エラー: {e}）")
            except Exception as e:
                errors.append(f"{member.display_name}（予期しないエラー: {e}）")
        
        response_parts = []
        
        if members_muted:
            response_parts.append(f"🔇 **ミュートしました:** {', '.join(members_muted)}")
        
        if members_already_muted:
            response_parts.append(f"ℹ️ **既にミュート済み:** {', '.join(members_already_muted)}")
        
        if errors:
            response_parts.append(f"❌ **ミュートできませんでした:** {', '.join(errors)}")
        
        if not members_muted and not members_already_muted and not errors:
            response_parts.append("❓ ミュート対象のメンバーが見つかりませんでした。")
        
        await interaction.response.send_message("\n".join(response_parts))

    @app_commands.command(name="unmute_all", description="通話にいる全員のサーバーミュートを解除します")
    async def unmute_all(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ ボイスチャンネルに参加してからこのコマンドを使用してください。", ephemeral=True)
            return
        
        voice_channel = interaction.user.voice.channel
        
        if not voice_channel:
            await interaction.response.send_message("❌ ボイスチャンネルが見つかりませんでした。", ephemeral=True)
            return
        
        members_unmuted = []
        members_already_unmuted = []
        errors = []
        
        for member in voice_channel.members:
            if member.bot:
                continue
                
            try:
                # member.voiceがNoneの場合やmute状態が不明な場合の処理を追加
                if member.voice is None:
                    errors.append(f"{member.display_name}（ボイス状態が取得できません）")
                    continue
                    
                if member.voice.mute:
                    await member.edit(mute=False, reason=f"{interaction.user}によって全体ミュート解除が実行されました")
                    members_unmuted.append(member.display_name)
                else:
                    members_already_unmuted.append(member.display_name)
            except discord.Forbidden:
                errors.append(f"{member.display_name}（権限不足）")
            except discord.HTTPException as e:
                errors.append(f"{member.display_name}（エラー: {e}）")
            except Exception as e:
                errors.append(f"{member.display_name}（予期しないエラー: {e}）")
        
        response_parts = []
        
        if members_unmuted:
            response_parts.append(f"🔊 **ミュート解除しました:** {', '.join(members_unmuted)}")
        
        if members_already_unmuted:
            response_parts.append(f"ℹ️ **既にミュート解除済み:** {', '.join(members_already_unmuted)}")
        
        if errors:
            response_parts.append(f"❌ **ミュート解除できませんでした:** {', '.join(errors)}")
        
        if not members_unmuted and not members_already_unmuted and not errors:
            response_parts.append("❓ ミュート解除対象のメンバーが見つかりませんでした。")
        
        await interaction.response.send_message("\n".join(response_parts))


async def setup(bot):
    await bot.add_cog(VoiceControl(bot))