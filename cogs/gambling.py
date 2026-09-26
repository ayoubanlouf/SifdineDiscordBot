import asyncio
import random
import io
import os
import aiohttp
import time
import json
import math
import uuid
import urllib.parse
from typing import Optional, Union

import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from converters import FuzzyMember
from cogs.economy import (
    parse_bet_argument, format_tad, TAD_EMOJI, calculate_pvp_payout,
    not_fraud, TAX_RATE, get_current_week_start_ts, get_next_week_start_ts
)
from cogs.games.helpers import (
    record_minigame_win, record_minigame_loss,
    is_user_in_game, set_user_in_game, clear_user_game
)


from cogs.casino.blackjack import (
    SUITS, RANKS, RANK_NAME_MAP, SUIT_NAME_MAP, RANK_VALUES,
    create_bj_deck, calculate_bj_score, format_bj_card, render_bj_table,
    BlackjackView
)
from cogs.casino.mines import MinesGambleButton, MinesGambleView
from cogs.casino.tower import (
    get_tower_door, render_tower_board, TowerDoorButton, TowerCashoutButton,
    TowerGameView
)
from cogs.casino.higherlower import draw_hl_card, get_hl_card_file, HigherLowerView
from cogs.casino.coinflip import CoinflipView
from cogs.casino.dice import render_dice_composite, DiceRollView
from cogs.casino.slots import spin_slots, build_slots_embed
from cogs.casino.roulette import (
    parse_roulette_choice, spin_roulette, ROULETTE_RED_NUMS, ROULETTE_BLACK_NUMS
)



# ============ INTERACTIVE 2-TIER LEADERBOARD UI ============

MINIGAME_DISPLAY_MAP = {
    "flags": ("🚩 Flags", ["flags", "flag", "rayat", "gtf"]),
    "craftingtable": ("🔨 CraftingTable", ["craftingtable", "crafting", "craft", "recipe"]),
    "guessthecar": ("🚗 GuessTheCar", ["guessthecar", "guesscar", "cars", "carguess", "carmodels", "models", "tomobil", "tomobila"]),
    "blacktea": ("☕ BlackTea", ["blacktea", "bt", "black", "jklm"]),
    "greentea": ("🍵 GreenTea", ["greentea", "gt", "green"]),
    "redtea": ("🔴 RedTea", ["redtea", "rt", "red"]),
    "unscramble": ("🧩 Unscramble", ["unscramble", "scramble", "fekk", "fek"]),
    "blackjack": ("🃏 Blackjack", ["blackjack", "bj", "21"]),
    "slots": ("🎰 Slots", ["slots", "slot", "machine"]),
    "mines": ("💣 Mines", ["mines", "gems", "gemhunt"]),
    "roulette": ("🎡 Roulette", ["roulette", "wheel", "roul"]),
    "higherlower": ("🃏 HigherLower", ["higherlower", "hl", "cardduel"]),
    "coinflip": ("🪙 Coinflip", ["coinflip", "cf", "drhm", "drhem"]),
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
    "geoguessr": ("🌍 GeoGuessr", ["geoguessr", "geo", "geoguesser", "geoguess"]),
    "guesstherank": ("🎖️ GuessTheRank", ["guesstherank", "gtr", "guessrank"]),
    "chesspuzzle": ("🧩 ChessPuzzle", ["chesspuzzle", "puzzle", "cpuzzle", "chesstactic", "tactic", "chessquiz"]),
    "tower": ("🏰 Tower", ["tower", "doors", "lborj", "lbiban"]),
}

class LeaderboardSelect(discord.ui.Select):
    def __init__(self, placeholder: str, options: list[discord.SelectOption], row: int = 0):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        view: LeaderboardInteractiveView = self.view
        selected_game = self.values[0]
        await view.show_game_page(interaction, selected_game, timeframe=view.timeframe, page=0)


