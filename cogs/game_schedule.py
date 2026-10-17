"""Steam連携とゲーム募集機能。既存のFirestoreクライアントを利用する。"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import firestore

from firestore_db import firestore_db


STEAM_API = "https://api.steampowered.com"
USERS = "game_schedule_users"
GAMES = "game_schedule_games"
TAGS = "game_schedule_tags"
SCHEDULES = "game_schedules"


def _db():
    return firestore_db.db


def _tag_is_visible(tag: dict) -> bool:
    """旧Firestoreデータで公開状態が文字列になっている場合も扱う。"""
    value = tag.get("is_visible", True)
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return value is True or value == 1


def _schedule_embed(data: dict) -> discord.Embed:
    def mentions(key: str) -> str:
        return "\n".join(f"<@{user_id}>" for user_id in data.get(key, [])) or "（なし）"

    embed = discord.Embed(title=f"🎮 {data['game_name']} の募集", color=0x5865F2)
    embed.add_field(name="📅 日付", value=data["date_str"], inline=False)
    embed.add_field(name="🎮 募集内容", value=data["game_name"], inline=False)
    embed.add_field(name="📝 備考", value=data.get("memo") or "（なし）", inline=False)
    embed.add_field(name="〇 参加希望", value=mentions("yes_users"), inline=True)
    embed.add_field(name="△ 未定・不確定", value=mentions("maybe_users"), inline=True)
    embed.add_field(name="× 参加無し", value=mentions("no_users"), inline=True)
    return embed


def _cast_vote(message_id: int, user_id: int, status: str) -> dict | None:
    db = _db()
    ref = db.collection(SCHEDULES).document(str(message_id))
    transaction = db.transaction()

    @firestore.transactional
    def update(transaction):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        groups = {key: list(data.get(key, [])) for key in ("yes_users", "maybe_users", "no_users")}
        for users in groups.values():
            if user_id in users:
                users.remove(user_id)
        groups[{"yes": "yes_users", "maybe": "maybe_users", "no": "no_users"}[status]].append(user_id)
        transaction.update(ref, groups)
        data.update(groups)
        return data

    return update(transaction)


class ScheduleView(discord.ui.View):
    """custom_id固定の永続View。募集内容はFirestoreから更新する。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _vote(self, interaction: discord.Interaction, status: str) -> None:
        try:
            data = await asyncio.to_thread(_cast_vote, interaction.message.id, interaction.user.id, status)
        except Exception as exc:
            print(f"[GameSchedule] 参加状況のFirestore更新エラー: {exc}")
            await interaction.response.send_message("参加状況を保存できませんでした。", ephemeral=True)
            return
        if data is None:
            await interaction.response.send_message("この募集データは見つかりません。", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_schedule_embed(data), view=self)

    @discord.ui.button(label="参加 (〇)", style=discord.ButtonStyle.success, custom_id="game_schedule:yes")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._vote(interaction, "yes")

    @discord.ui.button(label="未定 (△)", style=discord.ButtonStyle.secondary, custom_id="game_schedule:maybe")
    async def maybe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._vote(interaction, "maybe")

    @discord.ui.button(label="不参加 (×)", style=discord.ButtonStyle.danger, custom_id="game_schedule:no")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._vote(interaction, "no")


class RecruitModal(discord.ui.Modal, title="募集内容を入力"):
    memo = discord.ui.TextInput(label="備考", required=False, style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, cog: "GameSchedule", game_name: str, initial_yes: list[int], date_str: str,
                 mention_mode: str, role_id: int | None) -> None:
        super().__init__()
        self.cog = cog
        self.game_name = game_name
        self.initial_yes = initial_yes
        self.date_str = date_str
        self.mention_mode = mention_mode
        self.role_id = role_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ping_text = "@everyone" if self.mention_mode == "everyone" else f"<@&{self.role_id}>"
        allowed_mentions = discord.AllowedMentions(
            everyone=(self.mention_mode == "everyone"),
            roles=(self.mention_mode == "tag"),
            users=False,
        )
        message = await interaction.channel.send("募集を作成しています…")
        data = {
            "message_id": message.id,
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id,
            "organizer_id": interaction.user.id,
            "game_name": self.game_name,
            "date_str": self.date_str,
            "mention_mode": self.mention_mode,
            "role_id": self.role_id,
            "memo": str(self.memo.value),
            "yes_users": self.initial_yes,
            "maybe_users": [],
            "no_users": [],
        }
        try:
            await asyncio.wait_for(asyncio.to_thread(_save_schedule, message.id, data), timeout=18)
        except Exception as exc:
            print(f"[GameSchedule] 募集Firestore保存エラー: {type(exc).__name__}: {exc}")
            try:
                await message.edit(content="募集をFirestoreに保存できませんでした。接続設定とBotログを確認してください。")
            except discord.HTTPException:
                pass
            await interaction.followup.send("募集の作成に失敗しました。Firestore接続とBotログを確認してください。", ephemeral=True)
            return
        try:
            await message.edit(content=ping_text, embed=_schedule_embed(data), view=self.cog.view,
                               allowed_mentions=allowed_mentions)
        except discord.HTTPException as exc:
            error_code = getattr(exc, "code", None)
            error_text = getattr(exc, "text", str(exc))
            print(
                f"[GameSchedule] 募集メッセージ更新エラー: "
                f"HTTP {exc.status}, Discord code {error_code}: {error_text}"
            )
            if exc.status == 403 or error_code == 50013:
                message = "募集データは保存しましたが、Botにこのチャンネルの「埋め込みリンク」権限がない可能性があります。"
            elif exc.status == 400 or error_code == 50035:
                message = "募集データは保存しましたが、DiscordがカードのEmbedまたはボタン形式を拒否しました。Botログに出るInvalid Form Bodyの項目を確認してください。"
            else:
                message = f"募集データは保存しましたが、募集カードの更新に失敗しました（HTTP {exc.status}, code {error_code}）。Botログを確認してください。"
            await interaction.followup.send(
                message,
                ephemeral=True,
            )
            return
        await interaction.followup.send("募集を作成しました。", ephemeral=True)


def _save_schedule(message_id: int, data: dict) -> None:
    _db().collection(SCHEDULES).document(str(message_id)).set(data, timeout=15)


class RecruitMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent: "RecruitSetupView", role_members: list[discord.Member]) -> None:
        super().__init__(
            placeholder="参加予定者（ロール外のメンバーも検索できます）",
            min_values=0,
            max_values=25,
            default_values=[discord.SelectDefaultValue.from_user(member) for member in role_members],
        )
        self.parent_view = parent
        self.parent_view.selected_user_ids = [member.id for member in role_members]

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_user_ids = [member.id for member in self.values]
        await interaction.response.defer()


class RecruitSetupView(discord.ui.View):
    def __init__(self, cog: "GameSchedule", game_name: str, role_members: list[discord.Member],
                 date_str: str, mention_mode: str, role_id: int | None) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.game_name = game_name
        self.date_str = date_str
        self.mention_mode = mention_mode
        self.role_id = role_id
        self.selected_user_ids: list[int] = []
        self.add_item(RecruitMemberSelect(self, role_members))

    @discord.ui.button(label="備考を入力して募集作成", style=discord.ButtonStyle.primary)
    async def continue_to_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(RecruitModal(
            self.cog, self.game_name, self.selected_user_ids.copy(), self.date_str,
            self.mention_mode, self.role_id,
        ))


class DatePartSelect(discord.ui.Select):
    def __init__(self, parent: "RecruitDateView", kind: str, options: list[discord.SelectOption], row: int, placeholder: str) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row)
        self.parent_view = parent
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        if self.kind == "month":
            self.parent_view.month = int(value)
            self.parent_view.update_day_options()
        elif self.kind == "day":
            self.parent_view.day = int(value)
        elif self.kind == "hour":
            self.parent_view.hour = int(value)
        elif self.kind == "minute":
            self.parent_view.minute = int(value)
        else:
            return
        for option in self.options:
            option.default = option.value == value
        await interaction.response.defer()
        try:
            await interaction.edit_original_response(content=self.parent_view.prompt(), view=self.parent_view)
        except Exception as exc:
            print(f"[GameSchedule] 日時選択メニュー更新エラー: {type(exc).__name__}: {exc}")
            await interaction.followup.send("日時メニューを更新できませんでした。Botログを確認してください。", ephemeral=True)


