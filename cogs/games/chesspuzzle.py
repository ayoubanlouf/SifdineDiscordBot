from __future__ import annotations
import os
import json
import random
import asyncio
from typing import Optional, Union

import chess
import discord
from discord.ui import Modal, View, Button, TextInput

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import clear_user_game
from cogs.games.chess import render_chess_board

# ============ CHESS PUZZLE (ONE-MOVE TACTICS) CLASSES ============

_CHESS_PUZZLES_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "chess_puzzles.json"))
_chess_puzzles_dataset = None

def _load_chess_puzzles():
    global _chess_puzzles_dataset
    if _chess_puzzles_dataset is None:
        if os.path.exists(_CHESS_PUZZLES_PATH):
            try:
                with open(_CHESS_PUZZLES_PATH, "r", encoding="utf-8") as f:
                    _chess_puzzles_dataset = json.load(f)
            except Exception:
                _chess_puzzles_dataset = []
        else:
            _chess_puzzles_dataset = []
    return _chess_puzzles_dataset


class ChessPuzzleModal(Modal, title="7ell Chess Puzzle"):
    move_input = TextInput(
        label="Dkhel l move ta3k (SAN ola UCI)",
        placeholder="mtalan Qh7#, Nf7+, Rd8#, d1h5",
        required=True,
        max_length=15
    )

    def __init__(self, puzzle_view: "ChessPuzzleView"):
        super().__init__()
        self.puzzle_view = puzzle_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.puzzle_view.process_guess(interaction, self.move_input.value.strip())


