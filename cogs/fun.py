import asyncio
import random
import io
import chess
import sqlite3
import html
import difflib
import re
import unicodedata
from typing import Optional, Union
import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
from PIL import Image, ImageDraw, ImageFont
import os
import aiohttp
import time

from converters import FuzzyMember
from assets.wordle_words import WORDLE_TARGETS
from cogs.economy import parse_bet_argument, format_tad, TAD_EMOJI, calculate_pvp_payout, not_fraud

# Compatibility shim for newer akinator API variations
import akinator
import akinator.exceptions
if not hasattr(akinator.exceptions, 'CantGoBackAnyFurther'):
    class _CantGoBackAnyFurther(Exception):
        pass
    akinator.exceptions.CantGoBackAnyFurther = _CantGoBackAnyFurther

from akinator import AsyncAkinator
from akinator.async_client import AsyncClient

# Patch the AsyncClient.__handler to handle missing 'akitude' in API response
_original_handler = AsyncClient._AsyncClient__handler

async def _patched_handler(self, response):
    response.raise_for_status()
    try:
        data = response.json()
    except Exception as e:
        if "A technical problem has ocurred." in response.text:
            raise RuntimeError("A technical problem has occurred. Please try again later.") from e
        raise RuntimeError("Failed to parse the response as JSON.") from e

    if "completion" not in data:
        data["completion"] = self.completion
    if data["completion"] == "KO - TIMEOUT":
        raise RuntimeError("The session has timed out. Please start a new game.")
    if data["completion"] == "SOUNDLIKE":
        self.finished = True
        self.win = True
        if not self.id_proposition:
            await self.defeat()
    elif "id_proposition" in data:
        self.win = True
        self.id_proposition = data["id_proposition"]
        self.name_proposition = data["name_proposition"]
        self.description_proposition = data["description_proposition"]
        self.step_last_proposition = self.step
        self.pseudo = data["pseudo"]
        self.flag_photo = data["flag_photo"]
        self.photo = data["photo"]
    else:
        # Handle missing 'akitude' key gracefully
        self.akitude = data.get("akitude", "defi.png")
        self.step = int(data.get("step", self.step or 0))
        self.progression = float(data.get("progression", self.progression or 0))
        self.question = data.get("question", self.question)
    self.completion = data.get("completion", self.completion)

AsyncClient._AsyncClient__handler = _patched_handler


# ============ CHESS BOARD RENDERER ============

# Global cache for piece images
_PIECE_IMAGE_CACHE = {}

def render_chess_board(board: chess.Board) -> io.BytesIO:
    """Renders a chess board with loaded piece images and coordinate labels."""
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
                draw.text((margin - 18, y1 + 25), str(8 - row), fill=label_color)
            if row == 7:
                draw.text((x1 + square_size // 2 - 4, margin + board_size + 8), chr(97 + col), fill=label_color)

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
            col = chess.square_file(square)
            row = 7 - chess.square_rank(square)
            
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
        await self.game_view.process_move_input(interaction, self.move_input.value.strip())

class ChessView(View):
    def __init__(self, player_white: Union[discord.Member, discord.User], player_black: Union[discord.Member, discord.User], is_bot_game: bool = False, cog: Optional["Fun"] = None, bet: int = 0):
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
        return "⚪ (Byed)" if self.board.turn == chess.WHITE else "⚫ (Khel)"

    def is_current_player(self, user: Union[discord.Member, discord.User]) -> bool:
        return user == self.current_turn

    async def generate_board_file(self) -> discord.File:
        loop = asyncio.get_running_loop()
        buffer = await loop.run_in_executor(None, render_chess_board, self.board)
        return discord.File(buffer, filename="chess_board.png")

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="♟️ Match dial Chess", color=0x000000)
        embed.add_field(name="Byed ⚪", value=self.player_white.mention, inline=True)
        embed.add_field(name="Khel ⚫", value=self.player_black.mention, inline=True)

        if self.game_over:
            outcome = self.board.outcome()
            if outcome:
                if outcome.winner == chess.WHITE:
                    embed.description = f"🏆 **Checkmate! {self.player_white.mention} (Byed) rbe7!**"
                elif outcome.winner == chess.BLACK:
                    embed.description = f"🏆 **Checkmate! {self.player_black.mention} (Khel) rbe7!**"
                else:
                    embed.description = f"🤝 **Ta3adol! ({outcome.termination.name.replace('_', ' ').title()})**"
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
            embed = self.build_embed()
            embed.description = f"⏰ **Sala lwe9t! {self.current_turn.mention} khser b l inactivity. {winner.mention} rbe7!**"
            try:
                await self.message.edit(embed=embed, view=None)
            except:
                pass

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

        best_move = None
        best_value = -999999 if self.board.turn == chess.BLACK else 999999
        ordered = self._order_moves(self.board, legal_moves)

        # Increased depth for harder bot
        search_depth = 4
        
        for move in ordered:
            self.board.push(move)
            if self.board.turn == chess.WHITE:
                value = self._minimax(self.board, search_depth - 1, -999999, 999999, False)
                if value < best_value:
                    best_value = value
                    best_move = move
            else:
                value = self._minimax(self.board, search_depth - 1, -999999, 999999, True)
                if value > best_value:
                    best_value = value
                    best_move = move
            self.board.pop()

        if best_move:
            self.board.push(best_move)

    async def process_move_input(self, interaction: discord.Interaction, move_str: str):
        if self.game_over or not self.is_current_player(interaction.user):
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
            await interaction.response.send_message(f"❌ **l move ghalat (`{move_str}`)!** khdem b SAN (mtalan `e4`, `Nf3`) ola UCI (mtalan `e2e4`).", ephemeral=True)
            return

        # Push Human Move
        self.board.push(parsed_move)

        # Check win/draw
        if self.board.is_game_over():
            self.game_over = True
            self.stop()
            if self.board.is_checkmate() and not self.is_bot_game and self.cog and interaction.guild:
                outcome = self.board.outcome()
                if outcome and outcome.winner == chess.WHITE:
                    asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, self.player_white.id, "chess"))
                elif outcome and outcome.winner == chess.BLACK:
                    asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, self.player_black.id, "chess"))
            board_file = await self.generate_board_file()
            await interaction.response.edit_message(embed=self.build_embed(), attachments=[board_file], view=self)
            return

        # Switch Turn
        self.current_turn = self.player_black if self.current_turn == self.player_white else self.player_white

        # Bot Move (Single-Player)
        if self.is_bot_game and self.current_turn == self.player_black:
            self.make_bot_move()
            if self.board.is_game_over():
                self.game_over = True
                self.stop()
            else:
                self.current_turn = self.player_white

        board_file = await self.generate_board_file()
        await interaction.response.edit_message(embed=self.build_embed(), attachments=[board_file], view=self)

    @discord.ui.button(label="La3eb move", style=discord.ButtonStyle.primary, emoji="♟️")
    async def move_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_current_player(interaction.user):
            await interaction.response.send_message("Mashi nobtsek!", ephemeral=True)
            return
        await interaction.response.send_modal(MoveModal(self))

    @discord.ui.button(label="Ta3adol", style=discord.ButtonStyle.secondary, emoji="🤝")
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
            embed = self.build_embed()
            embed.description = "🤝 **Match sala b ta3adol btifa9!**"
            board_file = await self.generate_board_file()
            await interaction.response.edit_message(embed=embed, attachments=[board_file], view=None)
        else:
            await interaction.response.send_message("Deja drti l9tira7, tsnna lakhor ijawb.", ephemeral=True)

    @discord.ui.button(label="Steslem", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def resign_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user not in (self.player_white, self.player_black):
            await interaction.response.send_message("Nta mashi f had l match.", ephemeral=True)
            return

        self.game_over = True
        self.stop()
        winner = self.player_black if interaction.user == self.player_white else self.player_white
        if not self.is_bot_game and self.cog and interaction.guild:
            asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winner.id, "chess"))
        
        embed = self.build_embed()
        embed.description = f"🏳️ **{interaction.user.mention} steslem! {winner.mention} rbe7!**"
        board_file = await self.generate_board_file()
        await interaction.response.edit_message(embed=embed, attachments=[board_file], view=None)

class ChessChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun", bet: int = 0):
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

# ============ TIC-TAC-TOE UI CLASSES (Module Level) ============

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

    def __init__(self, player_x: Union[discord.Member, discord.User], player_o: Union[discord.Member, discord.User], is_bot_game: bool = False, turn_timeout: int = 60, cog: Optional["Fun"] = None, bet: int = 0):
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
                    return f"🤝 **Ta3adol!**\n💰 Kola wa7d rje3 lih {format_tad(d_split)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)."
                return "🤝 **Ta3adol!**"
            elif winner == "X":
                if self.bet > 0:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    return f"🏆 **{self.player_x.mention} (X) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)!"
                elif self.is_bot_game:
                    return f"🏆 **{self.player_x.mention} (X) rbe7!**\n🤖 Ghelbti bot AI o rbe7ti **80** {TAD_EMOJI} TAD!"
                return f"🏆 **{self.player_x.mention} (X) rbe7!**"
            elif winner == "O":
                if self.is_bot_game:
                    return "🤖 **Rb7tk!**"
                else:
                    if self.bet > 0:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        return f"🏆 **{self.player_o.mention} (O) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)!"
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
                else:
                    content = f"⏰ **{current_player.mention} sala lih lwe9t!** 🏆 **{winner.mention} ({winner_symbol}) rbe7!**"
                    if self.bet > 0 and self.cog:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        economy_cog = self.cog.bot.get_cog("Economy")
                        if economy_cog:
                            await economy_cog.add_balance(winner.id, w_payout, context="TTT Wager Win (Timeout)")
                        if self.message and self.message.guild:
                            await self.cog.record_minigame_win(self.message.guild.id, winner.id, "tictactoe", earnings=w_payout - self.bet)
                        content += f"\n💰 Rbe7ti {format_tad(w_payout)}!"
                    elif self.cog and self.message and self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, winner.id, "tictactoe")

                if self.message:
                    await self.message.edit(content=content, view=self)
                self.stop()
        except asyncio.CancelledError:
            pass

    def stop(self):
        if hasattr(self, '_timeout_task') and self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        super().stop()

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
                asyncio.create_task(economy_cog.add_balance(self.player_x.id, d_split, context="TTT Draw Split"))
                asyncio.create_task(economy_cog.add_balance(self.player_o.id, d_split, context="TTT Draw Split"))
            elif winner in ("X", "O"):
                winning_user = self.player_x if winner == "X" else self.player_o
                if self.bet > 0 and economy_cog:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    asyncio.create_task(economy_cog.add_balance(winning_user.id, w_payout, context="TTT Wager Win"))
                    if self.cog and interaction.guild:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "tictactoe", earnings=w_payout - self.bet))
                elif self.is_bot_game and winner == "X" and economy_cog:
                    asyncio.create_task(economy_cog.add_balance(self.player_x.id, 80, context="TTT Bot Win"))
                    if self.cog and interaction.guild:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, self.player_x.id, "tictactoe", earnings=80))
                elif not self.is_bot_game and self.cog and interaction.guild:
                    asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "tictactoe"))

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
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun", bet: int = 0):
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
    def __init__(self, player_red: Union[discord.Member, discord.User], player_yellow: Union[discord.Member, discord.User], is_bot_game: bool = False, turn_timeout: int = 60, cog: Optional["Fun"] = None, bet: int = 0):
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
                    return f"{board_text}\n\n🤝 **Ta3adol!**\n💰 Kola wa7d rje3 lih {format_tad(d_split)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)."
                return f"{board_text}\n\n🤝 **Ta3adol!**"
            elif winner == "🔴":
                if self.bet > 0:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)!"
                elif self.is_bot_game:
                    return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**\n🤖 Ghelbti bot AI o rbe7ti **100** {TAD_EMOJI} TAD!"
                return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**"
            elif winner == "🟡":
                if self.is_bot_game:
                    return f"{board_text}\n\n🤖 **Rb7tk!**"
                else:
                    if self.bet > 0:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        return f"{board_text}\n\n🏆 **{self.player_yellow.mention} (🟡) rbe7!**\n💰 Rbe7ti {format_tad(w_payout)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)!"
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
                else:
                    content = f"{self.render_board()}\n\n⏰ **{current_player.mention} sala lih lwe9t!** 🏆 **{winner.mention} ({winner_symbol}) rbe7!**"
                    if self.bet > 0 and self.cog:
                        w_payout, burned, _ = calculate_pvp_payout(self.bet)
                        economy_cog = self.cog.bot.get_cog("Economy")
                        if economy_cog:
                            await economy_cog.add_balance(winner.id, w_payout, context="ConnectFour Win (Timeout)")
                        if self.message and self.message.guild:
                            await self.cog.record_minigame_win(self.message.guild.id, winner.id, "connectfour", earnings=w_payout - self.bet)
                        content += f"\n💰 Rbe7ti {format_tad(w_payout)}!"
                    elif self.cog and self.message and self.message.guild:
                        await self.cog.record_minigame_win(self.message.guild.id, winner.id, "connectfour")

                if self.message:
                    await self.message.edit(content=content, view=self)
                self.stop()
        except asyncio.CancelledError:
            pass

    def stop(self):
        if hasattr(self, '_timeout_task') and self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        super().stop()

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
                asyncio.create_task(economy_cog.add_balance(self.player_red.id, d_split, context="ConnectFour Draw Split"))
                asyncio.create_task(economy_cog.add_balance(self.player_yellow.id, d_split, context="ConnectFour Draw Split"))
            elif winner in ("🔴", "🟡"):
                winning_user = self.player_red if winner == "🔴" else self.player_yellow
                if self.bet > 0 and economy_cog:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    asyncio.create_task(economy_cog.add_balance(winning_user.id, w_payout, context="ConnectFour Wager Win"))
                    if self.cog and interaction.guild:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "connectfour", earnings=w_payout - self.bet))
                elif self.is_bot_game and winner == "🔴" and economy_cog:
                    asyncio.create_task(economy_cog.add_balance(self.player_red.id, 100, context="ConnectFour Bot Win"))
                    if self.cog and interaction.guild:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, self.player_red.id, "connectfour", earnings=100))
                elif not self.is_bot_game and self.cog and interaction.guild:
                    asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "connectfour"))

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
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun", bet: int = 0):
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
class AkinatorButton(Button):
    def __init__(self, label: str, custom_id: str, style: discord.ButtonStyle, emoji: str, row: int):
        super().__init__(label=label, custom_id=custom_id, style=style, emoji=emoji, row=row)


