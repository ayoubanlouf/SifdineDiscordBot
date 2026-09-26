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

# ============ MINESWEEPER UI CLASSES (Module Level) ============

class MinesweeperButton(Button):
    def __init__(self, x: int, y: int):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="❓",
            custom_id=f"ms_{x}_{y}",
            row=y
        )
        self.x = x
        self.y = y


class MinesweeperSoloView(View):
    def __init__(self, player: discord.Member, cog: Optional["Minigames"] = None):
        super().__init__(timeout=180)
        self.player = player
        self.cog = cog
        self.message: Optional[discord.Message] = None
        self.width = 4
        self.height = 5
        self.mine_count = 4
        self.game_over = False

        # Place mines
        all_coords = [(x, y) for x in range(self.width) for y in range(self.height)]
        self.mines = set(random.sample(all_coords, self.mine_count))
        self.revealed = set()

        # Add grid buttons (rows 0, 1, 2, 3, 4)
        for y in range(self.height):
            for x in range(self.width):
                button = MinesweeperButton(x, y)
                button.callback = self.button_callback
                self.add_item(button)

        # Add Exit Game button on row 4 alongside the 4 grid buttons (total 5 buttons in row 4)
        exit_btn = Button(
            label="Exit Game",
            style=discord.ButtonStyle.danger,
            emoji="🚪",
            custom_id="ms_solo_exit",
            row=4
        )
        exit_btn.callback = self.exit_callback
        self.add_item(exit_btn)

    def get_button(self, x: int, y: int) -> Optional[MinesweeperButton]:
        for item in self.children:
            if isinstance(item, MinesweeperButton) and item.x == x and item.y == y:
                return item
        return None

    def count_adjacent_mines(self, x: int, y: int) -> int:
        count = 0
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                if (x + dx, y + dy) in self.mines:
                    count += 1
        return count

    def reveal_cell(self, x: int, y: int):
        if (x, y) in self.revealed:
            return
        self.revealed.add((x, y))

        button = self.get_button(x, y)
        if not button:
            return

        button.disabled = True
        button.style = discord.ButtonStyle.secondary

        adjacent = self.count_adjacent_mines(x, y)
        if adjacent == 0:
            button.label = "⬜"
            # Recursively reveal neighbors
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        if (nx, ny) not in self.mines and (nx, ny) not in self.revealed:
                            self.reveal_cell(nx, ny)
        else:
            number_emojis = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣"}
            button.label = number_emojis.get(adjacent, str(adjacent))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    async def exit_callback(self, interaction: discord.Interaction):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return

        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True
            if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines:
                item.label = "💣"
                item.style = discord.ButtonStyle.secondary

        total_safe = (self.width * self.height) - self.mine_count
        content = f"🚪 **{self.player.mention}** khrej mn lgame (Game Over).\nSafe squares revealed: **{len(self.revealed)}/{total_safe}**"
        await interaction.response.edit_message(content=content, view=self)

    async def button_callback(self, interaction: discord.Interaction):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return

        button_id = interaction.data.get("custom_id", "")
        try:
            _, x_str, y_str = button_id.split("_")
            x, y = int(x_str), int(y_str)
        except (ValueError, IndexError):
            return

        # Check if hit mine
        if (x, y) in self.mines:
            self.game_over = True
            self.stop()
            # Show all mines and disable everything
            for item in self.children:
                item.disabled = True
                if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines:
                    item.label = "💥"
                    item.style = discord.ButtonStyle.danger

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            eco_msg = ""
            if economy_cog and len(self.revealed) > 0:
                gross = min(25, max(5, int(len(self.revealed) * 1.5)))
                net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, gross, context="Minesweeper Participation")
                eco_msg = f"\n\n💰 Reb7a ta3 lmoucharaka: **+{net}** {TAD_EMOJI} TAD."

            content = f"💥 **Booooom! Game Over**\n{self.player.mention} khser hit 9as mine f ({x+1}, {y+1})!{eco_msg}"
            await interaction.response.edit_message(content=content, view=self)
            return

        # Reveal
        self.reveal_cell(x, y)

        total_safe = (self.width * self.height) - self.mine_count
        # Check Win
        if len(self.revealed) == total_safe:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True
                if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines:
                    item.label = "💣"
                    item.style = discord.ButtonStyle.success

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            eco_msg = ""
            if economy_cog:
                net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, 150, context="Minesweeper Clear")
                eco_msg = f"\n\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: 150 TAD • `{tax}` TAD tax)!"
                if interaction.guild:
                    await self.cog.record_minigame_win(interaction.guild.id, self.player.id, "minesweeper", earnings=net)

            content = f"🎉🏆 **Rbe7ti!**\n{self.player.mention} l9iti grid kaml blama t9is 7ta mine!{eco_msg}"
            await interaction.response.edit_message(content=content, view=self)
            return

        content = f"💣 **Minesweeper (Solo)** — Hreb mn l mines o l9a safe squares kamlin!\nSafe: **{len(self.revealed)}/{total_safe}**"
        await interaction.response.edit_message(content=content, view=self)

    def stop(self):
        if self.cog and hasattr(self.cog, "bot") and self.player:
            clear_user_game(self.cog.bot, self.player.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content=f"🚪 **{user.mention}** kherjti mn Minesweeper!", view=self)
            except Exception:
                pass
        self.stop()
        return "🚪 Kherjti mn Minesweeper!"

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ **Sala lwe9t!** Match sala bsbab inactivity.", view=self)
                except Exception:
                    pass


