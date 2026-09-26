from __future__ import annotations
import time
import random
from typing import Optional, Union

import discord
from discord.ui import Button, View

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import (
    record_minigame_win, record_minigame_loss,
    is_user_in_game, set_user_in_game, clear_user_game
)


class TicTacToeButton(Button):
    """A single button representing a cell in the Tic-Tac-Toe board."""
    def __init__(self, x: int, y: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="\u200b",
            row=y,
            custom_id=f"ttt_{x}_{y}"
        )
        self.x = x
        self.y = y


class TicTacToeView(View):
    """Base Tic-Tac-Toe view with common functionality."""
    WINNING_LINES = [
        [(0, 0), (1, 0), (2, 0)],
        [(0, 1), (1, 1), (2, 1)],
        [(0, 2), (1, 2), (2, 2)],
        [(0, 0), (0, 1), (0, 2)],
        [(1, 0), (1, 1), (1, 2)],
        [(2, 0), (2, 1), (2, 2)],
        [(0, 0), (1, 1), (2, 2)],
        [(2, 0), (1, 1), (0, 2)],
    ]

    def __init__(self, player_x: Union[discord.Member, discord.User], player_o: Union[discord.Member, discord.User], is_bot_game: bool = False, turn_timeout: int = 60, cog: Optional["Minigames"] = None, bet: int = 0):
        super().__init__(timeout=120)
        self.player_x = player_x
        self.player_o = player_o
        self.is_bot_game = is_bot_game
        self.cog = cog
        self.bet = bet
        self.current_turn = player_x  # X always goes first
        self.turn_timeout = turn_timeout
        self.turn_start = time.time()
        self.board = [[" " for _ in range(3)] for _ in range(3)]
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self._timeout_task: Optional[asyncio.Task] = None

        for y in range(3):
            for x in range(3):
                button = TicTacToeButton(x, y)
                button.callback = self.button_callback
                self.add_item(button)

        self._timeout_task = asyncio.create_task(self._turn_timeout_task())

    def get_button(self, x: int, y: int):
        for item in self.children:
            if isinstance(item, TicTacToeButton) and item.x == x and item.y == y:
                return item
        return None

    def update_button(self, x: int, y: int, player: str):
        button = self.get_button(x, y)
        if button:
            button.disabled = True
            if player == "X":
                button.label = "❌"
                button.style = discord.ButtonStyle.danger
            else:
                button.label = "⭕"
                button.style = discord.ButtonStyle.success

    def check_winner(self) -> Optional[str]:
        for line in self.WINNING_LINES:
            values = [self.board[y][x] for x, y in line]
            if values[0] != " " and values[0] == values[1] == values[2]:
                return values[0]

        if all(self.board[y][x] != " " for x in range(3) for y in range(3)):
            return "draw"

        return None

    def disable_all_buttons(self):
        for item in self.children:
            if isinstance(item, TicTacToeButton):
                item.disabled = True

    def get_status_content(self) -> str:
        if self.game_over:
            winner = self.check_winner()
            if winner == "draw":
                if self.bet > 0:
                    _, burned, d_split = calculate_pvp_payout(self.bet)
                    return f"🤝 **Ta3adol!**\n💰 Kola wa7d rj3at lih {format_tad(d_split)} (`{burned:,}` {TAD_EMOJI} tax)."
                return "🤝 **Ta3adol!**"
            elif winner == "X":
                if self.bet > 0:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    return f"🏆 **{self.player_x.mention} (X) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
                elif self.is_bot_game:
                    return f"🏆 **{self.player_x.mention} (X) rbe7!**\n🤖 Ghelbti bot AI o rbe7ti **5,000** {TAD_EMOJI} TAD!"
                return f"🏆 **{self.player_x.mention} (X) rbe7!**"
            elif winner == "O":
                if self.is_bot_game:
                    return "🤖 **Rb7tk!**"
                else:
                    if self.bet > 0:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        return f"🏆 **{self.player_o.mention} (O) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
                    return f"🏆 **{self.player_o.mention} (O) rbe7!**"
        else:
            current_player = "X" if self.current_turn == self.player_x else "O"
            return f"{'❌' if current_player == 'X' else '⭕'} **Dor ta3 {self.current_turn.mention} ({current_player})**"

    async def _turn_timeout_task(self):
        try:
            await asyncio.sleep(self.turn_timeout)
            if not self.game_over:
                self.game_over = True
                self.disable_all_buttons()
                current_player = self.current_turn
                if current_player == self.player_x:
                    winner = self.player_o
                    winner_symbol = "O"
                else:
                    winner = self.player_x
                    winner_symbol = "X"

                if self.is_bot_game:
                    content = f"⏰ **{current_player.mention} sala lik lwe9t!** Rb7tk!"
                    if self.cog and self.message and self.message.guild:
                        await self.cog.record_minigame_loss(self.message.guild.id, current_player.id, "tictactoe", loss_amount=0)
                else:
                    content = f"⏰ **{current_player.mention} sala lih lwe9t!** 🏆 **{winner.mention} ({winner_symbol}) rbe7!**"
                    if self.bet > 0 and self.cog:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        economy_cog = self.cog.bot.get_cog("Economy")
                        if economy_cog:
                            if burned > 0:
                                await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="TTT Wager Win (Timeout)")
                            await economy_cog.add_balance(winner.id, w_payout, context="TTT Wager Win (Timeout)")
                        if self.message and self.message.guild:
                            await self.cog.record_minigame_win(self.message.guild.id, winner.id, "tictactoe", earnings=w_payout - self.bet)
                            await self.cog.record_minigame_loss(self.message.guild.id, current_player.id, "tictactoe", loss_amount=self.bet)
                        content += f"\n💰 Rbe7ti {format_tad(w_payout)}!"
                    elif self.cog and self.message and self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, winner.id, "tictactoe")
                        await self.cog.record_minigame_loss(self.message.guild.id, current_player.id, "tictactoe", loss_amount=0)

                if self.message:
                    await self.message.edit(content=content, view=self)
                self.stop()
        except asyncio.CancelledError:
            pass

    def stop(self):
        if hasattr(self, '_timeout_task') and self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        if self.cog and hasattr(self.cog, "bot"):
            if self.player_x and not getattr(self.player_x, "bot", False):
                clear_user_game(self.cog.bot, self.player_x.id)
            if self.player_o and not getattr(self.player_o, "bot", False):
                clear_user_game(self.cog.bot, self.player_o.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        self.disable_all_buttons()
        winner = self.player_o if user == self.player_x else self.player_x
        winner_symbol = "O" if winner == self.player_o else "X"

        if self.is_bot_game:
            content = f"🚪 **{user.mention} kherjti mn lgame!**"
            if self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_loss(self.message.guild.id, user.id, "tictactoe", loss_amount=0)
        else:
            content = f"🚪 **{user.mention} kherj mn lmatch o t-3tbat forfeit!** 🏆 **{winner.mention} ({winner_symbol}) rbe7!**"
            if self.bet > 0 and self.cog:
                w_payout, burned, _ = calculate_pvp_payout(self.bet)
                economy_cog = self.cog.bot.get_cog("Economy")
                if economy_cog:
                    if burned > 0:
                        await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="TTT Wager Win (Forfeit)")
                    await economy_cog.add_balance(winner.id, w_payout, context="TTT Wager Win (Forfeit)")
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, winner.id, "tictactoe", earnings=w_payout - self.bet)
                    await self.cog.record_minigame_loss(self.message.guild.id, user.id, "tictactoe", loss_amount=self.bet)
                content += f"\n💰 Rbe7ti {format_tad(w_payout)}!"
            elif self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, winner.id, "tictactoe")
                await self.cog.record_minigame_loss(self.message.guild.id, user.id, "tictactoe", loss_amount=0)

        if self.message:
            try:
                await self.message.edit(content=content, view=self)
            except Exception:
                pass
        self.stop()
        return f"🚪 Kherjti mn match dial **Tic-Tac-Toe** o t-3tbat forfeit!"

    def make_bot_move(self):
        best_score = -float('inf')
        best_move = None

        # Try all legal moves
        for y in range(3):
            for x in range(3):
                if self.board[y][x] == " ":
                    self.board[y][x] = "O"
                    score = self.minimax(depth=0, is_maximizing=False)
                    self.board[y][x] = " "
                    if score > best_score:
                        best_score = score
                        best_move = (x, y)

        if best_move:
            x, y = best_move
            self.board[y][x] = "O"
            self.update_button(x, y, "O")

    def minimax(self, depth: int, is_maximizing: bool) -> int:
        winner = self.check_winner()
        if winner == "O":
            return 10 - depth
        elif winner == "X":
            return depth - 10
        elif winner == "draw":
            return 0

        if is_maximizing:
            best_score = -float('inf')
            for y in range(3):
                for x in range(3):
                    if self.board[y][x] == " ":
                        self.board[y][x] = "O"
                        score = self.minimax(depth + 1, False)
                        self.board[y][x] = " "
                        best_score = max(score, best_score)
            return best_score
        else:
            best_score = float('inf')
            for y in range(3):
                for x in range(3):
                    if self.board[y][x] == " ":
                        self.board[y][x] = "X"
                        score = self.minimax(depth + 1, True)
                        self.board[y][x] = " "
                        best_score = min(score, best_score)
            return best_score

    async def button_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        parts = custom_id.split("_")
        x, y = int(parts[1]), int(parts[2])

        if self.board[y][x] != " ":
            await interaction.response.send_message("Dak lmorba3 3amr ._.", ephemeral=True)
            return

        if self.game_over:
            await interaction.response.send_message("Had lmatch deja sala..", ephemeral=True)
            return

        if interaction.user != self.current_turn:
            if self.is_bot_game and interaction.user == self.player_x and self.current_turn == self.player_o:
                await interaction.response.send_message("Sber 3liya nl3eb..", ephemeral=True)
            else:
                await interaction.response.send_message("Machy dork hada asa7bi.", ephemeral=True)
            return

        if interaction.user not in (self.player_x, self.player_o):
            await interaction.response.send_message("Tferrej o zga.", ephemeral=True)
            return

        # Player move
        player_symbol = "X" if self.current_turn == self.player_x else "O"
        self.board[y][x] = player_symbol
        self.update_button(x, y, player_symbol)

        # Check win condition for human move
        winner = self.check_winner()
        if winner:
            self.game_over = True
            self.disable_all_buttons()
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

            if winner == "draw" and self.bet > 0 and economy_cog:
                _, burned, d_split = calculate_pvp_payout(self.bet)
                if burned > 0:
                    await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="TTT Draw")
                await economy_cog.add_balance(self.player_x.id, d_split, context="TTT Draw Split")
                await economy_cog.add_balance(self.player_o.id, d_split, context="TTT Draw Split")
            elif winner in ("X", "O"):
                winning_user = self.player_x if winner == "X" else self.player_o
                losing_user = self.player_o if winner == "X" else self.player_x
                if self.bet > 0 and economy_cog:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    if burned > 0:
                        await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="TTT Wager Win")
                    await economy_cog.add_balance(winning_user.id, w_payout, context="TTT Wager Win")
                    if self.cog and interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "tictactoe", earnings=w_payout - self.bet)
                        await self.cog.record_minigame_loss(interaction.guild.id, losing_user.id, "tictactoe", loss_amount=self.bet)
                elif self.is_bot_game and winner == "X" and economy_cog:
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player_x.id, 5000, context="TTT Bot Win")
                    if self.cog and interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, self.player_x.id, "tictactoe", earnings=net)
                elif self.is_bot_game and winner == "O" and self.cog and interaction.guild:
                    await self.cog.record_minigame_loss(interaction.guild.id, self.player_x.id, "tictactoe", loss_amount=0)
                elif not self.is_bot_game and self.cog and interaction.guild:
                    await self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "tictactoe")
                    await self.cog.record_minigame_loss(interaction.guild.id, losing_user.id, "tictactoe", loss_amount=0)

            await interaction.response.edit_message(content=self.get_status_content(), view=self)
            self.stop()
            return

        # Switch turn
        self.current_turn = self.player_o if self.current_turn == self.player_x else self.player_x

        # Single-player bot turn
        if self.is_bot_game and self.current_turn == self.player_o:
            self.make_bot_move()
            winner = self.check_winner()
            if winner:
                self.game_over = True
                self.disable_all_buttons()
                if winner == "O" and self.cog and interaction.guild:
                    await self.cog.record_minigame_loss(interaction.guild.id, self.player_x.id, "tictactoe", loss_amount=0)
                self.stop()
            else:
                self.current_turn = self.player_x

        if not self.game_over:
            self.turn_start = time.time()
            if hasattr(self, '_timeout_task') and self._timeout_task:
                self._timeout_task.cancel()
            self._timeout_task = asyncio.create_task(self._turn_timeout_task())

        await interaction.response.edit_message(content=self.get_status_content(), view=self)


class ChallengeView(View):
    """View for the challenge acceptance phase."""
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
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"TTT Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"TTT Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        players = [self.challenger, self.challenged]
        random.shuffle(players)
        player_x, player_o = players[0], players[1]

        game_view = TicTacToeView(player_x, player_o, is_bot_game=False, cog=self.cog, bet=self.bet)
        set_user_in_game(self.cog.bot, self.challenger.id, "Tic-Tac-Toe", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Tic-Tac-Toe", game_view)
        content = f"❌ **{player_x.mention}'s turn (X)**"
        await interaction.response.edit_message(content=content, view=game_view)
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
                    await self.message.edit(
                        content="⏰ Challenge ma t acceptach.",
                        view=self
                    )
                except discord.NotFound:
                    pass



TicTacToeChallengeView = ChallengeView
