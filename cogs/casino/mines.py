from __future__ import annotations
import random
from typing import Optional, Union, Dict, Set, Tuple

import discord

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import clear_user_game


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
        self.session_id: Optional[str] = None
        self.message: Optional[discord.Message] = None

        all_cells = [(x, y) for y in range(self.height) for x in range(self.width)]
        self.bombs: Set[Tuple[int, int]] = set(random.sample(all_cells, self.bomb_count))
        self.buttons_map: Dict[Tuple[int, int], MinesGambleButton] = {}

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

        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)

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
        clear_user_game(self.cog.bot, self.author.id)
        self.stop()

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            clear_user_game(self.cog.bot, self.author.id)
            self.stop()
            self._reveal_all_bombs()
            for item in self.children:
                item.disabled = True

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

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

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        clear_user_game(self.cog.bot, self.author.id)
        self.stop()
        self._reveal_all_bombs()
        for item in self.children:
            item.disabled = True

        if self.session_id and self.cog:
            await self.cog.complete_active_session(self.session_id)

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        eco_msg = ""
        if self.bet > 0 and economy_cog:
            if self.revealed_count == 0:
                await economy_cog.add_balance(self.author.id, self.bet, context="Mines Quit Refund")
                eco_msg = f" (Rje3 lik l bet: {format_tad(self.bet)})"
            else:
                mult = self.get_current_multiplier()
                gross = int(round(self.bet * mult))
                net, tax = await economy_cog.apply_tax_and_add_balance(self.author.id, gross, context=f"Mines Quit Cashout ({mult:.2f}x)", vault="casino")
                net_profit = net - self.bet
                eco_msg = f" (Auto-cashed out: +{format_tad(net_profit)})"
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines", earnings=max(0, net_profit))

        embed = discord.Embed(
            title="💣 Mines Table — Quit",
            description=f"🚪 **{user.mention}** kherjti mn Mines!{eco_msg}",
            color=0x000000
        )
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
        return "🚪 Kherjti mn match dial **Mines**!"

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

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

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
            clear_user_game(self.cog.bot, self.author.id)
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

            if self.session_id and self.cog:
                await self.cog.complete_active_session(self.session_id)

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
            clear_user_game(self.cog.bot, self.author.id)
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
