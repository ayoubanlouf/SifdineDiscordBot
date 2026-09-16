import asyncio
import random
import io
import os
import aiohttp
import time
import json
import math
import urllib.parse
from typing import Optional, Union

import discord
from discord.ext import commands
from discord.ui import Button, View
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from converters import FuzzyMember
from cogs.economy import (
    parse_bet_argument, format_tad, TAD_EMOJI, calculate_pvp_payout,
    not_fraud, TAX_RATE, get_current_week_start_ts, get_next_week_start_ts
)
from cogs.games.helpers import record_minigame_win, record_minigame_loss

# ============ PLAYING CARDS & TABLE RENDERING ============

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_NAME_MAP = {
    'A': 'ace', 'K': 'king', 'Q': 'queen', 'J': 'jack',
    '10': '10', '9': '9', '8': '8', '7': '7', '6': '6',
    '5': '5', '4': '4', '3': '3', '2': '2'
}
SUIT_NAME_MAP = {
    '♠️': 'spades', '♥️': 'hearts', '♦️': 'diamonds', '♣️': 'clubs'
}
RANK_VALUES = {r: i + 2 for i, r in enumerate(RANKS)}

def create_bj_deck():
    deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck

def calculate_bj_score(hand):
    val = 0
    aces = 0
    for card in hand:
        r = card["rank"]
        if r in ["J", "Q", "K"]:
            val += 10
        elif r == "A":
            aces += 1
            val += 11
        else:
            val += int(r)
    while val > 21 and aces > 0:
        val -= 10
        aces -= 1
    return val

def format_bj_card(card):
    return f"`{card['rank']}{card['suit']}`"

def render_bj_table(dealer_hand, player_hand, hide_dealer=True) -> io.BytesIO:
    cw, ch = 110, 160
    overlap = 40

    d_len = len(dealer_hand)
    p_len = len(player_hand)
    max_cards = max(d_len, p_len, 2)
    img_w = max(480, max_cards * (cw - overlap) + overlap + 60)
    img_h = 390

    canvas = Image.new('RGBA', (img_w, img_h), (16, 24, 18, 255))
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle([(10, 8), (img_w - 10, 185)], radius=10, fill=(22, 34, 26, 255), outline=(38, 62, 45, 255), width=2)
    draw.rounded_rectangle([(10, 198), (img_w - 10, 380)], radius=10, fill=(22, 34, 26, 255), outline=(38, 62, 45, 255), width=2)

    x_start = 30
    y_d = 16
    for idx, c in enumerate(dealer_hand):
        x = x_start + idx * (cw - overlap)
        if idx == 1 and hide_dealer:
            card_back = Image.new('RGBA', (cw, ch), (28, 44, 70, 255))
            b_draw = ImageDraw.Draw(card_back)
            b_draw.rounded_rectangle([(0, 0), (cw-1, ch-1)], radius=6, fill=(30, 50, 85, 255), outline=(180, 150, 90, 255), width=3)
            canvas.paste(card_back, (x, y_d), card_back)
        else:
            r = RANK_NAME_MAP.get(c['rank'], c['rank'].lower())
            s = SUIT_NAME_MAP.get(c['suit'], 'spades')
            path = os.path.join('assets', 'playing_cards', f'{r}_of_{s}.png')
            if os.path.exists(path):
                c_img = Image.open(path).convert('RGBA').resize((cw, ch), Image.Resampling.LANCZOS)
                canvas.paste(c_img, (x, y_d), c_img)

    y_p = 208
    for idx, c in enumerate(player_hand):
        x = x_start + idx * (cw - overlap)
        r = RANK_NAME_MAP.get(c['rank'], c['rank'].lower())
        s = SUIT_NAME_MAP.get(c['suit'], 'spades')
        path = os.path.join('assets', 'playing_cards', f'{r}_of_{s}.png')
        if os.path.exists(path):
            c_img = Image.open(path).convert('RGBA').resize((cw, ch), Image.Resampling.LANCZOS)
            canvas.paste(c_img, (x, y_p), c_img)

    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf


class BlackjackView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=90)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.deck = create_bj_deck()
        self.player_hand = [self.deck.pop(), self.deck.pop()]
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]
        self.game_over = False
        self.message: Optional[discord.Message] = None

    def get_render_file(self, dealer_reveal=False):
        buf = render_bj_table(self.dealer_hand, self.player_hand, hide_dealer=not dealer_reveal)
        return discord.File(buf, filename="blackjack_table.png")

    def get_embed(self, dealer_reveal=False, outcome_text=""):
        p_score = calculate_bj_score(self.player_hand)
        d_score_str = f"**{calculate_bj_score(self.dealer_hand)}**" if dealer_reveal else "**?**"

        embed = discord.Embed(
            title="🃏 Blackjack Table",
            color=0x000000
        )
        embed.add_field(name="🤖 Dealer", value=f"Score: {d_score_str}", inline=True)
        embed.add_field(name=f"👤 {self.author.display_name}", value=f"Score: **{p_score}**", inline=True)
        if self.bet > 0:
            embed.add_field(name="💰 Stake", value=format_tad(self.bet), inline=True)
        embed.set_image(url="attachment://blackjack_table.png")

        if outcome_text:
            embed.description = outcome_text

        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    async def end_game(self, outcome_text: str, is_win: bool = False, is_push: bool = False, is_blackjack: bool = False, interaction: Optional[discord.Interaction] = None):
        self.game_over = True
        for item in self.children:
            item.disabled = True

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

        if self.bet > 0 and economy_cog:
            if is_blackjack:
                gross_payout = int(round(self.bet * 2.5))
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context="Blackjack Natural 21", vault="casino")
                net_profit = net_payout - self.bet
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "blackjack", earnings=max(0, net_profit))
                outcome_text += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)!"
            elif is_win:
                gross_payout = self.bet * 2
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context="Blackjack Win", vault="casino")
                net_profit = net_payout - self.bet
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "blackjack", earnings=max(0, net_profit))
                outcome_text += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)!"
            elif is_push:
                await economy_cog.add_balance(self.author.id, self.bet, context="Blackjack Push Refund")
                outcome_text += f"\n\n🤝 Rje3 lik l bet ta3k: {format_tad(self.bet)}."
            else:
                tax = await economy_cog.apply_lost_gamble_tax(self.bet, context="Blackjack Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                outcome_text += f"\n\n💥 Khesrti l bet: -{format_tad(self.bet)}{tax_str}."
                if self.message and self.message.guild:
                    await self.cog.record_minigame_loss(self.message.guild.id, self.author.id, "blackjack", loss_amount=self.bet)
        elif is_win and self.message and self.message.guild:
            await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "blackjack")
        elif not is_win and not is_push and self.message and self.message.guild:
            await self.cog.record_minigame_loss(self.message.guild.id, self.author.id, "blackjack", loss_amount=0)

        embed = self.get_embed(dealer_reveal=True, outcome_text=outcome_text)
        file = self.get_render_file(dealer_reveal=True)
        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
        elif self.message:
            await self.message.edit(embed=embed, view=self, attachments=[file])
        self.stop()

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                await economy_cog.add_balance(self.author.id, self.bet, context="Blackjack Timeout Refund")
            embed = self.get_embed(dealer_reveal=True, outcome_text=f"⏰ **Sala lwe9t!**\nGame timed out o rje3 lik l bet: {format_tad(self.bet)}." if self.bet > 0 else "⏰ **Sala lwe9t!** Game timed out.")
            file = self.get_render_file(dealer_reveal=True)
            try:
                await self.message.edit(embed=embed, view=self, attachments=[file])
            except Exception:
                pass

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.success, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return
        self.player_hand.append(self.deck.pop())
        p_score = calculate_bj_score(self.player_hand)

        if p_score > 21:
            await self.end_game(f"💥 **BUST!** Fat 21 (**{p_score}**). Khsrti!", is_win=False, interaction=interaction)
        elif p_score == 21:
            await self._dealer_turn(interaction, status_msg="🎯 **21!** Dealer ghadi yl3eb daba...")
        else:
            embed = self.get_embed()
            file = self.get_render_file()
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return
        await self._dealer_turn(interaction)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.primary, emoji="⚡")
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if self.bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(self.author.id)
            if w["balance"] < self.bet:
                await interaction.response.send_message(f"❌ Ma 3ndekch flous kafyin bach t double bet! (Khassek {format_tad(self.bet)})", ephemeral=True)
                return
            success = await economy_cog.deduct_balance(self.author.id, self.bet, context="Blackjack Double Down Stake")
            if not success:
                await interaction.response.send_message("❌ Flousk makafyinch bach t double!", ephemeral=True)
                return
            self.bet *= 2

        self.player_hand.append(self.deck.pop())
        p_score = calculate_bj_score(self.player_hand)
        if p_score > 21:
            await self.end_game(f"💥 **BUST!** Double down o fatet 21 (**{p_score}**). Khsrti!", is_win=False, interaction=interaction)
        else:
            await self._dealer_turn(interaction, status_msg="⚡ **Double Down!**")

    async def _dealer_turn(self, interaction: Optional[discord.Interaction] = None, status_msg=""):
        p_score = calculate_bj_score(self.player_hand)
        while calculate_bj_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())

        d_score = calculate_bj_score(self.dealer_hand)

        is_win = False
        is_push = False

        if d_score > 21:
            outcome = f"🏆 **Dealer BUSTED ({d_score})!** Rbe7ti l game!"
            is_win = True
        elif p_score > d_score:
            outcome = f"🏆 **Rbe7ti!** (**{p_score}** vs **{d_score}**)"
            is_win = True
        elif d_score > p_score:
            outcome = f"💥 **Dealer rbe7!** (**{d_score}** vs **{p_score}**)"
            is_win = False
        else:
            outcome = f"🤝 **Ta3adol (Push)!** (**{p_score}** vs **{d_score}**)"
            is_push = True

        if status_msg:
            outcome = f"{status_msg}\n\n{outcome}"

        await self.end_game(outcome, is_win=is_win, is_push=is_push, interaction=interaction)