class LeaderboardInteractiveView(discord.ui.View):
    def __init__(self, ctx, cog, minigame_map: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.minigame_map = minigame_map
        self.current_game: Optional[str] = None
        self.timeframe: str = "weekly"  # "weekly" or "alltime"
        self.current_page: int = 0
        self.per_page: int = 10
        self.rows_cache = []
        self.message: Optional[discord.Message] = None

        self.setup_overview()

    def setup_overview(self):
        self.clear_items()
        self.current_game = None

        casino_keys = {"blackjack", "slots", "mines", "roulette", "higherlower", "coinflip", "dice", "tower"}
        casino_options = []
        puzzle_options = []

        for game_key, (display_name, _) in self.minigame_map.items():
            parts = display_name.split(" ", 1)
            emoji_part = parts[0] if len(parts) > 1 else None
            label_part = parts[1] if len(parts) > 1 else display_name
            opt = discord.SelectOption(
                label=label_part,
                value=game_key,
                emoji=emoji_part
            )
            if game_key in casino_keys:
                casino_options.append(opt)
            else:
                puzzle_options.append(opt)

        if casino_options:
            self.add_item(LeaderboardSelect("🎰 Casino & Gambling Leaderboards...", casino_options[:25], row=0))

        if puzzle_options:
            self.add_item(LeaderboardSelect("🧠 Puzzles, Quiz & Casual Leaderboards...", puzzle_options[:25], row=1))

    async def show_overview(self, interaction: Optional[discord.Interaction] = None):
        self.setup_overview()
        embed = await self.cog.get_main_leaderboard_embed(self.ctx.guild)
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            self.message = await self.ctx.send(embed=embed, view=self)

    async def show_game_page(self, interaction: Optional[discord.Interaction] = None, game_key: Optional[str] = None, timeframe: str = "weekly", page: int = 0):
        if game_key:
            self.current_game = game_key
        self.timeframe = timeframe
        self.current_page = page

        if self.timeframe == "weekly":
            week_start_ts = get_current_week_start_ts()
            async with self.cog.bot.db.execute("""
                SELECT user_id, COUNT(*) as wins, SUM(earnings) as earnings FROM minigame_win_logs
                WHERE guild_id = ? AND game = ? AND timestamp >= ?
                GROUP BY user_id
                ORDER BY earnings DESC, wins DESC
            """, (self.ctx.guild.id, self.current_game, week_start_ts)) as cursor:
                self.rows_cache = await cursor.fetchall()
        else:
            async with self.cog.bot.db.execute("""
                SELECT user_id, wins, earnings FROM minigame_leaderboard
                WHERE guild_id = ? AND game = ?
                ORDER BY earnings DESC, wins DESC
            """, (self.ctx.guild.id, self.current_game)) as cursor:
                self.rows_cache = await cursor.fetchall()

        self.clear_items()

        total_pages = max(1, (len(self.rows_cache) + self.per_page - 1) // self.per_page)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        # Pagination Buttons (Row 0)
        prev_btn = discord.ui.Button(label="◀️", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), row=0)
        async def prev_callback(i: discord.Interaction):
            await self.show_game_page(i, self.current_game, self.timeframe, self.current_page - 1)
        prev_btn.callback = prev_callback
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(label=f"Page {self.current_page + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=0)
        self.add_item(page_btn)

        next_btn = discord.ui.Button(label="▶️", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), row=0)
        async def next_callback(i: discord.Interaction):
            await self.show_game_page(i, self.current_game, self.timeframe, self.current_page + 1)
        next_btn.callback = next_callback
        self.add_item(next_btn)

        # Timeframe Switcher Button (Row 1: Weekly <-> All-Time)
        if self.timeframe == "weekly":
            tf_btn = discord.ui.Button(label="All-Time 👑", style=discord.ButtonStyle.primary, emoji="👑", row=1)
            async def tf_callback(i: discord.Interaction):
                await self.show_game_page(i, self.current_game, "alltime", 0)
            tf_btn.callback = tf_callback
        else:
            tf_btn = discord.ui.Button(label="Weekly 🗓️", style=discord.ButtonStyle.success, emoji="🗓️", row=1)
            async def tf_callback(i: discord.Interaction):
                await self.show_game_page(i, self.current_game, "weekly", 0)
            tf_btn.callback = tf_callback
        self.add_item(tf_btn)

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
        tf_title = "🗓️ Weekly (This Week)" if self.timeframe == "weekly" else "👑 All-Time"
        reset_str = f" • Resets <t:{get_next_week_start_ts()}:R>" if self.timeframe == "weekly" else ""
        embed = discord.Embed(
            title=f"{game_display} Leaderboard",
            description=f"*{tf_title}{reset_str} • Sorted by Gains* — **{self.ctx.guild.name}**\n\n",
            color=0x000000
        )

        if not self.rows_cache:
            embed.description += "*No records yet for this period.*"
            return embed

        start_idx = self.current_page * self.per_page
        page_rows = self.rows_cache[start_idx : start_idx + self.per_page]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, wins, earnings) in enumerate(page_rows, start=start_idx):
            rank_str = medals[i] if i < 3 else f"**#{i+1}**"
            win_str = f"**{wins}** win" if wins == 1 else f"**{wins}** wins"
            earn_str = format_tad(earnings)
            lines.append(f"{rank_str} <@{uid}> — {earn_str} *({win_str})*")

        embed.description += "\n".join(lines)
        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • Sorted by Gains")
        return embed




