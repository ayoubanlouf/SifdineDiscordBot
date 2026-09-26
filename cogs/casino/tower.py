from __future__ import annotations
import asyncio
import io
import os
import random
from typing import Optional, Union, Dict

import discord
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import clear_user_game

_TOWER_ASSETS = {}


def get_tower_door(name: str) -> Image.Image:
    if name not in _TOWER_ASSETS:
        p = os.path.join("assets", "tower", f"door_{name}.png")
        if os.path.exists(p):
            img = Image.open(p).convert("RGBA")
            _TOWER_ASSETS[name] = img.resize((110, 118), Image.Resampling.LANCZOS)
        else:
            _TOWER_ASSETS[name] = Image.new("RGBA", (110, 118), (80, 80, 80, 255))
    return _TOWER_ASSETS[name]


def render_tower_board(current_floor: int, story_doors: dict, game_state: str = "active") -> io.BytesIO:
    door_closed = get_tower_door("closed")
    door_win = get_tower_door("win")
    door_trap = get_tower_door("trap")

    w, h = 820, 680
    board = Image.new("RGBA", (w, h), (11, 10, 15, 255))
    draw = ImageDraw.Draw(board)

    frame_color = (197, 160, 89, 220)
    if game_state == "lost":
        frame_color = (200, 60, 60, 220)
    elif game_state in ("won", "cashed_out"):
        frame_color = (60, 200, 100, 220)

    draw.rounded_rectangle([(8, 8), (w - 8, h - 8)], radius=18, outline=frame_color, width=3)
    draw.rounded_rectangle([(14, 14), (w - 14, h - 14)], radius=14, outline=(40, 35, 45, 150), width=1)

    # Header - Minimalist as requested: "TOWER OF DOORS"
    draw.rectangle([(18, 18), (w - 18, 62)], fill=(18, 14, 24, 255))
    draw.line([(18, 62), (w - 18, 62)], fill=frame_color, width=2)

    try:
        font_title = ImageFont.truetype("arial.ttf", 26)
        font_badge_small = ImageFont.truetype("arial.ttf", 12)
        font_badge = ImageFont.truetype("arial.ttf", 22)
        font_door = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_badge_small = ImageFont.load_default()
        font_badge = ImageFont.load_default()
        font_door = ImageFont.load_default()

    title_text = "TOWER OF DOORS"
    title_color = (245, 215, 125)
    if game_state == "lost":
        title_text = "TOWER OF DOORS • COLLAPSED"
        title_color = (255, 90, 90)
    elif game_state == "won":
        title_text = "TOWER OF DOORS • SUMMIT CONQUERED!"
        title_color = (100, 240, 140)
    elif game_state == "cashed_out":
        title_text = "TOWER OF DOORS • CASHED OUT"
        title_color = (245, 215, 125)

    draw.text((w // 2, 40), title_text, fill=title_color, font=font_title, anchor="mm")

    door_w, door_h = 110, 118
    multipliers = {4: "50.0x", 3: "20.0x", 2: "8.0x", 1: "3.0x"}
    titles = {4: "SUMMIT 4", 3: "STORY 3", 2: "STORY 2", 1: "STORY 1"}

    row_start_y = 74
    row_gap = 146

    for idx, floor in enumerate([4, 3, 2, 1]):
        ry = row_start_y + idx * row_gap

        is_active = (floor == current_floor and game_state == "active")
        is_cleared = (floor < current_floor or (floor == current_floor and game_state in ("won", "cashed_out")))
        is_failed = (floor == current_floor and game_state == "lost")
        is_locked = (floor > current_floor)

        bg_color = (26, 21, 35, 230)
        border_color = (75, 65, 90, 160)
        border_width = 1

        if is_active:
            bg_color = (42, 33, 16, 240)
            border_color = (220, 180, 80, 255)
            border_width = 3
        elif is_cleared:
            bg_color = (20, 36, 24, 230)
            border_color = (50, 130, 70, 200)
        elif is_failed:
            bg_color = (40, 18, 18, 240)
            border_color = (220, 60, 60, 255)
            border_width = 3

        draw.rounded_rectangle([(30, ry), (w - 30, ry + 134)], radius=12, fill=bg_color, outline=border_color, width=border_width)

        badge_bg = (50, 42, 65, 200)
        badge_text_col = (220, 220, 220)
        status_label = "LOCKED"

        if is_active:
            badge_bg = (212, 175, 55, 240)
            badge_text_col = (15, 12, 10)
            status_label = "ACTIVE"
        elif is_cleared:
            badge_bg = (25, 105, 55, 230)
            badge_text_col = (255, 255, 255)
            status_label = "CLEARED"
        elif is_failed:
            badge_bg = (190, 45, 45, 240)
            badge_text_col = (255, 255, 255)
            status_label = "FAILED"

        draw.rounded_rectangle([(45, ry + 14), (175, ry + 120)], radius=8, fill=badge_bg)
        draw.text((110, ry + 39), titles[floor], fill=badge_text_col, font=font_badge_small, anchor="mm")
        draw.text((110, ry + 72), multipliers[floor], fill=badge_text_col, font=font_badge, anchor="mm")
        draw.text((110, ry + 101), f"[{status_label}]", fill=badge_text_col, font=font_badge_small, anchor="mm")

        door_xs = [240, 425, 610]
        doors_for_floor = story_doors.get(floor, ["closed", "closed", "closed"])

        for d_idx, x in enumerate(door_xs):
            dtype = doors_for_floor[d_idx]
            if dtype == "win":
                d_img = door_win
            elif dtype == "trap":
                d_img = door_trap
            else:
                d_img = door_closed

            if is_locked:
                dimmed = ImageEnhance.Brightness(d_img).enhance(0.4)
                board.paste(dimmed, (x, ry + 8), dimmed)
            elif is_active:
                draw.rounded_rectangle([(x - 6, ry + 3), (x + door_w + 6, ry + door_h + 13)], radius=8, outline=(245, 205, 80, 220), width=2)
                board.paste(d_img, (x, ry + 8), d_img)
                draw.text((x + door_w // 2, ry + door_h + 3), f"Door {d_idx + 1}", fill=(245, 215, 120), font=font_door, anchor="mm")
            elif is_failed and dtype == "trap":
                draw.rounded_rectangle([(x - 6, ry + 3), (x + door_w + 6, ry + door_h + 13)], radius=8, outline=(245, 60, 60, 220), width=2)
                board.paste(d_img, (x, ry + 8), d_img)
                draw.text((x + door_w // 2, ry + door_h + 3), "TRAP!", fill=(255, 80, 80), font=font_door, anchor="mm")
            else:
                board.paste(d_img, (x, ry + 8), d_img)

    buf = io.BytesIO()
    board.convert("RGB").save(buf, format="JPEG", quality=90, optimize=True)
    buf.seek(0)
    return buf


class TowerDoorButton(discord.ui.Button):
    def __init__(self, door_index: int):
        super().__init__(
            label=f"Door {door_index + 1}",
            style=discord.ButtonStyle.secondary,
            emoji="🚪",
            row=0
        )
        self.door_index = door_index

    async def callback(self, interaction: discord.Interaction):
        view: TowerGameView = self.view
        await view.handle_door_choice(interaction, self.door_index)


class TowerCashoutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="💰 Cash Out (Clear Story 1 first)",
            style=discord.ButtonStyle.success,
            disabled=True,
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        view: TowerGameView = self.view
        await view.handle_cashout(interaction)


class TowerGameView(discord.ui.View):
    def __init__(self, author: discord.Member, bet: int, cog):
        super().__init__(timeout=60)
        self.author = author
        self.bet = bet
        self.cog = cog
        self.current_floor = 1
        self.game_over = False
        self.session_id: Optional[str] = None
        self.message: Optional[discord.Message] = None

        self.winning_doors = {
            1: random.randint(0, 2),
            2: random.randint(0, 2),
            3: random.randint(0, 2),
            4: random.randint(0, 2),
        }

        self.story_doors = {
            1: ["closed", "closed", "closed"],
            2: ["closed", "closed", "closed"],
            3: ["closed", "closed", "closed"],
            4: ["closed", "closed", "closed"],
        }

        self.multipliers = {1: 3.0, 2: 8.0, 3: 20.0, 4: 50.0}

        self.door_buttons = [TowerDoorButton(i) for i in range(3)]
        for btn in self.door_buttons:
            self.add_item(btn)

        self.cashout_button = TowerCashoutButton()
        self.add_item(self.cashout_button)

    def _update_buttons(self):
        if self.current_floor <= 1:
            self.cashout_button.disabled = True
            self.cashout_button.label = "💰 Cash Out (Clear Story 1 first)"
        else:
            prev_mult = self.multipliers[self.current_floor - 1]
            if self.bet > 0:
                gross_payout = int(round(self.bet * prev_mult))
                self.cashout_button.label = f"💰 Cash Out ({prev_mult:.1f}x • {gross_payout:,} TAD)"
            else:
                self.cashout_button.label = f"💰 Cash Out ({prev_mult:.1f}x)"
            self.cashout_button.disabled = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    async def handle_door_choice(self, interaction: discord.Interaction, door_index: int):
        if self.game_over:
            return

        winning_door = self.winning_doors[self.current_floor]
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

        if door_index == winning_door:
            # Safe door!
            self.story_doors[self.current_floor][door_index] = "win"
            current_mult = self.multipliers[self.current_floor]

            if self.current_floor == 4:
                # Summit Reached! (50.0x JACKPOT!)
                self.game_over = True
                self.stop()
                for item in self.children:
                    item.disabled = True

                if self.session_id and self.cog:
                    await self.cog.complete_active_session(self.session_id)

                gross_payout = int(round(self.bet * current_mult))
                tax = round(gross_payout * 0.02)
                net_payout = gross_payout - tax
                if net_payout <= 0 and gross_payout > 0:
                    net_payout = 1
                    tax = gross_payout - 1
                net_profit = net_payout - self.bet

                guild = getattr(interaction, "guild", None) or (self.message.guild if self.message else None)
                guild_id = guild.id if guild else None

                board_bytes = await asyncio.to_thread(render_tower_board, self.current_floor, self.story_doors, "won")
                file = discord.File(board_bytes, filename="tower.jpg")

                if self.bet > 0:
                    desc = (
                        f"🎉 **{self.author.mention}** climbed all 4 stories to the summit!\n\n"
                        f"💵 **Net Profit:** 🟢 **+{format_tad(net_profit)}**\n"
                        f"💰 **Gross Payout:** **{gross_payout:,}** TAD (`{tax:,}` TAD tax split)\n"
                    )
                else:
                    desc = f"🎉 **{self.author.mention}** climbed all 4 stories to the summit! Multiplier: **50.0x** 👑"

                embed = discord.Embed(
                    title="👑 TOWER CONQUERED — 50.0x JACKPOT!",
                    description=desc,
                    color=0x000000
                )
                embed.set_image(url="attachment://tower.jpg")
                embed.set_footer(text="Sifdine Casino • Max Payout Achieved!")
                await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
                clear_user_game(self.cog.bot, self.author.id)
                self.stop()

                # Execute database payout asynchronously in background
                if self.bet > 0 and economy_cog:
                    async def _payout_summit():
                        try:
                            await economy_cog.apply_tax_and_add_balance(
                                self.author.id, gross_payout, context="Tower Jackpot (50.0x)", vault="casino"
                            )
                            if guild_id and self.cog:
                                await self.cog.record_minigame_win(guild_id, self.author.id, "tower", earnings=max(0, net_profit))
                        except Exception as e:
                            print(f"[Tower summit payout error]: {e}")
                    asyncio.create_task(_payout_summit())
                elif guild_id and self.cog:
                    asyncio.create_task(self.cog.record_minigame_win(guild_id, self.author.id, "tower"))
                return

            else:
                self.current_floor += 1
                self._update_buttons()

                board_bytes = await asyncio.to_thread(render_tower_board, self.current_floor, self.story_doors, "active")
                file = discord.File(board_bytes, filename="tower.jpg")

                prev_mult = self.multipliers[self.current_floor - 1]
                cur_gross = int(round(self.bet * prev_mult))

                bank_str = f"💰 **Current Bank:** **{cur_gross:,}** TAD (**{prev_mult:.1f}x**)\n" if self.bet > 0 else f"📈 **Current Multiplier:** **{prev_mult:.1f}x**\n"

                embed = discord.Embed(
                    title="🏰 Tower of Doors",
                    description=(
                        f"✨ **Story {self.current_floor - 1} Cleared!** l9iti lbab rrab7.\n"
                        f"{bank_str}"
                    ),
                    color=0x000000
                )
                embed.set_image(url="attachment://tower.jpg")
                author_name = getattr(self.author, "display_name", str(self.author))
                embed.set_footer(text=f" {author_name} • Khtar door wla Cash Out")
                await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

        else:
            # Trap door!
            self.game_over = True
            self.stop()
            self.story_doors[self.current_floor][door_index] = "trap"
            self.story_doors[self.current_floor][winning_door] = "win"

            for item in self.children:
                item.disabled = True

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

            board_bytes = await asyncio.to_thread(render_tower_board, self.current_floor, self.story_doors, "lost")
            file = discord.File(board_bytes, filename="tower.jpg")

            loss_str = f"\n💸 **Loss:** 🔴 **-{format_tad(self.bet)}**" if self.bet > 0 else ""
            embed = discord.Embed(
                title="💥 TOWER COLLAPSED!",
                description=(
                    f"💀 **{self.author.mention}** khserti f **Story {self.current_floor}**!\n\n"
                    f"Door **{winning_door + 1}** kan howa lbab rrab7.{loss_str}"
                ),
                color=0x000000
            )
            embed.set_image(url="attachment://tower.jpg")
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
            clear_user_game(self.cog.bot, self.author.id)

            if self.bet > 0 and economy_cog:
                asyncio.create_task(economy_cog.process_gamble_loss(self.bet, context=f"Tower Loss (Story {self.current_floor})"))

    async def handle_cashout(self, interaction: discord.Interaction):
        if self.game_over:
            return
        if self.current_floor <= 1:
            await interaction.response.send_message("⚠️ Khassek tfot Story 1 9bel ma dir Cash Out!", ephemeral=True)
            return

        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True

        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)

        mult = self.multipliers[self.current_floor - 1]
        gross_payout = int(round(self.bet * mult))
        tax = round(gross_payout * 0.02)
        net_payout = gross_payout - tax
        if net_payout <= 0 and gross_payout > 0:
            net_payout = 1
            tax = gross_payout - 1
        net_profit = net_payout - self.bet

        guild = getattr(interaction, "guild", None) or (self.message.guild if self.message else None)
        guild_id = guild.id if guild else None
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

        board_bytes = await asyncio.to_thread(render_tower_board, self.current_floor - 1, self.story_doors, "cashed_out")
        file = discord.File(board_bytes, filename="tower.jpg")

        if self.bet > 0:
            desc = (
                f"🎉 **{self.author.mention}** cashed out safely at **{mult:.1f}x**!\n\n"
                f"💵 **Net Profit:** 🟢 **+{format_tad(net_profit)}**\n"
                f"💰 **Gross Payout:** **{gross_payout:,}** TAD (`{tax:,}` TAD tax split)\n"
            )
        else:
            desc = f"🎉 **{self.author.mention}** cashed out safely at **{mult:.1f}x**!"

        embed = discord.Embed(
            title="💰 CASHED OUT!",
            description=desc,
            color=0x000000
        )
        embed.set_image(url="attachment://tower.jpg")
        embed.set_footer(text="Sifdine Casino • Winnings deposited to your wallet")
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        clear_user_game(self.cog.bot, self.author.id)

        if self.bet > 0 and economy_cog:
            async def _payout_cashout():
                try:
                    await economy_cog.apply_tax_and_add_balance(
                        self.author.id, gross_payout, context=f"Tower Cashout ({mult:.1f}x)", vault="casino"
                    )
                    if guild_id and self.cog:
                        await self.cog.record_minigame_win(guild_id, self.author.id, "tower", earnings=max(0, net_profit))
                except Exception as e:
                    print(f"[Tower cashout payout error]: {e}")
            asyncio.create_task(_payout_cashout())
        elif guild_id and self.cog:
            asyncio.create_task(self.cog.record_minigame_win(guild_id, self.author.id, "tower"))

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True
        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        eco_msg = ""
        if self.current_floor == 1:
            if self.bet > 0 and economy_cog:
                await economy_cog.add_balance(self.author.id, self.bet, context="Tower Quit Refund")
                eco_msg = f" (Rje3 lik l bet: {format_tad(self.bet)})"
        else:
            mult = self.multipliers[self.current_floor - 1]
            gross = int(round(self.bet * mult))
            if self.bet > 0 and economy_cog:
                net, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross, context=f"Tower Quit Cashout ({mult:.1f}x)", vault="casino")
                net_profit = net - self.bet
                eco_msg = f" (Auto-cashed out: +{format_tad(net_profit)})"
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "tower", earnings=max(0, net_profit))
        clear_user_game(self.cog.bot, self.author.id)
        embed = discord.Embed(
            title="🚪 Tower of Doors — Quit",
            description=f"🚪 **{user.mention}** kherjti mn Tower!{eco_msg}",
            color=0x000000
        )
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
        return "🚪 Kherjti mn match dial **Tower**!"

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

            if self.current_floor == 1:
                if self.bet > 0 and economy_cog:
                    await economy_cog.add_balance(self.author.id, self.bet, context="Tower Timeout Refund")
                refund_str = f"Ma khtarity ta door, rje3 lik l bet: {format_tad(self.bet)}." if self.bet > 0 else "Ma khtarity ta door f Story 1."
                embed = discord.Embed(
                    title="⏰ Game Timed Out!",
                    description=refund_str,
                    color=0x000000
                )
            else:
                mult = self.multipliers[self.current_floor - 1]
                gross_payout = int(round(self.bet * mult))
                net_profit = gross_payout - self.bet
                tax = 0
                if self.bet > 0 and economy_cog:
                    net_payout, tax = await economy_cog.apply_tax_and_add_balance(
                        self.author.id, gross_payout, context=f"Tower Auto-Cashout ({mult:.1f}x)", vault="casino"
                    )
                    net_profit = net_payout - self.bet
                if self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "tower", earnings=max(0, net_profit))

                if self.bet > 0:
                    desc = (
                        f"🎉 Kounti wasel l **{mult:.1f}x**, derti Auto-Cash Out!\n\n"
                        f"💵 **Net Profit:** 🟢 **+{format_tad(net_profit)}**\n"
                        f"💰 **Gross Payout:** **{gross_payout:,}** TAD (`{tax:,}` TAD tax split)\n"
                    )
                else:
                    desc = f"🎉 Kounti wasel l **{mult:.1f}x**, derti Auto-Cash Out!"

                embed = discord.Embed(
                    title="⏰ Game Timed Out (Auto-Cashed Out)!",
                    description=desc,
                    color=0x000000
                )

            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
            clear_user_game(self.cog.bot, self.author.id)