# ============ MINES GAMBLE VIEW ============

class MinesGambleButton(discord.ui.Button):
    def __init__(self, x: int, y: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=y)
        self.x = x
        self.y = y

    async def callback(self, interaction: discord.Interaction):
        view: MinesGambleView = self.view
        await view.process_click(interaction, self.x, self.y, self)


class MinesGambleView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, bomb_count: int = 3, bet: int = 50):
        super().__init__(timeout=120)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.width = 4
        self.height = 4  # 4 rows x 4 columns = 16 tiles on rows 0-3, Row 4 dedicated to Cash Out
        self.bomb_count = bomb_count
        self.revealed_count = 0
        self.game_over = False
        self.message: Optional[discord.Message] = None

        all_cells = [(x, y) for y in range(self.height) for x in range(self.width)]
        self.bombs = set(random.sample(all_cells, self.bomb_count))
        self.buttons_map = {}

        for y in range(self.height):
            for x in range(self.width):
                btn = MinesGambleButton(x, y)
                self.add_item(btn)
                self.buttons_map[(x, y)] = btn

        self.multipliers = [
            1.00, 1.15, 1.35, 1.62, 1.98, 2.45, 3.10, 4.00, 5.30, 7.20,
            10.10, 14.80, 22.50, 36.00
        ]

    def get_current_multiplier(self) -> float:
        if self.revealed_count == 0:
            return 1.00
        idx = min(self.revealed_count, len(self.multipliers) - 1)
        return self.multipliers[idx]

    def get_next_multiplier(self) -> float:
        idx = min(self.revealed_count + 1, len(self.multipliers) - 1)
        return self.multipliers[idx]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="💰 Cash Out (1.00x)", style=discord.ButtonStyle.success, row=4)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return
        if self.revealed_count == 0:
            await interaction.response.send_message("⚠️ Khassek t uncoveri minimum 1 Gem 9bel ma dir Cash Out!", ephemeral=True)
            return

        mult = self.get_current_multiplier()
        self.game_over = True
        self._reveal_all_bombs()
        for item in self.children:
            item.disabled = True

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        embed = discord.Embed(
            title="💰 CASHED OUT!",
            description=f"🎉 **{self.author.mention}** rbe7ti b multiplier **{mult:.2f}x**!\nGems uncovered: **{self.revealed_count}** 💎",
            color=0x000000
        )

        if self.bet > 0 and economy_cog:
            gross_payout = int(round(self.bet * mult))
            net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context=f"Mines Win ({mult:.2f}x)", vault="casino")
            net_profit = net_payout - self.bet
            if self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines", earnings=max(0, net_profit))
            embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
        elif self.message and self.message.guild:
            await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines")

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            self._reveal_all_bombs()
            for item in self.children:
                item.disabled = True

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                if self.revealed_count == 0:
                    await economy_cog.add_balance(self.author.id, self.bet, context="Mines Timeout Refund")
                    desc = f"⏰ **Game Timed Out!**\nMa uncoveriti walo, rje3 lik l bet: {format_tad(self.bet)}."
                else:
                    mult = self.get_current_multiplier()
                    gross_payout = int(round(self.bet * mult))
                    net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context=f"Mines Auto-Cashout ({mult:.2f}x)", vault="casino")
                    net_profit = net_payout - self.bet
                    if self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines", earnings=max(0, net_profit))
                    desc = f"⏰ **Game Timed Out (Auto-Cashed Out)!**\nMultiplier: **{mult:.2f}x** • Net Payout: **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)."
            else:
                desc = "⏰ **Game Timed Out!**"

            embed = discord.Embed(title="💣 Mines Table — Timed Out", description=desc, color=0x000000)
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    def _reveal_all_bombs(self):
        for (x, y), btn in self.buttons_map.items():
            if (x, y) in self.bombs:
                btn.emoji = "💣"
                btn.label = None
                btn.style = discord.ButtonStyle.danger
            elif btn.style != discord.ButtonStyle.success:
                btn.emoji = "💎"
                btn.label = None
                btn.style = discord.ButtonStyle.secondary

    async def process_click(self, interaction: discord.Interaction, x: int, y: int, button: MinesGambleButton):
        if self.game_over:
            return

        if (x, y) in self.bombs:
            self.game_over = True
            button.emoji = "💥"
            button.label = None
            button.style = discord.ButtonStyle.danger
            self._reveal_all_bombs()
            for item in self.children:
                item.disabled = True

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            tax = 0
            if self.bet > 0 and economy_cog:
                tax = await economy_cog.apply_lost_gamble_tax(self.bet, context="Mines Loss")
            tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""

            g_id = self.message.guild.id if (self.message and self.message.guild) else (interaction.guild.id if interaction.guild else None)
            if g_id and self.cog:
                await self.cog.record_minigame_loss(g_id, self.author.id, "mines", loss_amount=self.bet if self.bet > 0 else 0)

            embed = discord.Embed(
                title="💥 BOOM! Game Over",
                description=f"💣 Tferg3at 3lik bomb f tile `({x+1}, {y+1})`! Khesrti -{format_tad(self.bet)}{tax_str}.",
                color=0x000000
            )
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
            return

        button.emoji = "💎"
        button.label = None
        button.style = discord.ButtonStyle.success
        button.disabled = True
        self.revealed_count += 1

        mult = self.get_current_multiplier()
        next_mult = self.get_next_multiplier()
        self.cashout_button.label = f"💰 Cash Out ({mult:.2f}x)"

        total_gems = (self.width * self.height) - self.bomb_count
        if self.revealed_count >= total_gems:
            self.game_over = True
            self._reveal_all_bombs()
            for item in self.children:
                item.disabled = True
            
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            embed = discord.Embed(
                title="🏆 FULL CLEAR! JACKPOT!",
                description=f"👑 **{self.author.mention}** uncoveriti ga3 gems (**{total_gems}/{total_gems}**)! Multiplier: **{mult:.2f}x**!",
                color=0x000000
            )
            if self.bet > 0 and economy_cog:
                gross_payout = int(round(self.bet * mult))
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context=f"Mines Jackpot ({mult:.2f}x)", vault="casino")
                net_profit = net_payout - self.bet
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines", earnings=max(0, net_profit))
                embed.add_field(name="💵 Net Payout", value=f"👑 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            elif self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines")
            
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
            return

        embed = discord.Embed(
            title="💣 Mines Table",
            description=(
                f"💎 Gems: **{self.revealed_count}/{total_gems}**\n"
                f"📈 Multiplier: **{mult:.2f}x** (Next: **{next_mult:.2f}x**)\n"
                f"💣 Bombs: **{self.bomb_count}**\n"
                + (f"💰 Stake: {format_tad(self.bet)}" if self.bet > 0 else "")
            ),
            color=0x000000
        )
        await interaction.response.edit_message(embed=embed, view=self)