class MinesweeperMultiplayerView(View):
    def __init__(self, p1: discord.Member, p2: discord.Member, cog: Optional["Minigames"] = None, bet: int = 0):
        super().__init__(timeout=180)
        self.p1 = p1
        self.p2 = p2
        self.cog = cog
        self.bet = bet
        self.payout_handled = False
        self.scores = {p1.id: 0, p2.id: 0}
        self.current_turn = p1
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self.width = 4
        self.height = 5
        self.mine_count = 4

        # Place mines
        all_coords = [(x, y) for x in range(self.width) for y in range(self.height)]
        self.mines = set(random.sample(all_coords, self.mine_count))
        self.found_mines = 0

        # Add buttons (rows 0, 1, 2, 3, 4)
        for y in range(self.height):
            for x in range(self.width):
                button = MinesweeperButton(x, y)
                button.callback = self.button_callback
                self.add_item(button)

        # Add Exit Game button on row 4 alongside the 4 grid buttons (total 5 buttons in row 4)
        exit_btn = Button(
            label="Exit Game",
            style=discord.ButtonStyle.danger,
            emoji="🚪",
            custom_id="ms_multi_exit",
            row=4
        )
        exit_btn.callback = self.exit_callback
        self.add_item(exit_btn)

    def get_button(self, x: int, y: int) -> Optional[MinesweeperButton]:
        for item in self.children:
            if isinstance(item, MinesweeperButton) and item.x == x and item.y == y:
                return item
        return None

    def count_adjacent_mines(self, x: int, y: int) -> int:
        count = 0
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                if (x + dx, y + dy) in self.mines:
                    count += 1
        return count

    async def handle_economy_payout(self, winner: Optional[Union[discord.Member, discord.User]] = None, is_draw: bool = False) -> str:
        if self.payout_handled or self.bet <= 0:
            return ""
        self.payout_handled = True
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if not economy_cog:
            return ""

        winner_payout, tax_burned, _ = calculate_pvp_payout(self.bet)
        if is_draw or winner is None:
            await economy_cog.add_balance(self.p1.id, self.bet, context="Minesweeper Draw Refund")
            await economy_cog.add_balance(self.p2.id, self.bet, context="Minesweeper Draw Refund")
            return f"\n\n🤝 **Draw Refund:** {format_tad(self.bet)} returned to each player."
        else:
            loser = self.p2 if winner.id == self.p1.id else self.p1
            if tax_burned > 0:
                await economy_cog.deposit_vault("bank", tax_burned, source="pvp_wager", context=f"Minesweeper PvP vs {loser.name}")
            await economy_cog.add_balance(winner.id, winner_payout, context=f"Minesweeper Wager Win vs {loser.name}")
            net_profit = winner_payout - self.bet
            if self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, winner.id, "minesweeper", earnings=net_profit)
                await self.cog.record_minigame_loss(self.message.guild.id, loser.id, "minesweeper", loss_amount=self.bet)
            return f"\n\n💰 **Wager Payout:** {winner.mention} rbe7 **+{format_tad(winner_payout)}** (Gross: {self.bet*2:,} TAD • `{tax_burned:,}` TAD tax)!"

    def get_content(self, extra: str = "") -> str:
        if self.game_over:
            p1_score = self.scores[self.p1.id]
            p2_score = self.scores[self.p2.id]
            if p1_score > p2_score:
                res = f"🏆 **{self.p1.mention} rbe7!**\nNatija: 🔴 **{self.p1.display_name}** ({p1_score}) vs 🔵 **{self.p2.display_name}** ({p2_score})"
            elif p2_score > p1_score:
                res = f"🏆 **{self.p2.mention} rbe7!**\nNatija: 🔵 **{self.p2.display_name}** ({p2_score}) vs 🔴 **{self.p1.display_name}** ({p1_score})"
            else:
                res = f"🤝 **Ta3adol!**\nNatija: **{p1_score}-{p2_score}**"
            return res + extra
        else:
            wager_str = f" | 💰 Pot: {format_tad(self.bet*2)}" if self.bet > 0 else ""
            return (
                f"💣 **Minesweeper (Hunt the Mines)** — 9leb 3la l mines bach tjib points!{wager_str}\n"
                f"🔴 **{self.p1.display_name}**: {self.scores[self.p1.id]} pts | 🔵 **{self.p2.display_name}**: {self.scores[self.p2.id]} pts\n\n"
                f"⚡ Dor dial: {self.current_turn.mention}"
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    async def exit_callback(self, interaction: discord.Interaction):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return

        quitter = interaction.user
        winner = self.p2 if quitter == self.p1 else self.p1

        self.game_over = True
        self.stop()

        eco_msg = await self.handle_economy_payout(winner=winner)

        for item in self.children:
            item.disabled = True
            if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines and not item.disabled:
                item.label = "💣"
                item.style = discord.ButtonStyle.secondary

        content = (
            f"🚪 **{quitter.mention}** khrej mn lgame (Forfeit).\n"
            f"🏆 **{winner.mention}** rbe7 lmatch!{eco_msg}"
        )
        await interaction.response.edit_message(content=content, view=self)

    async def button_callback(self, interaction: discord.Interaction):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return

        if interaction.user != self.current_turn:
            await interaction.response.send_message("Machy dork asa7bi.", ephemeral=True)
            return

        button_id = interaction.data.get("custom_id", "")
        try:
            _, x_str, y_str = button_id.split("_")
            x, y = int(x_str), int(y_str)
        except (ValueError, IndexError):
            return

        button = self.get_button(x, y)
        if not button:
            return

        # Check if hit mine
        if (x, y) in self.mines:
            self.scores[self.current_turn.id] += 1
            self.found_mines += 1

            button.disabled = True
            button.label = "💥"
            button.style = discord.ButtonStyle.danger

            # Check win condition (majority is 3 or all 4 mines found)
            p1_score = self.scores[self.p1.id]
            p2_score = self.scores[self.p2.id]
            if p1_score >= 3 or p2_score >= 3 or self.found_mines == self.mine_count:
                self.game_over = True
                self.stop()
                winner = self.p1 if p1_score > p2_score else (self.p2 if p2_score > p1_score else None)
                is_draw = p1_score == p2_score
                eco_msg = await self.handle_economy_payout(winner=winner, is_draw=is_draw)
                # Disable all other buttons and show remaining mines
                for item in self.children:
                    item.disabled = True
                    if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines and not item.disabled:
                        item.label = "💣"
                        item.style = discord.ButtonStyle.secondary
                await interaction.response.edit_message(content=self.get_content(extra=eco_msg), view=self)
                return
            else:
                # Bonus turn, so turn does not change!
                pass
        else:
            # Hit safe cell: reveal adjacent
            button.disabled = True
            button.style = discord.ButtonStyle.secondary
            adjacent = self.count_adjacent_mines(x, y)
            if adjacent == 0:
                button.label = "⬜"
            else:
                number_emojis = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣"}
                button.label = number_emojis.get(adjacent, str(adjacent))

            # Pass turn to opponent
            self.current_turn = self.p2 if self.current_turn == self.p1 else self.p1

        await interaction.response.edit_message(content=self.get_content(), view=self)

    def stop(self):
        if self.cog and hasattr(self.cog, "bot"):
            if self.p1:
                clear_user_game(self.cog.bot, self.p1.id)
            if self.p2:
                clear_user_game(self.cog.bot, self.p2.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        winner = self.p2 if user.id == self.p1.id else self.p1
        loser = user
        eco_msg = await self.handle_economy_payout(winner=winner, is_draw=False)
        for item in self.children:
            item.disabled = True
        content = f"🚪 **{loser.mention} kherj mn lmatch o t-3tbat forfeit!** 🏆 **{winner.mention}** rbe7!{eco_msg}"
        if self.message:
            try:
                await self.message.edit(content=content, view=self)
            except Exception:
                pass
        self.stop()
        return "🚪 Kherjti mn match dial **Minesweeper** o t-3tbat forfeit!"

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            self.stop()
            eco_msg = await self.handle_economy_payout(is_draw=True)
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content=f"⏰ **Sala lwe9t!** Match sala bsbab inactivity.{eco_msg}", view=self)
                except Exception:
                    pass


class MinesweeperChallengeView(View):
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
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if economy_cog:
                w1 = await economy_cog.get_wallet(self.challenger.id)
                w2 = await economy_cog.get_wallet(self.challenged.id)
                if w1["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ {self.challenger.mention} ma b9ach 3ndo kafi dial flous!", ephemeral=True)
                    return
                if w2["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ Flousk makafyinch ({format_tad(w2['balance'])} / {format_tad(self.bet)})!", ephemeral=True)
                    return
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"Minesweeper Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"Minesweeper Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        game_view = MinesweeperMultiplayerView(self.challenger, self.challenged, cog=self.cog, bet=self.bet)
        set_user_in_game(self.cog.bot, self.challenger.id, "Minesweeper", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Minesweeper", game_view)

        await interaction.response.edit_message(content=game_view.get_content(), view=game_view)
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
            view=self
        )

    async def on_timeout(self):
        if not self.accepted:
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ Challenge ma t acceptach.", view=self)
                except discord.NotFound:
                    pass


