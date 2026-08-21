import asyncio
import random
import io
import chess
from typing import Optional
import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
from PIL import Image, ImageDraw
import aiohttp
import time
import json
import requests
from converters import FuzzyMember

# Compatibility shim for newer akinator API variations
import akinator
import akinator.exceptions
if not hasattr(akinator.exceptions, 'CantGoBackAnyFurther'):
    class _CantGoBackAnyFurther(Exception):
        pass
    akinator.exceptions.CantGoBackAnyFurther = _CantGoBackAnyFurther

from akinator import AsyncAkinator
from akinator.async_client import AsyncClient
from typing import Dict, Any
import asyncio

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
        label="Dkhel l-move dialek (SAN ola UCI)",
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
    def __init__(self, player_white: FuzzyMember, player_black: FuzzyMember, is_bot_game: bool = False):
        super().__init__(timeout=120)
        self.player_white = player_white
        self.player_black = player_black
        self.is_bot_game = is_bot_game
        self.board = chess.Board()
        self.current_turn = player_white
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self.draw_offered_by: Optional[FuzzyMember] = None

    def get_current_color_symbol(self) -> str:
        return "⚪ (Byed)" if self.board.turn == chess.WHITE else "⚫ (Khel)"

    def is_current_player(self, user: FuzzyMember) -> bool:
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
            embed.description = f"⏰ **Sala lwe9t! {self.current_turn.mention} khser b l-inactivity. {winner.mention} rbe7!**"
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
            await interaction.response.send_message(f"❌ **L-move ghalat (`{move_str}`)!** khdem b SAN (mtalan `e4`, `Nf3`) ola UCI (mtalan `e2e4`).", ephemeral=True)
            return

        # Push Human Move
        self.board.push(parsed_move)

        # Check win/draw
        if self.board.is_game_over():
            self.game_over = True
            self.stop()
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
            await interaction.response.send_message("Nta mashi f had l-match.", ephemeral=True)
            return

        if self.is_bot_game:
            await interaction.response.send_message("Ma9derch n-accepti ta3adol daba.", ephemeral=True)
            return

        if self.draw_offered_by is None:
            self.draw_offered_by = interaction.user
            await interaction.response.send_message(f"🤝 **{interaction.user.mention} i9tra7 ta3adol!** Lakher i-clicki 3la 'Ta3adol' bach i-accepti.", ephemeral=False)
        elif self.draw_offered_by != interaction.user:
            self.game_over = True
            self.stop()
            embed = self.build_embed()
            embed.description = f"🤝 **Match Ta3adol b l-itifaq!**"
            board_file = await self.generate_board_file()
            await interaction.response.edit_message(embed=embed, attachments=[board_file], view=None)
        else:
            await interaction.response.send_message("Derti deja l-i9tira7, sber lakher i-jawab.", ephemeral=True)

    @discord.ui.button(label="Steslem", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def resign_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user not in (self.player_white, self.player_black):
            await interaction.response.send_message("Nta mashi f had l-match.", ephemeral=True)
            return

        self.game_over = True
        self.stop()
        winner = self.player_black if interaction.user == self.player_white else self.player_white
        
        embed = self.build_embed()
        embed.description = f"🏳️ **{interaction.user.mention} steslem! {winner.mention} rbe7!**"
        board_file = await self.generate_board_file()
        await interaction.response.edit_message(embed=embed, attachments=[board_file], view=None)

class ChessChallengeView(View):
    def __init__(self, challenger: FuzzyMember, challenged: FuzzyMember):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged

    @discord.ui.button(label="Qbel", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Had l-challenge mashi lik!", ephemeral=True)
            return

        players = [self.challenger, self.challenged]
        random.shuffle(players)
        
        game_view = ChessView(players[0], players[1], is_bot_game=False)
        board_file = await game_view.generate_board_file()
        
        await interaction.response.edit_message(content=None, embed=game_view.build_embed(), attachments=[board_file], view=game_view)
        game_view.message = interaction.message
        self.stop()

    @discord.ui.button(label="Refed", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Had l-challenge mashi lik!", ephemeral=True)
            return
        
        await interaction.response.edit_message(content=f"❌ {self.challenged.mention} rfed l-match dial chess.", view=None)
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

    def __init__(self, player_x: FuzzyMember, player_o: FuzzyMember, is_bot_game: bool = False, turn_timeout: int = 60):
        super().__init__(timeout=120)
        self.player_x = player_x
        self.player_o = player_o
        self.is_bot_game = is_bot_game
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
                return "🤝 **Ta3adol!**"
            elif winner == "X":
                return f"🏆 **{self.player_x.mention} (X) rbe7!**"
            elif winner == "O":
                if self.is_bot_game:
                    return "🤖 **Rb7tk!**"
                else:
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

        for y in range(3):
            for x in range(3):
                if self.board[y][x] == " ":
                    self.board[y][x] = "O"
                    score = self.minimax(False)
                    self.board[y][x] = " "
                    if score > best_score:
                        best_score = score
                        best_move = (x, y)

        if best_move:
            x, y = best_move
            self.board[y][x] = "O"
            self.update_button(x, y, "O")

    def minimax(self, is_maximizing: bool) -> int:
        winner = self.check_winner()
        if winner == "O":
            return 1
        elif winner == "X":
            return -1
        elif winner == "draw":
            return 0

        if is_maximizing:
            best_score = -float('inf')
            for y in range(3):
                for x in range(3):
                    if self.board[y][x] == " ":
                        self.board[y][x] = "O"
                        score = self.minimax(False)
                        self.board[y][x] = " "
                        best_score = max(score, best_score)
            return best_score
        else:
            best_score = float('inf')
            for y in range(3):
                for x in range(3):
                    if self.board[y][x] == " ":
                        self.board[y][x] = "X"
                        score = self.minimax(True)
                        self.board[y][x] = " "
                        best_score = min(score, best_score)
            return best_score

    async def button_callback(self, interaction: discord.Interaction):
        button_id = interaction.data.get("custom_id", "")
        if not button_id.startswith("ttt_"):
            return

        try:
            _, x_str, y_str = button_id.split("_")
            x, y = int(x_str), int(y_str)
        except (ValueError, IndexError):
            return

        if self.game_over:
            await interaction.response.send_message("Had lmatch deja sala..", ephemeral=True)
            return

        if self.board[y][x] != " ":
            await interaction.response.send_message("Dak lmorba3 3amr ._.", ephemeral=True)
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
    def __init__(self, challenger: FuzzyMember, challenged: FuzzyMember):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.accepted = True
        self.stop()

        players = [self.challenger, self.challenged]
        random.shuffle(players)
        player_x, player_o = players[0], players[1]

        game_view = TicTacToeView(player_x, player_o, is_bot_game=False)
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
    def __init__(self, player_red: FuzzyMember, player_yellow: FuzzyMember, is_bot_game: bool = False, turn_timeout: int = 60):
        super().__init__(timeout=120)
        self.player_red = player_red
        self.player_yellow = player_yellow
        self.is_bot_game = is_bot_game
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
                return f"{board_text}\n\n🤝 **Ta3adol!**"
            elif winner == "🔴":
                return f"{board_text}\n\n🏆 **{self.player_red.mention} (🔴) rbe7!**"
            elif winner == "🟡":
                if self.is_bot_game:
                    return f"{board_text}\n\n🤖 **Rb7tk!**"
                else:
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

    def make_bot_move(self):
        valid_cols = [c for c in range(7) if self.board[0][c] == "⚪"]
        if not valid_cols:
            return

        # 1. Check if bot can win immediately
        for c in valid_cols:
            for r in reversed(range(6)):
                if self.board[r][c] == "⚪":
                    self.board[r][c] = "🟡"
                    if self.check_winner() == "🟡":
                        if r == 0:
                            self.get_button(c).disabled = True
                        return
                    self.board[r][c] = "⚪"
                    break

        # 2. Check if player can win next turn and block
        for c in valid_cols:
            for r in reversed(range(6)):
                if self.board[r][c] == "⚪":
                    self.board[r][c] = "🔴"
                    if self.check_winner() == "🔴":
                        self.board[r][c] = "🟡"
                        if r == 0:
                            self.get_button(c).disabled = True
                        return
                    self.board[r][c] = "⚪"
                    break

        # 3. Otherwise pick center or random column
        preferred = [3, 2, 4, 1, 5, 0, 6]
        best_col = next((c for c in preferred if c in valid_cols), random.choice(valid_cols))
        self.drop_piece(best_col, "🟡")

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
            await interaction.response.send_message("Had l-colonne 3amr ._.", ephemeral=True)
            return

        # Check win for current human player move
        winner = self.check_winner()
        if winner:
            self.game_over = True
            self.disable_all_buttons()
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
            if hasattr(self, '_timeout_task') and self._timeout_task:
                self._timeout_task.cancel()
            self._timeout_task = asyncio.create_task(self._turn_timeout_task())

        await interaction.response.edit_message(content=self.get_status_content(), view=self)


class ConnectFourChallengeView(View):
    """View for the Connect Four multiplayer challenge acceptance phase."""
    def __init__(self, challenger: FuzzyMember, challenged: FuzzyMember):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.accepted = True
        self.stop()

        players = [self.challenger, self.challenged]
        random.shuffle(players)
        player_red, player_yellow = players[0], players[1]

        game_view = ConnectFourView(player_red, player_yellow, is_bot_game=False)
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


# ============ AKINATOR UI CLASSES (Module Level) ============

class AkinatorButton(Button):
    def __init__(self, label: str, custom_id: str, style: discord.ButtonStyle, emoji: str, row: int):
        super().__init__(label=label, custom_id=custom_id, style=style, emoji=emoji, row=row)


class AkinatorView(View):
    def __init__(self, player: FuzzyMember, timeout: float = 60.0):
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


# ============ MAIN COG ============

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_combo(self):
        url = "https://random-word-api.herokuapp.com/word?number=50"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    words = await resp.json()
                    for word in words:
                        if len(word) >= 3:
                            i = random.randint(0, len(word) - 3)
                            return word[i:i + 3]
        return None

    def is_english_word(self, word):
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        try:
            r = json.loads(requests.get(url).content)[0]['word']
            return True
        except:
            return False

    @commands.command(aliases=["swl", "sewel", "swel"], help="Nswlk so2al khssk tjawb 3lih b sara7a.")
    async def truth(self, ctx):
        url = 'https://api.truthordarebot.xyz/v1/truth'
        r = json.loads(requests.get(url).content)['question']
        await ctx.send(r)

    @commands.command(aliases=["7kem", "7km", "hkm", "hkem"], help="N7kem 3lik b 7ekma khssk dirha darori.")
    async def dare(self, ctx):
        url = 'https://api.truthordarebot.xyz/v1/dare'
        r = json.loads(requests.get(url).content)['question']
        await ctx.send(r)

    @commands.command(aliases=["wyr", "khyrni"], help="Law khayarouk okda.")
    async def wouldyourather(self, ctx):
        url = 'https://api.truthordarebot.xyz/v1/wyr'
        r = json.loads(requests.get(url).content)['question']
        await ctx.send(r)

    @commands.command(name="flags", aliases=["gtf"], help="Guess the flag okda.")
    async def flags(self, ctx, round_duration:int=15):
        if round_duration < 5:
            await ctx.send(f"Lminimum tlwe9t howa 5s.")
            round_duration = 5

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
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nTime: <t:{int(time.time() + 21)}:R>",
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

        start_embed = discord.Embed(
            description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.",
            color=0x000000
        )
        await signup_msg.edit(embed=start_embed)
        await asyncio.sleep(2)

        while len(active_players) > 0:
            if not single_player and len(active_players) == 1:
                winner = active_players[0]
                win_embed = discord.Embed(
                    description=f"🏆 {winner.mention} rbe7 lgame!",
                    color=0x000000
                )
                await ctx.send(embed=win_embed)
                return

            for player in list(active_players):
                if not single_player and len(active_players) == 1:
                    break

                target = random.choice(country_pool)
                correct_name = target["name"]
                flag_url = f"https://flagcdn.com/w320/{target['code']}.png"

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

                        if msg.content.strip().lower() == correct_name.lower():
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
    async def blacktea(self, ctx, round_duration:int=10):
        if round_duration < 5:
            await ctx.send(f"Lminimum tlwe9t howa 5s.")
            round_duration = 5

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="☕ BlackTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nTime: <t:{int(time.time() + 21)}:R>",
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
                    combo = await self.get_combo()

                    await ctx.send(f"❓ {player.mention} kteb kelma fiha: **{combo.upper()}**")

                    def check(message):
                        return message.author == player and message.channel == ctx.channel and combo in message.content.lower() and self.is_english_word(
                            message.content.lower()) == True

                    try:
                        word = await self.bot.wait_for('message', check=check, timeout=15)
                        if word:
                            await word.add_reaction('✅')
                    except asyncio.TimeoutError:
                        lives[player.id] -= 1
                        if lives[player.id] > 0:
                            await ctx.send(f"⌛ Sala lwe9t: -1 HP (Left: **{lives[player.id]} HP**)")
                        else:
                            await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**)")
            else:
                while len(active_players) > 1:
                    for player in list(active_players):
                        combo = await self.get_combo()

                        await ctx.send(f"❓ {player.mention} kteb kelma fiha: **{combo.upper()}**")

                        def check(message):
                            return message.author == player and message.channel == ctx.channel and combo in message.content.lower() and self.is_english_word(
                                message.content.lower()) == True

                        try:
                            word = await self.bot.wait_for('message', check=check, timeout=15)
                            if word:
                                await word.add_reaction('✅')
                        except asyncio.TimeoutError:
                            lives[player.id] -= 1
                            if lives[player.id] > 0:
                                await ctx.send(f"⌛ Sala lwe9t: -1 HP (Left: **{lives[player.id]} HP**)")
                            else:
                                await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**)")
                                active_players.remove(player)

                if active_players:
                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {active_players[0].mention} rbe7 lgame!",
                        color=0x000000
                    ))
        except Exception as e:
            print(e)

    @commands.command(help="Kteb kelma fiha l7orof li ghan3tik bzerba.")
    async def greentea(self, ctx, round_duration:int=10):
        if round_duration < 5:
            await ctx.send(f"Lminimum tlwe9t howa 5s.")
            round_duration = 5

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="🍵 GreenTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nTime: <t:{int(time.time() + 20)}:R>",
                color=0x000000
            )
            start = await ctx.send(embed=signup_embed)
            await start.add_reaction(join_emoji)
            await asyncio.sleep(19)

            await start.edit(embed=discord.Embed(
                description="▶️ Bdina!",
                color=0x000000
            ))

            signup_msg = await ctx.channel.fetch_message(start.id)
            reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

            players = []
            errors = []
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        players.append(user)

            game = True
            if len(players) <= 1:
                game = False
                await start.edit(embed=discord.Embed(
                    description="💨 7ta wa7d ma dkhel lgame ._.",
                    color=0x000000
                ))
                errors.append("not enough players")

            points = {p.id: 0 for p in players}

            await ctx.send(embed=discord.Embed(
                description="🔟 10 rounds total!",
                color=0x000000
            ))
            await asyncio.sleep(2)

            rounds = 0
            while game and rounds < 10:
                rounds += 1
                combo = await self.get_combo()
                if not combo:
                    await ctx.send('error')
                    continue

                await ctx.send(embed=discord.Embed(
                    description=f"Kteb kelma fiha: **{combo.upper()}**\n⏱️ Round **{rounds}/10**",
                    color=0x000000
                ))

                def check(message):
                        return message.author in players and message.channel == ctx.channel and combo in message.content.lower() and self.is_english_word(
                            message.content.lower()) == True

                try:
                    word = await self.bot.wait_for('message', check=check, timeout=round_duration)
                    if word:
                        await word.add_reaction('✅')
                        fast = word.author
                        points[fast.id] += 1
                        await asyncio.sleep(3)
                        await ctx.send(embed=discord.Embed(
                                description=f"✅ {fast.mention} 5da 1 point. (Total: **{points[fast.id]} pts**)",
                                color=0x000000
                            ))

                except asyncio.TimeoutError:
                    await ctx.send(embed=discord.Embed(
                        description="⌛ Sala lwe9t. 7ta wa7d ma 5da lpoint.",
                        color=0x000000
                    ))

            if len(errors) > 0:
                try:
                    await start.clear_reactions()
                except Exception:
                    pass
            else:
                if points:
                    maxpoints = max(points.values())
                    winners = [pid for pid, pts in points.items() if pts == maxpoints]
                    if len(winners) == 1:
                        winner = self.bot.get_user(winners[0])
                        await ctx.send(embed=discord.Embed(
                            description=f"🏆 {winner.mention} rbe7 lgame b **{maxpoints} pts**!",
                            color=0x000000
                        ))
                    else:
                        mention_str = " o ".join(f"<@{wid}>" for wid in winners)
                        await ctx.send(embed=discord.Embed(
                            description=f"🏆 {mention_str} ta3adlo b **{maxpoints} pts**!",
                            color=0x000000
                        ))
        except Exception as e:
            print(e)

    @commands.command(name="tictactoe", aliases=["ttt"], help="X/O las9 3 bach trbe7.")
    async def tictactoe(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        if member is None:
            view = TicTacToeView(ctx.author, ctx.bot.user, is_bot_game=True)
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

        players = [ctx.author, member]
        random.shuffle(players)
        player_x, player_o = players[0], players[1]

        challenge_view = ChallengeView(ctx.author, member)
        content = (
            f"⚔️ **Tic-Tac-Toe Challenge!**\n"
            f"{player_x.mention} (❌ X) vs {player_o.mention} (⭕ O)\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message

    @commands.command(name="connectfour", aliases=["c4", "connect4"], help="Las9 4 bach trbe7.")
    async def connectfour(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        if member is None:
            view = ConnectFourView(ctx.author, ctx.bot.user, is_bot_game=True)
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

        challenge_view = ConnectFourChallengeView(ctx.author, member)
        content = (
            f"⚔️ **Connect Four Challenge!**\n"
            f"{ctx.author.mention} vs {member.mention}\n\n"
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

    @commands.command(name="chess", help="l3eb shitranj hh")
    async def chess_cmd(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        # Single Player vs Bot
        if member is None:
            game_view = ChessView(ctx.author, ctx.bot.user, is_bot_game=True)
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

        # Multiplayer Challenge
        challenge_view = ChessChallengeView(ctx.author, member)
        content = f"⚔️ **Challenge dial Chess!**\n{ctx.author.mention} challenga {member.mention} f match dial Chess!\n\n{member.mention}, t accepti?"
        await ctx.send(content=content, view=challenge_view)


async def setup(bot):
    await bot.add_cog(Fun(bot))