# ============ TOWER OF DOORS (BURJ SIFDINE) ============

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
        font_badge = ImageFont.truetype("arial.ttf", 22)
        font_badge_small = ImageFont.truetype("arial.ttf", 12)
        font_door = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_badge = ImageFont.load_default()
        font_badge_small = ImageFont.load_default()
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

                # Execute database payout asynchronously in background so Discord never times out
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

                next_mult = self.multipliers[self.current_floor]
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

        # Execute database payout asynchronously in background so Discord never times out
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

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True

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


# ============ HIGHER LOWER VIEW ============

def draw_hl_card():
    rank = random.choice(RANKS)
    suit = random.choice(SUITS)
    return {"rank": rank, "suit": suit, "value": RANK_VALUES[rank]}

def get_hl_card_file(card) -> Optional[discord.File]:
    r = RANK_NAME_MAP.get(card['rank'], card['rank'].lower())
    s = SUIT_NAME_MAP.get(card['suit'], 'spades')
    path = os.path.join('assets', 'playing_cards', f'{r}_of_{s}.png')
    if os.path.exists(path):
        return discord.File(path, filename="card.png")
    return None


class HigherLowerView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=60)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.current_card = draw_hl_card()
        self.streak = 0
        self.game_over = False
        self.message: Optional[discord.Message] = None

    def get_multiplier(self) -> float:
        if self.streak <= 0:
            return 1.00
        streak_table = [1.00, 1.20, 1.50, 2.00, 2.50, 3.20, 4.00, 5.00, 6.50, 8.00, 10.00]
        if self.streak < len(streak_table):
            return streak_table[self.streak]
        return round(10.00 + (self.streak - 10) * 1.50, 2)

    def get_embed(self, outcome_msg=""):
        embed = discord.Embed(
            title="🃏 Higher or Lower",
            description=(
                f"Lwr9a l7alia: **{self.current_card['rank']}{self.current_card['suit']}**\n\n"
                f"🔥 Streak: **{self.streak}**\n"
                f"📈 Multiplier: **{self.get_multiplier():.2f}x**\n"
                + (f"💰 Stake: {format_tad(self.bet)}" if self.bet > 0 else "")
            ),
            color=0x000000
        )
        embed.set_thumbnail(url="attachment://card.png")
        if outcome_msg:
            embed.add_field(name="Result", value=outcome_msg, inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    async def process_guess(self, interaction: discord.Interaction, is_higher: bool):
        if self.game_over:
            return

        next_card = draw_hl_card()
        c_val = self.current_card["value"]
        n_val = next_card["value"]

        card_reveal_str = f"Jat: `{next_card['rank']}{next_card['suit']}` (Kant: `{self.current_card['rank']}{self.current_card['suit']}`)"

        if n_val == c_val:
            self.current_card = next_card
            embed = self.get_embed(f"🤝 **Same Rank!** {card_reveal_str}. Streak b9a howa howa!")
            file = get_hl_card_file(self.current_card)
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])
            return

        won = (n_val > c_val) if is_higher else (n_val < c_val)

        if won:
            self.streak += 1
            self.current_card = next_card
            embed = self.get_embed(f"✅ **S7i7!** {card_reveal_str}!")
            file = get_hl_card_file(self.current_card)
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])
        else:
            self.game_over = True
            for item in self.children:
                item.disabled = True
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            tax = 0
            if self.bet > 0 and economy_cog:
                tax = await economy_cog.apply_lost_gamble_tax(self.bet, context="HigherLower Loss")
            tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""

            g_id = self.message.guild.id if (self.message and self.message.guild) else (interaction.guild.id if interaction.guild else None)
            if g_id and self.cog:
                await self.cog.record_minigame_loss(g_id, self.author.id, "higherlower", loss_amount=self.bet if self.bet > 0 else 0)

            embed = discord.Embed(
                title="💥 Ghalat! Game Over",
                description=f"❌ {card_reveal_str}.\nKhesrti -{format_tad(self.bet)}! Final Streak: **{self.streak}**.{tax_str}",
                color=0x000000
            )
            file = get_hl_card_file(next_card)
            if file:
                embed.set_thumbnail(url="attachment://card.png")
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])
            self.stop()

    @discord.ui.button(label="Higher", style=discord.ButtonStyle.success, emoji="⬆️")
    async def higher_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_guess(interaction, is_higher=True)

    @discord.ui.button(label="Lower", style=discord.ButtonStyle.danger, emoji="⬇️")
    async def lower_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_guess(interaction, is_higher=False)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return
        if self.streak == 0:
            await interaction.response.send_message("⚠️ Khassek tjawb minimum mra w7da s7i7a 9bel madir Cash Out!", ephemeral=True)
            return

        self.game_over = True
        for item in self.children:
            item.disabled = True
        mult = self.get_multiplier()
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

        embed = discord.Embed(
            title="💰 CASHED OUT!",
            description=f"🎉 **{self.author.mention}** rbe7ti b streak **{self.streak}** (Multiplier: **{mult:.2f}x**)!",
            color=0x000000
        )
        if self.bet > 0 and economy_cog:
            gross_payout = int(round(self.bet * mult))
            net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context=f"HigherLower Win ({mult:.2f}x)", vault="casino")
            net_profit = net_payout - self.bet
            if self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower", earnings=max(0, net_profit))
            embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
        elif self.message and self.message.guild:
            await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower")

        file = get_hl_card_file(self.current_card)
        if file:
            embed.set_thumbnail(url="attachment://card.png")
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])
        self.stop()

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                if self.streak == 0:
                    await economy_cog.add_balance(self.author.id, self.bet, context="HigherLower Timeout Refund")
                    desc = f"⏰ **Game Timed Out!**\nRje3 lik l bet: {format_tad(self.bet)}."
                else:
                    mult = self.get_multiplier()
                    gross_payout = int(round(self.bet * mult))
                    net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context=f"HigherLower Auto-Cashout ({mult:.2f}x)", vault="casino")
                    net_profit = net_payout - self.bet
                    if self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower", earnings=max(0, net_profit))
                    desc = f"⏰ **Game Timed Out (Auto-Cashed Out)!**\nStreak: **{self.streak}** (Multiplier: **{mult:.2f}x**) • Net Payout: **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)."
            else:
                desc = "⏰ **Game Timed Out!**"

            embed = discord.Embed(title="🃏 Higher or Lower — Timed Out", description=desc, color=0x000000)
            file = get_hl_card_file(self.current_card)
            if file:
                embed.set_thumbnail(url="attachment://card.png")
            try:
                await self.message.edit(embed=embed, view=self, attachments=[file] if file else [])
            except Exception:
                pass


