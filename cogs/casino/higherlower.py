from __future__ import annotations
import os
import random
from typing import Optional, Union, Dict

import discord

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


def draw_hl_card() -> Dict[str, Union[str, int]]:
    rank = random.choice(RANKS)
    suit = random.choice(SUITS)
    return {"rank": rank, "suit": suit, "value": RANK_VALUES[rank]}


def get_hl_card_file(card: dict) -> Optional[discord.File]:
    r = RANK_NAME_MAP.get(card['rank'], card['rank'].lower())
    s = SUIT_NAME_MAP.get(card['suit'], 'spades')
    path = os.path.join('assets', 'playing_cards', f'{r}_of_{s}.png')
    if os.path.exists(path):
        return discord.File(path, filename="card.png")
    return None


class HigherLowerView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, bet: int = 0, initial_card: Optional[dict] = None):
        super().__init__(timeout=60)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.current_card = initial_card or draw_hl_card()
        self.streak = 0
        self.game_over = False
        self.session_id: Optional[str] = None
        self.message: Optional[discord.Message] = None

    def get_multiplier(self) -> float:
        if self.streak <= 0:
            return 1.00
        streak_table = [1.00, 1.20, 1.50, 2.00, 2.50, 3.20, 4.00, 5.00, 6.50, 8.00, 10.00]
        if self.streak < len(streak_table):
            return streak_table[self.streak]
        return round(10.00 + (self.streak - 10) * 1.50, 2)

    def get_embed(self, outcome_msg: str = "") -> discord.Embed:
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

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

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
            clear_user_game(self.cog.bot, self.author.id)
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

        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)

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
        clear_user_game(self.cog.bot, self.author.id)
        self.stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        clear_user_game(self.cog.bot, self.author.id)
        self.stop()
        for item in self.children:
            item.disabled = True

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        eco_msg = ""
        if self.bet > 0 and economy_cog:
            if self.streak == 0:
                await economy_cog.add_balance(self.author.id, self.bet, context="HigherLower Quit Refund")
                eco_msg = f" (Rje3 lik l bet: {format_tad(self.bet)})"
            else:
                mult = self.get_multiplier()
                gross = int(round(self.bet * mult))
                net, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross, context=f"HigherLower Quit Cashout ({mult:.2f}x)", vault="casino")
                net_profit = net - self.bet
                eco_msg = f" (Auto-cashed out: +{format_tad(net_profit)})"
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower", earnings=max(0, net_profit))

        embed = discord.Embed(
            title="🚪 Higher or Lower — Quit",
            description=f"🚪 **{user.mention}** kherjti mn Higher or Lower!{eco_msg}",
            color=0x000000
        )
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
        return "🚪 Kherjti mn match dial **Higher or Lower**!"

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            clear_user_game(self.cog.bot, self.author.id)
            self.stop()
            for item in self.children:
                item.disabled = True

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                if self.streak == 0:
                    if self.cog:
                        self.cog.hl_sticky_sessions[self.author.id] = {
                            "card": self.current_card,
                            "bet": self.bet,
                            "session_id": self.session_id
                        }
                        if self.session_id:
                            await self.cog.pause_active_session(self.session_id)
                    desc = (
                        f"⏰ **Game Timed Out!**\n\n"
                        f"3awd dir `sat hl` bach tkemmel had ter7.\nBet: {format_tad(self.bet)}\nCard: `{self.current_card['rank']}{self.current_card['suit']}`"
                    )

                else:
                    if self.session_id and self.cog:
                        await self.cog.complete_active_session(self.session_id)
                    mult = self.get_multiplier()
                    gross_payout = int(round(self.bet * mult))
                    net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross_payout, context=f"HigherLower Auto-Cashout ({mult:.2f}x)", vault="casino")
                    net_profit = net_payout - self.bet
                    if self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower", earnings=max(0, net_profit))
                    desc = f"⏰ **Game Timed Out (Auto-Cashed Out)!**\nStreak: **{self.streak}** (Multiplier: **{mult:.2f}x**) • Net Payout: **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)."
            else:
                if self.session_id and self.cog:
                    await self.cog.complete_active_session(self.session_id)
                desc = "⏰ **Game Timed Out!**"

            embed = discord.Embed(title="🃏 Higher or Lower — Timed Out", description=desc, color=0x000000)
            file = get_hl_card_file(self.current_card)
            if file:
                embed.set_thumbnail(url="attachment://card.png")
            try:
                await self.message.edit(embed=embed, view=self, attachments=[file] if file else [])
            except Exception:
                pass
