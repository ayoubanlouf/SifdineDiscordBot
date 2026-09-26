from __future__ import annotations
import os
import random
from typing import Optional, Union

import discord

from cogs.economy import format_tad, TAD_EMOJI


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