# ============ COINFLIP & DICE VIEWS ============

class CoinflipView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=45)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.game_over = False
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                await economy_cog.add_balance(self.author.id, self.bet, context="Coinflip Timeout Refund")
                embed = discord.Embed(
                    title="🪙 Coinflip — Timed Out",
                    description=f"⏰ **Sala lwe9t!**\nMa khtariti walo f lwe9t, rje3 lik l bet: {format_tad(self.bet)}.",
                    color=0x000000
                )
            else:
                embed = discord.Embed(
                    title="🪙 Coinflip — Timed Out",
                    description="⏰ **Sala lwe9t!**",
                    color=0x000000
                )
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    async def _flip(self, interaction: discord.Interaction, user_choice: str):
        self.game_over = True
        for item in self.children:
            item.disabled = True

        result = random.choice(["ras", "njma"])
        result_label = "🪙 Ras (Heads)" if result == "ras" else "🪙 Njma (Tails)"
        user_choice_label = "Ras (Heads)" if user_choice == "ras" else "Njma (Tails)"

        won = (user_choice == result)
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

        outcome_title = "🏆 Rbe7ti!" if won else "💥 Khesrti!"
        embed = discord.Embed(
            title=f"{outcome_title} — {result_label}",
            description=f"Lkhtiyar ta3k: **{user_choice_label}**\nNatija: **{result_label}**",
            color=0x000000
        )

        if self.bet > 0 and economy_cog:
            if won:
                gross_payout = self.bet * 2
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context="Coinflip Win", vault="casino")
                net_profit = net_payout - self.bet
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "coinflip", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(self.bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(self.bet, context="Coinflip Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(self.bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(self.bet)}**{tax_str}", inline=False)
                if self.message and self.message.guild:
                    await self.cog.record_minigame_loss(self.message.guild.id, self.author.id, "coinflip", loss_amount=self.bet)
        elif won and self.message and self.message.guild:
            await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "coinflip")
        elif not won and self.message and self.message.guild:
            await self.cog.record_minigame_loss(self.message.guild.id, self.author.id, "coinflip", loss_amount=0)

        coin_path = os.path.join("assets", "coin", "Heads.png" if result == "ras" else "Tails.png")
        if os.path.exists(coin_path):
            file = discord.File(coin_path, filename="coin.png")
            embed.set_thumbnail(url="attachment://coin.png")
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
        else:
            await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Ras (Heads)", style=discord.ButtonStyle.primary, emoji="🪙")
    async def heads_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, "ras")

    @discord.ui.button(label="Njma (Tails)", style=discord.ButtonStyle.secondary, emoji="⭐")
    async def tails_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, "njma")


def render_dice_composite(rolls) -> Optional[io.BytesIO]:
    imgs = []
    for r in rolls:
        p = os.path.join('assets', 'dice', f'{r}.png')
        if os.path.exists(p):
            imgs.append(Image.open(p).convert('RGBA'))
    if not imgs:
        return None
    if len(imgs) == 1:
        buf = io.BytesIO()
        imgs[0].save(buf, format='PNG')
        buf.seek(0)
        return buf

    spacing = 15
    w, h = imgs[0].size
    total_w = len(imgs) * w + (len(imgs) - 1) * spacing
    canvas = Image.new('RGBA', (total_w, h), (0, 0, 0, 0))
    for i, im in enumerate(imgs):
        canvas.paste(im, (i * (w + spacing), 0), im)
    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf


class DiceRollView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, num_dice: int = 1, num_sides: int = 6):
        super().__init__(timeout=60)
        self.author = author
        self.cog = cog
        self.num_dice = num_dice
        self.num_sides = num_sides
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Roll Again", style=discord.ButtonStyle.primary, emoji="🎲")
    async def roll_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        rolls = [random.randint(1, self.num_sides) for _ in range(self.num_dice)]
        total = sum(rolls)
        rolls_str = " ".join(f"`{r}`" for r in rolls)

        embed = discord.Embed(
            title=f"🎲 Dice Roll ({self.num_dice}d{self.num_sides})",
            description=f"**Rolls:** {rolls_str}\n**Total Sum:** `{total}`",
            color=0x000000
        )
        if self.num_sides == 6:
            buf = render_dice_composite(rolls)
            if buf:
                file = discord.File(buf, filename="dice.png")
                if len(rolls) == 1:
                    embed.set_thumbnail(url="attachment://dice.png")
                else:
                    embed.set_image(url="attachment://dice.png")
                await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
                return

        await interaction.response.edit_message(embed=embed, view=self, attachments=[])


# ============ INTERACTIVE 2-TIER LEADERBOARD UI ============

MINIGAME_DISPLAY_MAP = {
    "flags": ("🚩 Flags", ["flags", "flag", "rayat", "gtf"]),
    "craftingtable": ("🔨 CraftingTable", ["craftingtable", "crafting", "craft", "recipe"]),
    "guessthecar": ("🚗 GuessTheCar", ["guessthecar", "guesscar", "cars", "carguess", "carmodels", "models", "tomobil", "tomobila"]),
    "blacktea": ("☕ BlackTea", ["blacktea", "bt", "black", "jklm"]),
    "greentea": ("🍵 GreenTea", ["greentea", "gt", "green"]),
    "redtea": ("🔴 RedTea", ["redtea", "rt", "red"]),
    "unscramble": ("🧩 Unscramble", ["unscramble", "scramble", "fekk", "fek"]),
    "blackjack": ("🃏 Blackjack", ["blackjack", "bj", "21"]),
    "slots": ("🎰 Slots", ["slots", "slot", "machine"]),
    "mines": ("💣 Mines", ["mines", "gems", "gemhunt"]),
    "roulette": ("🎡 Roulette", ["roulette", "wheel", "roul"]),
    "higherlower": ("🃏 HigherLower", ["higherlower", "hl", "cardduel"]),
    "coinflip": ("🪙 Coinflip", ["coinflip", "cf", "drhm", "drhem"]),
    "dice": ("🎲 Dice", ["dice", "nrd", "roll", "diceroll"]),
    "tictactoe": ("❌ TicTacToe", ["tictactoe", "ttt", "morpion"]),
    "connectfour": ("🔴 ConnectFour", ["connectfour", "c4", "connect4"]),
    "chess": ("♟️ Chess", ["chess", "playchess", "shitranj", "chessgame"]),
    "rockpaperscissors": ("✂️ RockPaperScissors", ["rockpaperscissors", "rps", "zdimbomba7", "zba7"]),
    "minesweeper": ("💣 Minesweeper", ["minesweeper", "ms", "demineur"]),
    "wordle": ("🟩 Wordle", ["wordle", "wdl", "klma", "kelma"]),
    "hangman": ("🪢 Hangman", ["hangman", "hm", "michna9a"]),
    "trivia": ("🧠 Trivia", ["trivia", "quiz", "as2ila"]),
    "typeracer": ("🏎️ TypeRacer", ["typeracer", "tr", "type", "monkeytype"]),
    "geoguessr": ("🌍 GeoGuessr", ["geoguessr", "geo", "geoguesser", "geoguess"]),
    "guesstherank": ("🎖️ GuessTheRank", ["guesstherank", "gtr", "guessrank"]),
    "chesspuzzle": ("🧩 ChessPuzzle", ["chesspuzzle", "puzzle", "cpuzzle", "chesstactic", "tactic", "chessquiz"]),
    "tower": ("🏰 Tower", ["tower", "doors", "lborj", "lbiban"]),
}

