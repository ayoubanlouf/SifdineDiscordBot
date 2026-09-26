from __future__ import annotations
import io
import os
import random
from typing import Optional, Union, Dict, List

import discord
from PIL import Image, ImageDraw

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import clear_user_game

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


def create_bj_deck() -> List[Dict[str, str]]:
    deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def calculate_bj_score(hand: List[Dict[str, str]]) -> int:
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


def format_bj_card(card: Dict[str, str]) -> str:
    return f"`{card['rank']}{card['suit']}`"


def render_bj_table(dealer_hand: List[Dict[str, str]], player_hand: List[Dict[str, str]], hide_dealer: bool = True) -> io.BytesIO:
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
            b_draw.rounded_rectangle([(0, 0), (cw - 1, ch - 1)], radius=6, fill=(30, 50, 85, 255), outline=(180, 150, 90, 255), width=3)
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
        self.session_id: Optional[str] = None
        self._doubling = False
        self.message: Optional[discord.Message] = None

    def get_render_file(self, dealer_reveal: bool = False) -> discord.File:
        buf = render_bj_table(self.dealer_hand, self.player_hand, hide_dealer=not dealer_reveal)
        return discord.File(buf, filename="blackjack_table.png")

    def get_embed(self, dealer_reveal: bool = False, outcome_text: str = "") -> discord.Embed:
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

        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)

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
        clear_user_game(self.cog.bot, self.author.id)
        self.stop()

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            clear_user_game(self.cog.bot, self.author.id)
            self.stop()
            for item in self.children:
                item.disabled = True

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                await economy_cog.add_balance(self.author.id, self.bet, context="Blackjack Timeout Refund")
            embed = self.get_embed(dealer_reveal=True, outcome_text=f"⏰ **Sala lwe9t!**\nGame timed out o rje3 lik l bet: {format_tad(self.bet)}." if self.bet > 0 else "⏰ **Sala lwe9t!** Game timed out.")
            file = self.get_render_file(dealer_reveal=True)
            try:
                await self.message.edit(embed=embed, view=self, attachments=[file])
            except Exception:
                pass

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        clear_user_game(self.cog.bot, self.author.id)
        self.stop()
        for item in self.children:
            item.disabled = True

        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        tax_str = ""
        if self.bet > 0 and economy_cog:
            tax = await economy_cog.apply_lost_gamble_tax(self.bet, context="Blackjack Quit Forfeit")
            tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
            if self.message and self.message.guild:
                await self.cog.record_minigame_loss(self.message.guild.id, self.author.id, "blackjack", loss_amount=self.bet)

        embed = self.get_embed(dealer_reveal=True, outcome_text=f"🚪 **{user.mention}** kherjti mn lmatch! T-3tbat loss (-{format_tad(self.bet)}{tax_str}).")
        file = self.get_render_file(dealer_reveal=True)
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self, attachments=[file])
            except Exception:
                pass

        return "🚪 Kherjti mn match dial **Blackjack**!"

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.success, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return

        # Disable double down once the player hits
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "Double Down":
                item.disabled = True

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

        # Double down is only legal on the initial 2-card hand!
        if len(self.player_hand) != 2:
            await interaction.response.send_message("❌ Mat9dch t-double down mn be3d ma drti Hit!", ephemeral=True)
            return

        if self._doubling:
            return
        self._doubling = True
        button.disabled = True

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if self.bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(self.author.id)
            if w["balance"] < self.bet:
                self._doubling = False
                button.disabled = False
                await interaction.response.send_message(f"❌ Ma 3ndekch flous kafyin bach t-double bet! (Khassek {format_tad(self.bet)})", ephemeral=True)
                return
            success = await economy_cog.deduct_balance(self.author.id, self.bet, context="Blackjack Double Down Stake")
            if not success:
                self._doubling = False
                button.disabled = False
                await interaction.response.send_message("❌ Flousk makafyinch bach t-double!", ephemeral=True)
                return
            self.bet *= 2
            if self.session_id and self.cog:
                await self.cog.update_session_bet(self.session_id, self.bet)

        self.player_hand.append(self.deck.pop())
        p_score = calculate_bj_score(self.player_hand)
        if p_score > 21:
            await self.end_game(f"💥 **BUST!** Double down o fatet 21 (**{p_score}**). Khsrti!", is_win=False, interaction=interaction)
        else:
            await self._dealer_turn(interaction, status_msg="⚡ **Double Down!**")

    async def _dealer_turn(self, interaction: Optional[discord.Interaction] = None, status_msg: str = ""):
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