class ChessPuzzleView(View):
    def __init__(self, author: Union[discord.Member, discord.User], cog: "Minigames", puzzle: Optional[dict] = None):
        super().__init__(timeout=120)
        self.author = author
        self.cog = cog
        pool = _load_chess_puzzles()
        self.puzzle = puzzle or (random.choice(pool) if pool else {})
        self.board = chess.Board(self.puzzle.get("fen", chess.STARTING_FEN))
        self.solved = False
        self.message: Optional[discord.Message] = None
        self._build_initial_buttons()

    def _build_initial_buttons(self):
        self.clear_items()
        submit_btn = Button(label="Submit Move", style=discord.ButtonStyle.primary, emoji="♟️")
        submit_btn.callback = self._on_submit_clicked
        quit_btn = Button(label="Quit", style=discord.ButtonStyle.danger, emoji="🚪")
        quit_btn.callback = self._on_quit_clicked
        self.add_item(submit_btn)
        self.add_item(quit_btn)

    def _build_result_buttons(self):
        self.clear_items()
        again_btn = Button(label="Play Again", style=discord.ButtonStyle.success, emoji="🔄")
        again_btn.callback = self._on_play_again_clicked
        quit_btn = Button(label="Quit", style=discord.ButtonStyle.danger, emoji="🚪")
        quit_btn.callback = self._on_quit_clicked
        self.add_item(again_btn)
        self.add_item(quit_btn)

    async def _on_submit_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l puzzle mashi dialk!", ephemeral=True)
            return
        if self.solved:
            await interaction.response.send_message("Had l puzzle deja jawbti 3liha!", ephemeral=True)
            return
        await interaction.response.send_modal(ChessPuzzleModal(self))

    async def _on_quit_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l puzzle mashi dialk!", ephemeral=True)
            return
        self.solved = True
        self.stop()
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        embed = discord.Embed(description="🛑 **Puzzle game salat! Chokran 3la l mosharaka.**", color=0x000000)
        if interaction.response.is_done():
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        self.solved = True
        self.stop()
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        embed = discord.Embed(description="🛑 **Puzzle game salat! Chokran 3la l mosharaka.**", color=0x000000)
        if self.message:
            try:
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass
        return "🚪 Kherjti mn **Chess Puzzle**!"

    async def _on_play_again_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l puzzle mashi dialk!", ephemeral=True)
            return
        await interaction.response.defer()
        pool = _load_chess_puzzles()
        new_pool = [p for p in pool if p.get("id") != self.puzzle.get("id")]
        self.puzzle = random.choice(new_pool) if new_pool else (random.choice(pool) if pool else {})
        self.board = chess.Board(self.puzzle.get("fen", chess.STARTING_FEN))
        self.solved = False
        self._build_initial_buttons()
        board_file = await self.generate_board_file()
        embed = self.build_puzzle_embed()
        await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, attachments=[board_file], view=self)

    async def generate_board_file(self) -> discord.File:
        loop = asyncio.get_running_loop()
        orientation = chess.BLACK if self.puzzle.get("turn") == "black" else chess.WHITE
        buffer = await loop.run_in_executor(None, render_chess_board, self.board, orientation)
        return discord.File(buffer, filename="chess_puzzle.png")

    def build_puzzle_embed(self) -> discord.Embed:
        turn_str = "⚪ **White to Move!**" if self.puzzle.get("turn") == "white" else "⚫ **Black to Move!**"
        embed = discord.Embed(
            title="♟️ One-Move Chess Tactic",
            description=(
                f"{turn_str}\n\n"
                f"🎯 **Objective:** L9a l best tactical move!\n"
                f"💰 **Reward:** **+100** {TAD_EMOJI} TAD\n\n"
                f"Clicki 3la **Submit Move** bach tdkhel l move dialk."
            ),
            color=0x000000
        )
        embed.set_image(url="attachment://chess_puzzle.png")
        embed.set_footer(text=f"Player: {self.author.display_name}")
        return embed

    async def process_guess(self, interaction: discord.Interaction, move_str: str):
        if interaction.user.id != self.author.id:
            await interaction.followup.send("❌ Had l puzzle mashi dialk!", ephemeral=True)
            return
        if self.solved:
            return

        self.solved = True
        parsed_move = None

        # Try parsing user move string via SAN or UCI
        try:
            parsed_move = self.board.parse_san(move_str)
        except Exception:
            try:
                parsed_move = chess.Move.from_uci(move_str.lower())
                if parsed_move not in self.board.legal_moves:
                    parsed_move = None
            except Exception:
                clean_str = move_str.replace("#", "").replace("+", "").strip()
                try:
                    parsed_move = self.board.parse_san(clean_str)
                except Exception:
                    parsed_move = None

        is_correct = False
        played_san = move_str
        is_checkmate = False
        if parsed_move and parsed_move in self.board.legal_moves:
            test_b = self.board.copy()
            test_b.push(parsed_move)
            is_checkmate = test_b.is_checkmate()
            # Correct if it matches the puzzle solution move OR delivers checkmate
            if parsed_move.uci() == self.puzzle.get("solution_uci") or is_checkmate:
                is_correct = True
                try:
                    played_san = self.board.san(parsed_move)
                except Exception:
                    played_san = move_str
                self.board.push(parsed_move)

        economy_cog = self.cog.bot.get_cog("Economy")
        eco_msg = ""
        theme_info = self.puzzle.get("theme_display", "Tactical Advantage")
        rating_info = f" • Rating: ~{self.puzzle.get('rating')}" if self.puzzle.get("rating") else ""

        if is_correct:
            if economy_cog:
                net, tax = await economy_cog.apply_tax_and_add_balance(
                    self.author.id, 100, context="Chess Puzzle Win"
                )
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (🔥 `{tax:,}` TAD tax burned)!"
                if interaction.guild:
                    await self.cog.record_minigame_win(interaction.guild.id, self.author.id, "chesspuzzle", earnings=net)

            outcome_header = "🎯 **S7I7! MHYEEEB!**"
            mate_tag = " (🎯 Checkmate!)" if is_checkmate else " (🎯 Best Tactical Move!)"
            details = (
                f"{outcome_header}{eco_msg}\n\n"
                f"🎮 L move dialek: **{played_san}**{mate_tag}\n"
                f"💡 Motif: **{theme_info}**{rating_info}"
            )
        else:
            # Play the correct solution move on the board to reveal it to the player
            sol_move = None
            try:
                sol_move = self.board.parse_san(self.puzzle.get("solution_san", ""))
            except Exception:
                try:
                    sol_move = chess.Move.from_uci(self.puzzle.get("solution_uci", ""))
                except Exception:
                    pass
            if sol_move and sol_move in self.board.legal_moves:
                self.board.push(sol_move)

            outcome_header = "❌ **GHALAT! Majbtihach.**"
            details = (
                f"{outcome_header}\n\n"
                f"🎮 L move dialek: `{move_str}`\n"
                f"🏆 L move s7i7: **{self.puzzle.get('solution_san')}**\n"
                f"💡 Motif: **{theme_info}**{rating_info}\n"
                f"💰 Reward: **0** TAD"
            )

        self._build_result_buttons()
        board_file = await self.generate_board_file()

        res_embed = discord.Embed(
            title="♟️ Chess Tactic — Natija",
            description=details,
            color=0x000000
        )
        res_embed.set_image(url="attachment://chess_puzzle.png")
        res_embed.set_footer(text="Clicki 🔄 Play Again bach t7ell puzzle khor ola 🚪 Quit")

        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=res_embed,
            attachments=[board_file],
            view=self
        )

    def stop(self):
        if self.cog and hasattr(self.cog, "bot") and self.author:
            clear_user_game(self.cog.bot, self.author.id)
        super().stop()

    async def on_timeout(self):
        self.stop()