class Gambling(commands.Cog, name="Gambling"):
    def __init__(self, bot):
        self.bot = bot
        self.hl_sticky_sessions: dict[int, dict] = {}
        self.active_sessions: dict[str, dict] = {}
        self.recover_orphaned_sessions.start()

    def cog_unload(self):
        self.recover_orphaned_sessions.cancel()

    async def register_active_session(self, session_id: str, user_id: int, game_name: str, bet_amount: int):
        now_ts = int(time.time())
        self.active_sessions[session_id] = {
            "user_id": user_id,
            "game_name": game_name,
            "bet_amount": bet_amount,
            "started_at": now_ts
        }
        try:
            if hasattr(self.bot, 'db') and self.bot.db:
                await self.bot.db.execute(
                    "INSERT OR REPLACE INTO active_game_sessions (session_id, user_id, game_name, bet_amount, started_at, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, user_id, game_name, bet_amount, now_ts, "in_progress")
                )
                await self.bot.db.commit()
        except Exception as e:
            print(f"[register_active_session error]: {e}")

    async def update_session_bet(self, session_id: str, new_bet: int):
        if session_id in self.active_sessions:
            self.active_sessions[session_id]["bet_amount"] = new_bet
        try:
            if hasattr(self.bot, 'db') and self.bot.db:
                await self.bot.db.execute(
                    "UPDATE active_game_sessions SET bet_amount = ? WHERE session_id = ?",
                    (new_bet, session_id)
                )
                await self.bot.db.commit()
        except Exception as e:
            print(f"[update_session_bet error]: {e}")

    async def complete_active_session(self, session_id: str):
        self.active_sessions.pop(session_id, None)
        try:
            if hasattr(self.bot, 'db') and self.bot.db:
                await self.bot.db.execute(
                    "UPDATE active_game_sessions SET status = 'completed' WHERE session_id = ?",
                    (session_id,)
                )
                await self.bot.db.commit()
        except Exception as e:
            print(f"[complete_active_session error]: {e}")

    async def pause_active_session(self, session_id: str):
        self.active_sessions.pop(session_id, None)
        try:
            if hasattr(self.bot, 'db') and self.bot.db:
                await self.bot.db.execute(
                    "UPDATE active_game_sessions SET status = 'timed_out' WHERE session_id = ?",
                    (session_id,)
                )
                await self.bot.db.commit()
        except Exception as e:
            print(f"[pause_active_session error]: {e}")



    @tasks.loop(seconds=60)
    async def recover_orphaned_sessions(self):
        if not hasattr(self.bot, 'db') or not self.bot.db:
            return
        now_ts = int(time.time())
        try:
            async with self.bot.db.execute(
                "SELECT session_id, user_id, game_name, bet_amount, started_at FROM active_game_sessions WHERE status = 'in_progress' AND started_at < ?",
                (now_ts - 180,)
            ) as cursor:
                orphaned = await cursor.fetchall()

            for row in orphaned:
                s_id, u_id, g_name, bet, started_at = row
                if s_id in self.active_sessions:
                    continue
                is_sticky = any(st.get("session_id") == s_id for st in self.hl_sticky_sessions.values())
                if is_sticky:
                    continue

                economy_cog = self.bot.get_cog("Economy")
                if economy_cog and bet > 0:
                    await economy_cog.add_balance(u_id, bet, context=f"Crash Recovery ({g_name})")

                await self.bot.db.execute(
                    "UPDATE active_game_sessions SET status = 'refunded' WHERE session_id = ?",
                    (s_id,)
                )
                await self.bot.db.commit()

                try:
                    user = self.bot.get_user(u_id) or await self.bot.fetch_user(u_id)
                    if user:
                        formatted_amount = f"{bet:,} TAD"
                        await user.send(f"⚠️ Tra chy mouchkil f **{g_name}**. {formatted_amount} ta3k rah rj3at lik!")
                except Exception as e:
                    print(f"[recover_orphaned_sessions DM error for {u_id}]: {e}")
        except Exception as e:
            print(f"[recover_orphaned_sessions loop error]: {e}")

    @recover_orphaned_sessions.before_loop
    async def before_recover_orphaned_sessions(self):
        await self.bot.wait_until_ready()

    async def record_minigame_win(self, guild_id: Optional[int], user_id: int, game: str, earnings: int = 0):
        await record_minigame_win(self.bot, guild_id, user_id, game, earnings)

    async def record_minigame_loss(self, guild_id: Optional[int], user_id: int, game: str, loss_amount: int = 0):
        await record_minigame_loss(self.bot, guild_id, user_id, game, loss_amount)

    # ============ REWORKED LEADERBOARD & CASINO COMMANDS ============

    async def get_main_leaderboard_embed(self, guild: Optional[discord.Guild] = None) -> discord.Embed:
        week_start_ts = get_current_week_start_ts()
        next_week_ts = get_next_week_start_ts()

        # 1. User with the most weekly earnings globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(earnings) as total_earnings
            FROM minigame_win_logs
            WHERE timestamp >= ?
            GROUP BY user_id
            ORDER BY total_earnings DESC LIMIT 1
        """, (week_start_ts,)) as cursor:
            top_weekly_row = await cursor.fetchone()

        # 2. User with the most weekly losses globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(loss_amount) as total_losses
            FROM minigame_loss_logs
            WHERE timestamp >= ?
            GROUP BY user_id
            ORDER BY total_losses DESC LIMIT 1
        """, (week_start_ts,)) as cursor:
            top_weekly_loss_row = await cursor.fetchone()

        # 3. User with the most all-time earnings globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(earnings) as total_earnings
            FROM minigame_leaderboard
            GROUP BY user_id
            ORDER BY total_earnings DESC LIMIT 1
        """) as cursor:
            top_alltime_row = await cursor.fetchone()

        # 4. User with the most all-time losses globally
        async with self.bot.db.execute("""
            SELECT user_id, SUM(loss_amount) as total_losses
            FROM minigame_leaderboard
            GROUP BY user_id
            ORDER BY total_losses DESC LIMIT 1
        """) as cursor:
            top_alltime_loss_row = await cursor.fetchone()

        embed = discord.Embed(
            title="🏆 Minigames Leaderboard (Global)",
            description="Khtar minigame mn lmenu lte7t bach tchouf rankings dialha f had lserver.\n",
            color=0x000000
        )

        # Field 1: Most Weekly Earnings
        if top_weekly_row and top_weekly_row[1] > 0:
            u_id, e_count = top_weekly_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(earnings) as game_earnings
                FROM minigame_win_logs
                WHERE user_id = ? AND timestamp >= ?
                GROUP BY game
                ORDER BY game_earnings DESC LIMIT 1
            """, (u_id, week_start_ts)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name=f"🗓️ Most Weekly Earnings (Resets <t:{next_week_ts}:R>)",
                value=f"<@{u_id}> — {format_tad(e_count)}{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name=f"🗓️ Most Weekly Earnings (Resets <t:{next_week_ts}:R>)",
                value="*No weekly earnings yet.*",
                inline=False
            )

        # Field 2: Most Weekly Losses
        if top_weekly_loss_row and top_weekly_loss_row[1] > 0:
            u_id, l_count = top_weekly_loss_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(loss_amount) as game_losses
                FROM minigame_loss_logs
                WHERE user_id = ? AND timestamp >= ?
                GROUP BY game
                ORDER BY game_losses DESC LIMIT 1
            """, (u_id, week_start_ts)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name=f"📉 Most Weekly Losses (Resets <t:{next_week_ts}:R>)",
                value=f"<@{u_id}> — **-{l_count:,}** {TAD_EMOJI} TAD{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name=f"📉 Most Weekly Losses (Resets <t:{next_week_ts}:R>)",
                value="*No weekly losses yet.*",
                inline=False
            )

        # Field 3: Most All-Time Earnings
        if top_alltime_row and top_alltime_row[1] > 0:
            u_id, e_count = top_alltime_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(earnings) as game_earnings
                FROM minigame_leaderboard
                WHERE user_id = ?
                GROUP BY game
                ORDER BY game_earnings DESC LIMIT 1
            """, (u_id,)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name="👑 Most All-Time Earnings",
                value=f"<@{u_id}> — {format_tad(e_count)}{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name="👑 Most All-Time Earnings",
                value="*No earnings yet.*",
                inline=False
            )

        # Field 4: Most All-Time Losses
        if top_alltime_loss_row and top_alltime_loss_row[1] > 0:
            u_id, l_count = top_alltime_loss_row
            top_game_str = ""
            async with self.bot.db.execute("""
                SELECT game, SUM(loss_amount) as game_losses
                FROM minigame_leaderboard
                WHERE user_id = ?
                GROUP BY game
                ORDER BY game_losses DESC LIMIT 1
            """, (u_id,)) as g_cur:
                g_row = await g_cur.fetchone()
                if g_row:
                    d_name = MINIGAME_DISPLAY_MAP.get(g_row[0], (g_row[0].title(), []))[0]
                    top_game_str = f" *(Top Game: **{d_name}**)*"

            embed.add_field(
                name="💥 Most All-Time Losses",
                value=f"<@{u_id}> — **-{l_count:,}** {TAD_EMOJI} TAD{top_game_str}",
                inline=False
            )
        else:
            embed.add_field(
                name="💥 Most All-Time Losses",
                value="*No losses yet.*",
                inline=False
            )

        embed.set_footer(text="Dropdowns lte7t kat affichi ga3 l minigames available f had server.")
        return embed

    @commands.command(name="minigames", aliases=["mg", "leaderboard", "lb", "top"], help="Leaderboard ta3 lminigames (sat mg [game]).")
    async def minigames(self, ctx: commands.Context, *args):
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

            await view.show_game_page(interaction=None, game_key=target_key, timeframe="weekly", page=0)

    @commands.command(aliases=['cf', 'drhm'], help="Nlou7 derhem o chouf wach jak ras wla njma (sat coinflip [ras/njma] [bet:500]).")
    @not_fraud()
    async def coinflip(self, ctx: commands.Context, *args):
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, remaining = parse_bet_argument(*args, user_balance=w.get("balance", 0))
        choice = remaining[0] if remaining else None

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Coinflip Bet")

        if choice is None:
            view = CoinflipView(ctx.author, self, bet=bet or 0)
            embed = discord.Embed(
                title="Coinflip Table",
                description="Khtar chno ghadi yji: **Ras (Heads)** wla **Njma (Tails)**?" + (f"\n\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""),
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
            await ctx.send("❌ Khtar `ras` (heads) wla `njma` (tails). Example: `sat coinflip ras 100`")
            return

        flip_msg = await ctx.send("🪙 *Le7t derhem f sma...*")
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
                gross_payout = bet * 2
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context="Coinflip Win", vault="casino")
                net_profit = net_payout - bet
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "coinflip", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Coinflip Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "coinflip", loss_amount=bet)
        elif won and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "coinflip")
        elif not won and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "coinflip", loss_amount=0)

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
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Dice Bet")

        roll = random.randint(1, 6)
        multipliers = {
            1: (0.0, "💥 Khesrti l bet! (0x)"),
            2: (0.5, "🤏 Rje3 lik ness l bet (0.5x)"),
            3: (0.75, "🤏 Rje3 lik 75% mn l bet (0.75x)"),
            4: (1.25, "✨ Small Win! (1.25x)"),
            5: (1.5, "🔥 Good Win! (1.5x)"),
            6: (2.0, "👑 DOUBLE JACKPOT! (2.0x)")
        }

        mult, desc = multipliers[roll]

        embed = discord.Embed(
            title=f"🎲 Dice: Rolled [ {roll} ]",
            description=f"{desc}\n\n📊 Multiplier: **{mult}x**",
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            gross_payout = int(round(bet * mult))
            embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
            if gross_payout > 0:
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context=f"Dice Payout ({mult}x)", vault="casino")
                net_profit = net_payout - bet
                if mult >= 1.25 and ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "dice", earnings=max(0, net_profit))
                if net_profit > 0:
                    embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
                elif net_profit < 0:
                    lost_amt = abs(net_profit)
                    tax = await economy_cog.apply_lost_gamble_tax(lost_amt, context="Dice Partial Loss")
                    tax_str = f" • `{tax:,}` TAD tax" if tax > 0 else ""
                    embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(lost_amt)}** (Refund: {net_payout:,} TAD{tax_str})", inline=False)
                    if ctx.guild:
                        await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "dice", loss_amount=lost_amt)
                else:
                    embed.add_field(name="💵 Net Payout", value=f"⚪ **+0 TAD** (Refund: {net_payout:,} TAD)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Dice Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "dice", loss_amount=bet)
        elif mult < 1.0 and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "dice", loss_amount=0)
        else:
            embed.set_footer(text="Bghiti t9emmer b flous? Kteb sat dice 100")

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
        busy = is_user_in_game(self.bot, ctx.author.id)
        if busy:
            await ctx.send(f"❌ 3ndek deja game khddama (**{busy}**)! Kemmelha wla tsennaha tsali 9bel matbda w7da khra.")
            return

        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        session_id = None
        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Blackjack Bet")
            session_id = f"bj_{uuid.uuid4().hex[:12]}"
            await self.register_active_session(session_id, ctx.author.id, "Blackjack", bet)

        set_user_in_game(self.bot, ctx.author.id, "Blackjack")
        view = BlackjackView(ctx.author, self, bet=bet or 0)
        view.session_id = session_id
        initial_embed = view.get_embed()
        initial_file = view.get_render_file()

        p_score = calculate_bj_score(view.player_hand)
        if p_score == 21:
            clear_user_game(self.bot, ctx.author.id)
            if session_id:
                await self.complete_active_session(session_id)

            d_score = calculate_bj_score(view.dealer_hand)
            if d_score == 21:
                initial_embed = view.get_embed(dealer_reveal=True, outcome_text="🤝 **Double Blackjack!** Ta3adol (Push)!")
                if bet and bet > 0 and economy_cog:
                    await economy_cog.add_balance(ctx.author.id, bet, context="Blackjack Push Refund")
            else:
                outcome_str = "🏆 **NATURAL 21 BLACKJACK!** Rbe7ti l game!"
                if bet and bet > 0 and economy_cog:
                    gross_payout = int(round(bet * 2.5))
                    net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context="Blackjack Natural 21", vault="casino")
                    net_profit = net_payout - bet
                    if ctx.guild:
                        await self.record_minigame_win(ctx.guild.id, ctx.author.id, "blackjack", earnings=max(0, net_profit))
                    outcome_str += f"\n\n💰 Rbe7ti **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)!"
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
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet and bet > 0 and economy_cog:
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

        embed = discord.Embed(
            title="🎰 Slots Machine",
            description=(
                f"**[ {r1} | {r2} | {r3} ]**\n\n"
                f"**{outcome_title}**\n"
                f"📊 Multiplier: **{payout_mult:.1f}x**"
            ),
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            gross_payout = int(round(bet * payout_mult))
            if gross_payout > 0:
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context=f"Slots Payout ({payout_mult:.1f}x)", vault="casino")
                net_profit = net_payout - bet
                if payout_mult > 0 and ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "slots", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Slots Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "slots", loss_amount=bet)
        elif payout_mult > 0 and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "slots")
        elif payout_mult == 0 and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "slots", loss_amount=0)

        await spin_msg.edit(embed=embed)

    @commands.command(aliases=["gems"], help="L9a gems o hreb 9bl matfrge3 (sat mines [bet:500]).")
    @not_fraud()
    async def mines(self, ctx: commands.Context, *args):
        busy = is_user_in_game(self.bot, ctx.author.id)
        if busy:
            await ctx.send(f"❌ 3ndek deja game khddama (**{busy}**)! Kemmelha wla tsennaha tsali 9bel matbda w7da khra.")
            return

        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))
        if bet is None or bet <= 0:
            bet = 50

        bombs = 3

        session_id = None
        if economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])} (Min: 50 TAD).")
                return
            success = await economy_cog.deduct_balance(ctx.author.id, bet, context="Mines Bet")
            if not success:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w['balance'])}.")
                return
            session_id = f"mines_{uuid.uuid4().hex[:12]}"
            await self.register_active_session(session_id, ctx.author.id, "Mines", bet)

        set_user_in_game(self.bot, ctx.author.id, "Mines")
        view = MinesGambleView(ctx.author, self, bomb_count=bombs, bet=bet)

        view.session_id = session_id
        total_gems = (view.width * view.height) - bombs
        embed = discord.Embed(
            title="💣 Mines Table",
            description=(
                f"💎 Gems: **0/{total_gems}**\n"
                f"📈 Multiplier: **1.00x** (Next: **{view.get_next_multiplier():.2f}x**)\n"
                f"💣 Bombs: **{bombs}**\n"
                f"💰 Stake: {format_tad(bet)}\n\n"
                "Click 3la ay tile bach t uncoveriha!"
            ),
            color=0x000000
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="tower", aliases=["doors", "lborj", "lbiban"], help="L9a lbab rrab7 f kola etage.")
    @not_fraud()
    async def tower(self, ctx: commands.Context, *args):
        busy = is_user_in_game(self.bot, ctx.author.id)
        if busy:
            await ctx.send(f"❌ 3ndek deja game khddama (**{busy}**)! Kemmelha wla tsennaha tsali 9bel matbda w7da khra.")
            return

        economy_cog = self.bot.get_cog("Economy")
        if not economy_cog:
            await ctx.send("❌ Economy system ma khdamch daba.")
            return

        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, remaining = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        if bet is not None and bet < 0:
            await ctx.send("❌ L bet khas ykoun kber mn 0.")
            return

        bet = bet or 0

        session_id = None
        if bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dyalk: {format_tad(w['balance'])}.")
                return
            success = await economy_cog.deduct_balance(ctx.author.id, bet, context="Tower Bet", force=True)
            if not success:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w['balance'])}.")
                return
            session_id = f"tower_{uuid.uuid4().hex[:12]}"
            await self.register_active_session(session_id, ctx.author.id, "Tower", bet)

        set_user_in_game(self.bot, ctx.author.id, "Tower")
        view = TowerGameView(ctx.author, bet, self)

        view.session_id = session_id
        board_bytes = await asyncio.to_thread(render_tower_board, 1, view.story_doors, "active")
        file = discord.File(board_bytes, filename="tower.jpg")

        bet_str = f"💰 **Bet:** {format_tad(bet)}\n" if bet > 0 else "🎮 **Mode:** Free Play (0 TAD)\n"
        embed = discord.Embed(
            title="🏰 Tower of Doors",
            description=(
                f"👤 **Player:** {ctx.author.mention}\n"
                f"{bet_str}"
                f"🚪 **Story 1/4** • Khtar door mn bach ttle3 l **3.0x**!"
            ),
            color=0x000000
        )
        embed.set_image(url="attachment://tower.jpg")
        embed.set_footer(text="Sifdine Casino • Khtar door wla Cash Out mn be3d Story 1")

        msg = await ctx.send(file=file, embed=embed, view=view)
        view.message = msg

    @commands.command(aliases=["wheel"], help="9emmer 3la loun wla ra9m (sat roulette [choice] [bet:500]).")
    @not_fraud()
    async def roulette(self, ctx: commands.Context, *args):
        red_nums = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        black_nums = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

        if not args:
            embed = discord.Embed(
                title="🎡 European Roulette Table",
                description=(
                    "Khtar 3layach baghi t9emmer:\n"
                    "• `red` / `black` (2x payout)\n"
                    "• `even` / `odd` (2x payout)\n"
                    "• `1-18` (Low) / `19-36` (High) (2x payout)\n"
                    "• `green` (36x payout)\n"
                    "• Number direct `0` - `36` (36x payout)\n\n"
                    f"Example: `{ctx.clean_prefix}roulette 1 200` wla `{ctx.clean_prefix}roulette red 500`"
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        def parse_roulette_choice(val: str) -> Optional[tuple[str, str, str]]:
            """Returns (choice_type, choice_val, display_label) or None."""
            if not val:
                return None
            s = str(val).strip().lower()
            if s.isdigit():
                num = int(s)
                if 0 <= num <= 36:
                    return ("number", str(num), f"Ra9m {num}")
                return None
            if s in ("zero",):
                return ("number", "0", "Ra9m 0")
            if s in ("red", "r", "7mer", "7mr"):
                return ("red", "red", "Red 🔴")
            if s in ("black", "b", "k7el", "k7l", "k7al"):
                return ("black", "black", "Black ⚫")
            if s in ("green", "g", "khder"):
                return ("green", "green", "Green 🟢")
            if s in ("even", "zawji"):
                return ("even", "even", "Even (Zawji)")
            if s in ("odd", "fardi"):
                return ("odd", "odd", "Odd (Fardi)")
            if s in ("1-18", "low", "fo9"):
                return ("low", "1-18", "1-18 (Low)")
            if s in ("19-36", "high", "ta7t", "t7t"):
                return ("high", "19-36", "19-36 (High)")
            return None

        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}

        # Resolve choice and bet: try choice first, then remaining as bet; or bet first, then choice
        choice_tuple = parse_roulette_choice(args[0])
        if choice_tuple:
            bet, _ = parse_bet_argument(*args[1:], user_balance=w.get("balance", 0))
        elif len(args) >= 2:
            choice_tuple = parse_roulette_choice(args[1])
            if choice_tuple:
                bet, _ = parse_bet_argument(args[0], user_balance=w.get("balance", 0))
            else:
                bet = None
        else:
            bet = None

        if not choice_tuple:
            embed = discord.Embed(
                title="❌ Lkhtiyar dialek machi s7i7 f Roulette",
                description=(
                    f"Had lkhtiyar `{args[0]}` makaynch f tabla dial Roulette!\n\n"
                    "**Lkhtiyarat li momkine:**\n"
                    "• **Ra9m direct:** `0` 7tal `36` (36x payout)\n"
                    "• **Alwan:** `red` 🔴 / `black` ⚫ (2x) wla `green` 🟢 (36x)\n"
                    "• **Zawji / Fardi:** `even` / `odd` (2x)\n"
                    "• **Nsf:** `1-18` (Low) / `19-36` (High) (2x)\n\n"
                    f"Example: `{ctx.clean_prefix}roulette 1 200` wla `{ctx.clean_prefix}roulette red 500`"
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        choice_type, choice_val, choice_display = choice_tuple

        session_id = None
        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="Roulette Bet")
            session_id = f"roulette_{uuid.uuid4().hex[:12]}"
            await self.register_active_session(session_id, ctx.author.id, "Roulette", bet)

        spin_embed = discord.Embed(
            description=f"🔄 *Roulette kaddor...* (Lkhtiyar: **{choice_display}**)" + (f"\n\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""),
            color=0x000000
        )
        spin_msg = await ctx.send(embed=spin_embed)

        await asyncio.sleep(4.0)

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

        if choice_type == "number":
            if landed_num == int(choice_val):
                won = True
                mult = 36.0
        elif choice_type == "red" and color_name == "red":
            won = True
            mult = 2.0
        elif choice_type == "black" and color_name == "black":
            won = True
            mult = 2.0
        elif choice_type == "green" and color_name == "green":
            won = True
            mult = 36.0
        elif choice_type == "even" and landed_num > 0 and landed_num % 2 == 0:
            won = True
            mult = 2.0
        elif choice_type == "odd" and landed_num % 2 != 0:
            won = True
            mult = 2.0
        elif choice_type == "low" and 1 <= landed_num <= 18:
            won = True
            mult = 2.0
        elif choice_type == "high" and 19 <= landed_num <= 36:
            won = True
            mult = 2.0

        outcome_title = f"🏆 Rbe7ti! ({mult:.0f}x)" if won else "💥 Khesrti!"
        embed = discord.Embed(
            title=f"🎡 Roulette: {color_emoji} **{landed_num} ({color_name.upper()})**",
            description=(
                f"Lkhtiyar ta3k: **{choice_display}** • Natija: {color_emoji} **{landed_num}**\n\n"
                f"**{outcome_title}**"
            ),
            color=0x000000
        )

        if bet and bet > 0 and economy_cog:
            if won:
                gross_payout = int(round(bet * mult))
                net_payout, tax = await economy_cog.apply_tax_and_add_balance(ctx.author.id, gross_payout, context=f"Roulette Win ({mult:.0f}x)", vault="casino")
                net_profit = net_payout - bet
                if ctx.guild:
                    await self.record_minigame_win(ctx.guild.id, ctx.author.id, "roulette", earnings=max(0, net_profit))
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🟢 **+{format_tad(net_profit)}** (Gross: {gross_payout:,} TAD • `{tax:,}` TAD tax)", inline=False)
            else:
                tax = await economy_cog.apply_lost_gamble_tax(bet, context="Roulette Loss")
                tax_str = f" (`{tax:,}` TAD tax)" if tax > 0 else ""
                embed.add_field(name="💰 Stake", value=format_tad(bet), inline=True)
                embed.add_field(name="💵 Net Payout", value=f"🔴 **-{format_tad(bet)}**{tax_str}", inline=False)
                if ctx.guild:
                    await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "roulette", loss_amount=bet)
        elif won and ctx.guild:
            await self.record_minigame_win(ctx.guild.id, ctx.author.id, "roulette")
        elif not won and ctx.guild:
            await self.record_minigame_loss(ctx.guild.id, ctx.author.id, "roulette", loss_amount=0)

        await spin_msg.edit(embed=embed)
        if session_id:
            await self.complete_active_session(session_id)

    @commands.command(aliases=["hl"], help="9emmer wach lwr9a jaya Higher wla Lower (sat higherlower [bet:500]).")
    @not_fraud()
    async def higherlower(self, ctx: commands.Context, *args):
        busy = is_user_in_game(self.bot, ctx.author.id)
        if busy:
            await ctx.send(f"❌ 3ndek deja game khddama (**{busy}**)! Kemmelha wla tsennaha tsali 9bel matbda w7da khra.")
            return

        economy_cog = self.bot.get_cog("Economy")
        sticky = self.hl_sticky_sessions.pop(ctx.author.id, None)

        if sticky:
            bet = sticky["bet"]
            initial_card = sticky["card"]
            session_id = f"hl_{uuid.uuid4().hex[:12]}"
            if bet > 0:
                await self.register_active_session(session_id, ctx.author.id, "HigherLower", bet)
            set_user_in_game(self.bot, ctx.author.id, "HigherLower")
            view = HigherLowerView(ctx.author, self, bet=bet or 0, initial_card=initial_card)
            view.session_id = session_id

            embed = view.get_embed("❗ **Resuming Timed-Out Session!**\n9emmer lwr9a l7alia wach **Higher ⬆️** wla **Lower ⬇️**!")
            file = get_hl_card_file(view.current_card)
            if file:
                msg = await ctx.send(embed=embed, view=view, file=file)
            else:
                msg = await ctx.send(embed=embed, view=view)
            view.message = msg
            return

        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}
        bet, _ = parse_bet_argument(*args, user_balance=w.get("balance", 0))

        session_id = None
        if bet and bet > 0 and economy_cog:
            if w["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance dialek: {format_tad(w['balance'])}.")
                return
            await economy_cog.deduct_balance(ctx.author.id, bet, context="HigherLower Bet")
            session_id = f"hl_{uuid.uuid4().hex[:12]}"
            await self.register_active_session(session_id, ctx.author.id, "HigherLower", bet)

        set_user_in_game(self.bot, ctx.author.id, "HigherLower")
        view = HigherLowerView(ctx.author, self, bet=bet or 0)

        view.session_id = session_id
        embed = view.get_embed("9emmer lwr9a jaya wach **Higher ⬆️** wla **Lower ⬇️**!")
        file = get_hl_card_file(view.current_card)
        if file:
            msg = await ctx.send(embed=embed, view=view, file=file)
        else:
            msg = await ctx.send(embed=embed, view=view)
        view.message = msg



async def setup(bot):
    await bot.add_cog(Gambling(bot))