class AkinatorView(View):
    def __init__(self, player: Union[discord.Member, discord.User], timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.player = player
        self.aki = AsyncAkinator()
        self.message: Optional[discord.Message] = None
        self.game_over = False
        self.guessing = False

        # Row 0: Primary Answers
        self.add_item(AkinatorButton("Yes", "aki_y", discord.ButtonStyle.success, "✅", 0))
        self.add_item(AkinatorButton("No", "aki_n", discord.ButtonStyle.danger, "❌", 0))
        self.add_item(AkinatorButton("I don't know", "aki_idk", discord.ButtonStyle.secondary, "❓", 0))

        # Row 1: Secondary Answers
        self.add_item(AkinatorButton("Probably", "aki_p", discord.ButtonStyle.primary, "👍", 1))
        self.add_item(AkinatorButton("Probably Not", "aki_pn", discord.ButtonStyle.primary, "👎", 1))

        # Row 2: Controls
        self.add_item(AkinatorButton("Back", "aki_b", discord.ButtonStyle.secondary, "⬅️", 2))
        self.add_item(AkinatorButton("Stop", "aki_s", discord.ButtonStyle.danger, "🛑", 2))

    async def start_game(self) -> discord.Embed:
        """Starts the Akinator session asynchronously."""
        await self.aki.start_game()
        return self.build_question_embed(self.aki.question)

    def build_question_embed(self, question: str) -> discord.Embed:
        embed = discord.Embed(
            title="🔮 Akinator",
            description=f"**{question}**",
            color=0x000000
        )
        embed.set_footer(
            text=f"Player: {self.player.display_name} • Step {self.aki.step + 1} ({int(self.aki.progression)}%)"
        )
        return embed

    def disable_all_buttons(self):
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            self.disable_all_buttons()
            if self.message:
                try:
                    embed = self.message.embeds[0]
                    embed.description = "⏰ **Match sala bsbab l inactivity!**"
                    await self.message.edit(embed=embed, view=self)
                except (discord.NotFound, discord.HTTPException):
                    pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr.", ephemeral=True)
            return False
        return True

    async def button_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")

        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat..", ephemeral=True)
            return

        await interaction.response.defer()

        # Stop Game
        if custom_id == "aki_s":
            self.game_over = True
            self.disable_all_buttons()
            self.stop()
            embed = discord.Embed(description="🛑 **Lgame 7bsat!**", color=0x000000)
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            return

        # Undo Move
        if custom_id == "aki_b":
            try:
                await self.aki.back()
                embed = self.build_question_embed(self.aki.question)
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            except Exception:
                await interaction.followup.send("Man9drch nrje3..", ephemeral=True)
            return

        # Shortcode Mapping for akinator.py
        # Supported answers for akinator.py: "yes", "no", "i don't know", "probably", "probably not"
        answer_map = {
            "aki_y": "yes",
            "aki_n": "no",
            "aki_idk": "i don't know",
            "aki_p": "probably",
            "aki_pn": "probably not"
        }

        ans = answer_map.get(custom_id)
        if not ans:
            return

        # Handle Guess Confirmation Phase
        if self.guessing:
            if ans in ("y", "p"):
                self.game_over = True
                self.disable_all_buttons()
                self.stop()
                embed = discord.Embed(
                    title="🎉 Rbe7t!",
                    description=f"**{self.aki.first_guess['name']}**\n{self.aki.first_guess.get('description', '')}",
                    color=0x000000
                )
                if self.aki.first_guess.get('absolute_picture_path'):
                    embed.set_image(url=self.aki.first_guess['absolute_picture_path'])
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            else:
                self.guessing = False
                self.clear_items()
                self.add_item(AkinatorButton("Yes", "aki_y", discord.ButtonStyle.success, "✅", 0))
                self.add_item(AkinatorButton("No", "aki_n", discord.ButtonStyle.danger, "❌", 0))
                self.add_item(AkinatorButton("I don't know", "aki_idk", discord.ButtonStyle.secondary, "❓", 0))
                self.add_item(AkinatorButton("Probably", "aki_p", discord.ButtonStyle.primary, "👍", 1))
                self.add_item(AkinatorButton("Probably Not", "aki_pn", discord.ButtonStyle.primary, "👎", 1))
                self.add_item(AkinatorButton("Back", "aki_b", discord.ButtonStyle.secondary, "⬅️", 2))
                self.add_item(AkinatorButton("Stop", "aki_s", discord.ButtonStyle.danger, "🛑", 2))

                for child in self.children:
                    if isinstance(child, Button):
                        child.callback = self.button_callback

                try:
                    await self.aki.answer("no")
                    embed = self.build_question_embed(self.aki.question)
                    await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
                except Exception as e:
                    print(f"[Akinator Rejection Error]: {e}")
                    await interaction.followup.send("❌ Ma9ditch nregistery ljawab ta3k, 3awd jrb.", ephemeral=True)
            return

        # Turn Processing with Retries & Terminal Logging
        for attempt in range(3):
            try:
                await self.aki.answer(ans)
                break
            except Exception as e:
                print(f"[Akinator Turn Error - Attempt {attempt + 1}]: {e}")
                # We catch the error but don't retry - the library itself is having an API issue
                await asyncio.sleep(1.5)

        # Win Check Logic
        if self.aki.progression >= 80 or self.aki.step >= 79:
            try:
                await self.aki.win()
                guess = self.aki.first_guess
                if guess:
                    self.guessing = True
                    embed = discord.Embed(
                        title="🤔 Wach hada howa l personnage ta3k?",
                        description=f"**{guess['name']}**\n*{guess.get('description', '')}*",
                        color=0x000000
                    )
                    if guess.get('absolute_picture_path'):
                        embed.set_image(url=guess['absolute_picture_path'])

                    self.clear_items()
                    self.add_item(AkinatorButton("Yes", "aki_y", discord.ButtonStyle.success, "✅", 0))
                    self.add_item(AkinatorButton("No", "aki_n", discord.ButtonStyle.danger, "❌", 0))

                    for child in self.children:
                        if isinstance(child, Button):
                            child.callback = self.button_callback

                    await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
                    return
            except Exception as e:
                print(f"[akipy Win Check Error]: {e}")

        embed = self.build_question_embed(self.aki.question)
        await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)

# ============ ROCK PAPER SCISSORS UI CLASSES (Module Level) ============

class RPSBotView(View):
    def __init__(self, player: discord.Member):
        super().__init__(timeout=60)
        self.player = player
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
        self.stop()
        for item in self.children:
            item.disabled = True

        counter_map = {
            "rock": "paper",
            "paper": "scissors",
            "scissors": "rock"
        }
        bot_choice = counter_map.get(player_choice, "rock")
        
        emoji_map = {
            "rock": "🪨 Rock",
            "paper": "📄 Paper",
            "scissors": "✂️ Scissors"
        }

        if player_choice == bot_choice:
            title = "🤝 Ta3adol!"
            outcome = f"Nta khtarti **{emoji_map[player_choice]}** o ana khtart **{emoji_map[bot_choice]}**."
        elif (player_choice == "rock" and bot_choice == "scissors") or \
             (player_choice == "paper" and bot_choice == "rock") or \
             (player_choice == "scissors" and bot_choice == "paper"):
            title = "🎉 Rbe7ti!"
            outcome = f"Nta khtarti **{emoji_map[player_choice]}** o ana khtart **{emoji_map[bot_choice]}**."
        else:
            title = "🤖 Rb7tk!"
            outcome = f"Nta khtarti **{emoji_map[player_choice]}** o ana khtart **{emoji_map[bot_choice]}**."

        embed = discord.Embed(
            title=title,
            description=outcome,
            color=0x000000
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                embed = discord.Embed(
                    title="⏰ Sala lwe9t",
                    description="Sala lwe9t o ma khtartich.",
                    color=0x000000
                )
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass


class RPSMultiplayerView(View):
    def __init__(self, player1: discord.Member, player2: discord.Member, cog: Optional["Fun"] = None, bet: int = 0):
        super().__init__(timeout=60)
        self.player1 = player1
        self.player2 = player2
        self.cog = cog
        self.bet = bet
        self.choices = {player1.id: None, player2.id: None}
        self.message: Optional[discord.Message] = None

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
                        asyncio.create_task(economy_cog.add_balance(self.player1.id, d_split, context="RPS Draw Split"))
                        asyncio.create_task(economy_cog.add_balance(self.player2.id, d_split, context="RPS Draw Split"))
                    outcome += f"\n\n💰 Kola wa7d rje3 lih {format_tad(d_split)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)."
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
                if self.bet > 0:
                    w_payout, burned, _ = calculate_pvp_payout(self.bet)
                    economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                    if economy_cog:
                        asyncio.create_task(economy_cog.add_balance(winning_user.id, w_payout, context="RPS Wager Win"))
                    if self.cog and interaction.guild:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "rockpaperscissors", earnings=w_payout - self.bet))
                    outcome += f"\n\n💰 **{winning_user.mention}** rbe7 {format_tad(w_payout)} (🔥 `{burned:,}` {TAD_EMOJI} 5% tax burned)!"
                elif self.cog and interaction.guild:
                    asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winning_user.id, "rockpaperscissors"))

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
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun", bet: int = 0):
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
    def __init__(self, player: discord.Member):
        super().__init__(timeout=180)
        self.player = player
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

            content = f"💥 **Booooom! Game Over**\n{self.player.mention} khser hit 9as mine f ({x+1}, {y+1})!"
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

            content = f"🎉🏆 **Rbe7ti!**\n{self.player.mention} l9iti grid kaml blama t9is 7ta mine!"
            await interaction.response.edit_message(content=content, view=self)
            return

        content = f"💣 **Minesweeper (Solo)** — Hreb mn l mines o l9a safe squares kamlin!\nSafe: **{len(self.revealed)}/{total_safe}**"
        await interaction.response.edit_message(content=content, view=self)

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ **Sala lwe9t!** Match sala bsbab inactivity.", view=self)
                except Exception:
                    pass


class MinesweeperMultiplayerView(View):
    def __init__(self, p1: discord.Member, p2: discord.Member, cog: Optional["Fun"] = None):
        super().__init__(timeout=180)
        self.p1 = p1
        self.p2 = p2
        self.cog = cog
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

    def get_content(self) -> str:
        if self.game_over:
            p1_score = self.scores[self.p1.id]
            p2_score = self.scores[self.p2.id]
            if p1_score > p2_score:
                return f"🏆 **{self.p1.mention} rbe7!**\nNatija: 🔴 **{self.p1.display_name}** ({p1_score}) vs 🔵 **{self.p2.display_name}** ({p2_score})"
            elif p2_score > p1_score:
                return f"🏆 **{self.p2.mention} rbe7!**\nNatija: 🔵 **{self.p2.display_name}** ({p2_score}) vs 🔴 **{self.p1.display_name}** ({p1_score})"
            else:
                return f"🤝 **Ta3adol!**\nNatija: **{p1_score}-{p2_score}**"
        else:
            return (
                f"💣 **Minesweeper (Hunt the Mines)** — 9leb 3la l mines bach tjib points!\n"
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

        if self.cog and interaction.guild:
            asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, winner.id, "minesweeper"))

        for item in self.children:
            item.disabled = True
            if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines and not item.disabled:
                item.label = "💣"
                item.style = discord.ButtonStyle.secondary

        content = (
            f"🚪 **{quitter.mention}** khrej mn lgame (Forfeit).\n"
            f"🏆 **{winner.mention}** rbe7 lmatch!"
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
                if self.cog and interaction.guild:
                    if p1_score > p2_score:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, self.p1.id, "minesweeper"))
                    elif p2_score > p1_score:
                        asyncio.create_task(self.cog.record_minigame_win(interaction.guild.id, self.p2.id, "minesweeper"))
                # Disable all other buttons and show remaining mines
                for item in self.children:
                    item.disabled = True
                    if isinstance(item, MinesweeperButton) and (item.x, item.y) in self.mines and not item.disabled:
                        item.label = "💣"
                        item.style = discord.ButtonStyle.secondary
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

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ **Sala lwe9t!** Match sala bsbab inactivity.", view=self)
                except Exception:
                    pass


class MinesweeperChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun"):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.accepted = True
        self.stop()

        game_view = MinesweeperMultiplayerView(self.challenger, self.challenged, cog=self.cog)
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


# ============ WORDLE HELPERS & UI CLASSES ============

def evaluate_wordle_guess(guess: str, secret: str) -> list[tuple[str, str]]:
    guess = guess.lower()
    secret = secret.lower()
    res = ["⬛"] * 5
    secret_counts = {}
    for i in range(5):
        if guess[i] == secret[i]:
            res[i] = "🟩"
        else:
            secret_counts[secret[i]] = secret_counts.get(secret[i], 0) + 1

    for i in range(5):
        if res[i] == "🟩":
            continue
        g_char = guess[i]
        if secret_counts.get(g_char, 0) > 0:
            res[i] = "🟨"
            secret_counts[g_char] -= 1
        else:
            res[i] = "⬛"

    return [(guess[i].upper(), res[i]) for i in range(5)]


class WordleSoloModal(Modal, title="Wordle — Guess"):
    guess_input = TextInput(
        label="5-Letter Word",
        placeholder="e.g. CRANE, PLANES...",
        min_length=5,
        max_length=5,
        required=True
    )

    def __init__(self, view: "WordleSoloView"):
        super().__init__()
        self.game_view = view

    async def on_submit(self, interaction: discord.Interaction):
        word = self.guess_input.value.strip().lower()
        if len(word) != 5 or not word.isalpha():
            await interaction.response.send_message("❌ Khes lkelma tkoun fiha 5 d l7orof alphabetic.", ephemeral=True)
            return

        if not self.game_view.cog.is_english_word(word):
            await interaction.response.send_message("❌ Had lkelma ma kaynach f dictionary.", ephemeral=True)
            return

        await self.game_view.process_guess(interaction, word)


class WordleSoloView(View):
    def __init__(self, player: discord.Member, secret: str, cog: "Fun"):
        super().__init__(timeout=300)
        self.player = player
        self.secret = secret.lower()
        self.cog = cog
        self.guesses: list[str] = []
        self.game_over = False
        self.message: Optional[discord.Message] = None

    def get_content(self) -> str:
        lines = [
            "🟩 **Wordle (Solo)** — L9a lkelma dial 5 d l7orof!",
            f"Attempts: **{len(self.guesses)}/6**\n"
        ]

        for g in self.guesses:
            eval_res = evaluate_wordle_guess(g, self.secret)
            pattern = " ".join(e[1] for e in eval_res)
            letters = " ".join(f"**{e[0]}**" for e in eval_res)
            lines.append(f"{pattern}  |  {letters}")

        for _ in range(6 - len(self.guesses)):
            lines.append("⬛ ⬛ ⬛ ⬛ ⬛  |  - - - - -")

        if self.game_over:
            if self.guesses and self.guesses[-1] == self.secret:
                lines.append(f"\n🎉🏆 **Rbe7ti!** L9iti lkelma f **{len(self.guesses)}/6** attempts!\nLkelma kant: **{self.secret.upper()}**")
            else:
                lines.append(f"\n💥 **Game Over!** Salat attempts dialk.\nLkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Type Word", style=discord.ButtonStyle.primary, emoji="⌨️")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return
        await interaction.response.send_modal(WordleSoloModal(self))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True
        content = self.get_content() + f"\n\n🚪 {self.player.mention} khrej mn lgame."
        await interaction.response.edit_message(content=content, view=self)

    async def process_guess(self, interaction: discord.Interaction, word: str):
        self.guesses.append(word)
        if word == self.secret or len(self.guesses) >= 6:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True

        await interaction.response.edit_message(content=self.get_content(), view=self)

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content=self.get_content() + "\n\n⏰ **Sala lwe9t!** Match sala bsbab inactivity.", view=self)
                except Exception:
                    pass


# Multiplayer 1v1 Classes

class WordleMultiplayerModal(Modal, title="Wordle 1v1 — Guess"):
    guess_input = TextInput(
        label="5-Letter Word",
        placeholder="Enter your 5-letter guess...",
        min_length=5,
        max_length=5,
        required=True
    )

    def __init__(self, match: "WordleMultiplayerMatch", player: discord.Member):
        super().__init__()
        self.match = match
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        word = self.guess_input.value.strip().lower()
        if len(word) != 5 or not word.isalpha():
            await interaction.response.send_message("❌ Khes lkelma tkoun fiha 5 d l7orof alphabetic.", ephemeral=True)
            return

        if not self.match.cog.is_english_word(word):
            await interaction.response.send_message("❌ Had lkelma ma kaynach f dictionary.", ephemeral=True)
            return

        await self.match.process_player_guess(interaction, self.player, word)