class LeaderboardSelect(discord.ui.Select):
    def __init__(self, placeholder: str, options: list[discord.SelectOption], row: int = 0):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        view: LeaderboardInteractiveView = self.view
        selected_game = self.values[0]
        await view.show_game_page(interaction, selected_game, timeframe=view.timeframe, page=0)


class LeaderboardInteractiveView(discord.ui.View):
    def __init__(self, ctx, cog, minigame_map: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.minigame_map = minigame_map
        self.current_game: Optional[str] = None
        self.timeframe: str = "weekly"  # "weekly" or "alltime"
        self.current_page: int = 0
        self.per_page: int = 10
        self.rows_cache = []
        self.message: Optional[discord.Message] = None

        self.setup_overview()

    def setup_overview(self):
        self.clear_items()
        self.current_game = None

        casino_keys = {"blackjack", "slots", "mines", "roulette", "higherlower", "coinflip", "dice", "tower"}
        casino_options = []
        puzzle_options = []

        for game_key, (display_name, _) in self.minigame_map.items():
            parts = display_name.split(" ", 1)
            emoji_part = parts[0] if len(parts) > 1 else None
            label_part = parts[1] if len(parts) > 1 else display_name
            opt = discord.SelectOption(
                label=label_part,
                value=game_key,
                emoji=emoji_part
            )
            if game_key in casino_keys:
                casino_options.append(opt)
            else:
                puzzle_options.append(opt)

        if casino_options:
            self.add_item(LeaderboardSelect("🎰 Casino & Gambling Leaderboards...", casino_options[:25], row=0))

        if puzzle_options:
            self.add_item(LeaderboardSelect("🧠 Puzzles, Quiz & Casual Leaderboards...", puzzle_options[:25], row=1))

    async def show_overview(self, interaction: Optional[discord.Interaction] = None):
        self.setup_overview()
        embed = await self.cog.get_main_leaderboard_embed(self.ctx.guild)
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            self.message = await self.ctx.send(embed=embed, view=self)

    async def show_game_page(self, interaction: Optional[discord.Interaction] = None, game_key: Optional[str] = None, timeframe: str = "weekly", page: int = 0):
        if game_key:
            self.current_game = game_key
        self.timeframe = timeframe
        self.current_page = page

        if self.timeframe == "weekly":
            week_start_ts = get_current_week_start_ts()
            async with self.cog.bot.db.execute("""
                SELECT user_id, COUNT(*) as wins, SUM(earnings) as earnings FROM minigame_win_logs
                WHERE guild_id = ? AND game = ? AND timestamp >= ?
                GROUP BY user_id
                ORDER BY earnings DESC, wins DESC
            """, (self.ctx.guild.id, self.current_game, week_start_ts)) as cursor:
                self.rows_cache = await cursor.fetchall()
        else:
            async with self.cog.bot.db.execute("""
                SELECT user_id, wins, earnings FROM minigame_leaderboard
                WHERE guild_id = ? AND game = ?
                ORDER BY earnings DESC, wins DESC
            """, (self.ctx.guild.id, self.current_game)) as cursor:
                self.rows_cache = await cursor.fetchall()

        self.clear_items()

        total_pages = max(1, (len(self.rows_cache) + self.per_page - 1) // self.per_page)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        # Pagination Buttons (Row 0)
        prev_btn = discord.ui.Button(label="◀️", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), row=0)
        async def prev_callback(i: discord.Interaction):
            await self.show_game_page(i, self.current_game, self.timeframe, self.current_page - 1)
        prev_btn.callback = prev_callback
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(label=f"Page {self.current_page + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=0)
        self.add_item(page_btn)

        next_btn = discord.ui.Button(label="▶️", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), row=0)
        async def next_callback(i: discord.Interaction):
            await self.show_game_page(i, self.current_game, self.timeframe, self.current_page + 1)
        next_btn.callback = next_callback
        self.add_item(next_btn)

        # Timeframe Switcher Button (Row 1: Weekly <-> All-Time)
        if self.timeframe == "weekly":
            tf_btn = discord.ui.Button(label="All-Time 👑", style=discord.ButtonStyle.primary, emoji="👑", row=1)
            async def tf_callback(i: discord.Interaction):
                await self.show_game_page(i, self.current_game, "alltime", 0)
            tf_btn.callback = tf_callback
        else:
            tf_btn = discord.ui.Button(label="Weekly 🗓️", style=discord.ButtonStyle.success, emoji="🗓️", row=1)
            async def tf_callback(i: discord.Interaction):
                await self.show_game_page(i, self.current_game, "weekly", 0)
            tf_btn.callback = tf_callback
        self.add_item(tf_btn)

        # Back to Overview Button
        back_btn = discord.ui.Button(label="Back to Overview", style=discord.ButtonStyle.danger, emoji="🔙", row=1)
        async def back_callback(i: discord.Interaction):
            await self.show_overview(i)
        back_btn.callback = back_callback
        self.add_item(back_btn)

        embed = self.get_game_embed(total_pages)
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            self.message = await self.ctx.send(embed=embed, view=self)

    def get_game_embed(self, total_pages: int) -> discord.Embed:
        game_display, _ = self.minigame_map.get(self.current_game, (self.current_game.title(), []))
        tf_title = "🗓️ Weekly (This Week)" if self.timeframe == "weekly" else "👑 All-Time"
        reset_str = f" • Resets <t:{get_next_week_start_ts()}:R>" if self.timeframe == "weekly" else ""
        embed = discord.Embed(
            title=f"{game_display} Leaderboard",
            description=f"*{tf_title}{reset_str} • Sorted by Gains* — **{self.ctx.guild.name}**\n\n",
            color=0x000000
        )

        if not self.rows_cache:
            embed.description += "*No records yet for this period.*"
            return embed

        start_idx = self.current_page * self.per_page
        page_rows = self.rows_cache[start_idx : start_idx + self.per_page]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, wins, earnings) in enumerate(page_rows, start=start_idx):
            rank_str = medals[i] if i < 3 else f"**#{i+1}**"
            win_str = f"**{wins}** win" if wins == 1 else f"**{wins}** wins"
            earn_str = format_tad(earnings)
            lines.append(f"{rank_str} <@{uid}> — {earn_str} *({win_str})*")

        embed.description += "\n".join(lines)
        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • Sorted by Gains")
        return embed




