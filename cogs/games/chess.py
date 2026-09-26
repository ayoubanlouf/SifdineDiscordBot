from __future__ import annotations
import io
import os
import time
import random
import asyncio
from typing import Optional, Union

import chess
import discord
from discord.ui import Button, View, Modal, TextInput
from PIL import Image, ImageDraw

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import (
    record_minigame_win, record_minigame_loss,
    is_user_in_game, set_user_in_game, clear_user_game
)

# ============ CHESS BOARD RENDERER ============

# Global cache for piece images
_PIECE_IMAGE_CACHE = {}

def render_chess_board(board: chess.Board, orientation: chess.Color = chess.WHITE) -> io.BytesIO:
    """Renders a chess board with loaded piece images and coordinate labels, supporting rotation."""
    square_size = 64
    board_size = square_size * 8
    margin = 30
    canvas_size = board_size + margin * 2
    
    # Colors
    light = (235, 209, 166)
    dark = (165, 117, 81)
    bg = (30, 30, 30)
    label_color = (200, 200, 200)

    image = Image.new("RGBA", (canvas_size, canvas_size), bg)
    draw = ImageDraw.Draw(image)

    # Grid
    for row in range(8):
        for col in range(8):
            color = light if (row + col) % 2 == 0 else dark
            x1, y1 = margin + col * square_size, margin + row * square_size
            draw.rectangle([x1, y1, x1 + square_size, y1 + square_size], fill=color)
            
            # Coordinates
            if col == 0:
                rank_lbl = str(8 - row) if orientation == chess.WHITE else str(row + 1)
                draw.text((margin - 18, y1 + 25), rank_lbl, fill=label_color)
            if row == 7:
                file_lbl = chr(97 + col) if orientation == chess.WHITE else chr(ord('h') - col)
                draw.text((x1 + square_size // 2 - 4, margin + board_size + 8), file_lbl, fill=label_color)

    # Load and draw pieces
    piece_map = {
        chess.PAWN: 'pawn',
        chess.KNIGHT: 'knight',
        chess.BISHOP: 'bishop',
        chess.ROOK: 'rook',
        chess.QUEEN: 'queen',
        chess.KING: 'king'
    }

    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            f = chess.square_file(square)
            r = chess.square_rank(square)
            if orientation == chess.WHITE:
                col = f
                row = 7 - r
            else:
                col = 7 - f
                row = r
            
            color_str = "white" if piece.color == chess.WHITE else "black"
            piece_str = piece_map.get(piece.piece_type)
            fname = f"assets/chess_pieces/{color_str}-{piece_str}.png"
            if not os.path.exists(fname):
                fname = f"chess_pieces/{color_str}-{piece_str}.png"
            
            if fname not in _PIECE_IMAGE_CACHE:
                try:
                    img = Image.open(fname).convert("RGBA")
                    _PIECE_IMAGE_CACHE[fname] = img.resize((square_size, square_size), Image.Resampling.LANCZOS)
                except Exception as e:
                    print(f"Error loading {fname}: {e}")
                    continue
            
            p_img = _PIECE_IMAGE_CACHE[fname]
            x = margin + col * square_size
            y = margin + row * square_size
            image.paste(p_img, (x, y), p_img)

    buffer = io.BytesIO()
    # Convert back to RGB for PNG
    image.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

# ============ CHESS UI CLASSES ============

class MoveModal(Modal, title="La3eb Chess"):
    move_input = TextInput(
        label="Dkhel l move ta3k (SAN ola UCI)",
        placeholder="mtalan e4, Nf3, Qh5, ola e2e4",
        required=True,
        max_length=10
    )

    def __init__(self, game_view: "ChessView"):
        super().__init__()
        self.game_view = game_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.game_view.process_move_input(interaction, self.move_input.value.strip())

class ChessView(View):
    def __init__(self, player_white: Union[discord.Member, discord.User], player_black: Union[discord.Member, discord.User], is_bot_game: bool = False, cog: Optional["Minigames"] = None, bet: int = 0):
        super().__init__(timeout=120)
        self.player_white = player_white
        self.player_black = player_black
        self.is_bot_game = is_bot_game
        self.cog = cog
        self.bet = bet
        self.board = chess.Board()
        self.current_turn = player_white
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self.draw_offered_by: Optional[Union[discord.Member, discord.User]] = None

    def get_current_color_symbol(self) -> str:
        return "⚪ (Byed)" if self.board.turn == chess.WHITE else "⚫ (K7el)"

    def is_current_player(self, user: Union[discord.Member, discord.User]) -> bool:
        return user == self.current_turn

    async def generate_board_file(self) -> discord.File:
        loop = asyncio.get_running_loop()
        buffer = await loop.run_in_executor(None, render_chess_board, self.board)
        return discord.File(buffer, filename="chess_board.png")

    async def handle_economy_payout(self, winner: Optional[Union[discord.Member, discord.User]] = None, is_draw: bool = False) -> str:
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if not economy_cog:
            return ""

        if self.is_bot_game and winner == self.player_white:
            net, tax = await economy_cog.apply_tax_and_add_balance(self.player_white.id, 5000, context="Chess Bot Win")
            if self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, self.player_white.id, "chess", earnings=net)
            return f"\n\n💰 **{winner.mention}** rbe7 **+{net}** {TAD_EMOJI} TAD (`{tax}` TAD tax)!"
        elif self.is_bot_game and is_draw:
            net, tax = await economy_cog.apply_tax_and_add_balance(self.player_white.id, 2000, context="Chess Bot Draw")
            return f"\n\n🤝 **Ta3adol m3a bot AI!**\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: 2,000 TAD • `{tax}` TAD tax)!"

        if self.bet <= 0:
            return ""

        w_payout, burned, d_split = calculate_pvp_payout(self.bet)
        if burned > 0:
            await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="Chess PvP")
        if is_draw:
            await economy_cog.add_balance(self.player_white.id, d_split, context="Chess Draw Split")
            await economy_cog.add_balance(self.player_black.id, d_split, context="Chess Draw Split")
            return f"\n\n💰 Kola wa7d rj3at lih {format_tad(d_split)} (`{burned:,}` {TAD_EMOJI} tax)."
        elif winner:
            await economy_cog.add_balance(winner.id, w_payout, context="Chess Wager Win")
            return f"\n\n💰 **{winner.mention}** rbe7 {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
        return ""

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="♟️ Match dial Chess", color=0x000000)
        embed.add_field(name="Byed ⚪", value=self.player_white.mention, inline=True)
        embed.add_field(name="K7el ⚫", value=self.player_black.mention, inline=True)
        if self.bet > 0:
            embed.add_field(name="💰 Stake", value=format_tad(self.bet), inline=True)

        if self.game_over:
            outcome = self.board.outcome()
            if outcome:
                w_payout, burned, d_split = calculate_pvp_payout(self.bet) if self.bet > 0 else (0, 0, 0)
                eco_suffix = ""
                if self.bet > 0:
                    if outcome.winner in (chess.WHITE, chess.BLACK):
                        eco_suffix = f"\n💰 Rbe7ti {format_tad(w_payout)} (`{burned:,}` {TAD_EMOJI} tax)!"
                    else:
                        eco_suffix = f"\n💰 Kola wa7d rj3at lih {format_tad(d_split)} (`{burned:,}` {TAD_EMOJI} tax)."
                elif self.is_bot_game:
                    if outcome.winner == chess.WHITE:
                        eco_suffix = f"\n🤖 Ghelbti bot AI o rbe7ti **5,000** {TAD_EMOJI} TAD!"
                    elif outcome.winner is None:
                        eco_suffix = f"\n🤖 Ta3adol m3a bot AI! Rbe7ti **2,000** {TAD_EMOJI} TAD!"

                if outcome.winner == chess.WHITE:
                    embed.description = f"🏆 **Checkmate! {self.player_white.mention} (Byed) rbe7!**{eco_suffix}"
                elif outcome.winner == chess.BLACK:
                    embed.description = f"🏆 **Checkmate! {self.player_black.mention} (K7el) rbe7!**{eco_suffix}"
                else:
                    embed.description = f"🤝 **Ta3adol! ({outcome.termination.name.replace('_', ' ').title()})**{eco_suffix}"
        else:
            turn_str = f"Nobet {self.current_turn.mention} {self.get_current_color_symbol()}"
            if self.board.is_check():
                turn_str += " **[CHECK!]**"
            embed.description = turn_str

        embed.set_image(url="attachment://chess_board.png")
        return embed

    async def on_timeout(self):
        if not self.game_over and self.message:
            self.game_over = True
            self.stop()
            winner = self.player_black if self.current_turn == self.player_white else self.player_white
            loser = self.current_turn
            eco_str = await self.handle_economy_payout(winner=winner)
            if not self.is_bot_game and self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_win(self.message.guild.id, winner.id, "chess", earnings=self.bet if self.bet > 0 else 0)
                await self.cog.record_minigame_loss(self.message.guild.id, loser.id, "chess", loss_amount=self.bet if self.bet > 0 else 0)
            elif self.is_bot_game and loser == self.player_white and self.cog and self.message and self.message.guild:
                await self.cog.record_minigame_loss(self.message.guild.id, loser.id, "chess", loss_amount=0)
            embed = self.build_embed()
            embed.description = f"⏰ **Sala lwe9t! {self.current_turn.mention} khser b l inactivity. {winner.mention} rbe7!**{eco_str}"
            try:
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        self.stop()
        winner = self.player_black if user == self.player_white else self.player_white
        loser = user
        eco_str = await self.handle_economy_payout(winner=winner)
        if not self.is_bot_game and self.cog and self.message and self.message.guild:
            await self.cog.record_minigame_win(self.message.guild.id, winner.id, "chess", earnings=self.bet if self.bet > 0 else 0)
            await self.cog.record_minigame_loss(self.message.guild.id, loser.id, "chess", loss_amount=self.bet if self.bet > 0 else 0)
        elif self.is_bot_game and loser == self.player_white and self.cog and self.message and self.message.guild:
            await self.cog.record_minigame_loss(self.message.guild.id, loser.id, "chess", loss_amount=0)
        embed = self.build_embed()
        embed.description = f"🚪 **{user.mention} kherj mn lmatch o t-3tbat forfeit!** 🏆 **{winner.mention}** rbe7!{eco_str}"
        if self.message:
            try:
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass
        if self.cog and hasattr(self.cog, "bot"):
            clear_user_game(self.cog.bot, self.player_white.id)
            if not self.is_bot_game and self.player_black:
                clear_user_game(self.cog.bot, self.player_black.id)
        return f"🚪 Kherjti mn match dial **Chess** o t-3tbat forfeit!"

    # ---------- Strong Bot Engine (Minimax + Alpha-Beta + Evaluation) ----------
    def _evaluate_board(self, board: chess.Board) -> int:
        if board.is_checkmate():
            if board.turn == chess.WHITE:
                return -99999
            else:
                return 99999
        if board.is_stalemate() or board.is_insufficient_material() or board.is_seventyfive_moves() or board.is_fivefold_repetition():
            return 0

        piece_values = {
            chess.PAWN: 100,
            chess.KNIGHT: 320,
            chess.BISHOP: 330,
            chess.ROOK: 500,
            chess.QUEEN: 900,
            chess.KING: 20000,
        }

        pawn_table = [
            0, 0, 0, 0, 0, 0, 0, 0,
            50, 50, 50, 50, 50, 50, 50, 50,
            10, 10, 20, 30, 30, 20, 10, 10,
            5, 5, 10, 25, 25, 10, 5, 5,
            0, 0, 0, 20, 20, 0, 0, 0,
            5, -5, -10, 0, 0, -10, -5, 5,
            5, 10, 10, -20, -20, 10, 10, 5,
            0, 0, 0, 0, 0, 0, 0, 0
        ]
        knight_table = [
            -50, -40, -30, -30, -30, -30, -40, -50,
            -40, -20, 0, 0, 0, 0, -20, -40,
            -30, 0, 10, 15, 15, 10, 0, -30,
            -30, 5, 15, 20, 20, 15, 5, -30,
            -30, 0, 15, 20, 20, 15, 0, -30,
            -30, 5, 10, 15, 15, 10, 5, -30,
            -40, -20, 0, 5, 5, 0, -20, -40,
            -50, -40, -30, -30, -30, -30, -40, -50
        ]
        bishop_table = [
            -20, -10, -10, -10, -10, -10, -10, -20,
            -10, 0, 0, 0, 0, 0, 0, -10,
            -10, 0, 10, 10, 10, 10, 0, -10,
            -10, 5, 5, 10, 10, 5, 5, -10,
            -10, 0, 10, 10, 10, 10, 0, -10,
            -10, 10, 10, 10, 10, 10, 10, -10,
            -10, 5, 0, 0, 0, 0, 5, -10,
            -20, -10, -10, -10, -10, -10, -10, -20
        ]
        rook_table = [
            0, 0, 0, 0, 0, 0, 0, 0,
            5, 10, 10, 10, 10, 10, 10, 5,
            -5, 0, 0, 0, 0, 0, 0, -5,
            -5, 0, 0, 0, 0, 0, 0, -5,
            -5, 0, 0, 0, 0, 0, 0, -5,
            -5, 0, 0, 0, 0, 0, 0, -5,
            -5, 0, 0, 0, 0, 0, 0, -5,
            0, 0, 0, 5, 5, 0, 0, 0
        ]
        queen_table = [
            -20, -10, -10, -5, -5, -10, -10, -20,
            -10, 0, 0, 0, 0, 0, 0, -10,
            -10, 0, 5, 5, 5, 5, 0, -10,
            -5, 0, 5, 5, 5, 5, 0, -5,
            0, 0, 5, 5, 5, 5, 0, -5,
            -10, 5, 5, 5, 5, 5, 0, -10,
            -10, 0, 5, 0, 0, 0, 0, -10,
            -20, -10, -10, -5, -5, -10, -10, -20
        ]
        king_table = [
            -30, -40, -40, -50, -50, -40, -40, -30,
            -30, -40, -40, -50, -50, -40, -40, -30,
            -30, -40, -40, -50, -50, -40, -40, -30,
            -30, -40, -40, -50, -50, -40, -40, -30,
            -20, -30, -30, -40, -40, -30, -30, -20,
            -10, -20, -20, -20, -20, -20, -20, -10,
            20, 20, 0, 0, 0, 0, 20, 20,
            20, 30, 10, 0, 0, 10, 30, 20
        ]

        score = 0
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if not piece:
                continue
            value = piece_values[piece.piece_type]
            rank = chess.square_rank(square)
            file = chess.square_file(square)
            table_index = rank * 8 + file
            if piece.piece_type == chess.PAWN:
                value += pawn_table[table_index]
            elif piece.piece_type == chess.KNIGHT:
                value += knight_table[table_index]
            elif piece.piece_type == chess.BISHOP:
                value += bishop_table[table_index]
            elif piece.piece_type == chess.ROOK:
                value += rook_table[table_index]
            elif piece.piece_type == chess.QUEEN:
                value += queen_table[table_index]
            elif piece.piece_type == chess.KING:
                value += king_table[table_index]

            if piece.color == chess.WHITE:
                score += value
            else:
                score -= value

        return score

    def _order_moves(self, board: chess.Board, moves):
        scored = []
        for move in moves:
            score = 0
            if board.is_capture(move):
                victim = board.piece_at(move.to_square)
                attacker = board.piece_at(move.from_square)
                victim_value = {
                    chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                    chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 20000
                }.get(victim.piece_type if victim else chess.PAWN, 100)
                attacker_value = {
                    chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                    chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 20000
                }.get(attacker.piece_type if attacker else chess.PAWN, 100)
                score += 10 * victim_value - attacker_value
            if move.promotion:
                score += 900
            scored.append((score, move))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored]

    def _quiescence(self, board: chess.Board, alpha: int, beta: int, depth: int = 4) -> int:
        stand_pat = self._evaluate_board(board)
        if depth == 0:
            return stand_pat

        if board.turn == chess.WHITE:
            if stand_pat >= beta:
                return beta
            if stand_pat > alpha:
                alpha = stand_pat
        else:
            if stand_pat <= alpha:
                return alpha
            if stand_pat < beta:
                beta = stand_pat

        legal_moves = list(board.legal_moves)
        captures = [m for m in legal_moves if board.is_capture(m)]
        ordered = self._order_moves(board, captures)

        for move in ordered:
            board.push(move)
            score = self._quiescence(board, alpha, beta, depth - 1)
            board.pop()
            if board.turn == chess.BLACK:
                if score >= beta:
                    return beta
                if score > alpha:
                    alpha = score
            else:
                if score <= alpha:
                    return alpha
                if score < beta:
                    beta = score

        if board.turn == chess.WHITE:
            return alpha
        else:
            return beta

    def _minimax(self, board: chess.Board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
        if depth == 0 or board.is_game_over():
            return self._quiescence(board, alpha, beta)

        legal_moves = list(board.legal_moves)
        ordered = self._order_moves(board, legal_moves)

        if maximizing:
            max_eval = -999999
            for move in ordered:
                board.push(move)
                eval_val = self._minimax(board, depth - 1, alpha, beta, False)
                board.pop()
                max_eval = max(max_eval, eval_val)
                alpha = max(alpha, eval_val)
                if beta <= alpha:
                    break
            return max_eval
        else:
            min_eval = 999999
            for move in ordered:
                board.push(move)
                eval_val = self._minimax(board, depth - 1, alpha, beta, True)
                board.pop()
                min_eval = min(min_eval, eval_val)
                beta = min(beta, eval_val)
                if beta <= alpha:
                    break
            return min_eval

    def make_bot_move(self):
        legal_moves = list(self.board.legal_moves)
        if not legal_moves:
            return

        bot_is_white = (self.board.turn == chess.WHITE)
        best_move = legal_moves[0]
        best_value = -999999 if bot_is_white else 999999
        ordered = self._order_moves(self.board, legal_moves)

        # Fast tactical depth for snappy response without blocking event loop
        search_depth = 2

        for move in ordered:
            self.board.push(move)
            if bot_is_white:
                value = self._minimax(self.board, search_depth - 1, -999999, 999999, False)
                if value > best_value:
                    best_value = value
                    best_move = move
            else:
                value = self._minimax(self.board, search_depth - 1, -999999, 999999, True)
                if value < best_value:
                    best_value = value
                    best_move = move
            self.board.pop()

        self.board.push(best_move)

    async def process_move_input(self, interaction: discord.Interaction, move_str: str):
        if self.game_over or not self.is_current_player(interaction.user):
            if interaction.response.is_done():
                await interaction.followup.send("Mashi nobtsek!", ephemeral=True)
            else:
                await interaction.response.send_message("Mashi nobtsek!", ephemeral=True)
            return

        parsed_move = None
        try:
            parsed_move = self.board.parse_san(move_str)
        except ValueError:
            try:
                parsed_move = chess.Move.from_uci(move_str)
                if parsed_move not in self.board.legal_moves:
                    parsed_move = None
            except ValueError:
                parsed_move = None

        if not parsed_move or parsed_move not in self.board.legal_moves:
            err_msg = f"❌ **l move ghalat (`{move_str}`)!** khdem b SAN (mtalan `e4`, `Nf3`) ola UCI (mtalan `e2e4`)."
            if interaction.response.is_done():
                await interaction.followup.send(err_msg, ephemeral=True)
            else:
                await interaction.response.send_message(err_msg, ephemeral=True)
            return

        # Push Human Move
        self.board.push(parsed_move)

        # Check win/draw
        if self.board.is_game_over():
            self.game_over = True
            self.stop()
            outcome = self.board.outcome()
            winner = None
            is_draw = False
            if outcome:
                if outcome.winner == chess.WHITE:
                    winner = self.player_white
                elif outcome.winner == chess.BLACK:
                    winner = self.player_black
                else:
                    is_draw = True
            await self.handle_economy_payout(winner=winner, is_draw=is_draw)
            if self.board.is_checkmate() and not self.is_bot_game and self.cog and interaction.guild and winner:
                loser = self.player_black if winner == self.player_white else self.player_white
                await self.cog.record_minigame_win(interaction.guild.id, winner.id, "chess", earnings=self.bet if self.bet > 0 else 0)
                await self.cog.record_minigame_loss(interaction.guild.id, loser.id, "chess", loss_amount=self.bet if self.bet > 0 else 0)
            board_file = await self.generate_board_file()
            if interaction.response.is_done():
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=self.build_embed(), attachments=[board_file], view=None)
            else:
                await interaction.response.edit_message(embed=self.build_embed(), attachments=[board_file], view=None)
            return

        # Switch Turn
        self.current_turn = self.player_black if self.current_turn == self.player_white else self.player_white

        # Bot Move (Single-Player) - Offloaded to worker thread so event loop / gateway never blocks
        if self.is_bot_game and self.current_turn == self.player_black:
            await asyncio.to_thread(self.make_bot_move)
            if self.board.is_game_over():
                self.game_over = True
                self.stop()
                outcome = self.board.outcome()
                winner = None
                is_draw = False
                if outcome:
                    if outcome.winner == chess.WHITE:
                        winner = self.player_white
                    elif outcome.winner == chess.BLACK:
                        winner = self.player_black
                    else:
                        is_draw = True
                await self.handle_economy_payout(winner=winner, is_draw=is_draw)
                if self.board.is_checkmate() and not self.is_bot_game and self.cog and interaction.guild and winner:
                    loser = self.player_black if winner == self.player_white else self.player_white
                    await self.cog.record_minigame_win(interaction.guild.id, winner.id, "chess", earnings=self.bet if self.bet > 0 else 0)
                    await self.cog.record_minigame_loss(interaction.guild.id, loser.id, "chess", loss_amount=self.bet if self.bet > 0 else 0)
                elif self.board.is_checkmate() and self.is_bot_game and winner == self.player_black and self.cog and interaction.guild:
                    await self.cog.record_minigame_loss(interaction.guild.id, self.player_white.id, "chess", loss_amount=0)
            else:
                self.current_turn = self.player_white

        board_file = await self.generate_board_file()
        if interaction.response.is_done():
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=self.build_embed(), attachments=[board_file], view=self if not self.game_over else None)
        else:
            await interaction.response.edit_message(embed=self.build_embed(), attachments=[board_file], view=self if not self.game_over else None)

    @discord.ui.button(label="Move", style=discord.ButtonStyle.primary, emoji="♟️")
    async def move_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_current_player(interaction.user):
            await interaction.response.send_message("Mashi nobtsek!", ephemeral=True)
            return
        await interaction.response.send_modal(MoveModal(self))

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.secondary, emoji="🤝")
    async def draw_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user not in (self.player_white, self.player_black):
            await interaction.response.send_message("Nta mashi f had l match.", ephemeral=True)
            return

        if self.is_bot_game:
            await interaction.response.send_message("Ma9derch n accepti ta3adol daba.", ephemeral=True)
            return

        if self.draw_offered_by is None:
            self.draw_offered_by = interaction.user
            await interaction.response.send_message(f"🤝 **{interaction.user.mention} 9tar7 ta3adol!** clicki 3la 'Ta3adol' bach t accepti.", ephemeral=False)
        elif self.draw_offered_by != interaction.user:
            self.game_over = True
            self.stop()
            eco_str = await self.handle_economy_payout(is_draw=True)
            embed = self.build_embed()
            embed.description = f"🤝 **Match sala b ta3adol btifa9!**{eco_str}"
            board_file = await self.generate_board_file()
            await interaction.response.edit_message(embed=embed, attachments=[board_file], view=None)
        else:
            await interaction.response.send_message("Deja drti l9tira7, tsnna lakhor ijawb.", ephemeral=True)

    @discord.ui.button(label="Resign", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def resign_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user not in (self.player_white, self.player_black):
            await interaction.response.send_message("Nta mashi f had l match.", ephemeral=True)
            return

        self.game_over = True
        self.stop()
        winner = self.player_black if interaction.user == self.player_white else self.player_white
        loser = interaction.user
        eco_str = await self.handle_economy_payout(winner=winner)
        if not self.is_bot_game and self.cog and interaction.guild:
            await self.cog.record_minigame_win(interaction.guild.id, winner.id, "chess", earnings=self.bet if self.bet > 0 else 0)
            await self.cog.record_minigame_loss(interaction.guild.id, loser.id, "chess", loss_amount=self.bet if self.bet > 0 else 0)
        elif self.is_bot_game and self.cog and interaction.guild:
            await self.cog.record_minigame_loss(interaction.guild.id, loser.id, "chess", loss_amount=0)
        
        embed = self.build_embed()
        embed.description = f"🏳️ **{interaction.user.mention} steslem! {winner.mention} rbe7!**{eco_str}"
        board_file = await self.generate_board_file()
        await interaction.response.edit_message(embed=embed, attachments=[board_file], view=None)

    def stop(self):
        if self.cog and hasattr(self.cog, "bot"):
            if self.player_white and not getattr(self.player_white, "bot", False):
                clear_user_game(self.cog.bot, self.player_white.id)
            if self.player_black and not getattr(self.player_black, "bot", False):
                clear_user_game(self.cog.bot, self.player_black.id)
        super().stop()

class ChessChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Minigames", bet: int = 0):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.bet = bet

    @discord.ui.button(label="Qbel", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Had l challenge mashi lik!", ephemeral=True)
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
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"Chess Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"Chess Wager Stake ({self.bet} TAD)")

        players = [self.challenger, self.challenged]
        random.shuffle(players)
        
        game_view = ChessView(players[0], players[1], is_bot_game=False, cog=self.cog, bet=self.bet)
        set_user_in_game(self.cog.bot, self.challenger.id, "Chess", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Chess", game_view)
        board_file = await game_view.generate_board_file()
        
        await interaction.response.edit_message(content=None, embed=game_view.build_embed(), attachments=[board_file], view=game_view)
        game_view.message = interaction.message
        self.stop()

    @discord.ui.button(label="Refed", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Had l challenge mashi lik!", ephemeral=True)
            return
        
        await interaction.response.edit_message(content=f"❌ {self.challenged.mention} rfed l match dial chess.", view=None)
        self.stop()