class WordleDMView(View):
    def __init__(self, match: "WordleMultiplayerMatch", player: discord.Member):
        super().__init__(timeout=300)
        self.match = match
        self.player = player

    @discord.ui.button(label="Type Word", style=discord.ButtonStyle.primary, emoji="⌨️")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.match.game_over or self.match.finished.get(self.player.id, False):
            await interaction.response.send_message("Saliti attempts dialk wla lmatch deja sala.", ephemeral=True)
            return
        await interaction.response.send_modal(WordleMultiplayerModal(self.match, self.player))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        await self.match.player_quit(interaction, self.player)


class WordleMultiplayerMatch:
    def __init__(self, p1: discord.Member, p2: discord.Member, channel_msg: discord.Message, secret: str, cog: "Fun"):
        self.p1 = p1
        self.p2 = p2
        self.channel_msg = channel_msg
        self.secret = secret.lower()
        self.cog = cog
        
        self.guesses = {p1.id: [], p2.id: []}
        self.finished = {p1.id: False, p2.id: False}
        self.won = {p1.id: False, p2.id: False}
        self.quit = {p1.id: False, p2.id: False}
        self.dm_messages: dict[int, discord.Message] = {}
        self.dm_views: dict[int, WordleDMView] = {}
        self.game_over = False

    def get_player_dm_content(self, player: discord.Member) -> str:
        opponent = self.p2 if player == self.p1 else self.p1
        p_guesses = self.guesses[player.id]
        lines = [
            f"🟩 **Wordle 1v1 Match** vs **{opponent.display_name}**",
            f"Attempts: **{len(p_guesses)}/6**\n"
        ]

        for g in p_guesses:
            eval_res = evaluate_wordle_guess(g, self.secret)
            pattern = " ".join(e[1] for e in eval_res)
            letters = " ".join(f"**{e[0]}**" for e in eval_res)
            lines.append(f"{pattern}  |  {letters}")

        for _ in range(6 - len(p_guesses)):
            lines.append("⬛ ⬛ ⬛ ⬛ ⬛  |  - - - - -")

        if self.quit[player.id]:
            lines.append("\n🚪 **Khrejti mn lgame.**")
        elif self.finished[player.id]:
            if self.won[player.id]:
                lines.append(f"\n🎉 L9iti lkelma f **{len(p_guesses)}/6**! Kattsna opponent isali.")
            else:
                lines.append("\n💥 Saliti attempts (6/6). Kattsna opponent isali.")

        if self.quit[opponent.id] and not self.game_over:
            lines.append(f"\nℹ️ **{opponent.display_name} khrej mn lmatch**, t9der attempts dialk!")

        if self.game_over:
            lines.append(f"\n🏁 **Match sala!** Lkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    def get_spectator_content(self) -> str:
        p1_guesses = self.guesses[self.p1.id]
        p2_guesses = self.guesses[self.p2.id]

        if not self.game_over:
            # Spoiler Protected View (Only squares, no letters!)
            lines = [
                "🟩 **Wordle 1v1 Match (Live Spectator)**",
                f"⚔️ **{self.p1.display_name}** vs **{self.p2.display_name}**\n"
            ]

            p1_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p1.id] else ""
            lines.append(f"🔴 **{self.p1.display_name}** ({len(p1_guesses)}/6){p1_status}:")
            for g in p1_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                lines.append("".join(e[1] for e in eval_res))
            for _ in range(6 - len(p1_guesses)):
                lines.append("⬛⬛⬛⬛⬛")

            p2_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p2.id] else ""
            lines.append(f"\n🔵 **{self.p2.display_name}** ({len(p2_guesses)}/6){p2_status}:")
            for g in p2_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                lines.append("".join(e[1] for e in eval_res))
            for _ in range(6 - len(p2_guesses)):
                lines.append("⬛⬛⬛⬛⬛")

            return "\n".join(lines)
        else:
            # Full Reveal with letters and tiles
            lines = ["🏁 **Wordle 1v1 Match — Final Results**"]
            
            p1_won = self.won[self.p1.id]
            p2_won = self.won[self.p2.id]
            p1_quit = self.quit[self.p1.id]
            p2_quit = self.quit[self.p2.id]
            p1_count = len(p1_guesses)
            p2_count = len(p2_guesses)

            if p1_quit and p2_quit:
                winner_text = "🚪 **Ta wa7d ma rbe7 (bjoj khrejo mn lmatch).**"
            elif p1_quit:
                winner_text = f"🏆 **{self.p2.mention} rbe7!** ({self.p1.display_name} khrej mn lmatch)"
            elif p2_quit:
                winner_text = f"🏆 **{self.p1.mention} rbe7!** ({self.p2.display_name} khrej mn lmatch)"
            elif p1_won and not p2_won:
                winner_text = f"🏆 **{self.p1.mention} rbe7!**"
            elif p2_won and not p1_won:
                winner_text = f"🏆 **{self.p2.mention} rbe7!**"
            elif p1_won and p2_won:
                if p1_count < p2_count:
                    winner_text = f"🏆 **{self.p1.mention} rbe7** (f {p1_count} attempts vs {p2_count})!"
                elif p2_count < p1_count:
                    winner_text = f"🏆 **{self.p2.mention} rbe7** (f {p2_count} attempts vs {p1_count})!"
                else:
                    winner_text = f"🤝 **Ta3adol!** Bjojkom l9itoha f **{p1_count} attempts**!"
            else:
                winner_text = "🤝 **Ta3adol!** Ta wa7d ma l9a lkelma."

            lines.append(f"{winner_text}\nLkelma kant: **{self.secret.upper()}**\n")

            # Reveal P1
            lines.append(f"🔴 **{self.p1.display_name}** ({p1_count}/6)" + (" (🚪 Khrej)" if p1_quit else "") + ":")
            for g in p1_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                pattern = " ".join(e[1] for e in eval_res)
                letters = " ".join(f"**{e[0]}**" for e in eval_res)
                lines.append(f"{pattern}  |  {letters}")

            # Reveal P2
            lines.append(f"\n🔵 **{self.p2.display_name}** ({p2_count}/6)" + (" (🚪 Khrej)" if p2_quit else "") + ":")
            for g in p2_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                pattern = " ".join(e[1] for e in eval_res)
                letters = " ".join(f"**{e[0]}**" for e in eval_res)
                lines.append(f"{pattern}  |  {letters}")

            return "\n".join(lines)

    async def process_player_guess(self, interaction: discord.Interaction, player: discord.Member, word: str):
        if self.game_over or self.finished[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla saliti attempts dialk.", ephemeral=True)
            return

        self.guesses[player.id].append(word)
        if word == self.secret:
            self.won[player.id] = True
            self.finished[player.id] = True
        elif len(self.guesses[player.id]) >= 6:
            self.finished[player.id] = True

        p1_guesses_len = len(self.guesses[self.p1.id])
        p2_guesses_len = len(self.guesses[self.p2.id])
        p1_won = self.won[self.p1.id]
        p2_won = self.won[self.p2.id]
        p1_quit = self.quit[self.p1.id]
        p2_quit = self.quit[self.p2.id]

        if (self.finished[self.p1.id] or p1_quit) and (self.finished[self.p2.id] or p2_quit):
            self.game_over = True
        elif p1_won and (p2_guesses_len > p1_guesses_len or self.finished[self.p2.id] or p2_quit):
            self.game_over = True
        elif p2_won and (p1_guesses_len > p2_guesses_len or self.finished[self.p1.id] or p1_quit):
            self.game_over = True

        view = self.dm_views.get(player.id)
        if self.finished[player.id] and view:
            for item in view.children:
                if isinstance(item, Button) and item.label == "Type Word":
                    item.disabled = True
        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        try:
            await self.channel_msg.edit(content=self.get_spectator_content())
        except Exception as e:
            print(f"[spectator update error]: {e}")

        if self.game_over:
            if self.cog and self.channel_msg and self.channel_msg.guild:
                winner = None
                if p1_quit and not p2_quit:
                    winner = self.p2
                elif p2_quit and not p1_quit:
                    winner = self.p1
                elif p1_won and not p2_won:
                    winner = self.p1
                elif p2_won and not p1_won:
                    winner = self.p2
                elif p1_won and p2_won:
                    if p1_guesses_len < p2_guesses_len:
                        winner = self.p1
                    elif p2_guesses_len < p1_guesses_len:
                        winner = self.p2
                if winner:
                    asyncio.create_task(self.cog.record_minigame_win(self.channel_msg.guild.id, winner.id, "wordle"))

            for p in (self.p1, self.p2):
                dm_msg = self.dm_messages.get(p.id)
                dm_v = self.dm_views.get(p.id)
                if dm_msg and dm_v:
                    for item in dm_v.children:
                        item.disabled = True
                    try:
                        await dm_msg.edit(content=self.get_player_dm_content(p), view=dm_v)
                    except Exception:
                        pass

    async def player_quit(self, interaction: discord.Interaction, player: discord.Member):
        if self.game_over or self.quit[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla khrejti deja.", ephemeral=True)
            return

        self.quit[player.id] = True
        self.finished[player.id] = True

        opponent = self.p2 if player == self.p1 else self.p1

        if self.finished[opponent.id] or self.quit[opponent.id]:
            self.game_over = True

        view = self.dm_views.get(player.id)
        if view:
            for item in view.children:
                item.disabled = True

        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        try:
            await self.channel_msg.edit(content=self.get_spectator_content())
        except Exception as e:
            print(f"[spectator update on quit error]: {e}")

        opp_msg = self.dm_messages.get(opponent.id)
        opp_v = self.dm_views.get(opponent.id)
        if opp_msg and opp_v:
            if self.game_over:
                for item in opp_v.children:
                    item.disabled = True
            try:
                await opp_msg.edit(content=self.get_player_dm_content(opponent), view=opp_v)
            except Exception:
                pass

        if self.game_over and self.cog and self.channel_msg and self.channel_msg.guild:
            if self.quit[self.p1.id] and not self.quit[self.p2.id]:
                asyncio.create_task(self.cog.record_minigame_win(self.channel_msg.guild.id, self.p2.id, "wordle"))
            elif self.quit[self.p2.id] and not self.quit[self.p1.id]:
                asyncio.create_task(self.cog.record_minigame_win(self.channel_msg.guild.id, self.p1.id, "wordle"))


class WordleChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun"):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.accepted = True
        self.stop()

        secret = self.cog.get_wordle_secret()
        match = WordleMultiplayerMatch(self.challenger, self.challenged, interaction.message, secret, self.cog)

        try:
            p1_view = WordleDMView(match, self.challenger)
            p1_msg = await self.challenger.send(content=match.get_player_dm_content(self.challenger), view=p1_view)
            match.dm_messages[self.challenger.id] = p1_msg
            match.dm_views[self.challenger.id] = p1_view
        except discord.Forbidden:
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenger.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        try:
            p2_view = WordleDMView(match, self.challenged)
            p2_msg = await self.challenged.send(content=match.get_player_dm_content(self.challenged), view=p2_view)
            match.dm_messages[self.challenged.id] = p2_msg
            match.dm_views[self.challenged.id] = p2_view
        except discord.Forbidden:
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenged.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        await interaction.response.edit_message(content=match.get_spectator_content(), view=None)

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


# ============ HANGMAN HELPERS & UI CLASSES ============

HANGMAN_STAGES = [
    """
  +---+
  |   |
      |
      |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
      |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
  |   |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|   |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|\\  |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|\\  |
 /    |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|\\  |
 / \\  |
      |
========="""
]


class HangmanSoloModal(Modal, title="Hangman — Guess"):
    guess_input = TextInput(
        label="Letter or Full Word",
        placeholder="e.g. E, A, or PLANET...",
        min_length=1,
        max_length=20,
        required=True
    )

    def __init__(self, view: "HangmanSoloView"):
        super().__init__()
        self.game_view = view

    async def on_submit(self, interaction: discord.Interaction):
        guess_str = self.guess_input.value.strip().lower()
        if not guess_str.isalpha():
            await interaction.response.send_message("❌ Dkhel 7arf wla kelma s7i7a.", ephemeral=True)
            return

        await self.game_view.process_guess(interaction, guess_str)


class HangmanSoloView(View):
    def __init__(self, player: discord.Member, secret: str, cog: "Fun"):
        super().__init__(timeout=300)
        self.player = player
        self.secret = secret.lower()
        self.cog = cog
        self.guessed_letters: set[str] = set()
        self.wrong_guesses: list[str] = []
        self.game_over = False
        self.won = False
        self.message: Optional[discord.Message] = None

    def get_content(self) -> str:
        mistakes = len(self.wrong_guesses)
        stage_ascii = HANGMAN_STAGES[min(mistakes, 6)]
        lives = max(0, 6 - mistakes)

        masked = " ".join(ch.upper() if ch in self.guessed_letters else "\\_" for ch in self.secret)
        wrong_str = ", ".join(w.upper() for w in self.wrong_guesses) if self.wrong_guesses else "None"

        lines = [
            "🪢 **Hangman (Solo)** — L9a lkelma 9bel ma tchn9!",
            f"Lives: **{lives}/6** ❤️ | Length: **{len(self.secret)} letters**",
            f"```{stage_ascii}```",
            f"Word: `{masked}`",
            f"Wrong guesses: **{wrong_str}**"
        ]

        if self.game_over:
            if self.won:
                lines.append(f"\n🎉🏆 **Rbe7ti!** L9iti lkelma 9bel ma tchn9!\nLkelma kant: **{self.secret.upper()}**")
            else:
                lines.append(f"\n💀 **Game Over!** Tchn9ti!\nLkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="🔤")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return
        await interaction.response.send_modal(HangmanSoloModal(self))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True
        content = self.get_content() + f"\n\n🚪 {self.player.mention} khrej mn lgame."
        await interaction.response.edit_message(content=content, view=self)

    async def process_guess(self, interaction: discord.Interaction, guess: str):
        if len(guess) == 1:
            if guess in self.guessed_letters or guess in self.wrong_guesses:
                await interaction.response.send_message("⚠️ Deja guessiti had l7arf.", ephemeral=True)
                return
            if guess in self.secret:
                self.guessed_letters.add(guess)
                if all(ch in self.guessed_letters for ch in self.secret):
                    self.game_over = True
                    self.won = True
            else:
                self.wrong_guesses.append(guess)
                if len(self.wrong_guesses) >= 6:
                    self.game_over = True
        else:
            if guess == self.secret:
                for ch in self.secret:
                    self.guessed_letters.add(ch)
                self.game_over = True
                self.won = True
            else:
                if guess not in self.wrong_guesses:
                    self.wrong_guesses.append(guess)
                if len(self.wrong_guesses) >= 6:
                    self.game_over = True

        if self.game_over:
            self.stop()
            for item in self.children:
                item.disabled = True

        await interaction.response.edit_message(content=self.get_content(), view=self)

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content=self.get_content() + "\n\n⏰ **Sala lwe9t!** Match sala bsbab inactivity.", view=self)
                except Exception:
                    pass


# Multiplayer 1v1 Classes

class HangmanMultiplayerModal(Modal, title="Hangman 1v1 — Guess"):
    guess_input = TextInput(
        label="Letter or Full Word",
        placeholder="e.g. E, A, or PLANET...",
        min_length=1,
        max_length=20,
        required=True
    )

    def __init__(self, match: "HangmanMultiplayerMatch", player: discord.Member):
        super().__init__()
        self.match = match
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        guess_str = self.guess_input.value.strip().lower()
        if not guess_str.isalpha():
            await interaction.response.send_message("❌ Dkhel 7arf wla kelma s7i7a.", ephemeral=True)
            return

        await self.match.process_player_guess(interaction, self.player, guess_str)


class HangmanDMView(View):
    def __init__(self, match: "HangmanMultiplayerMatch", player: discord.Member):
        super().__init__(timeout=300)
        self.match = match
        self.player = player

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="🔤")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.match.game_over or self.match.finished.get(self.player.id, False):
            await interaction.response.send_message("Saliti attempts dialk wla lmatch deja sala.", ephemeral=True)
            return
        await interaction.response.send_modal(HangmanMultiplayerModal(self.match, self.player))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        await self.match.player_quit(interaction, self.player)


class HangmanMultiplayerMatch:
    def __init__(self, p1: discord.Member, p2: discord.Member, channel_msg: discord.Message, secret: str, cog: "Fun"):
        self.p1 = p1
        self.p2 = p2
        self.channel_msg = channel_msg
        self.secret = secret.lower()
        self.cog = cog

        self.guessed_letters = {p1.id: set(), p2.id: set()}
        self.wrong_guesses = {p1.id: [], p2.id: []}
        self.finished = {p1.id: False, p2.id: False}
        self.won = {p1.id: False, p2.id: False}
        self.quit = {p1.id: False, p2.id: False}
        self.dm_messages: dict[int, discord.Message] = {}
        self.dm_views: dict[int, HangmanDMView] = {}
        self.game_over = False

    def get_player_dm_content(self, player: discord.Member) -> str:
        opponent = self.p2 if player == self.p1 else self.p1
        p_guessed = self.guessed_letters[player.id]
        p_wrong = self.wrong_guesses[player.id]
        mistakes = len(p_wrong)
        stage_ascii = HANGMAN_STAGES[min(mistakes, 6)]
        lives = max(0, 6 - mistakes)

        masked = " ".join(ch.upper() if ch in p_guessed else "\\_" for ch in self.secret)
        wrong_str = ", ".join(w.upper() for w in p_wrong) if p_wrong else "None"

        lines = [
            f"🪢 **Hangman 1v1 Match** vs **{opponent.display_name}**",
            f"Lives: **{lives}/6** ❤️ | Length: **{len(self.secret)} letters**",
            f"```{stage_ascii}```",
            f"Word: `{masked}`",
            f"Wrong guesses: **{wrong_str}**"
        ]

        if self.quit[player.id]:
            lines.append("\n🚪 **Khrejti mn lgame.**")
        elif self.finished[player.id]:
            if self.won[player.id]:
                lines.append(f"\n🎉 **L9iti lkelma!** ({mistakes} wrong guesses). Tsna l opponent isali.")
            else:
                lines.append("\n💀 **Tchn9ti!** (6/6 mistakes). Tsna l opponent isali.")

        if self.quit[opponent.id] and not self.game_over:
            lines.append(f"\nℹ️ **{opponent.display_name} khrej mn lmatch**, t9der tkml attempts dialk!")

        if self.game_over:
            lines.append(f"\n🏁 **Match sala!** Lkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    def get_spectator_content(self) -> str:
        p1_wrong = self.wrong_guesses[self.p1.id]
        p2_wrong = self.wrong_guesses[self.p2.id]
        p1_guessed = self.guessed_letters[self.p1.id]
        p2_guessed = self.guessed_letters[self.p2.id]

        p1_solved_count = sum(1 for ch in self.secret if ch in p1_guessed)
        p2_solved_count = sum(1 for ch in self.secret if ch in p2_guessed)

        if not self.game_over:
            # Spoiler Protected View
            lines = [
                "🪢 **Hangman 1v1 Match (Live Spectator)**",
                f"⚔️ **{self.p1.display_name}** vs **{self.p2.display_name}**\n"
            ]

            p1_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p1.id] else ""
            p1_lives = max(0, 6 - len(p1_wrong))
            lines.append(f"🔴 **{self.p1.display_name}**{p1_status}:")
            lines.append(f"❤️ Lives: **{p1_lives}/6** | Letters found: **{p1_solved_count}/{len(self.secret)}** | Mistakes: **{len(p1_wrong)}/6**")
            lines.append(f"```{HANGMAN_STAGES[min(len(p1_wrong), 6)]}```")

            p2_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p2.id] else ""
            p2_lives = max(0, 6 - len(p2_wrong))
            lines.append(f"\n🔵 **{self.p2.display_name}**{p2_status}:")
            lines.append(f"❤️ Lives: **{p2_lives}/6** | Letters found: **{p2_solved_count}/{len(self.secret)}** | Mistakes: **{len(p2_wrong)}/6**")
            lines.append(f"```{HANGMAN_STAGES[min(len(p2_wrong), 6)]}```")

            return "\n".join(lines)
        else:
            # Full Reveal
            lines = ["🏁 **Hangman 1v1 Match — Final Results**"]

            p1_won = self.won[self.p1.id]
            p2_won = self.won[self.p2.id]
            p1_quit = self.quit[self.p1.id]
            p2_quit = self.quit[self.p2.id]
            p1_mistakes = len(p1_wrong)
            p2_mistakes = len(p2_wrong)

            if p1_quit and p2_quit:
                winner_text = "🚪 **Ta wa7d ma rbe7 (bjoj khrejo mn lmatch).**"
            elif p1_quit:
                winner_text = f"🏆 **{self.p2.mention} rbe7!** ({self.p1.display_name} khrej mn lmatch)"
            elif p2_quit:
                winner_text = f"🏆 **{self.p1.mention} rbe7!** ({self.p2.display_name} khrej mn lmatch)"
            elif p1_won and not p2_won:
                winner_text = f"🏆 **{self.p1.mention} rbe7!**"
            elif p2_won and not p1_won:
                winner_text = f"🏆 **{self.p2.mention} rbe7!**"
            elif p1_won and p2_won:
                if p1_mistakes < p2_mistakes:
                    winner_text = f"🏆 **{self.p1.mention} rbe7** (b {p1_mistakes} mistakes vs {p2_mistakes})!"
                elif p2_mistakes < p1_mistakes:
                    winner_text = f"🏆 **{self.p2.mention} rbe7** (b {p2_mistakes} mistakes vs {p1_mistakes})!"
                else:
                    winner_text = f"🤝 **Ta3adol!** Bjojkom l9itoha b **{p1_mistakes} mistakes**!"
            else:
                winner_text = "💀 **Ta3adol!** Bjoj tchn9to."

            lines.append(f"{winner_text}\nLkelma kant: **{self.secret.upper()}**\n")

            # Reveal P1
            p1_masked = " ".join(ch.upper() if ch in p1_guessed else "\\_" for ch in self.secret)
            lines.append(f"🔴 **{self.p1.display_name}**" + (" (🚪 Khrej)" if p1_quit else "") + f": `{p1_masked}` (Mistakes: **{p1_mistakes}/6**)")
            lines.append(f"```{HANGMAN_STAGES[min(p1_mistakes, 6)]}```")

            # Reveal P2
            p2_masked = " ".join(ch.upper() if ch in p2_guessed else "\\_" for ch in self.secret)
            lines.append(f"\n🔵 **{self.p2.display_name}**" + (" (🚪 Khrej)" if p2_quit else "") + f": `{p2_masked}` (Mistakes: **{p2_mistakes}/6**)")
            lines.append(f"```{HANGMAN_STAGES[min(p2_mistakes, 6)]}```")

            return "\n".join(lines)

    async def process_player_guess(self, interaction: discord.Interaction, player: discord.Member, guess: str):
        if self.game_over or self.finished[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla saliti attempts dialk.", ephemeral=True)
            return

        p_guessed = self.guessed_letters[player.id]
        p_wrong = self.wrong_guesses[player.id]

        if len(guess) == 1:
            if guess in p_guessed or guess in p_wrong:
                await interaction.response.send_message("⚠️ Deja guessiti had l7arf.", ephemeral=True)
                return
            if guess in self.secret:
                p_guessed.add(guess)
                if all(ch in p_guessed for ch in self.secret):
                    self.won[player.id] = True
                    self.finished[player.id] = True
            else:
                p_wrong.append(guess)
                if len(p_wrong) >= 6:
                    self.finished[player.id] = True
        else:
            if guess == self.secret:
                for ch in self.secret:
                    p_guessed.add(ch)
                self.won[player.id] = True
                self.finished[player.id] = True
            else:
                if guess not in p_wrong:
                    p_wrong.append(guess)
                if len(p_wrong) >= 6:
                    self.finished[player.id] = True

        p1_won = self.won[self.p1.id]
        p2_won = self.won[self.p2.id]
        p1_quit = self.quit[self.p1.id]
        p2_quit = self.quit[self.p2.id]

        if (self.finished[self.p1.id] or p1_quit) and (self.finished[self.p2.id] or p2_quit):
            self.game_over = True

        view = self.dm_views.get(player.id)
        if self.finished[player.id] and view:
            for item in view.children:
                if isinstance(item, Button) and item.label == "Guess":
                    item.disabled = True
        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        try:
            await self.channel_msg.edit(content=self.get_spectator_content())
        except Exception as e:
            print(f"[hangman spectator update error]: {e}")

        if self.game_over:
            if self.cog and self.channel_msg and self.channel_msg.guild:
                winner = None
                p1_mistakes = len(self.wrong_guesses[self.p1.id])
                p2_mistakes = len(self.wrong_guesses[self.p2.id])
                if p1_quit and not p2_quit:
                    winner = self.p2
                elif p2_quit and not p1_quit:
                    winner = self.p1
                elif p1_won and not p2_won:
                    winner = self.p1
                elif p2_won and not p1_won:
                    winner = self.p2
                elif p1_won and p2_won:
                    if p1_mistakes < p2_mistakes:
                        winner = self.p1
                    elif p2_mistakes < p1_mistakes:
                        winner = self.p2
                if winner:
                    asyncio.create_task(self.cog.record_minigame_win(self.channel_msg.guild.id, winner.id, "hangman"))

            for p in (self.p1, self.p2):
                dm_msg = self.dm_messages.get(p.id)
                dm_v = self.dm_views.get(p.id)
                if dm_msg and dm_v:
                    for item in dm_v.children:
                        item.disabled = True
                    try:
                        await dm_msg.edit(content=self.get_player_dm_content(p), view=dm_v)
                    except Exception:
                        pass

    async def player_quit(self, interaction: discord.Interaction, player: discord.Member):
        if self.game_over or self.quit[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla khrejti deja.", ephemeral=True)
            return

        self.quit[player.id] = True
        self.finished[player.id] = True

        opponent = self.p2 if player == self.p1 else self.p1

        if self.finished[opponent.id] or self.quit[opponent.id]:
            self.game_over = True

        view = self.dm_views.get(player.id)
        if view:
            for item in view.children:
                item.disabled = True

        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        try:
            await self.channel_msg.edit(content=self.get_spectator_content())
        except Exception as e:
            print(f"[hangman spectator update on quit error]: {e}")

        opp_msg = self.dm_messages.get(opponent.id)
        opp_v = self.dm_views.get(opponent.id)
        if opp_msg and opp_v:
            if self.game_over:
                for item in opp_v.children:
                    item.disabled = True
            try:
                await opp_msg.edit(content=self.get_player_dm_content(opponent), view=opp_v)
            except Exception:
                pass

        if self.game_over and self.cog and self.channel_msg and self.channel_msg.guild:
            if self.quit[self.p1.id] and not self.quit[self.p2.id]:
                asyncio.create_task(self.cog.record_minigame_win(self.channel_msg.guild.id, self.p2.id, "hangman"))
            elif self.quit[self.p2.id] and not self.quit[self.p1.id]:
                asyncio.create_task(self.cog.record_minigame_win(self.channel_msg.guild.id, self.p1.id, "hangman"))


class HangmanChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Fun"):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.accepted = True
        self.stop()

        secret = self.cog.get_hangman_secret()
        match = HangmanMultiplayerMatch(self.challenger, self.challenged, interaction.message, secret, self.cog)

        try:
            p1_view = HangmanDMView(match, self.challenger)
            p1_msg = await self.challenger.send(content=match.get_player_dm_content(self.challenger), view=p1_view)
            match.dm_messages[self.challenger.id] = p1_msg
            match.dm_views[self.challenger.id] = p1_view
        except discord.Forbidden:
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenger.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        try:
            p2_view = HangmanDMView(match, self.challenged)
            p2_msg = await self.challenged.send(content=match.get_player_dm_content(self.challenged), view=p2_view)
            match.dm_messages[self.challenged.id] = p2_msg
            match.dm_views[self.challenged.id] = p2_view
        except discord.Forbidden:
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenged.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        await interaction.response.edit_message(content=match.get_spectator_content(), view=None)

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


# ============ TRIVIA HELPERS & UI CLASSES ============

async def fetch_trivia_batch(session: aiohttp.ClientSession, amount: int = 15) -> list[dict]:
    url = f"https://opentdb.com/api.php?amount={amount}&type=multiple"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                cleaned = []
                for item in results:
                    cleaned.append({
                        "category": html.unescape(item.get("category", "General Knowledge")),
                        "difficulty": item.get("difficulty", "medium").capitalize(),
                        "question": html.unescape(item.get("question", "")),
                        "correct_answer": html.unescape(item.get("correct_answer", "")),
                        "incorrect_answers": [html.unescape(ans) for ans in item.get("incorrect_answers", [])]
                    })
                if cleaned:
                    return cleaned
    except Exception as e:
        print(f"[fetch_trivia_batch error]: {e}")

    return [
        {
            "category": "Science",
            "difficulty": "Easy",
            "question": "What is the chemical symbol for Gold?",
            "correct_answer": "Au",
            "incorrect_answers": ["Ag", "Fe", "Gd"]
        },
        {
            "category": "Geography",
            "difficulty": "Easy",
            "question": "What is the capital of Morocco?",
            "correct_answer": "Rabat",
            "incorrect_answers": ["Casablanca", "Marrakech", "Fes"]
        },
        {
            "category": "General Knowledge",
            "difficulty": "Medium",
            "question": "How many bones are in the adult human body?",
            "correct_answer": "206",
            "incorrect_answers": ["208", "210", "204"]
        },
        {
            "category": "Computers",
            "difficulty": "Medium",
            "question": "What does CPU stand for?",
            "correct_answer": "Central Processing Unit",
            "incorrect_answers": ["Central Process Unit", "Computer Personal Unit", "Central Processor Universal"]
        },
        {
            "category": "History",
            "difficulty": "Medium",
            "question": "In which year did World War II end?",
            "correct_answer": "1945",
            "incorrect_answers": ["1944", "1946", "1939"]
        }
    ]


class TriviaChoiceButton(Button):
    def __init__(self, label: str, is_correct: bool, index: int):
        display_label = label[:80]
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=display_label,
            custom_id=f"trivia_{index}"
        )
        self.raw_label = label
        self.is_correct = is_correct


class TriviaQuestionView(View):
    def __init__(self, player: discord.Member, question_data: dict, timeout_duration: int = 15):
        super().__init__(timeout=timeout_duration)
        self.player = player
        self.question_data = question_data
        self.answered = False
        self.selected_correct = False
        self.selected_label: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self.event = asyncio.Event()

        choices = [(question_data["correct_answer"], True)] + [
            (ans, False) for ans in question_data["incorrect_answers"]
        ]
        random.shuffle(choices)

        for i, (choice_text, is_corr) in enumerate(choices):
            btn = TriviaChoiceButton(choice_text, is_corr, i)
            btn.callback = self.button_callback
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message("Machy dork asa7bi.", ephemeral=True)
            return False
        return True

    async def button_callback(self, interaction: discord.Interaction):
        if self.answered:
            await interaction.response.send_message("Jawbti deja.", ephemeral=True)
            return

        self.answered = True
        button_id = interaction.data.get("custom_id", "")
        clicked_button: Optional[TriviaChoiceButton] = None

        for item in self.children:
            if isinstance(item, TriviaChoiceButton):
                item.disabled = True
                if item.custom_id == button_id:
                    clicked_button = item

        if clicked_button:
            self.selected_label = clicked_button.raw_label
            if clicked_button.is_correct:
                self.selected_correct = True
                clicked_button.style = discord.ButtonStyle.success
            else:
                self.selected_correct = False
                clicked_button.style = discord.ButtonStyle.danger
                for item in self.children:
                    if isinstance(item, TriviaChoiceButton) and item.is_correct:
                        item.style = discord.ButtonStyle.success

        self.stop()
        self.event.set()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        if not self.answered:
            self.answered = True
            self.selected_correct = False
            for item in self.children:
                if isinstance(item, TriviaChoiceButton):
                    item.disabled = True
                    if item.is_correct:
                        item.style = discord.ButtonStyle.success
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass
            self.event.set()


# ============ TYPERACER HELPERS ============

def render_typeracer_image(text: str) -> io.BytesIO:
    width = 960
    height = 240
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    # Accent line at the very top
    draw.rectangle([(0, 0), (width, 4)], fill=(255, 255, 255, 255))

    try:
        header_font = ImageFont.truetype("arial.ttf", 16)
        text_font = ImageFont.truetype("arialbd.ttf", 46)
    except Exception:
        header_font = ImageFont.load_default()
        text_font = ImageFont.load_default()

    draw.text((40, 22), "TYPERACER  •  Type the 5 words below as fast as you can!", fill=(160, 160, 160, 255), font=header_font)

    # Inner container card
    card_top = 58
    card_bottom = height - 25
    draw.rounded_rectangle([(30, card_top), (width - 30, card_bottom)], radius=12, fill=(15, 15, 15, 255), outline=(45, 45, 45, 255), width=2)

    words = text.split()
    lines = []
    current_line = []
    for word in words:
        current_line.append(word)
        line_str = " ".join(current_line)
        bbox = draw.textbbox((0, 0), line_str, font=text_font)
        if (bbox[2] - bbox[0]) > 860:
            current_line.pop()
            lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))

    card_center_y = (card_top + card_bottom) // 2

    if len(lines) == 1:
        line = lines[0]
        bbox = draw.textbbox((0, 0), line, font=text_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (width - text_w) // 2
        y = card_center_y - (text_h // 2) - bbox[1]
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=text_font)
    else:
        line_spacing = 54
        total_h = len(lines) * line_spacing
        y_start = card_center_y - (total_h // 2)
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=text_font)
            text_w = bbox[2] - bbox[0]
            x = (width - text_w) // 2
            draw.text((x, y_start + i * line_spacing), line, fill=(255, 255, 255, 255), font=text_font)

    output = io.BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output





# ============ FLAGS HELPERS ============

FLAG_ALIASES = {
    "ae": ["uae", "emirates", "united arab emirates"],
    "us": ["usa", "us", "america", "united states", "united states of america"],
    "gb": ["uk", "britain", "great britain", "england", "united kingdom"],
    "cd": ["dr congo", "drc", "democratic republic of the congo", "congo"],
    "cg": ["congo", "republic of the congo", "congo brazzaville"],
    "kr": ["south korea", "korea"],
    "kp": ["north korea"],
    "sa": ["saudi", "saudi arabia", "ksa"],
    "ru": ["russia", "russian federation"],
    "cz": ["czechia", "czech republic"],
    "tz": ["tanzania", "united republic of tanzania"],
    "va": ["vatican", "vatican city", "holy see"],
    "ps": ["palestine", "state of palestine"],
    "sy": ["syria", "syrian arab republic"],
    "la": ["laos", "lao"],
    "ci": ["ivory coast", "cote d ivoire", "cote divoire", "cote d'ivoire"],
    "cf": ["car", "central african republic"],
    "nz": ["nz", "new zealand"],
    "do": ["dominican republic", "dominican rep"],
    "tt": ["trinidad", "trinidad and tobago"],
    "st": ["sao tome", "sao tome and principe"],
    "pg": ["png", "papua new guinea"],
    "ba": ["bosnia", "bosnia and herzegovina"],
    "cv": ["cape verde", "cabo verde"],
    "kn": ["saint kitts", "st kitts", "st kitts and nevis", "saint kitts and nevis"],
    "lc": ["saint lucia", "st lucia"],
    "vc": ["saint vincent", "st vincent", "saint vincent and the grenadines", "st vincent and the grenadines"],
    "fm": ["micronesia", "federated states of micronesia"],
    "ir": ["iran", "islamic republic of iran"],
    "mm": ["myanmar", "burma"],
    "mk": ["north macedonia", "macedonia"],
    "sz": ["eswatini", "swaziland"],
    "tl": ["east timor", "timor leste", "timor"],
    "nl": ["netherlands", "holland"],
    "tr": ["turkey", "turkiye"],
}

def normalize_country_text(text: str) -> str:
    if not text:
        return ""
    # Strip accents / diacritics
    t = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    t = t.lower().strip()
    if t.startswith("the "):
        t = t[4:].strip()
    t = re.sub(r"[.,'\-/&]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def is_flag_guess_correct(guess: str, code: str, country_name: str) -> bool:
    norm_guess = normalize_country_text(guess)
    norm_target = normalize_country_text(country_name)

    if not norm_guess or not norm_target:
        return False

    # 1. Direct exact match
    if norm_guess == norm_target:
        return True

    # 2. Check predefined aliases
    code_aliases = FLAG_ALIASES.get(code.lower(), [])
    for alias in code_aliases:
        norm_alias = normalize_country_text(alias)
        if norm_guess == norm_alias:
            return True
        if len(norm_guess) >= 4 and difflib.SequenceMatcher(None, norm_guess, norm_alias).ratio() >= 0.85:
            return True

    # 3. Fuzzy match on country name (e.g. minor typos: "philipines" -> "philippines")
    similarity = difflib.SequenceMatcher(None, norm_guess, norm_target).ratio()
    if similarity >= 0.82:
        return True

    return False


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
                payout = int(round(self.bet * 2.5))
                net_profit = payout - self.bet
                asyncio.create_task(economy_cog.add_balance(self.author.id, payout, context="Blackjack Natural 21"))
                if self.message and self.message.guild:
                    asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "blackjack", earnings=net_profit))
                outcome_text += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Payout: {format_tad(payout)})!"
            elif is_win:
                payout = self.bet * 2
                net_profit = self.bet
                asyncio.create_task(economy_cog.add_balance(self.author.id, payout, context="Blackjack Win"))
                if self.message and self.message.guild:
                    asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "blackjack", earnings=net_profit))
                outcome_text += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Payout: {format_tad(payout)})!"
            elif is_push:
                asyncio.create_task(economy_cog.add_balance(self.author.id, self.bet, context="Blackjack Push Refund"))
                outcome_text += f"\n\n🤝 Rje3 lik l bet ta3k: {format_tad(self.bet)}."
            else:
                outcome_text += f"\n\n💥 Khesrti l bet: -{format_tad(self.bet)}."
        elif is_win and self.message and self.message.guild:
            asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "blackjack"))

        embed = self.get_embed(dealer_reveal=True, outcome_text=outcome_text)
        file = self.get_render_file(dealer_reveal=True)
        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
        elif self.message:
            await self.message.edit(embed=embed, view=self, attachments=[file])
        self.stop()

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
    def __init__(self, author: discord.Member, cog, bomb_count: int = 3, bet: int = 0):
        super().__init__(timeout=120)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.width = 5
        self.height = 4  # 4 rows x 5 columns = 20 tiles + Cashout on row 4
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
            10.10, 14.80, 22.50, 36.00, 62.00, 120.00, 280.00
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
            payout = int(round(self.bet * mult))
            net_profit = payout - self.bet
            asyncio.create_task(economy_cog.add_balance(self.author.id, payout, context=f"Mines Win ({mult:.2f}x)"))
            if self.message and self.message.guild:
                asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines", earnings=net_profit))
            embed.add_field(name="💵 Payout", value=f"🟢 **+{format_tad(net_profit)}** (Total: {format_tad(payout)})", inline=False)
        elif self.message and self.message.guild:
            asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines"))

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

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

            embed = discord.Embed(
                title="💥 BOOM! Game Over",
                description=f"💣 Tferg3at 3lik bomb f tile `({x+1}, {y+1})`! Khesrti.",
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
                payout = int(round(self.bet * mult))
                net_profit = payout - self.bet
                asyncio.create_task(economy_cog.add_balance(self.author.id, payout, context=f"Mines Jackpot ({mult:.2f}x)"))
                if self.message and self.message.guild:
                    asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines", earnings=net_profit))
                embed.add_field(name="💵 Payout", value=f"👑 **+{format_tad(net_profit)}** (Total: {format_tad(payout)})", inline=False)
            elif self.message and self.message.guild:
                asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "mines"))
            
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
        if self.streak == 0:
            return 1.00
        return round(1.0 + (self.streak * 0.45) + (self.streak ** 1.3) * 0.15, 2)

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
            embed = discord.Embed(
                title="💥 Ghalat! Game Over",
                description=f"❌ {card_reveal_str}.\nKhesrti! Final Streak: **{self.streak}**.",
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
            payout = int(round(self.bet * mult))
            net_profit = payout - self.bet
            asyncio.create_task(economy_cog.add_balance(self.author.id, payout, context=f"HigherLower Win ({mult:.2f}x)"))
            if self.message and self.message.guild:
                asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower", earnings=net_profit))
            embed.add_field(name="💵 Payout", value=f"🟢 **+{format_tad(net_profit)}** (Total: {format_tad(payout)})", inline=False)
        elif self.message and self.message.guild:
            asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "higherlower"))

        file = get_hl_card_file(self.current_card)
        if file:
            embed.set_thumbnail(url="attachment://card.png")
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])
        self.stop()