class Gambling(commands.Cog, name="Gambling"):
    def __init__(self, bot):
        self.bot = bot

    async def record_minigame_win(self, guild_id: Optional[int], user_id: int, game: str, earnings: int = 0):
        await record_minigame_win(self.bot, guild_id, user_id, game, earnings)

    async def record_minigame_loss(self, guild_id: Optional[int], user_id: int, game: str, loss_amount: int = 0):
        await record_minigame_loss(self.bot, guild_id, user_id, game, loss_amount)

    # ============ REWORKED LEADERBOARD & CASINO COMMANDS ============

    async def get_main_leaderboard_embed(self, guild: Optional[discord.Guild] = None) -> discord.Embed:
        week_start_ts = get_current_week_start_ts()
        next_week_ts = get_next_week_start_ts()

        # 1. User with the most weekly earnings globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(earnings) as total_earnings
            FROM minigame_win_logs
            WHERE timestamp >= ?
            GROUP BY user_id
            ORDER BY total_earnings DESC LIMIT 1
        """, (week_start_ts,)) as cursor:
            top_weekly_row = await cursor.fetchone()

        # 2. User with the most weekly losses globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(loss_amount) as total_losses
            FROM minigame_loss_logs
            WHERE timestamp >= ?
            GROUP BY user_id
            ORDER BY total_losses DESC LIMIT 1
        """, (week_start_ts,)) as cursor:
            top_weekly_loss_row = await cursor.fetchone()

        # 3. User with the most all-time earnings globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(earnings) as total_earnings
            FROM minigame_leaderboard
            GROUP BY user_id
            ORDER BY total_earnings DESC LIMIT 1
        """) as cursor:
            top_alltime_row = await cursor.fetchone()

        # 4. User with the most all-time losses globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(loss_amount) as total_losses
            FROM minigame_leaderboard
            GROUP BY user_id
            ORDER BY total_losses DESC LIMIT 1
        """) as cursor:
            top_alltime_loss_row = await cursor.fetchone()

        embed = discord.Embed(
            title="🏆 Minigames Leaderboard (Global)",
            description="Khtar minigame mn lmenu lte7t bach tchouf rankings dialha f had lserver.\n",
            color=0x000000
        )

        # Field 1: Most Weekly Earnings
        if top_weekly_row and top_weekly_row[1] > 0:
            u_id, e_count = top_weekly_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(earnings) as game_earnings
                FROM minigame_win_logs
                WHERE user_id = ? AND timestamp >= ?
                GROUP BY game
                ORDER BY game_earnings DESC LIMIT 1
            """, (u_id, week_start_ts)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name=f"🗓️ Most Weekly Earnings (Resets <t:{next_week_ts}:R>)",
                value=f"<@{u_id}> — {format_tad(e_count)}{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name=f"🗓️ Most Weekly Earnings (Resets <t:{next_week_ts}:R>)",
                value="*No weekly earnings yet.*",
                inline=False
            )

        # Field 2: Most Weekly Losses
        if top_weekly_loss_row and top_weekly_loss_row[1] > 0:
            u_id, l_count = top_weekly_loss_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(loss_amount) as game_losses
                FROM minigame_loss_logs
                WHERE user_id = ? AND timestamp >= ?
                GROUP BY game
                ORDER BY game_losses DESC LIMIT 1
            """, (u_id, week_start_ts)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name=f"📉 Most Weekly Losses (Resets <t:{next_week_ts}:R>)",
                value=f"<@{u_id}> — **-{l_count:,}** {TAD_EMOJI} TAD{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name=f"📉 Most Weekly Losses (Resets <t:{next_week_ts}:R>)",
                value="*No weekly losses yet.*",
                inline=False
            )

        # Field 3: Most All-Time Earnings
        if top_alltime_row and top_alltime_row[1] > 0:
            u_id, e_count = top_alltime_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(earnings) as game_earnings
                FROM minigame_leaderboard
                WHERE user_id = ?
                GROUP BY game
                ORDER BY game_earnings DESC LIMIT 1
            """, (u_id,)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name="👑 Most All-Time Earnings",
                value=f"<@{u_id}> — {format_tad(e_count)}{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name="👑 Most All-Time Earnings",
                value="*No earnings yet.*",
                inline=False
            )

        # Field 4: Most All-Time Losses
        if top_alltime_loss_row and top_alltime_loss_row[1] > 0:
            u_id, l_count = top_alltime_loss_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(loss_amount) as game_losses
                FROM minigame_leaderboard
                WHERE user_id = ?
                GROUP BY game
                ORDER BY game_losses DESC LIMIT 1
            """, (u_id,)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name="💥 Most All-Time Losses",
                value=f"<@{u_id}> — **-{l_count:,}** {TAD_EMOJI} TAD{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name="💥 Most All-Time Losses",
                value="*No losses yet.*",
                inline=False
            )

        embed.set_footer(text="Dropdowns lte7t kat affichi ga3 l minigames available f had server.")
        return embed

    @commands.command(name="minigames", aliases=["mg", "leaderboard", "lb", "top"], help="Leaderboard ta3 lminigames (sat mg [game]).")
    async def minigames(self, ctx: commands.Context, *args):
        if not ctx.guild:
            await ctx.send("❌ Had l command khedama ghir f servers.")
            return

        game = " ".join(args).strip().lower() if args else None

        view = LeaderboardInteractiveView(ctx, self, MINIGAME_DISPLAY_MAP)

        if not game:
            await view.show_overview()
        else:
            target_key = None
            for k, (d_name, aliases) in MINIGAME_DISPLAY_MAP.items():
                if game == k or game in aliases:
                    target_key = k
                    break

            if not target_key:
                valid_list = ", ".join(f"`{k}`" for k in MINIGAME_DISPLAY_MAP.keys())
                await ctx.send(embed=discord.Embed(
                    description=f"❌ Had l game makynch: `{game}`.\n\nGames li kaynin:\n{valid_list}",
                    color=0x000000
                ))
                return

            await view.show_game_page(interaction=None, game_key=target_key, timeframe="weekly", page=0)

    @commands.command(aliases=['cf', 'drhm'], help="Nlou7 derhem o chouf wach jak ras wla njma (sat coinflip [ras/njma] [bet:500]).")
    @not_fraud()
    async def coinflip(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, remaining = parse_bet_argument(*args, user_balance=w.get("balance", 0))
        choice = remaining[0] if remaining else None

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Coinflip Bet")

        if choice is None:
            view = CoinflipView(ctx.author, self, bet=bet or 0)
            embed = discord.Embed(
                title="Coinflip Table",
                description="Khtar chno ghadi yji: **Ras (Heads)** wla **Njma (Tails)**?" + (f"\n\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""),
                color=0x000000
            )
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
            return

        c = choice.strip().lower()
        if c in ["ras", "head", "heads", "h"]:
            user_choice = "ras"
        elif c in ["njma", "nejma", "tail", "tails", "t"]:
            user_choice = "njma"
        else:
            if bet and bet > 0 and economy_cog:
                await economy_cog.add_balance(ctx.author.id, bet, context="Coinflip Invalid Bet Refund")
            await ctx.send("❌ Khtar `ras` (heads) wla `njma` (tails). Example: `sat coinflip ras 100`")
            return

        flip_msg = await ctx.send("🪙 *Kanlou7 derhem f sma...*")
        await asyncio.sleep(1.2)

        result = random.choice(["ras", "njma"])
        result_label = "🪙 Ras (Heads)" if result == "ras" else "🪙 Njma (Tails)"
        user_choice_label = "Ras (Heads)" if user_choice == "ras" else "Njma (Tails)"

        won = (user_choice == result)
        outcome_title = "🏆 Rbe7ti!" if won else "💥 Khesrti!"
        embed = discord.Embed(
            title=f"🪙 Coinflip: {result_label}",
            description=f"Lkhtiyar: **{user_choice_label}** • Natija: **{result_label}**\n\n**{outcome_title}**",
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            if won:
                gross_payout = bet * 2
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context="Coinflip Win", vault="casino")
                net_profit = net_payout - bet
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "coinflip", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Coinflip Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "coinflip", loss_amount=bet)
        elif won and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "coinflip")
        elif not won and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "coinflip", loss_amount=0)

        coin_path = os.path.join("assets", "coin", "Heads.png" if result == "ras" else "Tails.png")
        if os.path.exists(coin_path):
            file = discord.File(coin_path, filename="coin.png")
            embed.set_thumbnail(url="attachment://coin.png")
            await flip_msg.delete()
            await ctx.send(embed=embed, file=file)
        else:
            await flip_msg.edit(content=None, embed=embed)

    @commands.command(aliases=["nrd", "roll", "diceroll"], help="Lo7 dice o rbe7 multiplier (sat dice [bet:100]).")
    @not_fraud()
    async def dice(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Dice Bet")

        roll = random.randint(1, 6)
        multipliers = {
            1: (0.0, "💥 Khesrti l bet! (0x)"),
            2: (0.5, "🤏 Rje3 lik ness l bet (0.5x)"),
            3: (0.75, "🤏 Rje3 lik 75% mn l bet (0.75x)"),
            4: (1.25, "✨ Small Win! (1.25x)"),
            5: (1.5, "🔥 Good Win! (1.5x)"),
            6: (2.0, "👑 DOUBLE JACKPOT! (2.0x)")
        }

        mult, desc = multipliers[roll]

        embed = discord.Embed(
            title=f"🎲 Dice: Rolled [ {roll} ]",
            description=f"{desc}\n\n📊 Multiplier: **{mult}x**",
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            gross_payout = int(round(bet * mult))
            embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
            if gross_payout > 0:
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context=f"Dice Payout ({mult}x)", vault="casino")
                net_profit = net_payout - bet
                if mult >= 1.25 and ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "dice", earnings=max(0, net_profit))
                if net_profit > 0:
                    embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
                elif net_profit < 0:
                    lost_amt = abs(net_profit)
                    tax = await economy_cog.apply_lost_gamble_tax(lost_amt, context="Dice Partial Loss")
                    tax_str = f" • `{tax:,}` TAD tax" if tax > 0 else ""
                    embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(lost_amt)}** (Refund: {net_payout:,} TAD{tax_str})", inline=False)
                    if ctx.guild:
                        await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "dice", loss_amount=lost_amt)
                else:
                    embed.add_field(name="💵 Net Payout", value=f"⚪ **+0 TAD** (Refund: {net_payout:,} TAD)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Dice Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "dice", loss_amount=bet)
        elif mult < 1.0 and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "dice", loss_amount=0)
        else:
            embed.set_footer(text="Bghiti t9emmer b flous? Kteb sat dice 100")

        img_path = os.path.join("assets", "dice", f"{roll}.png")
        if os.path.exists(img_path):
            file = discord.File(img_path, filename="dice.png")
            embed.set_thumbnail(url="attachment://dice.png")
            await ctx.send(embed=embed, file=file)
        else:
            await ctx.send(embed=embed)

    @commands.command(aliases=["bj", "21"], help="Fout dealer blama tfout 21 (sat blackjack [bet:500]).")
    @not_fraud()
    async def blackjack(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Blackjack Bet")

        view = BlackjackView(ctx.author, self, bet=bet or 0)
        initial_embed = view.get_embed()
        initial_file = view.get_render_file()

        p_score = calculate_bj_score(view.player_hand)
        if p_score == 21:
            d_score = calculate_bj_score(view.dealer_hand)
            if d_score == 21:
                initial_embed = view.get_embed(dealer_reveal=True, outcome_text="🤝 **Double Blackjack!** Ta3adol (Push)!")
                if bet and bet > 0 and economy_cog:
                    await economy_cog.add_balance(ctx.author.id, bet, context="Blackjack Push Refund")
            else:
                outcome_str = "🏆 **NATURAL 21 BLACKJACK!** Rbe7ti l game!"
                if bet and bet > 0 and economy_cog:
                    gross_payout = int(round(bet * 2.5))
                    net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context="Blackjack Natural 21", vault="casino")
                    net_profit = net_payout - bet
                    if ctx.guild:
                        await self.record_minigame_win(ctx.guild.id, ctx.author.id, "blackjack", earnings=max(0, net_profit))
                    outcome_str += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)!"
                elif ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "blackjack")
                initial_embed = view.get_embed(dealer_reveal=True, outcome_text=outcome_str)
            view.game_over = True
            for item in view.children:
                item.disabled = True
            initial_file = view.get_render_file(dealer_reveal=True)

        msg = await ctx.send(embed=initial_embed, view=view, file=initial_file)
        view.message = msg

    @commands.command(aliases=["slot", "machine"], help="L3eb casino slot machine (sat slots [bet:500]).")
    @not_fraud()
    async def slots(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Slots Bet")

        slot_items = ["💎", "7️⃣", "🔔", "🍇", "🍒", "🍋", "🍊"]
        weights = [5, 10, 15, 20, 25, 30, 35]

        spin_msg = await ctx.send(embed=discord.Embed(
            title="🎰 Casino Slot Machine",
            description="**[ 🔄 | 🔄 | 🔄 ]**\n*Spinning the reels...*" + (f"\n\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""),
            color=0x000000
        ))
        await asyncio.sleep(1.2)

        r1 = random.choices(slot_items, weights=weights, k=1)[0]
        r2 = random.choices(slot_items, weights=weights, k=1)[0]
        r3 = random.choices(slot_items, weights=weights, k=1)[0]

        payout_mult = 0.0
        outcome_title = "Khesrti!"
        if r1 == r2 == r3:
            if r1 == "💎":
                payout_mult = 15.0
                outcome_title = "JACKPOT! Triple Diamonds!"
            elif r1 == "7️⃣":
                payout_mult = 10.0
                outcome_title = "MEGA WIN! Triple Sevens!"
            elif r1 == "🔔":
                payout_mult = 6.0
                outcome_title = "SUPER WIN! Triple Bells!"
            elif r1 == "🍇":
                payout_mult = 5.0
                outcome_title = "BIG WIN! Triple Grapes!"
            elif r1 == "🍒":
                payout_mult = 4.0
                outcome_title = "WIN! Triple Cherries!"
            else:
                payout_mult = 3.0
                outcome_title = f"WIN! Triple {r1}!"
        elif r1 == r2 or r2 == r3 or r1 == r3:
            payout_mult = 1.5
            outcome_title = "Small Win! Double Match!"

        embed = discord.Embed(
            title="🎰 Slots Machine",
            description=(
                f"**[ {r1} | {r2} | {r3} ]**\n\n"
                f"**{outcome_title}**\n"
                f"📊 Multiplier: **{payout_mult:.1f}x**"
            ),
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            gross_payout = int(round(bet * payout_mult))
            if gross_payout > 0:
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context=f"Slots Payout ({payout_mult:.1f}x)", vault="casino")
                net_profit = net_payout - bet
                if payout_mult > 0 and ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "slots", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Slots Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "slots", loss_amount=bet)
        elif payout_mult > 0 and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "slots")
        elif payout_mult == 0 and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "slots", loss_amount=0)

        await spin_msg.edit(embed=embed)

    @commands.command(aliases=["gems"], help="L9a gems o hreb 9bl matfrge3 (sat mines [bet:500]).")
    @not_fraud()
    async def mines(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))
        if bet is None or bet <= 0:
            bet = 50

        bombs = 3

        if economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])} (Min: 50 TAD).")
                return
            success = await economy_cog.deduct_balance(ctx.author.id, bet, context="Mines Bet")
            if not success:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w['balance'])}.")
                return

        view = MinesGambleView(ctx.author, self, bomb_count=bombs, bet=bet)
        total_gems = (view.width * view.height) - bombs
        embed = discord.Embed(
            title="💣 Mines Table",
            description=(
                f"💎 Gems: **0/{total_gems}**\n"
                f"📈 Multiplier: **1.00x** (Next: **{view.get_next_multiplier():.2f}x**)\n"
                f"💣 Bombs: **{bombs}**\n"
                f"💰 Stake: {format_tad(bet)}\n\n"
                "Click 3la ay tile bach t uncoveriha!"
            ),
            color=0x000000
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="tower", aliases=["doors", "lborj", "lbiban"], help="L9a lbab rrab7 f kola etage.")
    @not_fraud()
    async def tower(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        if not economy_cog:
            await ctx.send("❌ Economy system ma khdamch daba.")
            return

        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, remaining = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet is not None and bet < 0:
            await ctx.send("❌ L bet khas ykoun kber mn 0.")
            return

        bet = bet or 0

        if bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dyalk: {format_tad(w['balance'])}.")
                return
            success = await economy_cog.deduct_balance(ctx.author.id, bet, context="Tower Bet", force=True)
            if not success:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w['balance'])}.")
                return

        view = TowerGameView(ctx.author, bet, self)
        board_bytes = await asyncio.to_thread(render_tower_board, 1, view.story_doors, "active")
        file = discord.File(board_bytes, filename="tower.jpg")

        bet_str = f"💰 **Bet:** {format_tad(bet)}\n" if bet > 0 else "🎮 **Mode:** Free Play (0 TAD)\n"
        embed = discord.Embed(
            title="🏰 Tower of Doors",
            description=(
                f"👤 **Player:** {ctx.author.mention}\n"
                f"{bet_str}"
                f"🚪 **Story 1/4** • Khtar door mn bach ttle3 l **3.0x**!"
            ),
            color=0x000000
        )
        embed.set_image(url="attachment://tower.jpg")
        embed.set_footer(text="Sifdine Casino • Khtar door wla Cash Out mn be3d Story 1")

        msg = await ctx.send(file=file, embed=embed, view=view)
        view.message = msg

    @commands.command(aliases=["wheel"], help="9emmer 3la loun wla ra9m (sat roulette [choice] [bet:500]).")
    @not_fraud()
    async def roulette(self, ctx: commands.Context, *args):
        red_nums = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        black_nums = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

        if not args:
            embed = discord.Embed(
                title="🎡 European Roulette Table",
                description=(
                    "Khtar 3layach baghi t9emmer:\n"
                    "• `red` / `black` (2x payout)\n"
                    "• `even` / `odd` (2x payout)\n"
                    "• `1-18` (Low) / `19-36` (High) (2x payout)\n"
                    "• `green` (36x payout)\n"
                    "• Number direct `0` - `36` (36x payout)\n\n"
                    f"Example: `{ctx.clean_prefix}roulette 1 200` wla `{ctx.clean_prefix}roulette red 500`"
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        def parse_roulette_choice(val: str) -> Optional[tuple[str, str, str]]:
            """Returns (choice_type, choice_val, display_label) or None."""
            if not val:
                return None
            s = str(val).strip().lower()
            if s.isdigit():
                num = int(s)
                if 0 <= num <= 36:
                    return ("number", str(num), f"Ra9m {num}")
                return None
            if s in ("zero",):
                return ("number", "0", "Ra9m 0")
            if s in ("red", "r", "7mer", "7mr"):
                return ("red", "red", "Red 🔴")
            if s in ("black", "b", "k7el", "k7l", "k7al"):
                return ("black", "black", "Black ⚫")
            if s in ("green", "g", "khder"):
                return ("green", "green", "Green 🟢")
            if s in ("even", "zawji"):
                return ("even", "even", "Even (Zawji)")
            if s in ("odd", "fardi"):
                return ("odd", "odd", "Odd (Fardi)")
            if s in ("1-18", "low", "fo9"):
                return ("low", "1-18", "1-18 (Low)")
            if s in ("19-36", "high", "ta7t", "t7t"):
                return ("high", "19-36", "19-36 (High)")
            return None

        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}

        # Resolve choice and bet: try choice first, then remaining as bet; or bet first, then choice
        choice_tuple = parse_roulette_choice(args[0])
        if choice_tuple:
            bet, _ = parse_bet_argument(*args[1:], user_balance=w.get("balance", 0))
        elif len(args) >= 2:
            choice_tuple = parse_roulette_choice(args[1])
            if choice_tuple:
                bet, _ = parse_bet_argument(args[0], user_balance=w.get("balance", 0))
            else:
                bet = None
        else:
            bet = None

        if not choice_tuple:
            embed = discord.Embed(
                title="❌ Lkhtiyar dialek machi s7i7 f Roulette",
                description=(
                    f"Had lkhtiyar `{args[0]}` makaynch f tabla dial Roulette!\n\n"
                    "**Lkhtiyarat li momkine:**\n"
                    "• **Ra9m direct:** `0` 7tal `36` (36x payout)\n"
                    "• **Alwan:** `red` 🔴 / `black` ⚫ (2x) wla `green` 🟢 (36x)\n"
                    "• **Zawji / Fardi:** `even` / `odd` (2x)\n"
                    "• **Nsf:** `1-18` (Low) / `19-36` (High) (2x)\n\n"
                    f"Example: `{ctx.clean_prefix}roulette 1 200` wla `{ctx.clean_prefix}roulette red 500`"
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        choice_type, choice_val, choice_display = choice_tuple

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Roulette Bet")

        spin_embed = discord.Embed(
            description=f"🔄 *Roulette kaddor...* (Lkhtiyar: **{choice_display}**)" + (f"\n\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""),
            color=0x000000
        )
        spin_msg = await ctx.send(embed=spin_embed)

        await asyncio.sleep(4.0)

        landed_num = random.randint(0, 36)
        if landed_num == 0:
            color_emoji = "🟢"
            color_name = "green"
        elif landed_num in red_nums:
            color_emoji = "🔴"
            color_name = "red"
        elif landed_num in black_nums:
            color_emoji = "⚫"
            color_name = "black"
        else:
            color_emoji = "🟢"
            color_name = "green"

        won = False
        mult = 0.0

        if choice_type == "number":
            if landed_num == int(choice_val):
                won = True
                mult = 36.0
        elif choice_type == "red" and color_name == "red":
            won = True
            mult = 2.0
        elif choice_type == "black" and color_name == "black":
            won = True
            mult = 2.0
        elif choice_type == "green" and color_name == "green":
            won = True
            mult = 36.0
        elif choice_type == "even" and landed_num > 0 and landed_num % 2 == 0:
            won = True
            mult = 2.0
        elif choice_type == "odd" and landed_num % 2 != 0:
            won = True
            mult = 2.0
        elif choice_type == "low" and 1 <= landed_num <= 18:
            won = True
            mult = 2.0
        elif choice_type == "high" and 19 <= landed_num <= 36:
            won = True
            mult = 2.0

        outcome_title = f"🏆 Rbe7ti! ({mult:.0f}x)" if won else "💥 Khesrti!"
        embed = discord.Embed(
            title=f"🎡 Roulette: {color_emoji} **{landed_num} ({color_name.upper()})**",
            description=(
                f"Lkhtiyar ta3k: **{choice_display}** • Natija: {color_emoji} **{landed_num}**\n\n"
                f"**{outcome_title}**"
            ),
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            if won:
                gross_payout = int(round(bet * mult))
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context=f"Roulette Win ({mult:.0f}x)", vault="casino")
                net_profit = net_payout - bet
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "roulette", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Roulette Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "roulette", loss_amount=bet)
        elif won and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "roulette")
        elif not won and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "roulette", loss_amount=0)

        await spin_msg.edit(embed=embed)

    @commands.command(aliases=["hl"], help="9emmer wach lwr9a jaya Higher wla Lower (sat higherlower [bet:500]).")
    @not_fraud()
    async def higherlower(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="HigherLower Bet")

        view = HigherLowerView(ctx.author, self, bet=bet or 0)
        embed = view.get_embed("9emmer lwr9a jaya wach **Higher ⬆️** wla **Lower ⬇️**!")
        file = get_hl_card_file(view.current_card)
        if file:
            msg = await ctx.send(embed=embed, view=view, file=file)
        else:
            msg = await ctx.send(embed=embed, view=view)
        view.message = msg



async def setup(bot):
    await bot.add_cog(Gambling(bot))
