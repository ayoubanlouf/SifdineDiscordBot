from __future__ import annotations
import random
from typing import Optional, Union

import discord
from discord.ui import Button, View

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import (
    record_minigame_win, record_minigame_loss,
    is_user_in_game, set_user_in_game, clear_user_game
)

# ============ ROCK PAPER SCISSORS UI CLASSES (Module Level) ============

class RPSBotView(View):
    def __init__(self, player: discord.Member, cog: Optional["Minigames"] = None, bet: int = 0):
        super().__init__(timeout=60)
        self.player = player
        self.cog = cog
        self.bet = bet
        self.game_over = False
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.secondary, emoji="🪨", custom_id="rps_rock")
    async def rock(self, interaction: discord.Interaction, button: Button):
        await self.process_choice(interaction, "rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="rps_paper")
    async def paper(self, interaction: discord.Interaction, button: Button):
        await self.process_choice(interaction, "paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.secondary, emoji="✂️", custom_id="rps_scissors")
    async def scissors(self, interaction: discord.Interaction, button: Button):
        await self.process_choice(interaction, "scissors")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    async def process_choice(self, interaction: discord.Interaction, player_choice: str):
        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True

        bot_choice = random.choice(["rock", "paper", "scissors"])
        
        emoji_map = {
            "rock": "🪨 Rock",
            "paper": "📄 Paper",
            "scissors": "✂️ Scissors"
        }

        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

        if player_choice == bot_choice:
            title = "🤝 Ta3adol!"
            outcome = f"Nta khtarti **{emoji_map[player_choice]}** o ana khtart **{emoji_map[bot_choice]}**."
            if self.bet > 0 and economy_cog:
                await economy_cog.add_balance(self.player.id, self.bet, context="RPS Bot Draw (1x)")
                outcome += f"\n\n🤝 **Ta3adol (1x):** Rje3 lik l bet: {format_tad(self.bet)}."
        elif (player_choice == "rock" and bot_choice == "scissors") or \
             (player_choice == "paper" and bot_choice == "rock") or \
             (player_choice == "scissors" and bot_choice == "paper"):
            title = "🎉 Rbe7ti!"
            outcome = f"Nta khtarti **{emoji_map[player_choice]}** o ana khtart **{emoji_map[bot_choice]}**."
            if self.bet > 0 and economy_cog:
                gross_payout = self.bet * 2
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, gross_payout, context="RPS Bot Win (2x)")
                net_profit = net_payout - self.bet
                outcome += f"\n\n💰 Rbe7ti (2x) **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)!"
            elif economy_cog:
                net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, 20, context="RPS Bot Win")
                outcome += f"\n\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (`{tax}` TAD tax)!"
            if self.cog and interaction.guild:
                await self.cog.record_minigame_win(interaction.guild.id, self.player.id, "rockpaperscissors", earnings=self.bet if self.bet > 0 else 20)
        else:
            title = "🤖 Rb7tk!"
            outcome = f"Nta khtarti **{emoji_map[player_choice]}** o ana khtart **{emoji_map[bot_choice]}**."
            if self.bet > 0:
                outcome += f"\n\n💥 **Khserti (0x):** -{format_tad(self.bet)}."
                if self.cog and interaction.guild:
                    await self.cog.record_minigame_loss(interaction.guild.id, self.player.id, "rockpaperscissors", loss_amount=self.bet)
            elif self.cog and interaction.guild:
                await self.cog.record_minigame_loss(interaction.guild.id, self.player.id, "rockpaperscissors", loss_amount=0)

        embed = discord.Embed(
            title=title,
            description=outcome,
            color=0x000000
        )
        await interaction.response.edit_message(embed=embed, view=self)

    def stop(self):
        if self.cog and hasattr(self.cog, "bot") and self.player:
            clear_user_game(self.cog.bot, self.player.id)
        super().stop()

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if self.bet > 0 and economy_cog:
                await economy_cog.add_balance(self.player.id, self.bet, context="RPS Timeout Refund")
                embed = discord.Embed(
                    title="⏰ Sala lwe9t",
                    description=f"Sala lwe9t o ma khtartich. Rje3 lik l bet: {format_tad(self.bet)}.",
                    color=0x000000
                )
            else:
                embed = discord.Embed(
                    title="⏰ Sala lwe9t",
                    description="Sala lwe9t o ma khtartich.",
                    color=0x000000
                )
            if self.message:
                try:
                    await self.message.edit(embed=embed, view=self)
                except Exception:
                    pass

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        for item in self.children:
            item.disabled = True
        embed = discord.Embed(
            title="🚪 Khrejt mn lgame",
            description=f"🚪 **{user.mention}** kherjti mn Rock Paper Scissors!",
            color=0x000000
        )
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
        self.stop()
        return "🚪 Kherjti mn Rock Paper Scissors!"