# ============ COINFLIP & DICE VIEWS ============

class CoinflipView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=45)
        self.author = author
        self.cog = cog
        self.bet = bet
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    async def _flip(self, interaction: discord.Interaction, user_choice: str):
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
                payout = self.bet * 2
                asyncio.create_task(economy_cog.add_balance(self.author.id, payout, context="Coinflip Win"))
                if self.message and self.message.guild:
                    asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "coinflip", earnings=self.bet))
        elif won and self.message and self.message.guild:
            asyncio.create_task(self.cog.record_minigame_win(self.message.guild.id, self.author.id, "coinflip"))

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
    "blacktea": ("☕ BlackTea", ["blacktea", "bt", "black", "jklm"]),
    "greentea": ("🍵 GreenTea", ["greentea", "gt", "green"]),
    "blackjack": ("🃏 Blackjack", ["blackjack", "bj", "21"]),
    "slots": ("🎰 Slots", ["slots", "slot", "machine"]),
    "mines": ("💣 Mines", ["mines", "gems", "gemhunt"]),
    "roulette": ("🎡 Roulette", ["roulette", "wheel", "roul"]),
    "higherlower": ("🃏 HigherLower", ["higherlower", "hl", "cardduel"]),
    "coinflip": ("🪙 Coinflip", ["coinflip", "cf", "drhm", "drhem", "flip"]),
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
}

