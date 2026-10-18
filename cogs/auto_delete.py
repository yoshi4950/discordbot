import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import os


async def _send_temporary_followup(interaction: discord.Interaction, *, content=None, embed=None, delay: int = 5):
    """完了通知を短時間だけ表示し、削除対象チャンネルに残さない。"""
    message = await interaction.followup.send(content=content, embed=embed, wait=True)

    async def remove_later():
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    asyncio.create_task(remove_later())

class AutoDelete(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.delete_tasks = {}  # チャンネルID: {メッセージID: 削除時刻} の辞書
        self.pending_delete = None  # 削除確認待ちのチャンネルID
        print("✅ AutoDeleteバックグラウンドタスク開始")
        self.check_delete_tasks.start()

    def cog_unload(self):
        """Cogがアンロードされる時にタスクを停止"""
        self.check_delete_tasks.cancel()

    @app_commands.command(name='delete-now', description='即座にチャンネル内の全メッセージを削除します（管理者のみ）')
    async def delete_now(self, interaction: discord.Interaction):
        """即座にチャンネル内の全メッセージを削除するコマンド"""
        # 管理者権限チェック
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return

        channel = interaction.channel
        
        # 確認メッセージを送信
        embed = discord.Embed(
            title="⚠️ 即座削除の確認",
            description=f"**{channel.mention}** の全メッセージを即座に削除しますか？",
            color=discord.Color.red()
        )
        embed.add_field(name="⚠️ 注意", value="この操作は取り消しできません！", inline=False)
        embed.add_field(name="確認方法", value="`/confirm-delete` を入力して削除を実行", inline=False)
        embed.add_field(name="キャンセル", value="何も入力しない場合は自動的にキャンセルされます", inline=False)
        
        await interaction.response.send_message(embed=embed)
        
        # 確認待ちの状態を保存
        self.pending_delete = channel.id

    @app_commands.command(name='confirm-delete', description='削除の確認を実行します（管理者のみ）')
    async def confirm_delete(self, interaction: discord.Interaction):
        """削除の確認を実行するコマンド"""
        # 管理者権限チェック
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return

        channel = interaction.channel
        
        # 確認待ちのチャンネルかチェック
        if self.pending_delete != channel.id:
            await interaction.response.send_message("❌ 削除の確認が行われていません。`/delete-now` を先に実行してください。", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # 削除対象のメッセージ数を確認
            message_count = 0
            async for message in channel.history(limit=None):
                message_count += 1  # Botのメッセージも含める

            if message_count == 0:
                await _send_temporary_followup(interaction, content="📝 削除対象のメッセージが見つかりません。")
                self.pending_delete = None
                return

            # 削除実行の確認メッセージ
            embed = discord.Embed(
                title="🗑️ 削除実行",
                description=f"**{channel.mention}** の **{message_count}件** のメッセージを削除します。",
                color=discord.Color.orange()
            )
            embed.add_field(name="削除開始", value="削除を開始します...", inline=False)
            
            await interaction.followup.send(embed=embed)

            # メッセージを削除
            deleted_count = 0
            async for message in channel.history(limit=None):
                try:
                    await message.delete()
                    deleted_count += 1
                    # レート制限を避けるため、より長い間隔で削除
                    await asyncio.sleep(1.0)  # 1秒間隔に変更
                except discord.Forbidden:
                    # 削除権限がない場合
                    pass
                except discord.NotFound:
                    # メッセージが既に削除されている場合
                    pass
                except Exception as e:
                    print(f"メッセージ削除エラー: {e}")

            # 削除完了メッセージ
            embed = discord.Embed(
                title="✅ 削除完了",
                description=f"**{channel.mention}** の削除が完了しました。",
                color=discord.Color.green()
            )
            embed.add_field(name="削除されたメッセージ数", value=f"{deleted_count}件", inline=True)
            
            await _send_temporary_followup(interaction, embed=embed)
            
            # 確認待ち状態をクリア
            self.pending_delete = None
            
        except discord.Forbidden:
            await _send_temporary_followup(interaction, content="❌ メッセージの削除権限がありません。")
            self.pending_delete = None
        except Exception as e:
            await _send_temporary_followup(interaction, content=f"❌ エラーが発生しました: {e}")
            self.pending_delete = None

    @app_commands.command(name='delete', description='指定した時間ごとにチャンネル内の全メッセージを繰り返し削除します（管理者のみ）')
    @app_commands.describe(hours='削除間隔（時間）')
    async def delete(self, interaction: discord.Interaction, hours: int):
        """指定した時間（時間）ごとにチャンネル内の全メッセージを繰り返し削除するコマンド"""
        # 管理者権限チェック
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return

        # 時間の妥当性チェック
        if hours <= 0 or hours > 168:  # 最大1週間（168時間）
            await interaction.response.send_message("❌ 削除間隔は1時間以上168時間（1週間）以下で指定してください。", ephemeral=True)
            return

        channel = interaction.channel
        first_delete_time = datetime.now() + timedelta(hours=hours)
        
        # チャンネルの自動削除設定を保存
        if not hasattr(self, 'auto_delete_channels'):
            self.auto_delete_channels = {}
        
        self.auto_delete_channels[channel.id] = {
            'interval_hours': hours,
            'next_delete_time': first_delete_time,
            'enabled': True
        }

        # チャンネルトピックを更新して削除間隔を表示
        try:
            current_topic = channel.topic or ""
            # 既存の自動削除情報を削除
            lines = current_topic.split('\n')
            filtered_lines = [line for line in lines if not line.startswith('🗑️ 自動削除:')]
            
            # 新しい自動削除情報を追加
            auto_delete_info = f"🗑️ 自動削除: {hours}時間ごとに全メッセージが削除されます"
            if filtered_lines:
                new_topic = '\n'.join(filtered_lines) + '\n' + auto_delete_info
            else:
                new_topic = auto_delete_info
            
            await channel.edit(topic=new_topic)
        except discord.Forbidden:
            # チャンネル編集権限がない場合はスキップ
            pass
        except Exception as e:
            print(f"チャンネルトピック更新エラー: {e}")

        # 確認メッセージを送信
        embed = discord.Embed(
            title="🔄 自動削除スケジュール設定",
            description=f"**{channel.mention}** のメッセージを **{hours}時間ごと** に繰り返し削除するように設定しました。",
            color=discord.Color.orange()
        )
        embed.add_field(name="削除間隔", value=f"{hours}時間", inline=True)
        embed.add_field(name="初回削除予定時刻", value=f"<t:{int(first_delete_time.timestamp())}:F>", inline=True)
        embed.add_field(name="初回削除予定時刻（相対）", value=f"<t:{int(first_delete_time.timestamp())}:R>", inline=True)
        embed.add_field(name="対象", value="チャンネル内の全メッセージ（Botのメッセージも含む）", inline=False)
        embed.add_field(name="チャンネルトピック", value="✅ 削除間隔がチャンネルトピックに表示されます", inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='cancel-delete', description='チャンネルの自動削除スケジュールをキャンセルします（管理者のみ）')
    async def cancel_delete(self, interaction: discord.Interaction):
        """チャンネルの自動削除スケジュールをキャンセルするコマンド"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return

        channel_id = interaction.channel.id
        if hasattr(self, 'auto_delete_channels') and channel_id in self.auto_delete_channels:
            interval = self.auto_delete_channels[channel_id]['interval_hours']
            del self.auto_delete_channels[channel_id]
            
            # チャンネルトピックから自動削除情報を除去
            try:
                current_topic = interaction.channel.topic or ""
                lines = current_topic.split('\n')
                filtered_lines = [line for line in lines if not line.startswith('🗑️ 自動削除:')]
                new_topic = '\n'.join(filtered_lines).strip()
                
                await interaction.channel.edit(topic=new_topic if new_topic else None)
            except discord.Forbidden:
                # チャンネル編集権限がない場合はスキップ
                pass
            except Exception as e:
                print(f"チャンネルトピック更新エラー: {e}")

            embed = discord.Embed(
                title="✅ 自動削除スケジュールキャンセル",
                description=f"**{interaction.channel.mention}** の自動削除スケジュールをキャンセルしました。",
                color=discord.Color.green()
            )
            embed.add_field(name="キャンセルされた設定", value=f"削除間隔: {interval}時間", inline=True)
            
            await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(
                title="❌ 自動削除スケジュールが見つかりません",
                description=f"**{interaction.channel.mention}** には自動削除スケジュールが設定されていません。",
                color=discord.Color.red()
            )
            
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name='delete-status', description='現在の自動削除スケジュールの状態を表示します（管理者のみ）')
    async def delete_status(self, interaction: discord.Interaction):
        """現在の自動削除スケジュールの状態を表示するコマンド"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return

        channel_id = interaction.channel.id
        
        if hasattr(self, 'auto_delete_channels') and channel_id in self.auto_delete_channels:
            config = self.auto_delete_channels[channel_id]
            interval = config['interval_hours']
            next_delete = config['next_delete_time']
            enabled = config['enabled']
            
            embed = discord.Embed(
                title="📊 自動削除スケジュール状態",
                description=f"**{interaction.channel.mention}** の自動削除設定",
                color=discord.Color.blue()
            )
            embed.add_field(name="状態", value="✅ 有効" if enabled else "❌ 無効", inline=True)
            embed.add_field(name="削除間隔", value=f"{interval}時間", inline=True)
            embed.add_field(name="次回削除予定時刻", value=f"<t:{int(next_delete.timestamp())}:F>", inline=True)
            embed.add_field(name="次回削除予定時刻（相対）", value=f"<t:{int(next_delete.timestamp())}:R>", inline=True)
            
            await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(
                title="📊 自動削除スケジュール状態",
                description=f"**{interaction.channel.mention}** には自動削除スケジュールが設定されていません。",
                color=discord.Color.gray()
            )
            
            await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=1)  # 1分ごとにチェック
    async def check_delete_tasks(self):
        """削除時刻になったチャンネルの全メッセージを削除するバックグラウンドタスク"""
        if not hasattr(self, 'auto_delete_channels'):
            return
            
        current_time = datetime.now()
        channels_to_remove = []
        
        for channel_id, config in self.auto_delete_channels.items():
            if not config['enabled']:
                continue
                
            if current_time >= config['next_delete_time']:
                try:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        # チャンネル内の全メッセージを削除
                        deleted_count = 0
                        async for message in channel.history(limit=None):
                            try:
                                await message.delete()
                                deleted_count += 1
                                # レート制限を避けるため、間隔を空ける
                                await asyncio.sleep(1.0)
                            except discord.NotFound:
                                # メッセージが既に削除されている場合
                                pass
                            except discord.Forbidden:
                                # 削除権限がない場合
                                pass
                            except Exception as e:
                                print(f"メッセージ削除エラー: {e}")
                        
                        if deleted_count > 0:
                            print(f"チャンネル {channel.name} で {deleted_count} 件のメッセージを削除しました。")
                            
                            # 次回の削除時刻を設定
                            next_delete_time = current_time + timedelta(hours=config['interval_hours'])
                            config['next_delete_time'] = next_delete_time
                            
                            # チャンネルトピックを更新して次回削除時刻を表示
                            try:
                                current_topic = channel.topic or ""
                                lines = current_topic.split('\n')
                                filtered_lines = [line for line in lines if not line.startswith('🗑️ 自動削除:')]
                                
                                # 次回削除時刻を含む情報を追加
                                auto_delete_info = f"🗑️ 自動削除: {config['interval_hours']}時間ごとに削除 (次回: <t:{int(next_delete_time.timestamp())}:R>)"
                                if filtered_lines:
                                    new_topic = '\n'.join(filtered_lines) + '\n' + auto_delete_info
                                else:
                                    new_topic = auto_delete_info
                                
                                await channel.edit(topic=new_topic)
                            except discord.Forbidden:
                                # チャンネル編集権限がない場合はスキップ
                                pass
                            except Exception as e:
                                print(f"チャンネルトピック更新エラー: {e}")
                            
                            # 定刻実行時はチャンネルに通知を残さず、削除処理のみ行う。
                
                except Exception as e:
                    print(f"チャンネル {channel_id} のメッセージ削除中にエラー: {e}")
                    # エラーが発生した場合、チャンネルを削除対象から除外
                    channels_to_remove.append(channel_id)
        
        # エラーが発生したチャンネルを削除対象から除外
        for channel_id in channels_to_remove:
            if channel_id in self.auto_delete_channels:
                del self.auto_delete_channels[channel_id]

    @check_delete_tasks.before_loop
    async def before_check_delete_tasks(self):
        """タスク開始前の処理"""
        await self.bot.wait_until_ready() 
