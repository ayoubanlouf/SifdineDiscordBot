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

# ============ CONNECT FOUR UI CLASSES (Module Level) ============

class ConnectFourButton(Button):
    """A single button representing a column selector in Connect Four."""
    def __init__(self, col: int):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=f"{col + 1}",
            custom_id=f"c4_col_{col}",
            row=0 if col < 4 else 1
        )
        self.col = col


class ConnectFourView(View):
    """The main Connect Four game view."""
    def __init__(self, player_red: Union[discord.Member, discord.User], player_yellow: Union[discord.Member, discord.User], is_bot_game: bool = False, turn_timeout: int = 60, cog: Optional["Minigames"] = None, bet: int = 0):
        super().__init__(timeout=120)
        self.player_red = player_red
        self.player_yellow = player_yellow
        self.is_bot_game = is_bot_game
        self.cog = cog
        self.bet = bet
        self.current_turn = player_red  # Red (🔴) goes first
        self.turn_timeout = turn_timeout
        self.turn_start = time.time()
        self.board = [["⚪" for _ in range(7)] for _ in range(6)]
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self._timeout_task: Optional[asyncio.Task] = None

        # Add 7 Column Buttons
        for col in range(7):
            button = ConnectFourButton(col)
            button.callback = self.button_callback
            self.add_item(button)

        self._timeout_task = asyncio.create_task(self._turn_timeout_task())

    def render_board(self) -> str:
        board_str = ""
        for row in self.board:
            board_str += "".join(row) + "\n"
        board_str += "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣"
        return board_str

    def get_status_content(self) -> str:
        board_text = self.render_board()
        if self.game_over:
            winner = self.check_winner()
            if winner == "draw":
                if self.bet > 0:
                    _, burned, d_split = calculate_pvp_payout(self.bet)
                    return f"{board_text}\n\n🤝 **Ta3adol!**\n💰 Kola wa7d rj3at lih {format_tad(d_split)} (`{burned:,}` {TAD_EMOJI} tax)."
                elif self.is_bot_game:
                    return f"{board_text}\n\n🤝 **Ta3adol!**\n🤖 Ta3adol m3a bot AI! Rbe7ti **1,000** {TAD_EMOJI} TAD!"
                return f"{board_text}\n\n🤝 **Ta3adol!**"
            elif winner == "🔴":
                if self.bet > 0:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
                elif self.is_bot_game:
                    return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**\n🤖 Ghelbti bot AI o rbe7ti **5,000** {TAD_EMOJI} TAD!"
                return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**"
            elif winner == "🟡":
                if self.is_bot_game:
                    return f"{board_text}\n\n🤖 **Rb7tk!**"
                else:
                    if self.bet > 0:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        return f"{board_text}\n\n🏆 **{self.player_yellow.mention} (🟡) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
                    return f"{board_text}\n\n🏆 **{self.player_yellow.mention} (🟡) rbe7!**"

        current_symbol = "🔴" if self.current_turn == self.player_red else "🟡"
        return f"{board_text}\n\n{current_symbol} **Dor ta3 {self.current_turn.mention} ({current_symbol})**"

    def drop_piece(self, col: int, symbol: str) -> bool:
        """Drops a piece into the lowest available row in `col`. Returns True if successful."""
        for row in reversed(range(6)):
            if self.board[row][col] == "⚪":
                self.board[row][col] = symbol
                # Disable column button if it is now full
                if row == 0:
                    for item in self.children:
                        if isinstance(item, ConnectFourButton) and item.col == col:
                            item.disabled = True
                return True
        return False

    def check_winner(self) -> Optional[str]:
        # Horizontal
        for r in range(6):
            for c in range(4):
                if self.board[r][c] != "⚪" and self.board[r][c] == self.board[r][c+1] == self.board[r][c+2] == self.board[r][c+3]:
                    return self.board[r][c]
        # Vertical
        for r in range(3):
            for c in range(7):
                if self.board[r][c] != "⚪" and self.board[r][c] == self.board[r+1][c] == self.board[r+2][c] == self.board[r+3][c]:
                    return self.board[r][c]
        # Positive Diagonal
        for r in range(3):
            for c in range(4):
                if self.board[r][c] != "⚪" and self.board[r][c] == self.board[r+1][c+1] == self.board[r+2][c+2] == self.board[r+3][c+3]:
                    return self.board[r][c]
        # Negative Diagonal
        for r in range(3, 6):
            for c in range(4):
                if self.board[r][c] != "⚪" and self.board[r][c] == self.board[r-1][c+1] == self.board[r-2][c+2] == self.board[r-3][c+3]:
                    return self.board[r][c]
        # Draw check
        if all(self.board[0][c] != "⚪" for c in range(7)):
            return "draw"

        return None

    def _evaluate_window(self, window: list[str], piece: str) -> int:
        opp_piece = "🔴" if piece == "🟡" else "🟡"
        score = 0
        p_cnt = window.count(piece)
        opp_cnt = window.count(opp_piece)
        empty_cnt = window.count("⚪")

        if p_cnt == 4:
            score += 10000
        elif p_cnt == 3 and empty_cnt == 1:
            score += 100
        elif p_cnt == 2 and empty_cnt == 2:
            score += 10

        if opp_cnt == 3 and empty_cnt == 1:
            score -= 120
        elif opp_cnt == 2 and empty_cnt == 2:
            score -= 15

        return score

    def _score_position(self, piece: str) -> int:
        score = 0

        # Center column preference
        center_count = [self.board[r][3] for r in range(6)].count(piece)
        score += center_count * 6

        # Center-adjacent columns
        c2_count = [self.board[r][2] for r in range(6)].count(piece)
        c4_count = [self.board[r][4] for r in range(6)].count(piece)
        score += (c2_count + c4_count) * 3

        # Horizontal
        for r in range(6):
            row_array = self.board[r]
            for c in range(4):
                window = row_array[c:c+4]
                score += self._evaluate_window(window, piece)

        # Vertical
        for c in range(7):
            col_array = [self.board[r][c] for r in range(6)]
            for r in range(3):
                window = col_array[r:r+4]
                score += self._evaluate_window(window, piece)

        # Positive Diagonal
        for r in range(3):
            for c in range(4):
                window = [self.board[r+i][c+i] for i in range(4)]
                score += self._evaluate_window(window, piece)

        # Negative Diagonal
        for r in range(3, 6):
            for c in range(4):
                window = [self.board[r-i][c+i] for i in range(4)]
                score += self._evaluate_window(window, piece)

        return score

    def _c4_minimax(self, depth: int, alpha: float, beta: float, is_maximizing: bool) -> tuple[Optional[int], float]:
        valid_cols = [c for c in [3, 2, 4, 1, 5, 0, 6] if self.board[0][c] == "⚪"]
        winner = self.check_winner()

        if winner == "🟡":
            return (None, 1000000 + depth * 1000)
        elif winner == "🔴":
            return (None, -1000000 - depth * 1000)
        elif winner == "draw" or not valid_cols:
            return (None, 0)
        elif depth == 0:
            return (None, self._score_position("🟡"))

        if is_maximizing:
            value = -float('inf')
            best_col = valid_cols[0]
            for col in valid_cols:
                for r in reversed(range(6)):
                    if self.board[r][col] == "⚪":
                        self.board[r][col] = "🟡"
                        new_score = self._c4_minimax(depth - 1, alpha, beta, False)[1]
                        self.board[r][col] = "⚪"
                        if new_score > value:
                            value = new_score
                            best_col = col
                        alpha = max(alpha, value)
                        break
                if alpha >= beta:
                    break
            return best_col, value
        else:
            value = float('inf')
            best_col = valid_cols[0]
            for col in valid_cols:
                for r in reversed(range(6)):
                    if self.board[r][col] == "⚪":
                        self.board[r][col] = "🔴"
                        new_score = self._c4_minimax(depth - 1, alpha, beta, True)[1]
                        self.board[r][col] = "⚪"
                        if new_score < value:
                            value = new_score
                            best_col = col
                        beta = min(beta, value)
                        break
                if alpha >= beta:
                    break
            return best_col, value

    def make_bot_move(self):
        valid_cols = [c for c in [3, 2, 4, 1, 5, 0, 6] if self.board[0][c] == "⚪"]
        if not valid_cols:
            return

        col, _ = self._c4_minimax(depth=5, alpha=-float('inf'), beta=float('inf'), is_maximizing=True)
        if col is None or col not in valid_cols:
            col = valid_cols[0]

        self.drop_piece(col, "🟡")

    def get_button(self, col: int) -> Optional[ConnectFourButton]:
        for item in self.children:
            if isinstance(item, ConnectFourButton) and item.col == col:
                return item
        return None

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

    async def _turn_timeout_task(self):
        try:
            await asyncio.sleep(self.turn_timeout)
            if not self.game_over:
                self.game_over = True
                self.disable_all_buttons()
                current_player = self.current_turn
                winner = self.player_yellow if current_player == self.player_red else self.player_red
                winner_symbol = "🟡" if winner == self.player_yellow else "🔴"

                if self.is_bot_game:
                    content = f"{self.render_board()}\n\n⏰ **{current_player.mention} sala lik lwe9t!** Rb7tk!"
                    if self.cog and self.message and self.message.guild:
                        await self.cog.record_minigame_loss(self.message.guild.id, current_player.id, "connectfour", loss_amount=0)
                else:
                    content = f"{self.render_board()}\n\n⏰ **{current_player.mention} sala lih lwe9t!** 🏆 **{winner.mention} ({winner_symbol}) rbe7!**"
                    if self.bet > 0 and self.cog:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        economy_cog = self.cog.bot.get_cog("Economy")
                        if economy_cog:
                            if burned > 0:
                                await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="ConnectFour Win (Timeout)")
                            await economy_cog.add_balance(winner.id, w_payout, context="ConnectFour Win (Timeout)")
                        if self.message and self.message.guild:
                            await self.cog.record_minigame_win(self.message.guild.id, winner.id, "connectfour", earnings=w_payout - self.bet)
                            await self.cog.record_minigame_loss(self.message.guild.id, current_player.id, "connectfour", loss_amount=self.bet)
                        content += f"\n💰 Rbe7ti {format_tad(w_payout)}!"
                    elif self.cog and self.message and self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, winner.id, "connectfour")
                        await self.cog.record_minigame_loss(self.message.guild.id, current_player.id, "connectfour", loss_amount=0)

                if self.message:
                    await self.message.edit(content=content, view=self)
                self.stop()
        except asyncio.CancelledError:
            pass

    def stop(self):
        if hasattr(self, '_timeout_task') and self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        if self.cog and hasattr(self.cog, "bot"):
            if self.player_red and not getattr(self.player_red, "bot", False):
                clear_user_game(self.cog.bot, self.player_red.id)
            if self.player_yellow and not getattr(self.player_yellow, "bot", False):
                clear_user_game(self.cog.bot, self.player_yellow.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        self.disable_all_buttons()
        winner = self.player_yellow if user == self.player_red else self.player_red
        winner_symbol = "🟡" if winner == self.player_yellow else "🔴"

        if self.is_bot_game:
            content = f"{self.render_board()}\n\n🚪 **{user.mention} kherjti mn lgame!**"
            if self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_loss(self.message.guild.id, user.id, "connectfour", loss_amount=0)
        else:
            content = f"{self.render_board()}\n\n🚪 **{user.mention} kherj mn lmatch o t-3tbat forfeit!** 🏆 **{winner.mention} ({winner_symbol}) rbe7!**"
            if self.bet > 0 and self.cog:
                w_payout, burned, _ = calculate_pvp_payout(self.bet)
                economy_cog = self.cog.bot.get_cog("Economy")
                if economy_cog:
                    if burned > 0:
                        await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="ConnectFour Win (Forfeit)")
                    await economy_cog.add_balance(winner.id, w_payout, context="ConnectFour Win (Forfeit)")
                if self.message and self.message.guild:
                    await self.cog.record_minigame_win(self.message.guild.id, winner.id, "connectfour", earnings=w_payout - self.bet)
                    await self.cog.record_minigame_loss(self.message.guild.id, user.id, "connectfour", loss_amount=self.bet)
                content += f"\n💰 Rbe7ti {format_tad(w_payout)}!"
            elif self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, winner.id, "connectfour")
                await self.cog.record_minigame_loss(self.message.guild.id, user.id, "connectfour", loss_amount=0)

        if self.message:
            try:
                await self.message.edit(content=content, view=self)
            except Exception:
                pass
        self.stop()
        return f"🚪 Kherjti mn match dial **Connect 4** o t-3tbat forfeit!"

    async def button_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("c4_col_"):
            return

        col = int(custom_id.split("_")[-1])

        if self.game_over:
            await interaction.response.send_message("Had lmatch deja sala..", ephemeral=True)
            return

        if interaction.user != self.current_turn:
            if self.is_bot_game and interaction.user == self.player_red and self.current_turn == self.player_yellow:
                await interaction.response.send_message("Sber 3liya nl3eb..", ephemeral=True)
            else:
                await interaction.response.send_message("Machy dork hada asa7bi.", ephemeral=True)
            return

        current_symbol = "🔴" if self.current_turn == self.player_red else "🟡"
        if not self.drop_piece(col, current_symbol):
            await interaction.response.send_message("Had l colonne 3amr ._.", ephemeral=True)
            return

        # Check win for current human player move
        winner = self.check_winner()
        if winner:
            self.game_over = True
            self.disable_all_buttons()
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None

            if winner == "draw" and self.bet > 0 and economy_cog:
                _, burned, d_split = calculate_pvp_payout(self.bet)
                if burned > 0:
                    await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="ConnectFour Draw")
                await economy_cog.add_balance(self.player_red.id, d_split, context="ConnectFour Draw Split")
                await economy_cog.add_balance(self.player_yellow.id, d_split, context="ConnectFour Draw Split")
            elif winner == "draw" and self.is_bot_game and economy_cog:
                await economy_cog.apply_tax_and_add_balance(self.player_red.id, 1000, context="ConnectFour Bot Draw")
            elif winner in ("🔴", "🟡"):
                winning_user = self.player_red if winner == "🔴" else self.player_yellow
                losing_user = self.player_yellow if winner == "🔴" else self.player_red
                if self.bet > 0 and economy_cog:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    if burned > 0:
                        await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="ConnectFour Wager Win")
                    await economy_cog.add_balance(winning_user.id, w_payout, context="ConnectFour Wager Win")
                    if self.cog and interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "connectfour", earnings=w_payout - self.bet)
                        await self.cog.record_minigame_loss(interaction.guild.id, losing_user.id, "connectfour", loss_amount=self.bet)
                elif self.is_bot_game and winner == "🔴" and economy_cog:
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player_red.id, 5000, context="ConnectFour Bot Win")
                    if self.cog and interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, self.player_red.id, "connectfour", earnings=net)
                elif not self.is_bot_game and self.cog and interaction.guild:
                    await self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "connectfour")
                    await self.cog.record_minigame_loss(interaction.guild.id, losing_user.id, "connectfour", loss_amount=0)

            await interaction.response.edit_message(content=self.get_status_content(), view=self)
            self.stop()
            return

        # Switch turn
        self.current_turn = self.player_yellow if self.current_turn == self.player_red else self.player_red

        # Bot response (Single-player)
        if self.is_bot_game and self.current_turn == self.player_yellow:
            self.make_bot_move()
            winner = self.check_winner()
            if winner:
                self.game_over = True
                self.disable_all_buttons()
                if winner == "draw":
                    economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                    if economy_cog:
                        await economy_cog.apply_tax_and_add_balance(self.player_red.id, 1000, context="ConnectFour Bot Draw")
                elif winner == "🟡" and self.cog and interaction.guild:
                    await self.cog.record_minigame_loss(interaction.guild.id, self.player_red.id, "connectfour", loss_amount=0)
                self.stop()
            else:
                self.current_turn = self.player_red

        # Reset turn timer
        if not self.game_over:
            self.turn_start = time.time()
            if hasattr(self, '_timeout_task') and self._timeout_task:
                self._timeout_task.cancel()
            self._timeout_task = asyncio.create_task(self._turn_timeout_task())

        await interaction.response.edit_message(content=self.get_status_content(), view=self)


class ConnectFourChallengeView(View):
    """View for the Connect Four multiplayer challenge acceptance phase."""
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
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"C4 Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"C4 Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        players = [self.challenger, self.challenged]
        random.shuffle(players)
        player_red, player_yellow = players[0], players[1]

        game_view = ConnectFourView(player_red, player_yellow, is_bot_game=False, cog=self.cog, bet=self.bet)
        set_user_in_game(self.cog.bot, self.challenger.id, "Connect 4", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Connect 4", game_view)
        content = game_view.get_status_content()
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
                    await self.message.edit(content="⏰ Challenge ma t acceptach.", view=self)
                except discord.NotFound:
                    pass