class LeaderboardSelect(discord.ui.Select):
    def __init__(self, minigame_map: dict):
        options = []
        for game_key, (display_name, _) in minigame_map.items():
            parts = display_name.split(" ", 1)
            emoji_part = parts[0] if len(parts) > 1 else None
            label_part = parts[1] if len(parts) > 1 else display_name
            options.append(discord.SelectOption(
                label=label_part,
                value=game_key,
                emoji=emoji_part
            ))
        super().__init__(placeholder="🎯 Khtar minigame bach tchouf ranking dialha...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: LeaderboardInteractiveView = self.view
        selected_game = self.values[0]
        await view.show_game_page(interaction, selected_game, sort_by="wins", page=0)


class LeaderboardInteractiveView(discord.ui.View):
    def __init__(self, ctx, cog, minigame_map: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.minigame_map = minigame_map
        self.current_game: Optional[str] = None
        self.sort_by: str = "wins"  # "wins" or "earnings"
        self.current_page: int = 0
        self.per_page: int = 10
        self.rows_cache = []
        self.message: Optional[discord.Message] = None

        self.setup_overview()

    def setup_overview(self):
        self.clear_items()
        self.current_game = None
        self.add_item(LeaderboardSelect(self.minigame_map))

    async def show_overview(self, interaction: Optional[discord.Interaction] = None):
        self.setup_overview()
        embed = await self.cog.get_main_leaderboard_embed(self.ctx.guild)
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            self.message = await self.ctx.send(embed=embed, view=self)

    async def show_game_page(self, interaction: Optional[discord.Interaction] = None, game_key: Optional[str] = None, sort_by: str = "wins", page: int = 0):
        if game_key:
            self.current_game = game_key
        self.sort_by = sort_by
        self.current_page = page

        order_col = "earnings" if sort_by == "earnings" else "wins"
        async with self.cog.bot.db.execute(f"""
            SELECT user_id, wins, earnings FROM minigame_leaderboard
            WHERE guild_id = ? AND game = ?
            ORDER BY {order_col} DESC
        """, (self.ctx.guild.id, self.current_game)) as cursor:
            self.rows_cache = await cursor.fetchall()

        self.clear_items()

        total_pages = max(1, (len(self.rows_cache) + self.per_page - 1) // self.per_page)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        # Pagination & Switch Buttons
        prev_btn = discord.ui.Button(label="◀️", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), row=0)
        async def prev_callback(i: discord.Interaction):
            await self.show_game_page(i, self.current_game, self.sort_by, self.current_page - 1)
        prev_btn.callback = prev_callback
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(label=f"Page {self.current_page + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=0)
        self.add_item(page_btn)

        next_btn = discord.ui.Button(label="▶️", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), row=0)
        async def next_callback(i: discord.Interaction):
            await self.show_game_page(i, self.current_game, self.sort_by, self.current_page + 1)
        next_btn.callback = next_callback
        self.add_item(next_btn)

        # Sort Switcher Button
        if sort_by == "wins":
            sort_btn = discord.ui.Button(label="Sort by Money 💰", style=discord.ButtonStyle.success, emoji="💰", row=1)
            async def sort_callback(i: discord.Interaction):
                await self.show_game_page(i, self.current_game, "earnings", 0)
            sort_btn.callback = sort_callback
        else:
            sort_btn = discord.ui.Button(label="Sort by Wins 🏆", style=discord.ButtonStyle.primary, emoji="🏆", row=1)
            async def sort_callback(i: discord.Interaction):
                await self.show_game_page(i, self.current_game, "wins", 0)
            sort_btn.callback = sort_callback
        self.add_item(sort_btn)

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
        sort_title = "💰 Sorted by Gains" if self.sort_by == "earnings" else "🏆 Sorted by Wins"
        embed = discord.Embed(
            title=f"{game_display} Leaderboard",
            description=f"*{sort_title}* — **{self.ctx.guild.name}**\n\n",
            color=0x000000
        )

        if not self.rows_cache:
            embed.description += "*No records yet.*"
            return embed

        start_idx = self.current_page * self.per_page
        page_rows = self.rows_cache[start_idx : start_idx + self.per_page]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, wins, earnings) in enumerate(page_rows, start=start_idx):
            rank_str = medals[i] if i < 3 else f"**#{i+1}**"
            win_str = f"**{wins}** win" if wins == 1 else f"**{wins}** wins"
            earn_str = format_tad(earnings)
            if self.sort_by == "earnings":
                lines.append(f"{rank_str} <@{uid}> — {earn_str} *({win_str})*")
            else:
                lines.append(f"{rank_str} <@{uid}> — {win_str} *({earn_str})*")

        embed.description += "\n".join(lines)
        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages}")
        return embed