class RPSMultiplayerView(View):
    def __init__(self, player1: discord.Member, player2: discord.Member, cog: Optional["Minigames"] = None, bet: int = 0):
        super().__init__(timeout=60)
        self.player1 = player1
        self.player2 = player2
        self.cog = cog
        self.bet = bet
        self.choices = {player1.id: None, player2.id: None}
        self.message: Optional[discord.Message] = None

    def stop(self):
        if self.cog and hasattr(self.cog, "bot"):
            if self.player1:
                clear_user_game(self.cog.bot, self.player1.id)
            if self.player2:
                clear_user_game(self.cog.bot, self.player2.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if all(c is not None for c in self.choices.values()):
            return ""
        winner = self.player2 if user.id == self.player1.id else self.player1
        loser = user
        outcome = f"🚪 **{loser.mention} kherj mn lgame o t-3tbat forfeit!** 🏆 **{winner.mention}** rbe7!"
        if self.bet > 0 and self.cog:
            w_payout, burned, _ = calculate_pvp_payout(self.bet)
            economy_cog = self.cog.bot.get_cog("Economy")
            if economy_cog:
                if burned > 0:
                    await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="RPS Win (Forfeit)")
                await economy_cog.add_balance(winner.id, w_payout, context="RPS Win (Forfeit)")
            if self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, winner.id, "rockpaperscissors", earnings=w_payout - self.bet)
                await self.cog.record_minigame_loss(self.message.guild.id, loser.id, "rockpaperscissors", loss_amount=self.bet)
            outcome += f"\n\n💰 **{winner.mention}** rbe7 {format_tad(w_payout)}!"
        for item in self.children:
            item.disabled = True
        embed = discord.Embed(title="🪨 Rock Paper Scissors (Forfeit)", description=outcome, color=0x000000)
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
        self.stop()
        return f"🚪 Kherjti mn match dial **Rock Paper Scissors** o t-3tbat forfeit!"

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.secondary, emoji="🪨", custom_id="rps_m_rock")
    async def rock(self, interaction: discord.Interaction, button: Button):
        await self.record_choice(interaction, "rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="rps_m_paper")
    async def paper(self, interaction: discord.Interaction, button: Button):
        await self.record_choice(interaction, "paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.secondary, emoji="✂️", custom_id="rps_m_scissors")
    async def scissors(self, interaction: discord.Interaction, button: Button):
        await self.record_choice(interaction, "scissors")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.player1, self.player2):
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    async def record_choice(self, interaction: discord.Interaction, choice: str):
        user_id = interaction.user.id
        if self.choices[user_id] is not None:
            await interaction.response.send_message("Khtarti deja, mat9derch tbedel.", ephemeral=True)
            return

        self.choices[user_id] = choice
        
        if all(c is not None for c in self.choices.values()):
            self.stop()
            for item in self.children:
                item.disabled = True

            emoji_map = {
                "rock": "🪨 Rock",
                "paper": "📄 Paper",
                "scissors": "✂️ Scissors"
            }
            
            p1_choice = self.choices[self.player1.id]
            p2_choice = self.choices[self.player2.id]

            winning_user = None
            if p1_choice == p2_choice:
                title = "🤝 Ta3adol!"
                outcome = f"{self.player1.mention} khtar **{emoji_map[p1_choice]}** o {self.player2.mention} khtar **{emoji_map[p2_choice]}**."
                if self.bet > 0:
                    _, burned, d_split = calculate_pvp_payout(self.bet)
                    economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                    if economy_cog:
                        if burned > 0:
                            await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="RPS Draw")
                        await economy_cog.add_balance(self.player1.id, d_split, context="RPS Draw Split")
                        await economy_cog.add_balance(self.player2.id, d_split, context="RPS Draw Split")
                    outcome += f"\n\n💰 Kola wa7d rj3at lih {format_tad(d_split)} (`{burned:,}` {TAD_EMOJI} tax)."
            elif (p1_choice == "rock" and p2_choice == "scissors") or \
                 (p1_choice == "paper" and p2_choice == "rock") or \
                 (p1_choice == "scissors" and p2_choice == "paper"):
                title = f"🏆 Winner: {self.player1.display_name}!"
                outcome = f"{self.player1.mention} khtar **{emoji_map[p1_choice]}** o {self.player2.mention} khtar **{emoji_map[p2_choice]}**."
                winning_user = self.player1
            else:
                title = f"🏆 Winner: {self.player2.display_name}!"
                outcome = f"{self.player1.mention} khtar **{emoji_map[p1_choice]}** o {self.player2.mention} khtar **{emoji_map[p2_choice]}**."
                winning_user = self.player2

            if winning_user:
                losing_user = self.player1 if winning_user == self.player2 else self.player2
                if self.bet > 0:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                    if economy_cog:
                        if burned > 0:
                            await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="RPS Wager Win")
                        await economy_cog.add_balance(winning_user.id, w_payout, context="RPS Wager Win")
                    if self.cog and interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "rockpaperscissors", earnings=w_payout - self.bet)
                        await self.cog.record_minigame_loss(interaction.guild.id, losing_user.id, "rockpaperscissors", loss_amount=self.bet)
                    outcome += f"\n\n💰 **{winning_user.mention}** rbe7 {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
                elif self.cog and interaction.guild:
                    await self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "rockpaperscissors")
                    await self.cog.record_minigame_loss(interaction.guild.id, losing_user.id, "rockpaperscissors", loss_amount=0)

            embed = discord.Embed(
                title=title,
                description=outcome,
                color=0x000000
            )
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            other_player = self.player2 if interaction.user == self.player1 else self.player1
            await interaction.response.send_message(f"Khtarti {choice}! Tsna {other_player.mention} i khtar.", ephemeral=True)
            
            embed = discord.Embed(
                title="🪨 Rock Paper Scissors",
                description=(
                    f"⚔️ {self.player1.mention} vs {self.player2.mention}\n\n"
                    f"✅ {interaction.user.mention} khtar choice dialo.\n"
                    f"⏳ Tsna {other_player.mention} i khtar."
                ),
                color=0x000000
            )
            await interaction.message.edit(embed=embed)

    async def on_timeout(self):
        self.stop()
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                embed = discord.Embed(
                    title="⏰ Game Timeout",
                    description="Sala lwe9t o ma kmltoch lgame.",
                    color=0x000000
                )
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass


class RPSChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Minigames", bet: int = 0):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.bet = bet
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        if is_user_in_game(self.cog.bot, self.challenger.id):
            await interaction.response.send_message(f"❌ {self.challenger.mention} 3ndo deja game khddama!", ephemeral=True)
            return
        if is_user_in_game(self.cog.bot, self.challenged.id):
            await interaction.response.send_message("❌ 3ndek deja game khddama!", ephemeral=True)
            return

        if self.bet > 0:
            economy_cog = self.cog.bot.get_cog("Economy")
            if economy_cog:
                w1 = await economy_cog.get_wallet(self.challenger.id)
                w2 = await economy_cog.get_wallet(self.challenged.id)
                if w1["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ {self.challenger.mention} ma b9ach 3ndo kafi dial flous!", ephemeral=True)
                    return
                if w2["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ Flousk makafyinch ({format_tad(w2['balance'])} / {format_tad(self.bet)})!", ephemeral=True)
                    return
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"RPS Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"RPS Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        game_view = RPSMultiplayerView(self.challenger, self.challenged, cog=self.cog, bet=self.bet)
        set_user_in_game(self.cog.bot, self.challenger.id, "Rock Paper Scissors", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Rock Paper Scissors", game_view)

        embed = discord.Embed(
            title="🪨 Rock Paper Scissors",
            description=f"⚔️ {self.challenger.mention} vs {self.challenged.mention}\n\nKola wa7d ikhtar choice dialo b tkhbia!",
            color=0x000000
        )
        await interaction.response.edit_message(content=None, embed=embed, view=game_view)
        game_view.message = interaction.message

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=f"❌ {self.challenged.mention} mabghach il3eb.",
            embed=None,
            view=self
        )

    async def on_timeout(self):
        if not self.accepted:
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ Challenge ma t acceptach.", embed=None, view=self)
                except discord.NotFound:
                    pass