class RecruitDateView(discord.ui.View):
    """月日を選ぶ段階。31日分は選択肢上限に合わせて範囲を切り替える。"""

    def __init__(self, cog: "GameSchedule", tags: list[dict]) -> None:
        super().__init__(timeout=300)
        # Windows環境ではIANA tzdataが未導入だとZoneInfo("Asia/Tokyo")が失敗する。
        # 日本は夏時間を採用していないため、固定UTC+9で扱う。
        japan_tz = timezone(timedelta(hours=9))
        now = datetime.now(japan_tz)
        self.year, self.month, self.day = now.year, now.month, now.day
        self.day_page = 1 if self.day <= 25 else 2
        self.hour, self.minute = 21, 0
        self.cog = cog
        self.tags = tags

        month_options = [
            discord.SelectOption(label=f"{month:02d}月", value=str(month), default=(month == self.month))
            for month in range(1, 13)
        ]
        self.add_item(DatePartSelect(self, "month", month_options, 0, "MM（月）を選択"))
        self.day_select = DatePartSelect(self, "day", self.make_day_options(), 1, "DD（日）を選択")
        self.add_item(self.day_select)
        hour_options = [discord.SelectOption(label=f"{hour:02d}", value=str(hour), default=(hour == self.hour)) for hour in range(1, 25)]
        minute_options = [discord.SelectOption(label=f"{minute:02d}", value=str(minute), default=(minute == self.minute)) for minute in (0, 15, 30, 45)]
        self.add_item(DatePartSelect(self, "hour", hour_options, 2, "HH（時: 01〜24）"))
        self.add_item(DatePartSelect(self, "minute", minute_options, 3, "MM（分: 00 / 15 / 30 / 45）"))

    def prompt(self) -> str:
        return ("募集日時を選択してください（日本時間）\n"
                f"選択中: **MM {self.month:02d} / DD {self.day:02d} — HH {self.hour:02d} : MM {self.minute:02d}**")

    def make_day_options(self) -> list[discord.SelectOption]:
        max_day = calendar.monthrange(self.year, self.month)[1]
        start = 1 if self.day_page == 1 else 26
        end = min(25, max_day) if self.day_page == 1 else max_day
        return [discord.SelectOption(label=f"{day:02d}日", value=str(day), default=(day == self.day))
                for day in range(start, end + 1)]

    def update_day_options(self, reset_day: bool = False) -> None:
        max_day = calendar.monthrange(self.year, self.month)[1]
        self.day = min(self.day, max_day)
        if self.day > 25:
            self.day_page = 2
        elif self.day_page == 2 and max_day < 26:
            self.day_page = 1
        if reset_day:
            self.day = 1 if self.day_page == 1 else 26
        self.remove_item(self.day_select)
        self.day_select = DatePartSelect(self, "day", self.make_day_options(), 1, "DD（日）を選択")
        self.add_item(self.day_select)

    async def set_day_page(self, interaction: discord.Interaction, page: int) -> None:
        self.day_page = page
        self.update_day_options(reset_day=True)
        await interaction.response.edit_message(content=self.prompt(), view=self)

    @discord.ui.button(label="DD 01〜25", style=discord.ButtonStyle.secondary, row=4)
    async def first_day_range(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.set_day_page(interaction, 1)

    @discord.ui.button(label="DD 26〜31", style=discord.ButtonStyle.secondary, row=4)
    async def second_day_range(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.set_day_page(interaction, 2)

    @discord.ui.button(label="今すぐ", style=discord.ButtonStyle.success, row=4)
    async def recruit_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = RecruitTagView(self.cog, self.tags, "今すぐ")
        await interaction.response.edit_message(content="ゲームタグと通知方法を選択してください。", view=view)

    @discord.ui.button(label="日時を確定して備考へ", style=discord.ButtonStyle.primary, row=4)
    async def confirm_date(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            datetime(self.year, self.month, self.day)
        except ValueError:
            await interaction.response.send_message("選択した月に存在しない日付です。日を選び直してください。", ephemeral=True)
            return
        date_str = f"{self.month:02d}/{self.day:02d} {self.hour:02d}:{self.minute:02d}"
        view = RecruitTagView(self.cog, self.tags, date_str)
        await interaction.response.edit_message(content="ゲームタグと通知方法を選択してください。", view=view)


class RecruitTagSelect(discord.ui.Select):
    def __init__(self, parent: "RecruitTagView", tags: list[dict]) -> None:
        options = [discord.SelectOption(
            label=tag["tag_name"][:100], value=tag["id"],
            description="カスタム追加" if tag.get("is_custom") else "Steam自動取得",
            default=(index == 0),
        ) for index, tag in enumerate(tags[:25])]
        super().__init__(placeholder="募集するタグを選択", min_values=1, max_values=1, options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_tag_id = self.values[0]
        await interaction.response.defer()


class RecruitMentionSelect(discord.ui.Select):
    def __init__(self, parent: "RecruitTagView") -> None:
        super().__init__(
            placeholder="通知方法を選択",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="タグをメンション", value="tag", description="選択したゲームタグのロールに通知", default=True),
                discord.SelectOption(label="@everyone", value="everyone", description="サーバー全員に通知"),
            ],
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.mention_mode = self.values[0]
        await interaction.response.defer()


class RecruitTagView(discord.ui.View):
    def __init__(self, cog: "GameSchedule", tags: list[dict], date_str: str) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.tags = tags
        self.date_str = date_str
        self.selected_tag_id = tags[0]["id"]
        self.mention_mode = "tag"
        self.add_item(RecruitTagSelect(self, tags))
        self.add_item(RecruitMentionSelect(self))

    @discord.ui.button(label="参加者を選択", style=discord.ButtonStyle.primary, row=2)
    async def choose_participants(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        tag = next((item for item in self.tags if item["id"] == self.selected_tag_id), None)
        if tag is None:
            await interaction.response.send_message("タグを選択してください。", ephemeral=True)
            return
        if self.mention_mode == "everyone" and interaction.guild is not None:
            bot_member = interaction.guild.get_member(self.cog.bot.user.id) if self.cog.bot.user else None
            if (bot_member is None or interaction.channel is None
                    or not interaction.channel.permissions_for(bot_member).mention_everyone):
                await interaction.response.send_message(
                    "@everyoneを通知するには、Botに「@everyone、@here、すべてのロールにメンション」のチャンネル権限が必要です。",
                    ephemeral=True,
                )
                return
        role_id = tag.get("role_id")
        if self.mention_mode == "tag" and not role_id:
            await interaction.response.send_message(
                "このタグにはDiscordロールが紐づいていません。管理者に `/game-tag-add` でロールを登録してもらうか、@everyoneを選択してください。",
                ephemeral=True,
            )
            return
        role = interaction.guild.get_role(role_id) if interaction.guild and role_id else None
        if self.mention_mode == "tag" and role is None:
            await interaction.response.send_message(
                "このタグに紐づいたDiscordロールが見つかりません。管理者に `/game-tag-add` でタグを更新してもらうか、@everyoneを選択してください。",
                ephemeral=True,
            )
            return
        if self.mention_mode == "tag" and role is not None and interaction.guild is not None:
            bot_member = interaction.guild.get_member(self.cog.bot.user.id) if self.cog.bot.user else None
            can_mention_role = role.mentionable or (
                bot_member is not None
                and interaction.channel is not None
                and interaction.channel.permissions_for(bot_member).mention_everyone
            )
            if not can_mention_role:
                await interaction.response.send_message(
                    "このロールはメンション不可です。ロール設定で「このロールに対して@mentionを許可」を有効にするか、Botに「@everyone、@here、すべてのロールにメンション」の権限を付けてください。",
                    ephemeral=True,
                )
                return
        role_members = sorted(role.members, key=lambda member: member.display_name.casefold()) if role else []
        view = RecruitSetupView(
            self.cog, tag["tag_name"], role_members[:25], self.date_str,
            self.mention_mode, role_id,
        )
        await interaction.response.edit_message(
            content="参加者を選択してください。対象ロールのメンバーは先に選択済みです。ロール外のメンバーも検索して追加できます。",
            view=view,
        )


class TagToggleButton(discord.ui.Button):
    def __init__(self, parent: "TagChoiceView", tag: dict, index: int, selected: bool) -> None:
        self.parent_view = parent
        self.tag_id = tag["id"]
        role_id = int(tag.get("role_id") or 0)
        registered = role_id in parent.owned_role_ids
        if parent.mode == "update":
            prefix = ("解除: " if selected else "登録済: ") if registered else ("追加: " if selected else "未登録: ")
            # 登録済みは緑、登録解除を選択中は赤、未登録は灰色で区別する。
            style = (discord.ButtonStyle.danger if selected else discord.ButtonStyle.success) if registered else (
                discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary
            )
        else:
            prefix = "✓ " if selected else "＋ "
            style = discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary
        super().__init__(
            label=prefix + tag["tag_name"][:max(1, 80 - len(prefix))],
            style=style,
            row=index // 5,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.toggle(interaction, self.tag_id)


class TagCategoryHeader(discord.ui.Button):
    def __init__(self, label: str, slot: int) -> None:
        super().__init__(label=f"── {label} ──", style=discord.ButtonStyle.secondary,
                         row=slot // 5, disabled=True)


class TagChoiceView(discord.ui.View):
    """個人のタグ追加・更新用。ボタンをページ分割してDiscordの上限内に収める。"""

    # 操作行（ページ選択・確定）を確保するため、タグは1ページ10件にする。
    PAGE_SIZE = 10

    def __init__(self, cog: "GameSchedule", tags: list[dict], mode: str, owned_role_ids: set[int]) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.tags = tags
        self.mode = mode
        self.owned_role_ids = owned_role_ids
        self.add_ids: set[str] = set()
        self.remove_ids: set[str] = set()
        self.tags.sort(key=lambda tag: (not tag.get("is_custom", False), str(tag.get("tag_name", "")).casefold()))
        self.page = 0
        self.refresh()

    def _selected(self, tag: dict) -> bool:
        if self.mode == "register":
            return tag["id"] in self.add_ids
        if int(tag.get("role_id") or 0) in self.owned_role_ids:
            return tag["id"] in self.remove_ids
        return tag["id"] in self.add_ids

    def refresh(self) -> None:
        self.clear_items()
        start = self.page * self.PAGE_SIZE
        page_tags = self.tags[start:start + self.PAGE_SIZE]
        slot = 0
        previous_category = None
        for index, tag in enumerate(page_tags):
            category = "custom" if tag.get("is_custom", False) else "steam"
            if category != previous_category:
                label = "Steam以外" if category == "custom" else "Steamゲーム"
                self.add_item(TagCategoryHeader(label, slot))
                slot += 1
                previous_category = category
            self.add_item(TagToggleButton(self, tag, slot, self._selected(tag)))
            slot += 1
        self.add_item(TagPageSelect(self))
        self.add_item(TagApplyButton(self, row=4))

    def prompt(self) -> str:
        total_pages = max(1, (len(self.tags) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self.mode == "register":
            instruction = "登録するタグを選択してください。Steam以外のタグを先に表示し、その後にSteamゲームを表示します。選んだタグは最後にまとめて登録します。"
            instruction += f"\n選択中: {len(self.add_ids)}件"
        else:
            instruction = "Steam以外のタグを先に表示し、その後にSteamゲームを表示します。登録済みタグは解除対象、未登録タグは追加対象として選択できます。"
            instruction += f"\n追加: {len(self.add_ids)}件 / 解除: {len(self.remove_ids)}件"
        custom_count = sum(bool(tag.get("is_custom", False)) for tag in self.tags)
        steam_count = len(self.tags) - custom_count
        return (f"{instruction}\n表示対象: Steam以外 {custom_count}件 / Steamゲーム {steam_count}件"
                f"\nSteam以外を先に表示し、その続きにSteamゲームを表示します。"
                f"\nページ {self.page + 1}/{total_pages}")

    async def toggle(self, interaction: discord.Interaction, tag_id: str) -> None:
        tag = next((item for item in self.tags if item["id"] == tag_id), None)
        if tag is None:
            await interaction.response.send_message("タグ一覧を更新してから、もう一度お試しください。", ephemeral=True)
            return
        if self.mode == "register" or int(tag.get("role_id") or 0) not in self.owned_role_ids:
            self.add_ids.symmetric_difference_update({tag_id})
        else:
            self.remove_ids.symmetric_difference_update({tag_id})
        self.refresh()
        await interaction.response.edit_message(content=self.prompt(), view=self)

    async def apply(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        to_add = self.add_ids.copy()
        to_remove = self.remove_ids.copy()
        changed_add = changed_remove = 0
        errors: list[str] = []
        for tag in self.tags:
            role_id = tag.get("role_id")
            if not role_id:
                continue
            role = interaction.guild.get_role(int(role_id))
            if role is None:
                errors.append(f"{tag['tag_name']}: ロールがありません")
                continue
            try:
                if tag["id"] in to_add and role not in interaction.user.roles:
                    await interaction.user.add_roles(role, reason="タグパネルからの登録")
                    changed_add += 1
                elif tag["id"] in to_remove and role in interaction.user.roles:
                    await interaction.user.remove_roles(role, reason="タグパネルからの登録解除")
                    changed_remove += 1
            except discord.HTTPException as exc:
                print(f"[GameSchedule] タグロール変更エラー ({tag['tag_name']}): {exc}")
                errors.append(f"{tag['tag_name']}: ロールを変更できません")
        summary = f"タグを更新しました。追加 {changed_add}件、解除 {changed_remove}件。"
        if errors:
            summary += "\n一部を変更できませんでした: " + " / ".join(errors[:8])
        await interaction.followup.send(summary, ephemeral=True)
        try:
            await interaction.edit_original_response(content="タグ更新を処理しました。", view=None)
        except discord.HTTPException:
            pass


class TagPageSelect(discord.ui.Select):
    def __init__(self, parent: TagChoiceView) -> None:
        page_count = max(1, (len(parent.tags) + parent.PAGE_SIZE - 1) // parent.PAGE_SIZE)
        start = max(0, min(parent.page - 12, page_count - 25))
        end = min(page_count, start + 25)
        options = [discord.SelectOption(
            label=f"{page + 1}ページ",
            value=str(page),
            description=("Steam以外" if page * parent.PAGE_SIZE < sum(
                bool(tag.get("is_custom", False)) for tag in parent.tags
            ) else "Steamゲーム"),
            default=page == parent.page,
        ) for page in range(start, end)]
        super().__init__(placeholder="ページを選択", min_values=1, max_values=1, options=options, row=3)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.page = int(self.values[0])
        self.parent_view.refresh()
        await interaction.response.edit_message(content=self.parent_view.prompt(), view=self.parent_view)


class TagApplyButton(discord.ui.Button):
    def __init__(self, parent: TagChoiceView, row: int) -> None:
        super().__init__(label="選択を登録・更新", style=discord.ButtonStyle.primary, row=row)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.apply(interaction)


class TagPanelView(discord.ui.View):
    """チャンネル常設のタグ登録・Steam連携パネル。"""

    def __init__(self, cog: "GameSchedule") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def _open_tags(self, interaction: discord.Interaction, mode: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で利用してください。", ephemeral=True)
            return
        # 先に応答を確定し、Firestore取得やView構築で失敗してもDiscord側の
        # 「応答していません」状態が残らないよう、すべての経路で元応答を編集する。
        await interaction.response.defer(ephemeral=True, thinking=True)
        print(f"[GameSchedule] タグ登録・更新を開始: guild_id={interaction.guild.id}, user_id={interaction.user.id}")
        try:
            tags = await asyncio.wait_for(
                self.cog.get_guild_tags(interaction.guild.id),
                timeout=20,
            )
        except Exception as exc:
            print(f"[GameSchedule] タグパネル取得エラー: {type(exc).__name__}: {exc}")
            try:
                await interaction.edit_original_response(
                    content="タグ一覧の取得に失敗しました。時間をおいて再度お試しください。",
                    view=None,
                )
            except discord.HTTPException as response_exc:
                print(f"[GameSchedule] タグ取得失敗の応答エラー: {response_exc}")
            return
        if not tags:
            await interaction.edit_original_response(
                content="表示対象のタグがありません。管理者が `/game-tag-manage` でタグを表示設定してください。",
                view=None,
            )
            return
        # Steam以外（カスタム）を先にまとめ、その後にSteamゲームを名前順で表示する。
        tags.sort(key=lambda tag: (not tag.get("is_custom", False), str(tag.get("tag_name", "")).casefold()))
        try:
            current_member = await interaction.guild.fetch_member(interaction.user.id)
        except discord.HTTPException:
            current_member = interaction.user
        owned = {role.id for role in current_member.roles}
        try:
            view = TagChoiceView(self.cog, tags, mode, owned)
            await interaction.edit_original_response(content=view.prompt(), view=view)
            print(f"[GameSchedule] タグ選択画面を表示: tag_count={len(tags)}")
        except Exception as exc:
            print(f"[GameSchedule] タグ選択画面の構築・表示エラー: {type(exc).__name__}: {exc}")
            try:
                await interaction.edit_original_response(
                    content="タグ選択画面を表示できませんでした。Botログを確認してください。",
                    view=None,
                )
            except discord.HTTPException as response_exc:
                print(f"[GameSchedule] タグ画面エラーの応答エラー: {response_exc}")

    @discord.ui.button(label="タグ登録・更新", style=discord.ButtonStyle.success, custom_id="game_schedule:tag_panel:register")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_tags(interaction, "update")

    @discord.ui.button(label="Steam連携", style=discord.ButtonStyle.secondary, custom_id="game_schedule:tag_panel:steam")
    async def steam(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SteamLinkModal(self.cog))


class LegacyTagPanelUpdateView(discord.ui.View):
    """既に設置済みの旧パネルにある「タグ更新」ボタンの互換処理。"""

    def __init__(self, cog: "GameSchedule") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="タグ更新", style=discord.ButtonStyle.primary, custom_id="game_schedule:tag_panel:update")
    async def update(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await TagPanelView(self.cog)._open_tags(interaction, "update")


class SteamLinkModal(discord.ui.Modal, title="Steamプロフィール連携"):
    def __init__(self, cog: "GameSchedule") -> None:
        super().__init__()
        self.cog = cog
        supports_label_description = hasattr(discord.ui, "Label")
        self.profile_url = discord.ui.TextInput(
            label=None if supports_label_description else "SteamプロフィールURL",
            placeholder="ここにSteamプロフィールURLを貼り付け",
            max_length=200,
        )
        if supports_label_description:
            self.add_item(discord.ui.Label(
                text="SteamプロフィールURLを取得",
                description=(
                    "1. Steamクライアントを起動\n"
                    "2. 画面上部のアカウント名をクリックし、プロフィール画面のURLをコピー"
                ),
                component=self.profile_url,
            ))
        else:
            # discord.py 2.4/2.5向け。Label.description（2.6以降）がない場合も案内を残す。
            self.profile_url.label = "Steamクライアント起動後、名前からプロフィールURLをコピー"
            self.add_item(self.profile_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.save_steam_link(interaction, str(self.profile_url.value))


class SteamTagNameModal(discord.ui.Modal, title="Steamゲームのタグ名設定"):
    display_name = discord.ui.TextInput(label="表示タグ名・Discordロール名", max_length=100)
    def __init__(self, cog: "GameSchedule", tag: dict) -> None:
        super().__init__()
        self.cog = cog
        self.tag = tag
        self.display_name.default = tag.get("tag_name", tag.get("steam_game_name", ""))[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("タグ設定を編集できるのは「ロールの管理」権限を持つ管理者のみです。", ephemeral=True)
            return
        name = str(self.display_name.value).strip()
        if not name:
            await interaction.response.send_message("表示タグ名を入力してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        role = interaction.guild.get_role(int(self.tag.get("role_id") or 0))
        try:
            if role is None:
                old_name = self.tag.get("tag_name")
                role = discord.utils.get(interaction.guild.roles, name=old_name) if old_name else None
            if role is None and self.tag.get("is_visible", False):
                role = discord.utils.get(interaction.guild.roles, name=name)
            if role is None and self.tag.get("is_visible", False):
                role = await interaction.guild.create_role(name=name, mentionable=True, reason="Steamゲームタグ設定")
            elif role is not None and (role.name != name or not role.mentionable):
                await role.edit(name=name, mentionable=True, reason="Steamゲームタグ設定")
            update = {
                "tag_name": name,
                "role_id": role.id if role else None,
            }
            await asyncio.to_thread(
                _db().collection(TAGS).document(self.tag["id"]).update,
                update,
            )
        except Exception as exc:
            import traceback
            print(f"[GameSchedule] Steamタグ設定エラー: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            await interaction.followup.send("タグまたはロールの更新に失敗しました。Botのロール管理権限・ロール階層とログを確認してください。", ephemeral=True)
            return
        await interaction.followup.send(
            f"Steam名「{self.tag.get('steam_game_name', self.tag.get('tag_name', '不明'))}」の表示タグ名を「{name}」に設定しました。",
            ephemeral=True,
        )


class HiddenTagDeleteConfirmView(discord.ui.View):
    def __init__(self, cog: "GameSchedule", tag: dict) -> None:
        super().__init__(timeout=60)
        self.cog, self.tag = cog, tag

    @discord.ui.button(label="タグ設定を削除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で操作してください。", ephemeral=True)
            return
        if self.tag.get("is_visible", False):
            await interaction.response.send_message("表示中のタグは削除できません。先に非表示にしてください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await asyncio.to_thread(
                _db().collection(TAGS).document(self.tag["id"]).update,
                {"is_deleted": True, "deleted_at": datetime.now(timezone.utc)},
            )
        except Exception as exc:
            print(f"[GameSchedule] 非表示タグ削除エラー: {type(exc).__name__}: {exc}")
            await interaction.followup.send("タグ設定を削除できませんでした。", ephemeral=True)
            return
        await interaction.followup.send("非表示タグを削除済みにしました。記録は保持され、Steamキャッシュを再取得しても復元されません。関連するDiscordロール自体は残しています。", ephemeral=True)


class SteamTagEditView(discord.ui.View):
    def __init__(self, cog: "GameSchedule", tag: dict) -> None:
        super().__init__(timeout=180)
        self.cog, self.tag = cog, tag
        self.visibility_select = discord.ui.Select(
            placeholder="表示状態を選択",
            options=[
                discord.SelectOption(label="表示", value="true", default=bool(tag.get("is_visible", False))),
                discord.SelectOption(label="非表示", value="false", default=not bool(tag.get("is_visible", False))),
            ],
            min_values=1, max_values=1,
        )
        self.visibility_select.callback = self.change_visibility
        self.add_item(self.visibility_select)
        self.delete_button.disabled = bool(tag.get("is_visible", False))

    async def change_visibility(self, interaction: discord.Interaction) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で操作してください。", ephemeral=True)
            return
        value = self.visibility_select.values[0] == "true"
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if value:
                role = await self.cog.enable_steam_tag(interaction.guild, self.tag)
                status = f"表示に変更し、ロール「{role.name}」を作成しました。ユーザーへの登録はタグパネルまたはSteam同期で行われます。"
            else:
                role_id = int(self.tag.get("role_id") or 0)
                role = interaction.guild.get_role(role_id) if role_id else None
                if role is not None:
                    await role.delete(reason="Steamゲームタグを非表示")
                await asyncio.to_thread(
                    _db().collection(TAGS).document(self.tag["id"]).update,
                    {"is_visible": False, "role_id": None},
                )
                self.tag["is_visible"] = False
                self.tag["role_id"] = None
                status = "非表示にし、紐づくロールを削除しました。" if role is not None else "非表示にしました（紐づくロールはありませんでした）。"
            self.delete_button.disabled = value
            await interaction.edit_original_response(
                content=f"「{self.tag.get('steam_game_name', '不明')}」を{status} 非表示タグはここから削除できます。",
                view=self,
            )
        except Exception as exc:
            print(f"[GameSchedule] Steamタグ表示状態更新エラー: {type(exc).__name__}: {exc}")
            await interaction.followup.send("表示状態またはロールを更新できませんでした。Botのロール管理権限・ロール階層とログを確認してください。", ephemeral=True)

    @discord.ui.button(label="表示タグ名を編集", style=discord.ButtonStyle.primary, row=1)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        await interaction.response.send_modal(SteamTagNameModal(self.cog, self.tag))

    @discord.ui.button(label="非表示タグ設定を削除", style=discord.ButtonStyle.danger, row=1)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        if self.tag.get("is_visible", False):
            await interaction.response.send_message("先に表示状態を「非表示」にしてください。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Steamゲーム「{self.tag.get('steam_game_name', '不明')}」の非表示タグ設定を削除します。Discordロール自体は残ります。よろしいですか？",
            view=HiddenTagDeleteConfirmView(self.cog, self.tag), ephemeral=True,
        )


class SteamTagButton(discord.ui.Button):
    def __init__(self, parent: "SteamTagManageView", tag: dict, index: int) -> None:
        super().__init__(label=tag.get("steam_game_name", tag.get("tag_name", "不明"))[:80],
                         style=discord.ButtonStyle.secondary, row=index // 5)
        self.parent_view = parent
        self.tag = tag

    async def callback(self, interaction: discord.Interaction) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("Steamタグ設定を編集できるのは管理者のみです。", ephemeral=True)
            return
        view = SteamTagEditView(self.parent_view.cog, self.tag)
        await interaction.response.send_message(
            f"Steam名: {self.tag.get('steam_game_name', '不明')}\nタグ名: {self.tag.get('tag_name', '未設定')}\n表示状態を選択してください。",
            view=view, ephemeral=True,
        )


class SteamTagPageButton(discord.ui.Button):
    def __init__(self, parent: "SteamTagManageView", step: int, disabled: bool) -> None:
        super().__init__(label="前へ" if step < 0 else "次へ", style=discord.ButtonStyle.secondary,
                         row=4, disabled=disabled)
        self.parent_view = parent
        self.step = step

    async def callback(self, interaction: discord.Interaction) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("Steamタグ設定を編集できるのは管理者のみです。", ephemeral=True)
            return
        self.parent_view.page += self.step
        self.parent_view.refresh()
        await interaction.response.edit_message(content=self.parent_view.prompt(), view=self.parent_view)


class ManualGameTagModal(discord.ui.Modal, title="手動ゲームタグを追加"):
    tag_name = discord.ui.TextInput(
        label="タグ名（Minecraft、ボードゲームなど）",
        placeholder="例: Minecraft",
        max_length=100,
    )

    def __init__(self, parent_view: "SteamTagManageView") -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("タグの追加はロール管理権限を持つ管理者のみ実行できます。", ephemeral=True)
            return

        name = str(self.tag_name.value).strip()
        if not name:
            await interaction.response.send_message("タグ名を入力してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            tags = await self.parent_view.cog.get_guild_tags(interaction.guild.id, visible_only=False)
            if any(tag.get("is_custom") and tag.get("tag_name", "").casefold() == name.casefold() for tag in tags):
                await interaction.followup.send(f"カスタムタグ「{name}」は既に登録されています。", ephemeral=True)
                return

            role = discord.utils.get(interaction.guild.roles, name=name)
            if role is None:
                role = await interaction.guild.create_role(name=name, mentionable=True, reason="手動ゲームタグ登録")
            elif not role.mentionable:
                await role.edit(mentionable=True, reason="募集タグのメンションを有効化")

            doc_id = f"{interaction.guild.id}_{hashlib.sha256(('custom:' + name.casefold()).encode()).hexdigest()[:32]}"
            data = {
                "guild_id": interaction.guild.id,
                "tag_name": name,
                "match_keyword": "",
                "role_id": role.id,
                "is_custom": True,
                "is_visible": True,
            }
            await asyncio.to_thread(_db().collection(TAGS).document(doc_id).set, data)
            self.parent_view.tags = await self.parent_view.cog.get_guild_tags(interaction.guild.id, visible_only=False)
            self.parent_view.tags.sort(key=lambda tag: str(tag.get("steam_game_name") or tag.get("tag_name", "")).casefold())
            self.parent_view.refresh()
            if self.parent_view.message is not None:
                await self.parent_view.message.edit(content=self.parent_view.prompt(), view=self.parent_view)
            await interaction.followup.send(
                f"手動タグ「{name}」を追加しました。タグ登録パネルと募集タグ一覧から利用できます。",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"[GameSchedule] 手動タグ追加エラー: {type(exc).__name__}: {exc}")
            await interaction.followup.send("手動タグを追加できませんでした。Botのロール管理権限・ロール階層とFirestoreを確認してください。", ephemeral=True)


class ManualGameTagButton(discord.ui.Button):
    def __init__(self, parent: "SteamTagManageView") -> None:
        super().__init__(label="手動タグを追加", style=discord.ButtonStyle.primary, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("この操作はロール管理権限を持つ管理者のみ実行できます。", ephemeral=True)
            return
        self.parent_view.message = interaction.message
        await interaction.response.send_modal(ManualGameTagModal(self.parent_view))


class SteamTagImportButton(discord.ui.Button):
    def __init__(self, parent: "SteamTagManageView") -> None:
        super().__init__(label="Steamキャッシュからタグを追加", style=discord.ButtonStyle.success, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("この操作はサーバーの管理者のみ実行できます。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            added = await self.parent_view.cog.import_cached_steam_tags(interaction.guild)
            self.parent_view.tags = await self.parent_view.cog.get_guild_tags(interaction.guild.id, visible_only=False)
            self.parent_view.tags.sort(key=lambda tag: str(tag.get("steam_game_name") or tag.get("tag_name", "")).casefold())
            self.parent_view.page = 0
            self.parent_view.refresh()
            await interaction.edit_original_response(
                content=f"Steamゲーム {added} 件を新しいタグ候補として追加しました。各候補は初期状態で非表示です。\n{self.parent_view.prompt()}",
                view=self.parent_view,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            print(f"[GameSchedule] 管理者によるSteamタグ取込エラー: {type(exc).__name__}: {exc}")
            await interaction.followup.send("Steamゲームのタグ追加に失敗しました。Botログを確認してください。", ephemeral=True)


class SteamTagManageView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, cog: "GameSchedule", tags: list[dict]) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.tags = tags
        self.message: discord.Message | None = None
        self.page = 0
        self.refresh()

    def prompt(self) -> str:
        pages = max(1, (len(self.tags) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        return ("編集するSteamタグまたは手動タグを選んでください。ボタンにはタグ名と現在の表示状態が出ます。\n"
                "タグを選ぶと、表示状態はコンボボックス、タグ名はボタンから編集できます。非表示タグは削除できます。\n"
                "Steam取得タグは「Steamキャッシュからタグを追加」、Minecraft等は「手動タグを追加」から登録できます。\n"
                f"ページ {self.page + 1}/{pages}・候補 {len(self.tags)}件")

    def refresh(self) -> None:
        self.clear_items()
        start = self.page * self.PAGE_SIZE
        for index, tag in enumerate(self.tags[start:start + self.PAGE_SIZE]):
            state = "表示" if tag.get("is_visible", False) else "非表示"
            # 元のSteam名を選択肢にしつつ、状態は管理画面の本文で確認できる。
            button = SteamTagButton(self, tag, index)
            button.label = f"{state}: {button.label}"[:80]
            self.add_item(button)
        self.add_item(SteamTagPageButton(self, -1, self.page == 0))
        self.add_item(SteamTagPageButton(self, 1, (self.page + 1) * self.PAGE_SIZE >= len(self.tags)))
        self.add_item(SteamTagImportButton(self))
        self.add_item(ManualGameTagButton(self))


class GameSchedule(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.view = ScheduleView()

    async def cog_load(self) -> None:
        self.bot.add_view(self.view)
        self.bot.add_view(TagPanelView(self))
        self.bot.add_view(LegacyTagPanelUpdateView(self))

    async def get_guild_tags(self, guild_id: int, visible_only: bool = True) -> list[dict]:
        docs = await asyncio.to_thread(
            lambda: list(_db().collection(TAGS).where(
                filter=FieldFilter("guild_id", "==", guild_id)
            ).stream())
        )
        tags = [dict(doc.to_dict(), id=doc.id) for doc in docs]
        # 削除済みタグの記録は保持するが、通常の選択・募集・管理一覧には出さない。
        tags = [tag for tag in tags if not tag.get("is_deleted", False)]
        if visible_only:
            tags = [tag for tag in tags if _tag_is_visible(tag)]
        return sorted(tags, key=lambda tag: tag["tag_name"].casefold())

    async def import_cached_steam_tags(self, guild: discord.Guild) -> int:
        """管理者操作で、サーバーメンバーがSteam連携で取得済みのゲームをタグ候補化する。"""
        member_ids = {str(member.id) for member in guild.members}

        def read_cached_games() -> list[dict]:
            db = _db()
            users = list(db.collection(USERS).stream())
            games_by_app: dict[str, dict] = {}
            for doc in users:
                data = doc.to_dict() or {}
                discord_id = str(data.get("discord_id", doc.id))
                if discord_id not in member_ids:
                    continue
                cached = list(db.collection(GAMES).where(
                    filter=FieldFilter("discord_id", "==", discord_id)
                ).stream())
                # 旧キャッシュ（steam_id64で保存）も移行期間中は読み取る。
                if not cached and data.get("steam_id64"):
                    cached = list(db.collection(GAMES).where(
                        filter=FieldFilter("steam_id64", "==", str(data["steam_id64"]))
                    ).stream())
                for game_doc in cached:
                    game_data = game_doc.to_dict() or {}
                    app_id = game_data.get("app_id")
                    if app_id is not None:
                        games_by_app.setdefault(str(app_id), {
                            "appid": int(app_id),
                            "name": game_data.get("game_name", str(app_id)),
                        })
            return list(games_by_app.values())

        games = await asyncio.to_thread(read_cached_games)
        if not games:
            raise ValueError("サーバーメンバーのSteamゲームキャッシュがありません。先に各ユーザーがSteam連携してください。")
        return await asyncio.to_thread(self._upsert_steam_game_tags, guild.id, games)

    async def enable_steam_tag(self, guild: discord.Guild, tag: dict) -> discord.Role:
        """タグを表示状態にし、対応するDiscordロールだけを用意する。"""
        role_name = str(tag.get("tag_name") or tag.get("steam_game_name") or "ゲーム")[:100]
        role = guild.get_role(int(tag.get("role_id") or 0)) if tag.get("role_id") else None
        if role is None:
            role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            role = await guild.create_role(name=role_name, mentionable=True, reason="Steamゲームタグを表示")
        elif role.name != role_name or not role.mentionable:
            await role.edit(name=role_name, mentionable=True, reason="Steamゲームタグを表示")

        await asyncio.to_thread(
            _db().collection(TAGS).document(tag["id"]).update,
            {"is_visible": True, "role_id": role.id},
        )
        tag["is_visible"] = True
        tag["role_id"] = role.id
        return role

    @staticmethod
    def _upsert_steam_game_tags(guild_id: int, games: list[dict]) -> int:
        """管理者の追加操作で指定されたゲームをサーバーのタグ候補に登録する。"""
        db = _db()
        collection = db.collection(TAGS)
        docs = list(collection.where(filter=FieldFilter("guild_id", "==", guild_id)).stream())
        by_app_id: dict[str, object] = {}
        by_legacy_name: dict[str, object] = {}
        legacy_candidates: list[tuple[str, object]] = []
        for doc in docs:
            data = doc.to_dict()
            if data.get("is_custom", False):
                continue
            if data.get("app_id") is not None:
                by_app_id[str(data["app_id"])] = doc
            for name in (data.get("match_keyword"), data.get("steam_game_name"), data.get("tag_name")):
                if name:
                    by_legacy_name.setdefault(str(name).casefold(), doc)
            keyword = str(data.get("match_keyword") or "").strip().casefold()
            if keyword and data.get("app_id") is None:
                legacy_candidates.append((keyword, doc))

        batch = db.batch()
        operations = 0
        added = 0
        for game in games:
            app_id = str(game.get("appid", ""))
            steam_name = str(game.get("name") or app_id)
            if not app_id:
                continue
            doc = by_app_id.get(app_id) or by_legacy_name.get(steam_name.casefold())
            if doc is None:
                matches = [(keyword, candidate) for keyword, candidate in legacy_candidates if keyword in steam_name.casefold()]
                if matches:
                    doc = max(matches, key=lambda item: len(item[0]))[1]
            if doc is None:
                ref = collection.document(f"{guild_id}_steam_{app_id}")
                values = {
                    "guild_id": guild_id,
                    "app_id": int(app_id),
                    "steam_game_name": steam_name,
                    "tag_name": steam_name,
                    "match_keyword": steam_name,
                    "role_id": None,
                    "is_custom": False,
                    "is_visible": False,
                }
                added += 1
            else:
                ref = doc.reference
                values = {
                    "guild_id": guild_id,
                    "app_id": int(app_id),
                    "steam_game_name": steam_name,
                    "match_keyword": steam_name,
                    "is_custom": False,
                }
            batch.set(ref, values, merge=True)
            operations += 1
            if operations == 450:
                batch.commit()
                batch = db.batch()
                operations = 0
        if operations:
            batch.commit()
        return added

    @staticmethod
    def _steam_id(profile_url: str) -> tuple[str | None, str | None]:
        parsed = urlparse(profile_url.strip())
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in ("steamcommunity.com", "www.steamcommunity.com"):
            return None, None
        match = re.match(r"^/(id|profiles)/([^/]+)", parsed.path)
        if not match:
            return None, None
        kind, value = match.groups()
        if kind == "profiles" and value.isdigit() and len(value) == 17:
            return value, None
        if kind == "id":
            return None, value
        return None, None

    async def _resolve_steam_id(self, profile_url: str) -> str:
        steam_id, vanity = self._steam_id(profile_url)
        if steam_id:
            return steam_id
        if not vanity:
            raise ValueError("SteamプロフィールURLを認識できません。")
        key = os.getenv("STEAM_API_KEY")
        if not key:
            raise ValueError("STEAM_API_KEYが未設定です。.envに `STEAM_API_KEY=取得したSteam Web APIキー` を設定してBotを再起動してください。")
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{STEAM_API}/ISteamUser/ResolveVanityURL/v1/", params={"key": key, "vanityurl": vanity}) as response:
                response.raise_for_status()
                payload = await response.json()
        result = payload.get("response", {})
        if result.get("success") != 1:
            raise ValueError("Steamプロフィールを解決できませんでした。")
        return str(result["steamid"])

    @app_commands.command(name="link", description="SteamプロフィールをDiscordアカウントに連携")
    @app_commands.describe(profile_url="SteamプロフィールURL")
    async def link(self, interaction: discord.Interaction, profile_url: str) -> None:
        await self.save_steam_link(interaction, profile_url)

    async def save_steam_link(self, interaction: discord.Interaction, profile_url: str) -> None:
        # Steam APIとFirestoreの処理が3秒を超えることがあるため、先に応答猶予を確保する。
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            steam_id = await self._resolve_steam_id(profile_url)
            data = {"discord_id": interaction.user.id, "steam_id64": steam_id}
            await asyncio.to_thread(_db().collection(USERS).document(str(interaction.user.id)).set, data)
        except (ValueError, aiohttp.ClientError) as exc:
            await interaction.followup.send(f"連携に失敗しました: {exc}", ephemeral=True)
            return
        except Exception as exc:
            import traceback
            print(f"[GameSchedule] Steam連携処理エラー: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            await interaction.followup.send("Steam連携の保存に失敗しました。Botログを確認してください。", ephemeral=True)
            return
        if not os.getenv("STEAM_API_KEY"):
            await interaction.followup.send(
                "Steamアカウントを連携しましたが、所持ゲームは取得できませんでした。"
                "`.env` に `STEAM_API_KEY=取得したSteam Web APIキー` を設定してBotを再起動してください。"
                "その後 `/link` をもう一度実行すると、ゲーム取得と表示対象タグのロール同期を行います。",
                ephemeral=True,
            )
            return
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Steamアカウントを連携しました。所持ゲーム取得とロール同期は対象サーバー内で `/sync` を実行してください。",
                ephemeral=True,
            )
            return
        try:
            game_count, role_additions, role_failures = await self._sync_user(interaction, steam_id)
        except (ValueError, aiohttp.ClientError, discord.HTTPException) as exc:
            print(f"[GameSchedule] Steam連携後の初回ゲーム同期エラー: {type(exc).__name__}: {exc}")
            await interaction.followup.send(
                f"Steamアカウントを連携しましたが、所持ゲーム同期に失敗しました: {exc}。"
                "設定を直した後に `/link` を再実行するか、連携済みなら `/sync` を実行してください。",
                ephemeral=True,
            )
            return
        except Exception as exc:
            import traceback
            print(f"[GameSchedule] Steam連携後の初回ゲーム同期エラー: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            await interaction.followup.send(
                "Steamアカウントは連携しましたが、初回の所持ゲーム同期に失敗しました。Botログを確認し、後から `/sync` を実行してください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Steamアカウントを連携しました。所持ゲーム {game_count} 件を取得しました。ロール追加 {role_additions} 件です。"
            + (f" ロール追加に失敗したタグが {role_failures} 件あります。Botログとロール権限を確認してください。" if role_failures else ""),
            ephemeral=True,
        )

    @app_commands.command(name="game-tag-panel", description="このチャンネルにタグ登録・更新・Steam連携パネルを設置（管理者）")
    async def post_tag_panel(self, interaction: discord.Interaction) -> None:
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("このコマンドは「ロールの管理」権限を持つ管理者のみ実行できます。", ephemeral=True)
            return
        target = interaction.channel
        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("このチャンネルにはパネルを設置できません。テキストチャンネルで実行してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await target.send(
                "**ゲームタグ登録・Steam自動取得**\n\n"
                "**タグ登録・更新：** タグを手動で選択・修正します。\n"
                "**Steam連携：** Steamプロフィールから所持ゲームを自動取得します。\n"
                "（プロフィールが非公開の場合、正常に取得できないことがあります。）",
                view=TagPanelView(self),
            )
        except discord.Forbidden:
            await interaction.followup.send("指定チャンネルにメッセージを送信する権限がありません。", ephemeral=True)
            return
        except discord.HTTPException as exc:
            print(f"[GameSchedule] タグパネル設置エラー: {exc}")
            await interaction.followup.send("パネルを設置できませんでした。Botログを確認してください。", ephemeral=True)
            return
        await interaction.followup.send(f"このチャンネル（{target.mention}）にパネルを設置しました。", ephemeral=True)

    @post_tag_panel.error
    async def post_tag_panel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "このコマンドは「ロールの管理」権限を持つ管理者のみ実行できます。"
        else:
            original = getattr(error, "original", error)
            print(f"[GameSchedule] game-tag-panelコマンドエラー: {type(original).__name__}: {original}")
            message = "パネル設置コマンドでエラーが発生しました。Botログを確認してください。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @commands.command(name="game-tag-panel", help="このチャンネルにタグ登録・更新・Steam連携パネルを設置します（ロール管理権限が必要）")
    async def post_tag_panel_text(self, ctx: commands.Context) -> None:
        """チャットに `/game-tag-panel` と入力して実行する場合の互換コマンド。"""
        if not ctx.author.guild_permissions.manage_roles and not ctx.author.guild_permissions.administrator:
            await ctx.send("このコマンドは「ロールの管理」権限を持つ管理者のみ実行できます。")
            return
        if not isinstance(ctx.channel, (discord.TextChannel, discord.Thread)):
            await ctx.send("このチャンネルにはパネルを設置できません。サーバーのテキストチャンネルで実行してください。")
            return
        try:
            await ctx.send(
                "**ゲームタグ登録・Steam自動取得**\n\n"
                "**タグ登録・更新：** タグを手動で選択・修正します。\n"
                "**Steam連携：** Steamプロフィールから所持ゲームを自動取得します。\n"
                "（プロフィールが非公開の場合、正常に取得できないことがあります。）",
                view=TagPanelView(self),
            )
            print(f"[GameSchedule] テキストコマンドでパネルを設置しました: channel_id={ctx.channel.id}, user_id={ctx.author.id}")
        except discord.Forbidden:
            await ctx.send("Botにこのチャンネルの「チャンネルを見る」「メッセージを送信」権限がありません。")
        except discord.HTTPException as exc:
            print(f"[GameSchedule] テキストコマンドのパネル設置エラー: {type(exc).__name__}: {exc}")
            await ctx.send("パネルを設置できませんでした。Botログを確認してください。")

    @post_tag_panel_text.error
    async def post_tag_panel_text_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドは「ロールの管理」権限を持つ管理者のみ実行できます。")
            return
        original = getattr(error, "original", error)
        print(f"[GameSchedule] game-tag-panelテキストコマンドエラー: {type(original).__name__}: {original}")
        await ctx.send("パネル設置コマンドでエラーが発生しました。Botログを確認してください。")

    async def _sync_user(self, interaction: discord.Interaction, steam_id: str) -> tuple[int, int, int]:
        key = os.getenv("STEAM_API_KEY")
        if not key:
            raise ValueError("STEAM_API_KEYが未設定です。.envに `STEAM_API_KEY=取得したSteam Web APIキー` を設定してBotを再起動してください。")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.get(f"{STEAM_API}/IPlayerService/GetOwnedGames/v1/", params={"key": key, "steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1}) as response:
                response.raise_for_status()
                payload = await response.json()
        steam_data = payload.get("response", {})
        if "games" not in steam_data:
            # 非公開プロフィールではSteam APIが空のresponseを返すことがある。
            # これを「ゲームを一つも持っていない」と誤認して既存キャッシュ/ロールを消さない。
            if "game_count" not in steam_data:
                raise ValueError("Steamから所持ゲーム一覧を取得できません。Steamプロフィールのゲーム公開設定とSteam APIキーを確認してください。")
            game_count = int(steam_data.get("game_count") or 0)
            if game_count:
                raise ValueError("Steamのゲーム件数は取得できましたが、ゲーム一覧は非公開です。既存の同期データは保持しました。")
            games = []
        else:
            games = steam_data.get("games") or []
            expected_count = steam_data.get("game_count")
            if expected_count is not None and int(expected_count) > len(games):
                raise ValueError("Steamのゲーム一覧を完全に取得できませんでした。既存の同期データは保持しました。")
        db = _db()

        def cache_games() -> None:
            collection = db.collection(GAMES)
            discord_id = str(interaction.user.id)
            old_docs = list(collection.where(filter=FieldFilter("discord_id", "==", discord_id)).stream())
            # 古い保存形式を再同期時にまとめて整理する。
            old_docs.extend(collection.where(filter=FieldFilter("steam_id64", "==", steam_id)).stream())
            old_docs_by_path = {doc.reference.path: doc for doc in old_docs}
            batch = db.batch()
            count = 0
            for doc in old_docs_by_path.values():
                batch.delete(doc.reference)
                count += 1
                if count == 450:
                    batch.commit()
                    batch = db.batch()
                    count = 0
            for game in games:
                ref = collection.document(f"{discord_id}_{game['appid']}")
                batch.set(ref, {
                    "discord_id": discord_id,
                    "app_id": game["appid"],
                    "game_name": game.get("name", str(game["appid"])),
                })
                count += 1
                if count == 450:
                    batch.commit()
                    batch = db.batch()
                    count = 0
            if count:
                batch.commit()

        await asyncio.to_thread(cache_games)
        if interaction.guild_id is None:
            return len(games), 0, 0
        print("[GameSchedule] Steam所持ゲームキャッシュを更新しました。")
        # Steam連携はタグ候補の作成を行わず、管理者が表示対象に設定したSteamタグだけを同期する。
        tags = await asyncio.to_thread(lambda: [dict(doc.to_dict(), id=doc.id) for doc in db.collection(TAGS).where(
            filter=FieldFilter("guild_id", "==", interaction.guild_id)
        ).stream() if not doc.to_dict().get("is_custom", False)
            and not doc.to_dict().get("is_deleted", False)
            and _tag_is_visible(doc.to_dict())])
        if interaction.guild is None:
            return len(games), 0, 0
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                return len(games), 0, 0
        owned_names = [game.get("name", "").casefold() for game in games]
        owned_app_ids = {str(game.get("appid")) for game in games}
        additions = 0
        failures = 0
        unmatched_visible_tags: list[str] = []
        for tag in tags:
            role_id = int(tag.get("role_id") or 0)
            role = interaction.guild.get_role(role_id) if role_id else None
            if role is None:
                try:
                    # 表示対象なのにロールが欠落している場合は同期時に再作成して紐付ける。
                    role = await self.enable_steam_tag(interaction.guild, tag)
                except Exception as exc:
                    failures += 1
                    print(f"[GameSchedule] 表示Steamタグのロール準備失敗: tag={tag.get('tag_name')}, error={type(exc).__name__}: {exc}")
                    continue
            # app_idを優先しつつ、古い/誤ったapp_idで取りこぼさないようSteam名・照合語も確認。
            match_terms = [str(tag.get(field) or "").strip().casefold()
                           for field in ("match_keyword", "steam_game_name", "tag_name")]
            match_terms = [term for term in dict.fromkeys(match_terms) if len(term) >= 3]
            owns = (
                (tag.get("app_id") is not None and str(tag["app_id"]) in owned_app_ids)
                or any(term in name for term in match_terms for name in owned_names)
            )
            if not owns:
                unmatched_visible_tags.append(str(tag.get("tag_name") or tag.get("steam_game_name") or "不明"))
            try:
                if owns and role not in member.roles:
                    await member.add_roles(role, reason="Steam所持ゲーム同期")
                    additions += 1
                elif not owns and role in member.roles:
                    await member.remove_roles(role, reason="Steam所持ゲーム同期")
            except discord.HTTPException as exc:
                failures += 1
                print(f"[GameSchedule] Steamロール同期失敗: user_id={member.id}, tag={tag.get('tag_name')}, role_id={role.id}, error={exc}")
        if unmatched_visible_tags:
            print("[GameSchedule] 所持ゲームと一致しなかった表示タグ: " + ", ".join(unmatched_visible_tags[:30]))
        return len(games), additions, failures

    @app_commands.command(name="sync", description="Steam所持ゲームとゲームロールを同期")
    async def sync(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        try:
            user = await asyncio.to_thread(_db().collection(USERS).document(str(interaction.user.id)).get)
        except Exception as exc:
            print(f"[GameSchedule] Firestore連携情報取得エラー: {exc}")
            await interaction.response.send_message("Firestoreから連携情報を取得できませんでした。", ephemeral=True)
            return
        if not user.exists:
            await interaction.response.send_message("先に `/link` でSteamアカウントを連携してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            count, additions, failures = await self._sync_user(interaction, user.to_dict()["steam_id64"])
        except (ValueError, aiohttp.ClientError, discord.HTTPException) as exc:
            await interaction.followup.send(f"同期に失敗しました: {exc}", ephemeral=True)
            return
        except Exception as exc:
            print(f"[GameSchedule] Steam同期エラー: {exc}")
            await interaction.followup.send("Steamゲーム情報またはFirestoreの同期に失敗しました。", ephemeral=True)
            return
        await interaction.followup.send(
            f"所持ゲーム {count} 件を取得しました。ロール追加 {additions} 件です。"
            + (f" ロール同期に失敗したタグが {failures} 件あります。Botログとロール権限を確認してください。" if failures else ""),
            ephemeral=True,
        )

    @app_commands.command(name="game-tag-manage", description="Steam・手動ゲームタグの表示名、ロール、公開状態を管理（管理者）")
    @app_commands.default_permissions(manage_roles=True)
    async def manage_steam_tags(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if not (interaction.permissions.manage_roles or interaction.permissions.administrator):
            await interaction.response.send_message("このコマンドは「ロールの管理」権限を持つ管理者のみ実行できます。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            tags = await self.get_guild_tags(interaction.guild.id, visible_only=False)
        except Exception as exc:
            print(f"[GameSchedule] Steamタグ管理一覧取得エラー: {exc}")
            await interaction.followup.send("Firestoreからタグ一覧を取得できませんでした。", ephemeral=True)
            return
        tags.sort(key=lambda tag: str(tag.get("steam_game_name") or tag.get("tag_name", "")).casefold())
        view = SteamTagManageView(self, tags)
        await interaction.edit_original_response(content=view.prompt(), view=view)

    @commands.command(name="game-tag-manage", help="Steam・手動ゲームタグとロール名、表示状態を管理します（ロール管理権限が必要）")
    async def manage_steam_tags_text(self, ctx: commands.Context) -> None:
        """チャット本文に `/game-tag-manage` と入力した場合の互換コマンド。"""
        if ctx.guild is None:
            await ctx.send("サーバー内で実行してください。")
            return
        if not (ctx.author.guild_permissions.manage_roles or ctx.author.guild_permissions.administrator):
            await ctx.send("このコマンドは「ロールの管理」権限を持つ管理者のみ実行できます。")
            return
        try:
            tags = await self.get_guild_tags(ctx.guild.id, visible_only=False)
        except Exception as exc:
            print(f"[GameSchedule] Steamタグ管理一覧取得エラー（テキストコマンド）: {exc}")
            await ctx.send("Firestoreからタグ一覧を取得できませんでした。Botログを確認してください。")
            return
        tags.sort(key=lambda tag: str(tag.get("steam_game_name") or tag.get("tag_name", "")).casefold())
        view = SteamTagManageView(self, tags)
        await ctx.send(view.prompt(), view=view)

    @manage_steam_tags_text.error
    async def manage_steam_tags_text_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        original = getattr(error, "original", error)
        print(f"[GameSchedule] game-tag-manageテキストコマンドエラー: {type(original).__name__}: {original}")
        await ctx.send("タグ管理画面を表示できませんでした。Botログを確認してください。")

    @app_commands.command(name="game-tag-add", description="募集用ゲームタグとSteam照合キーワードを追加（管理者）")
    @app_commands.describe(tag_name="募集に表示するタグ名", match_keyword="Steamゲーム名に含まれる語。空欄ならカスタムタグ", custom="Minecraft等のカスタムタグとして登録")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def add_tag(self, interaction: discord.Interaction, tag_name: str, match_keyword: str = "", custom: bool = False) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        is_custom = custom or not match_keyword.strip()
        role = discord.utils.get(interaction.guild.roles, name=tag_name)
        try:
            if role is None:
                role = await interaction.guild.create_role(
                    name=tag_name, mentionable=True, reason="ゲームタグ登録"
                )
            elif not role.mentionable:
                await role.edit(mentionable=True, reason="募集でのタグメンションを有効化")
            doc_id = f"{interaction.guild.id}_{hashlib.sha256(tag_name.casefold().encode()).hexdigest()[:32]}"
            data = {"guild_id": interaction.guild.id, "tag_name": tag_name, "match_keyword": match_keyword.strip(),
                    "role_id": role.id if role else None, "is_custom": is_custom,
                    "is_visible": True}
            await asyncio.to_thread(_db().collection(TAGS).document(doc_id).set, data)
        except Exception as exc:
            print(f"[GameSchedule] ゲームタグ保存エラー: {exc}")
            await interaction.response.send_message("ゲームタグを保存できませんでした。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"タグ「{tag_name}」を登録しました（{'カスタム追加' if is_custom else 'Steam自動取得'}）。"
            "募集タグとしてメンションできるロールを紐づけました。",
            ephemeral=True,
        )

    @app_commands.command(name="recruit", description="今すぐまたは日時を選んでゲーム募集を作成")
    async def recruit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            tags = await self.get_guild_tags(interaction.guild_id)
        except Exception as exc:
            print(f"[GameSchedule] 募集タグ取得エラー: {exc}")
            await interaction.followup.send("Firestoreから募集タグを取得できませんでした。", ephemeral=True)
            return
        if not tags:
            await interaction.followup.send("募集タグがありません。管理者に `/game-tag-add` で登録してもらってください。", ephemeral=True)
            return
        view = RecruitDateView(self, tags[:25])
        await interaction.edit_original_response(content=view.prompt(), view=view)

    @app_commands.command(name="game-tag-list", description="このサーバーの募集タグを表示")
    async def list_tags(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        try:
            tags = await self.get_guild_tags(interaction.guild_id, visible_only=False)
        except Exception as exc:
            print(f"[GameSchedule] タグ一覧取得エラー: {exc}")
            await interaction.response.send_message("Firestoreからタグを取得できませんでした。", ephemeral=True)
            return
        can_manage = interaction.permissions.manage_roles or interaction.permissions.administrator
        if not can_manage:
            tags = [tag for tag in tags if tag.get("is_visible", True)]
        lines = []
        for tag in tags:
            state = "表示" if tag.get("is_visible", True) else "非表示"
            if tag.get("is_custom", False):
                source = "カスタム追加"
            else:
                steam_name = tag.get("steam_game_name") or tag.get("match_keyword") or tag["tag_name"]
                source = f"Steam: {steam_name} → {tag['tag_name']}"
            lines.append(f"• {source} [{state}]")
        text = "\n".join(lines) or "登録されたタグはありません。"
        await interaction.response.send_message(text[:1900], ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameSchedule(bot))