# ============ MAIN COG ============

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.words_db_path = os.path.join("assets", "words.db")
        self.dict_conn = sqlite3.connect(self.words_db_path, check_same_thread=False)
        try:
            self.dict_conn.execute("PRAGMA cache_size = -2000")
        except Exception:
            pass

    def _get_cursor(self):
        try:
            return self.dict_conn.cursor()
        except Exception:
            self.dict_conn = sqlite3.connect(self.words_db_path, check_same_thread=False)
            try:
                self.dict_conn.execute("PRAGMA cache_size = -2000")
            except Exception:
                pass
            return self.dict_conn.cursor()

    async def record_minigame_win(self, guild_id: Optional[int], user_id: int, game: str, earnings: int = 0):
        if not guild_id:
            return
        try:
            await self.bot.db.execute("""
                INSERT INTO minigame_leaderboard (guild_id, user_id, game, wins, earnings)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(guild_id, user_id, game) DO UPDATE SET wins = wins + 1, earnings = earnings + ?
            """, (guild_id, user_id, game.lower(), max(0, earnings), max(0, earnings)))
            await self.bot.db.commit()
        except Exception as e:
            print(f"[record_minigame_win error]: {e}")

    def get_typeracer_text(self) -> str:
        count = 5
        words = []
        for attempt in range(2):
            try:
                cur = self._get_cursor()
                cur.execute("SELECT word FROM dictionary_words WHERE word GLOB '[a-z]*' ORDER BY RANDOM() LIMIT ?", (count,))
                rows = cur.fetchall()
                if rows:
                    words = [r[0].lower() for r in rows if r[0] and r[0].isalpha()]
                break
            except Exception as e:
                self.dict_conn = sqlite3.connect(self.words_db_path, check_same_thread=False)
                if attempt == 1:
                    print(f"[get_typeracer_text error]: {e}")

        fallback_pool = [
            "guitar", "bridge", "summer", "yellow", "orange", "bottle", "window", "forest",
            "dragon", "castle", "planet", "silver", "garden", "market", "shadow", "future",
            "system", "engine", "wonder", "nature", "memory", "rocket", "energy", "stream",
            "island", "harbor", "puzzle", "flight", "circle", "season", "moment", "tunnel",
            "coffee", "camera", "mirror", "pencil", "desert", "shield", "breeze", "beacon"
        ]
        while len(words) < count:
            words.append(random.choice(fallback_pool))

        return " ".join(words[:count])

    def get_wordle_secret(self) -> str:
        return random.choice(WORDLE_TARGETS)

    def get_hangman_secret(self) -> str:
        for attempt in range(2):
            try:
                cur = self._get_cursor()
                cur.execute("SELECT word FROM hangman_targets ORDER BY RANDOM() LIMIT 1")
                row = cur.fetchone()
                if row:
                    return row[0]
                break
            except Exception as e:
                self.dict_conn = sqlite3.connect(self.words_db_path, check_same_thread=False)
                if attempt == 1:
                    print(f"[get_hangman_secret error]: {e}")
        return random.choice(["planet", "castle", "dragon", "monster", "python", "bridge", "silver", "garden", "forest", "wizard"])

    def get_combo(self) -> str:
        for attempt in range(2):
            try:
                cur = self._get_cursor()
                cur.execute("SELECT combo FROM word_combos ORDER BY RANDOM() LIMIT 1")
                row = cur.fetchone()
                if row:
                    return row[0]
                break
            except Exception as e:
                self.dict_conn = sqlite3.connect(self.words_db_path, check_same_thread=False)
                if attempt == 1:
                    print(f"[get_combo error]: {e}")
        return random.choice(["ing", "ter", "con", "sta", "ent", "ear", "tra", "man", "all", "ver", "pro", "dis", "cal", "ted", "ith"])

    def is_english_word(self, word: str) -> bool:
        if not word or not isinstance(word, str):
            return False
        clean_word = word.strip().lower()
        if not clean_word.isalpha() or len(clean_word) < 3:
            return False
        for attempt in range(2):
            try:
                cur = self._get_cursor()
                cur.execute("SELECT 1 FROM dictionary_words WHERE word = ? LIMIT 1", (clean_word,))
                return cur.fetchone() is not None
            except Exception as e:
                self.dict_conn = sqlite3.connect(self.words_db_path, check_same_thread=False)
                if attempt == 1:
                    print(f"[is_english_word error]: {e}")
        return False

    @commands.command(aliases=["swl", "sewel", "swel"], help="Nswlk so2al khssk tjawb 3lih b sara7a.")
    async def truth(self, ctx):
        url = 'https://api.truthordarebot.xyz/v1/truth'
        async with self.bot.session.get(url) as resp:
            data = await resp.json()
        await ctx.send(data['question'])

    @commands.command(aliases=["7kem", "7km", "hkm", "hkem"], help="N7kem 3lik b 7ekma khssk dirha darori.")
    async def dare(self, ctx):
        url = 'https://api.truthordarebot.xyz/v1/dare'
        async with self.bot.session.get(url) as resp:
            data = await resp.json()
        await ctx.send(data['question'])

    @commands.command(aliases=["wyr", "khyrni"], help="Law khayarouk okda.")
    async def wouldyourather(self, ctx):
        url = 'https://api.truthordarebot.xyz/v1/wyr'
        async with self.bot.session.get(url) as resp:
            data = await resp.json()
        await ctx.send(data['question'])

    @commands.command(name="flags", aliases=["gtf"], help="Guess the flag okda.")
    async def flags(self, ctx, round_duration: int = 15):
        if round_duration < 5:
            round_duration = 5
            time_display = "5s (Minimum)"
        else:
            time_display = f"{round_duration}s"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://flagcdn.com/en/codes.json") as resp:
                    if resp.status == 200:
                        raw_data = await resp.json()
                        country_pool = [
                            {"name": name, "code": code}
                            for code, name in raw_data.items()
                            if "-" not in code and code != "eu"
                        ]
                    else:
                        raise Exception()
            except Exception:
                country_pool = [
                    {"name": "Morocco", "code": "ma"}, {"name": "France", "code": "fr"},
                    {"name": "Spain", "code": "es"}, {"name": "Italy", "code": "it"},
                    {"name": "Germany", "code": "de"}, {"name": "Japan", "code": "jp"},
                    {"name": "Brazil", "code": "br"}, {"name": "Argentina", "code": "ar"}
                ]

        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🏁 Guess the Flag!",
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**",
            color=0x000000
        )
        signup_msg = await ctx.send(embed=signup_embed)
        await signup_msg.add_reaction(join_emoji)

        await asyncio.sleep(19)

        signup_msg = await ctx.channel.fetch_message(signup_msg.id)
        reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

        players = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    players.append(user)

        if not players:
            await signup_msg.edit(embed=discord.Embed(description="💨 7ta wa7d ma dkhel lgame ._.", color=0x000000))
            return

        single_player = len(players) == 1
        hp = {player.id: 3 for player in players}
        active_players = list(players)

        # Match pool to prevent any repeated flags in the same match
        match_pool = list(country_pool)
        random.shuffle(match_pool)

        start_embed = discord.Embed(
            description="▶️ Bdina! Kola wa7d 3ndo **3 HP**.",
            color=0x000000
        )
        await signup_msg.edit(embed=start_embed)
        await asyncio.sleep(2)

        while len(active_players) > 0:
            if not single_player and len(active_players) == 1:
                winner = active_players[0]
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, winner.id, "flags", earnings=40)
                    economy_cog = self.bot.get_cog("Economy")
                    if economy_cog:
                        asyncio.create_task(economy_cog.add_balance(winner.id, 40, context="Flags Win"))
                win_embed = discord.Embed(
                    description=f"🏆 {winner.mention} rbe7 lgame!\n💰 Rbe7ti **40** {TAD_EMOJI} TAD!",
                    color=0x000000
                )
                await ctx.send(embed=win_embed)
                return

            for player in list(active_players):
                if not single_player and len(active_players) == 1:
                    break

                if not match_pool:
                    match_pool = list(country_pool)
                    random.shuffle(match_pool)

                target = match_pool.pop()
                correct_name = target["name"]
                target_code = target["code"]
                flag_url = f"https://flagcdn.com/w320/{target_code}.png"

                game_embed = discord.Embed(
                    description=f"❓ Chno smit had dawla?\n⌛ Time: {round_duration}s\n❤️ HP: {hp[player.id]}",
                    color=0x000000
                )
                game_embed.set_image(url=flag_url)
                await ctx.send(player.mention, embed=game_embed)

                def check(m):
                    return m.author.id == player.id and m.channel.id == ctx.channel.id

                start_time = time.time()
                guessed_correctly = False

                while time.time() - start_time < round_duration:
                    time_left = round_duration - (time.time() - start_time)
                    if time_left <= 0:
                        break

                    try:
                        msg = await self.bot.wait_for("message", check=check, timeout=time_left)

                        if msg.content.strip().lower() == "exitgame":
                            hp[player.id] = 0
                            await ctx.send(f"🚪 **{player.mention}** khrej mn lgame.")
                            active_players.remove(player)
                            guessed_correctly = True
                            break

                        if is_flag_guess_correct(msg.content, target_code, correct_name):
                            await msg.add_reaction("✅")
                            guessed_correctly = True
                            break

                    except asyncio.TimeoutError:
                        break

                if not guessed_correctly:
                    hp[player.id] -= 1
                    if hp[player.id] <= 0:
                        await ctx.send(
                            embed=discord.Embed(description=f"💥 **{player.mention}** t elimina **0 HP**. Ljawab howa **{correct_name}**.",
                                                color=0x000000))
                        active_players.remove(player)
                    else:
                        await ctx.send(embed=discord.Embed(description=f"⌛ Sala lwe9t {player.mention}: **-1 HP**. Ljawab howa **{correct_name}**.",
                                            color=0x000000))

                await asyncio.sleep(2)

    @commands.command(aliases=["jklm"], help="Kteb kelma fiha l7orof li ghan3tik.")
    async def blacktea(self, ctx, round_duration: int = 15):
        if round_duration < 5:
            round_duration = 5
            time_display = "5s (Minimum)"
        else:
            time_display = f"{round_duration}s"

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="☕ BlackTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**",
                color=0x000000
            )
            start = await ctx.send(embed=signup_embed)
            await start.add_reaction(join_emoji)
            await asyncio.sleep(19)

            signup_msg = await ctx.channel.fetch_message(start.id)
            reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

            players = []
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        players.append(user)

            if not players:
                await start.edit(embed=discord.Embed(
                    description="💨 7ta wa7d ma dkhel lgame ._.",
                    color=0x000000
                ))
                return

            single_player = len(players) == 1
            lives = {p.id: 3 for p in players}
            active_players = list(players)
            used_words = set()

            if single_player:
                await start.edit(embed=discord.Embed(
                    description="▶️ Bdina! 3ndek **3 HP**.",
                    color=0x000000
                ))
            else:
                await start.edit(embed=discord.Embed(
                    description="▶️ Bdina! Kola wa7d 3ndo **3 HP**.",
                    color=0x000000
                ))
            await asyncio.sleep(2)

            if single_player:
                player = active_players[0]
                while lives[player.id] > 0:
                    combo = self.get_combo()

                    await ctx.send(f"❓ {player.mention} kteb kelma fiha: **{combo.upper()}** (HP: **{lives[player.id]}**)")

                    def check(message):
                        if message.author.id != player.id or message.channel.id != ctx.channel.id:
                            return False
                        word = message.content.strip().lower()
                        if word == "exitgame":
                            return True
                        if combo not in word or word in used_words:
                            return False
                        return self.is_english_word(word)

                    try:
                        word_msg = await self.bot.wait_for('message', check=check, timeout=round_duration)
                        if word_msg:
                            if word_msg.content.strip().lower() == "exitgame":
                                lives[player.id] = 0
                                await ctx.send(f"🚪 **{player.mention}** khrej mn lgame (**Game Over**).")
                                break
                            used_words.add(word_msg.content.strip().lower())
                            await word_msg.add_reaction('✅')
                    except asyncio.TimeoutError:
                        lives[player.id] -= 1
                        if lives[player.id] > 0:
                            await ctx.send(f"⌛ Sala lwe9t: -1 HP (Ba9i: **{lives[player.id]} HP**)")
                        else:
                            await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**)")
            else:
                while len(active_players) > 1:
                    for player in list(active_players):
                        if len(active_players) <= 1:
                            break

                        combo = self.get_combo()

                        await ctx.send(f"❓ {player.mention} kteb kelma fiha: **{combo.upper()}** (HP: **{lives[player.id]}**)")

                        def check(message):
                            if message.author.id != player.id or message.channel.id != ctx.channel.id:
                                return False
                            word = message.content.strip().lower()
                            if word == "exitgame":
                                return True
                            if combo not in word or word in used_words:
                                return False
                            return self.is_english_word(word)

                        try:
                            word_msg = await self.bot.wait_for('message', check=check, timeout=round_duration)
                            if word_msg:
                                if word_msg.content.strip().lower() == "exitgame":
                                    lives[player.id] = 0
                                    await ctx.send(f"🚪 **{player.mention}** khrej mn lgame o t elimina.")
                                    active_players.remove(player)
                                    continue
                                used_words.add(word_msg.content.strip().lower())
                                await word_msg.add_reaction('✅')
                        except asyncio.TimeoutError:
                            lives[player.id] -= 1
                            if lives[player.id] > 0:
                                await ctx.send(f"⌛ Sala lwe9t: -1 HP (Ba9i: **{lives[player.id]} HP**)")
                            else:
                                await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**)")
                                active_players.remove(player)

                if active_players:
                    if not single_player and ctx.guild:
                        await self.record_minigame_win(ctx.guild.id, active_players[0].id, "blacktea", earnings=40)
                        economy_cog = self.bot.get_cog("Economy")
                        if economy_cog:
                            asyncio.create_task(economy_cog.add_balance(active_players[0].id, 40, context="BlackTea Win"))
                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {active_players[0].mention} rbe7 lgame!\n💰 Rbe7ti **40** {TAD_EMOJI} TAD!",
                        color=0x000000
                    ))
        except Exception as e:
            print(f"[blacktea error]: {e}")

    @commands.command(help="Kteb kelma fiha l7orof li ghan3tik bzerba.")
    async def greentea(self, ctx, round_duration: int = 15):
        if round_duration < 5:
            round_duration = 5
            time_display = "5s (Minimum)"
        else:
            time_display = f"{round_duration}s"

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="🍵 GreenTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nMin Players: **2**",
                color=0x000000
            )
            start = await ctx.send(embed=signup_embed)
            await start.add_reaction(join_emoji)
            await asyncio.sleep(19)

            signup_msg = await ctx.channel.fetch_message(start.id)
            reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

            players = []
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        players.append(user)

            if len(players) < 2:
                await start.edit(embed=discord.Embed(
                    description="❌ Khass minimum **2 players** bach tl3bo GreenTea.",
                    color=0x000000
                ))
                return

            await start.edit(embed=discord.Embed(
                description="▶️ **Bdina!** (10 Rounds)\nPlayers: " + ", ".join(p.mention for p in players),
                color=0x000000
            ))

            points = {p.id: 0 for p in players}
            player_ids = {p.id for p in players}
            used_words = set()
            await asyncio.sleep(2)

            for round_num in range(1, 11):
                if len(player_ids) <= 1:
                    break

                combo = self.get_combo()
                if not combo:
                    continue

                await ctx.send(embed=discord.Embed(
                    description=f"Kteb kelma fiha: **{combo.upper()}**\n⏱️ Round **{round_num}/10**",
                    color=0x000000
                ))

                def check(message):
                    if message.author.id not in player_ids or message.channel.id != ctx.channel.id:
                        return False
                    word = message.content.strip().lower()
                    if word == "exitgame":
                        return True
                    if combo not in word or word in used_words:
                        return False
                    return self.is_english_word(word)

                try:
                    word_msg = await self.bot.wait_for('message', check=check, timeout=round_duration)
                    if word_msg:
                        fast = word_msg.author
                        if word_msg.content.strip().lower() == "exitgame":
                            player_ids.discard(fast.id)
                            players = [p for p in players if p.id != fast.id]
                            await ctx.send(f"🚪 **{fast.mention}** khrej mn lgame.")
                            if len(player_ids) <= 1:
                                break
                        else:
                            used_words.add(word_msg.content.strip().lower())
                            points[fast.id] += 1
                            await word_msg.add_reaction('✅')
                            await asyncio.sleep(1.5)
                            await ctx.send(embed=discord.Embed(
                                description=f"✅ {fast.mention} 5da 1 point. (Total: **{points[fast.id]} pts**)",
                                color=0x000000
                            ))
                except asyncio.TimeoutError:
                    await ctx.send(embed=discord.Embed(
                        description="⌛ Sala lwe9t. 7ta wa7d ma 5da lpoint.",
                        color=0x000000
                    ))
                await asyncio.sleep(1.5)

            if points:
                maxpoints = max(points.values())
                winners = [pid for pid, pts in points.items() if pts == maxpoints]
                if len(winners) == 1:
                    winner = self.bot.get_user(winners[0])
                    winner_str = winner.mention if winner else f"<@{winners[0]}>"
                    if maxpoints > 0 and ctx.guild:
                        await self.record_minigame_win(ctx.guild.id, winners[0], "greentea", earnings=40)
                        economy_cog = self.bot.get_cog("Economy")
                        if economy_cog:
                            asyncio.create_task(economy_cog.add_balance(winners[0], 40, context="GreenTea Win"))
                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {winner_str} rbe7 lgame b **{maxpoints} pts**!\n💰 Rbe7ti **40** {TAD_EMOJI} TAD!",
                        color=0x000000
                    ))
                else:
                    mention_str = " o ".join(f"<@{wid}>" for wid in winners)
                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {mention_str} ta3adlo b **{maxpoints} pts**!",
                        color=0x000000
                    ))
        except Exception as e:
            print(f"[greentea error]: {e}")

    @commands.command(name="tictactoe", aliases=["ttt"], help="X/O las9 3 bach trbe7 (sat ttt @user [bet:500]).")
    @not_fraud()
    async def tictactoe(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        bet, _ = parse_bet_argument(*args)
        if member is None:
            view = TicTacToeView(ctx.author, ctx.bot.user, is_bot_game=True, cog=self)
            content = f"❌ **{ctx.author.mention}'s turn (X)**"
            message = await ctx.send(content=content, view=view)
            view.message = message
            return

        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w_author = await economy_cog.get_wallet(ctx.author.id)
            w_target = await economy_cog.get_wallet(member.id)
            if w_author["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w_author['balance'])}.")
                return
            if w_target["balance"] < bet:
                await ctx.send(f"❌ **{member.display_name}** ma 3ndo kafi dial flous ({format_tad(w_target['balance'])} / {format_tad(bet)})!")
                return

        players = [ctx.author, member]
        random.shuffle(players)
        player_x, player_o = players[0], players[1]

        p_x_str = player_x.mention if player_x == member else player_x.display_name
        p_o_str = player_o.mention if player_o == member else player_o.display_name

        challenge_view = ChallengeView(ctx.author, member, self, bet=bet or 0)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"🔥 **5% Tax Burned:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Tic-Tac-Toe Challenge!**\n"
            f"**{p_x_str}** (❌ X) vs **{p_o_str}** (⭕ O)"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="connectfour", aliases=["c4", "connect4"], help="Las9 4 bach trbe7 (sat c4 @user [bet:500]).")
    @not_fraud()
    async def connectfour(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        bet, _ = parse_bet_argument(*args)
        if member is None:
            view = ConnectFourView(ctx.author, ctx.bot.user, is_bot_game=True, cog=self)
            content = view.get_status_content()
            message = await ctx.send(content=content, view=view)
            view.message = message
            return

        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w_author = await economy_cog.get_wallet(ctx.author.id)
            w_target = await economy_cog.get_wallet(member.id)
            if w_author["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w_author['balance'])}.")
                return
            if w_target["balance"] < bet:
                await ctx.send(f"❌ **{member.display_name}** ma 3ndo kafi dial flous ({format_tad(w_target['balance'])} / {format_tad(bet)})!")
                return

        challenge_view = ConnectFourChallengeView(ctx.author, member, self, bet=bet or 0)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"🔥 **5% Tax Burned:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Connect Four Challenge!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="akinator", aliases=["aki"], help="Fekker f chy character o khsni n3erfo.")
    async def akinator_cmd(self, ctx: commands.Context):
        async with ctx.typing():
            view = AkinatorView(ctx.author, timeout=60.0)

            # Setup dynamic callbacks for buttons
            for child in view.children:
                if isinstance(child, Button):
                    child.callback = view.button_callback

            try:
                embed = await view.start_game()
                message = await ctx.send(embed=embed, view=view)
                view.message = message
            except Exception as e:
                print(f"[Akinator Command Error]: {e}")
                await ctx.send("❌ Makhdamach Akinator daba, 7awel mn be3d.")

    @commands.command(name="playchess", aliases=["shitranj", "chessgame"], help="L3eb chess (sat playchess @user [bet:500]).")
    @not_fraud()
    async def playchess(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        bet, _ = parse_bet_argument(*args)
        # Single Player vs Bot
        if member is None:
            game_view = ChessView(ctx.author, ctx.bot.user, is_bot_game=True, cog=self)
            board_file = await game_view.generate_board_file()
            msg = await ctx.send(embed=game_view.build_embed(), file=board_file, view=game_view)
            game_view.message = msg
            return

        if member.bot:
            await ctx.send("❌ Mat9derch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9derch tl3eb Chess ded rask..")
            return

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w_author = await economy_cog.get_wallet(ctx.author.id)
            w_target = await economy_cog.get_wallet(member.id)
            if w_author["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w_author['balance'])}.")
                return
            if w_target["balance"] < bet:
                await ctx.send(f"❌ **{member.display_name}** ma 3ndo kafi dial flous ({format_tad(w_target['balance'])} / {format_tad(bet)})!")
                return

        # Multiplayer Challenge
        challenge_view = ChessChallengeView(ctx.author, member, self, bet=bet or 0)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"🔥 **5% Tax Burned:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Challenge dial Chess!**\n"
            f"**{ctx.author.display_name}** challenga {member.mention} f match dial Chess!"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        await ctx.send(content=content, view=challenge_view)

    @commands.command(name="rockpaperscissors", aliases=["rps", "zdimbomba7", "zba7"], help="7ajar wara9 mi9as (sat rps @user [bet:500]).")
    @not_fraud()
    async def rps(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        bet, _ = parse_bet_argument(*args)
        if member is None:
            view = RPSBotView(ctx.author)
            embed = discord.Embed(
                title="🪨 Rock Paper Scissors",
                description=f"⚔️ {ctx.author.mention} vs 🤖 Bot\n\nKhtar choice dialk:",
                color=0x000000
            )
            message = await ctx.send(embed=embed, view=view)
            view.message = message
            return

        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w_author = await economy_cog.get_wallet(ctx.author.id)
            w_target = await economy_cog.get_wallet(member.id)
            if w_author["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w_author['balance'])}.")
                return
            if w_target["balance"] < bet:
                await ctx.send(f"❌ **{member.display_name}** ma 3ndo kafi dial flous ({format_tad(w_target['balance'])} / {format_tad(bet)})!")
                return

        challenge_view = RPSChallengeView(ctx.author, member, self, bet=bet or 0)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"🔥 **5% Tax Burned:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Challenge dial Rock Paper Scissors!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="minesweeper", aliases=["ms", "demineur"], help="Hreb mn l9nabl (solo) wla l9a l9nabl (1v1).")
    @not_fraud()
    async def minesweeper(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        if member is None:
            view = MinesweeperSoloView(ctx.author)
            content = "💣 **Minesweeper (Solo)** — Hreb mn l mines o l9a safe squares kamlin!\nSafe: **0/16**"
            message = await ctx.send(content=content, view=view)
            view.message = message
            return

        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        challenge_view = MinesweeperChallengeView(ctx.author, member, self)
        content = (
            f"⚔️ **Challenge dial Minesweeper!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="wordle", aliases=["klma", "kelma"], help="9edder kelmat bach tl9a lkelma fach kanfkr.")
    @not_fraud()
    async def wordle(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        if member is None:
            secret = self.get_wordle_secret()
            view = WordleSoloView(ctx.author, secret, self)
            message = await ctx.send(content=view.get_content(), view=view)
            view.message = message
            return

        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        challenge_view = WordleChallengeView(ctx.author, member, self)
        content = (
            f"⚔️ **Challenge dial Wordle 1v1!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="hangman", aliases=["hm", "michna9a"], help="l9a lkelma 9bel matchne9.")
    @not_fraud()
    async def hangman(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        if member is None:
            secret = self.get_hangman_secret()
            view = HangmanSoloView(ctx.author, secret, self)
            message = await ctx.send(content=view.get_content(), view=view)
            view.message = message
            return

        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot..")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        challenge_view = HangmanChallengeView(ctx.author, member, self)
        content = (
            f"⚔️ **Challenge dial Hangman 1v1!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="trivia", aliases=["quiz", "as2ila"], help="Man sayarba7 2 drahm.")
    @not_fraud()
    async def trivia(self, ctx, round_duration: int = 20):
        if round_duration < 5:
            round_duration = 5
            time_display = "5s (Minimum)"
        else:
            time_display = f"{round_duration}s"

        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🧠 Trivia Quiz!",
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**",
            color=0x000000
        )
        signup_msg = await ctx.send(embed=signup_embed)
        await signup_msg.add_reaction(join_emoji)
        await asyncio.sleep(19)

        signup_msg = await ctx.channel.fetch_message(signup_msg.id)
        reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

        players = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    players.append(user)

        if not players:
            await signup_msg.edit(embed=discord.Embed(
                description="💨 7ta wa7d ma dkhel lgame ._.",
                color=0x000000
            ))
            return

        single_player = len(players) == 1
        hp = {p.id: 3 for p in players}
        scores = {p.id: 0 for p in players}
        active_players = list(players)

        if single_player:
            await signup_msg.edit(embed=discord.Embed(
                description="▶️ Bdina! 3ndek **3 HP**.",
                color=0x000000
            ))
        else:
            await signup_msg.edit(embed=discord.Embed(
                description="▶️ Bdina! Kola wa7d 3ndo **3 HP**.",
                color=0x000000
            ))
        await asyncio.sleep(2)

        question_pool = await fetch_trivia_batch(self.bot.session, amount=20)

        while len(active_players) > 0:
            if not single_player and len(active_players) == 1:
                winner = active_players[0]
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, winner.id, "trivia", earnings=40)
                    economy_cog = self.bot.get_cog("Economy")
                    if economy_cog:
                        asyncio.create_task(economy_cog.add_balance(winner.id, 40, context="Trivia Win"))
                await ctx.send(embed=discord.Embed(
                    description=f"🏆 {winner.mention} rbe7 lgame b **{scores[winner.id]} answers correct**!\n💰 Rbe7ti **40** {TAD_EMOJI} TAD!",
                    color=0x000000
                ))
                return

            for player in list(active_players):
                if not single_player and len(active_players) == 1:
                    break

                if not question_pool:
                    question_pool = await fetch_trivia_batch(self.bot.session, amount=20)

                q_data = question_pool.pop(0) if question_pool else {
                    "category": "General", "difficulty": "Medium",
                    "question": "What is the capital of France?",
                    "correct_answer": "Paris", "incorrect_answers": ["London", "Berlin", "Madrid"]
                }

                q_embed = discord.Embed(
                    title=f"❓ Question ({q_data['category']} • {q_data['difficulty']})",
                    description=f"**{q_data['question']}**\n\nDor dial: {player.mention}\n❤️ HP: **{hp[player.id]}/3**",
                    color=0x000000
                )
                q_embed.set_footer(text=f"Time: {round_duration}s")

                q_view = TriviaQuestionView(player, q_data, timeout_duration=round_duration)
                q_msg = await ctx.send(content=player.mention, embed=q_embed, view=q_view)
                q_view.message = q_msg

                await q_view.event.wait()
                await asyncio.sleep(1)

                if q_view.selected_correct:
                    scores[player.id] += 1
                    await ctx.send(embed=discord.Embed(
                        description=f"✅ {player.mention} jawb s7i7! (Score: **{scores[player.id]}**)",
                        color=0x000000
                    ))
                else:
                    hp[player.id] -= 1
                    corr_ans = q_data["correct_answer"]
                    if q_view.selected_label is None:
                        if hp[player.id] <= 0:
                            await ctx.send(embed=discord.Embed(
                                description=f"⌛ Sala lwe9t! 💥 {player.mention} t elimina (**0 HP**). Ljawab howa **{corr_ans}**.",
                                color=0x000000
                            ))
                            active_players.remove(player)
                        else:
                            await ctx.send(embed=discord.Embed(
                                description=f"⌛ Sala lwe9t {player.mention}: **-1 HP** (Ba9i: **{hp[player.id]} HP**). Ljawab howa **{corr_ans}**.",
                                color=0x000000
                            ))
                    else:
                        if hp[player.id] <= 0:
                            await ctx.send(embed=discord.Embed(
                                description=f"❌ Khata2! 💥 {player.mention} t elimina (**0 HP**). Ljawab howa **{corr_ans}**.",
                                color=0x000000
                            ))
                            active_players.remove(player)
                        else:
                            await ctx.send(embed=discord.Embed(
                                description=f"❌ Khata2 {player.mention}: **-1 HP** (Ba9i: **{hp[player.id]} HP**). Ljawab howa **{corr_ans}**.",
                                color=0x000000
                            ))

                await asyncio.sleep(2)

        if single_player:
            player = players[0]
            await ctx.send(embed=discord.Embed(
                description=f"🎯 Game Over {player.mention}! Score dialk: **{scores[player.id]} questions correct**.",
                color=0x000000
            ))

    @commands.command(name="typeracer", aliases=["tr", "type", "monkeytype"], help="Kteb text li ghan3tik bzerba bach trb7.")
    @not_fraud()
    async def typeracer(self, ctx, rounds: int = 3):
        rounds = max(1, min(10, rounds))
        join_emoji = "✅"

        signup_embed = discord.Embed(
            title="🏎️ TypeRacer!",
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nRounds: **{rounds}**\nMin Players: **2**",
            color=0x000000
        )
        signup_msg = await ctx.send(embed=signup_embed)
        await signup_msg.add_reaction(join_emoji)
        await asyncio.sleep(19)

        signup_msg = await ctx.channel.fetch_message(signup_msg.id)
        reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

        players = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    players.append(user)

        if len(players) < 2:
            await signup_msg.edit(embed=discord.Embed(
                description="❌ Khass minimum **2 players** bach tl3bo TypeRacer.",
                color=0x000000
            ))
            return

        scores = {p.id: 0 for p in players}
        wpm_records = {p.id: [] for p in players}
        active_players = list(players)

        await signup_msg.edit(embed=discord.Embed(
            description=f"▶️ **TypeRacer bda!** ({rounds} Rounds)\nPlayers: " + ", ".join(p.mention for p in players),
            color=0x000000
        ))
        await asyncio.sleep(2)

        for round_idx in range(1, rounds + 1):
            if len(active_players) < 2:
                break

            sentence = self.get_typeracer_text()

            # Ready countdown
            countdown_msg = await ctx.send(embed=discord.Embed(
                title=f"🏎️ Round {round_idx}/{rounds}",
                description="3...",
                color=0x000000
            ))
            await asyncio.sleep(1)
            await countdown_msg.edit(embed=discord.Embed(
                title=f"🏎️ Round {round_idx}/{rounds}",
                description="2...",
                color=0x000000
            ))
            await asyncio.sleep(1)
            await countdown_msg.edit(embed=discord.Embed(
                title=f"🏎️ Round {round_idx}/{rounds}",
                description="1... **GO! 🚀**",
                color=0x000000
            ))

            # Send image
            img_buf = render_typeracer_image(sentence)
            file = discord.File(img_buf, filename="typeracer.png")
            await ctx.send(file=file)

            start_time = time.perf_counter()
            round_winner = None
            round_elapsed = 0
            round_wpm = 0

            active_ids = {p.id for p in active_players}

            def check(m):
                if m.channel.id != ctx.channel.id or m.author.id not in active_ids:
                    return False
                content = m.content.strip()
                if content.lower() == "exitgame":
                    return True
                return content.lower() == sentence.lower()

            round_active = True
            while round_active and len(active_players) >= 2:
                time_left = max(1.0, 45.0 - (time.perf_counter() - start_time))
                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=time_left)
                except asyncio.TimeoutError:
                    await ctx.send(embed=discord.Embed(
                        description="⌛ **Sala lwe9t!** 7ta wa7d ma kteb lkelma s7i7a f had round.",
                        color=0x000000
                    ))
                    round_active = False
                    break

                if msg.content.strip().lower() == "exitgame":
                    quitter = next((p for p in active_players if p.id == msg.author.id), None)
                    if quitter:
                        active_players.remove(quitter)
                        active_ids.discard(quitter.id)
                        await ctx.send(f"🚪 {quitter.mention} khrej mn lgame.")
                        if len(active_players) < 2:
                            round_active = False
                            break
                    continue

                # Correct sentence typed!
                round_elapsed = time.perf_counter() - start_time
                round_wpm = round(((len(sentence) / 5) / (round_elapsed / 60))) if round_elapsed > 0 else 0
                round_winner = msg.author
                scores[msg.author.id] += 1
                wpm_records[msg.author.id].append(round_wpm)

                await ctx.send(embed=discord.Embed(
                    description=f"🎉 {round_winner.mention} rbe7 **Round {round_idx}** f **{round_elapsed:.2f}s** (**{round_wpm} WPM**)!",
                    color=0x000000
                ))
                round_active = False
                break

            await asyncio.sleep(3)

        # Game Over Leaderboard
        def player_rank_key(p):
            p_scores = scores.get(p.id, 0)
            avg_wpm = (sum(wpm_records[p.id]) / len(wpm_records[p.id])) if wpm_records[p.id] else 0
            return (p_scores, avg_wpm)

        ranked = sorted(players, key=player_rank_key, reverse=True)
        medals = ["🥇", "🥈", "🥉"] + [f"**#{i+1}**" for i in range(3, len(ranked))]

        lines = []
        for i, p in enumerate(ranked):
            p_scores = scores.get(p.id, 0)
            avg_wpm = round(sum(wpm_records[p.id]) / len(wpm_records[p.id])) if wpm_records[p.id] else 0
            lines.append(f"{medals[i]} {p.mention} — **{p_scores} wins** (Avg: **{avg_wpm} WPM**)")

        leaderboard_embed = discord.Embed(
            title="🏆 TypeRacer — Final Results",
            description="\n".join(lines),
            color=0x000000
        )
        if ranked:
            leaderboard_embed.set_footer(text=f"Winner: {ranked[0].display_name} 🎉")
            if scores.get(ranked[0].id, 0) > 0 and ctx.guild:
                await self.record_minigame_win(ctx.guild.id, ranked[0].id, "typeracer", earnings=40)
                economy_cog = self.bot.get_cog("Economy")
                if economy_cog:
                    asyncio.create_task(economy_cog.add_balance(ranked[0].id, 40, context="TypeRacer Win"))
        await ctx.send(embed=leaderboard_embed)


    # ============ REWORKED LEADERBOARD & CASINO COMMANDS ============

    async def get_main_leaderboard_embed(self, guild: discord.Guild) -> discord.Embed:
        async with self.bot.db.execute("""
            SELECT game, user_id, wins FROM minigame_leaderboard
            WHERE guild_id = ?
            ORDER BY wins DESC LIMIT 1
        """, (guild.id,)) as cursor:
            top_wins_row = await cursor.fetchone()

        async with self.bot.db.execute("""
            SELECT game, user_id, earnings FROM minigame_leaderboard
            WHERE guild_id = ?
            ORDER BY earnings DESC LIMIT 1
        """, (guild.id,)) as cursor:
            top_gains_row = await cursor.fetchone()

        embed = discord.Embed(
            title=f"🏆 Minigame Hall of Fame — {guild.name}",
            description="Khtar minigame mn lmenu lte7t bach tchouf rankings dialha.\n",
            color=0x000000
        )

        if top_wins_row and top_wins_row[2] > 0:
            g_key, u_id, w_count = top_wins_row
            d_name = MINIGAME_DISPLAY_MAP.get(g_key, (g_key.title(), []))[0]
            win_str = f"**{w_count}** win" if w_count == 1 else f"**{w_count}** wins"
            embed.add_field(
                name="🏆 Most Wins",
                value=f"**{d_name}** • <@{u_id}> ({win_str})",
                inline=False
            )
        else:
            embed.add_field(
                name="🏆 Most Wins",
                value="*No wins yet.*",
                inline=False
            )

        if top_gains_row and top_gains_row[2] > 0:
            g_key, u_id, e_count = top_gains_row
            d_name = MINIGAME_DISPLAY_MAP.get(g_key, (g_key.title(), []))[0]
            embed.add_field(
                name="💰 Most Earnings",
                value=f"**{d_name}** • <@{u_id}> ({format_tad(e_count)})",
                inline=False
            )
        else:
            embed.add_field(
                name="💰 Most Earnings",
                value="*No earnings yet.*",
                inline=False
            )

        embed.set_footer(text="Dropdown lte7t kat affichi ga3 l minigames available.")
        return embed

    @commands.command(name="leaderboard", aliases=["lb", "top"], help="Leaderboard ta3 lminigames (sat lb [game]).")
    async def leaderboard(self, ctx: commands.Context, *args):
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

            await view.show_game_page(interaction=None, game_key=target_key, sort_by="wins", page=0)

    @commands.command(aliases=['cf', 'drhm', 'flip'], help="Nlou7 derhem o chouf wach jak ras wla njma (sat coinflip [ras/njma] [bet:500]).")
    @not_fraud()
    async def coinflip(self, ctx: commands.Context, *args):
        bet, remaining = parse_bet_argument(*args)
        choice = remaining[0] if remaining else None

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Coinflip Bet")

        if choice is None:
            view = CoinflipView(ctx.author, self, bet=bet or 0)
            embed = discord.Embed(
                title="Coinflip Table",
                description="Khtar chno ghadi yji: **Ras (Heads)** wla **Njma (Tails)**?" + (f"\n\nStake: {format_tad(bet)}" if bet and bet > 0 else ""),
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
            await ctx.send("❌ Khtar `ras` (heads) wla `njma` (tails). Mital: `sat coinflip ras bet:100`")
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
                payout = bet * 2
                await economy_cog.add_balance(ctx.author.id, payout, context="Coinflip Win")
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "coinflip", earnings=bet)
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Payout", value=format_tad(payout), inline=True)
            else:
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Payout", value=format_tad(0), inline=True)
        elif won and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "coinflip")

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
        bet, _ = parse_bet_argument(*args)

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Dice Bet")

        roll = random.randint(1, 6)
        multipliers = {
            1: (0.0, "💥 Khesrti l bet! (0x)"),
            2: (0.5, "🤏 Rje3 lik ness l bet (0.5x)"),
            3: (1.0, "🤝 Rje3 lik floussek (1.0x)"),
            4: (1.2, "✨ Small Win! (1.2x)"),
            5: (1.5, "🔥 Good Win! (1.5x)"),
            6: (2.0, "👑 DOUBLE JACKPOT! (2.0x)")
        }

        mult, desc = multipliers[roll]

        payout = 0
        net_profit = 0
        if bet and bet > 0 and economy_cog:
            payout = int(round(bet * mult))
            net_profit = payout - bet
            if payout > 0:
                await economy_cog.add_balance(ctx.author.id, payout, context=f"Dice Payout ({mult}x)")
            if mult >= 1.2 and ctx.guild:
                await self.record_minigame_win(ctx.guild.id, ctx.author.id, "dice", earnings=max(0, net_profit))

        embed = discord.Embed(
            title=f"🎲 Dice: Rolled [ {roll} ]",
            description=f"{desc}\n\n📊 Multiplier: **{mult}x**",
            color=0x000000
        )

        if bet and bet > 0:
            embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
            embed.add_field(name="💵 Payout", value=format_tad(payout), inline=True)
        else:
            embed.set_footer(text="Bghiti t9emmer b flous? Kteb sat dice bet:100")

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
        bet, _ = parse_bet_argument(*args)

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
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
                    payout = int(round(bet * 2.5))
                    net_profit = payout - bet
                    await economy_cog.add_balance(ctx.author.id, payout, context="Blackjack Natural 21")
                    if ctx.guild:
                        await self.record_minigame_win(ctx.guild.id, ctx.author.id, "blackjack", earnings=net_profit)
                    outcome_str += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Payout: {format_tad(payout)})!"
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
        bet, _ = parse_bet_argument(*args)

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
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

        payout = 0
        net_profit = 0
        if bet and bet > 0 and economy_cog:
            payout = int(round(bet * payout_mult))
            net_profit = payout - bet
            if payout > 0:
                await economy_cog.add_balance(ctx.author.id, payout, context=f"Slots Payout ({payout_mult:.1f}x)")
            if payout_mult > 0 and ctx.guild:
                await self.record_minigame_win(ctx.guild.id, ctx.author.id, "slots", earnings=max(0, net_profit))
        elif payout_mult > 0 and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "slots")

        embed = discord.Embed(
            title="🎰 Slots Machine",
            description=(
                f"**[ {r1} | {r2} | {r3} ]**\n\n"
                f"**{outcome_title}**\n"
                f"📊 Multiplier: **{payout_mult:.1f}x**"
            ),
            color=0x000000
        )

        if bet and bet > 0:
            embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
            embed.add_field(name="💵 Payout", value=format_tad(payout), inline=True)

        await spin_msg.edit(embed=embed)

    @commands.command(aliases=["gems"], help="L9a gems o hreb 9bl matfrge3 (sat mines [bombs] [bet:500]).")
    @not_fraud()
    async def mines(self, ctx: commands.Context, *args):
        bet, remaining = parse_bet_argument(*args)
        bombs = 3
        if remaining and remaining[0].isdigit():
            bombs = int(remaining[0])

        bombs = max(1, min(bombs, 8))

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Mines Bet")

        view = MinesGambleView(ctx.author, self, bomb_count=bombs, bet=bet or 0)
        total_gems = (view.width * view.height) - bombs
        embed = discord.Embed(
            title="💣 Mines Table",
            description=(
                f"💎 Gems: **0/{total_gems}**\n"
                f"📈 Multiplier: **1.00x** (Next: **1.15x**)\n"
                f"💣 Bombs: **{bombs}**\n"
                + (f"💰 Stake: {format_tad(bet)}\n\n" if bet and bet > 0 else "\n")
                + "Click 3la ay tile bach t uncoveriha!"
            ),
            color=0x000000
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(aliases=["wheel"], help="9emmer 3la loun wla ra9m (sat roulette [choice] [bet:500]).")
    @not_fraud()
    async def roulette(self, ctx: commands.Context, *args):
        red_nums = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        black_nums = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

        bet, remaining = parse_bet_argument(*args)
        choice = remaining[0] if remaining else None

        if choice is None:
            embed = discord.Embed(
                title="🎡 European Roulette Table",
                description=(
                    "Khtar 3layach baghi t9emmer:\n"
                    "• `red` / `black` (2x payout)\n"
                    "• `even` / `odd` (2x payout)\n"
                    "• `1-18` (Low) / `19-36` (High) (2x payout)\n"
                    "• `green` / `0` (14x payout)\n"
                    "• Number direct `0` - `36` (36x payout)\n\n"
                    f"Mital: `{ctx.clean_prefix}roulette red bet:100` wla `{ctx.clean_prefix}roulette 7 bet:500`"
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        choice = choice.strip().lower()

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Roulette Bet")

        spin_embed = discord.Embed(
            description="🔄 *Roulette kaddor...*" + (f"\n\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""),
            color=0x000000
        )
        spin_msg = await ctx.send(embed=spin_embed)

        await asyncio.sleep(5.0)

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

        if choice in ["red", "r", "7mer", "7mr"] and color_name == "red":
            won = True
            mult = 2.0
        elif choice in ["black", "b", "k7el", "k7l", "k7al"] and color_name == "black":
            won = True
            mult = 2.0
        elif choice in ["green", "g", "khder", "0", "zero"] and color_name == "green":
            won = True
            mult = 14.0
        elif choice in ["even", "zawji"] and landed_num > 0 and landed_num % 2 == 0:
            won = True
            mult = 2.0
        elif choice in ["odd", "fardi"] and landed_num % 2 != 0:
            won = True
            mult = 2.0
        elif choice in ["1-18", "low", "fo9"] and 1 <= landed_num <= 18:
            won = True
            mult = 2.0
        elif choice in ["19-36", "high", "ta7t", "t7t"] and 19 <= landed_num <= 36:
            won = True
            mult = 2.0
        elif choice.isdigit() and int(choice) == landed_num:
            won = True
            mult = 36.0

        payout = 0
        net_profit = 0
        if bet and bet > 0 and economy_cog:
            if won:
                payout = int(round(bet * mult))
                net_profit = payout - bet
                await economy_cog.add_balance(ctx.author.id, payout, context=f"Roulette Win ({mult:.0f}x)")
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "roulette", earnings=net_profit)
            else:
                net_profit = -bet
        elif won and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "roulette")

        outcome_title = f"🏆 Rbe7ti! ({mult:.0f}x)" if won else "💥 Khesrti!"
        embed = discord.Embed(
            title=f"🎡 Roulette: {color_emoji} **{landed_num} ({color_name.upper()})**",
            description=(
                f"Lkhtiyar: `{choice}` • Landed on: {color_emoji} **{landed_num}**\n\n"
                f"**{outcome_title}**"
            ),
            color=0x000000
        )

        if bet and bet > 0:
            embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
            embed.add_field(name="💵 Payout", value=format_tad(payout), inline=True)

        await spin_msg.edit(embed=embed, attachments=[])

    @commands.command(aliases=["hl"], help="9emmer wach lwr9a jaya Higher wla Lower (sat higherlower [bet:500]).")
    @not_fraud()
    async def higherlower(self, ctx: commands.Context, *args):
        bet, _ = parse_bet_argument(*args)

        economy_cog = self.bot.get_cog("Economy")
        if bet and bet > 0 and economy_cog:
            w = await economy_cog.get_wallet(ctx.author.id)
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
    await bot.add_cog(Fun(bot))