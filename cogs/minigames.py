from __future__ import annotations
import asyncio
import time
import random
from typing import Optional, Union

import discord
from discord.ext import commands
from discord.ui import Button

from converters import FuzzyMember
from cogs.economy import (
    parse_bet_argument, format_tad, TAD_EMOJI, calculate_pvp_payout,
    not_fraud
)
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game, get_user_game_session,
    record_minigame_win, record_minigame_loss, WORDS_DB_PATH,
    get_dictionary_cursor, is_english_word, get_combo, get_typeracer_text,
    get_word, get_words_batch, get_unscramble_word, get_wordle_secret, get_hangman_secret
)
from cogs.games.common import parse_minigame_args

# Game modules
from cogs.games.chess import ChessView, ChessChallengeView
from cogs.games.chesspuzzle import ChessPuzzleView, _load_chess_puzzles
from cogs.games.tictactoe import TicTacToeView, ChallengeView
from cogs.games.connectfour import ConnectFourView, ConnectFourChallengeView
from cogs.games.akinator import AkinatorView
from cogs.games.rps import RPSBotView, RPSChallengeView
from cogs.games.minesweeper import MinesweeperSoloView, MinesweeperChallengeView
from cogs.games.wordle import WordleSoloView, WordleChallengeView
from cogs.games.hangman import HangmanSoloView, HangmanChallengeView
from cogs.games.guesstherank import (
    GuessTheRankView, GuessTheRankSelectView, GTR_GAMES,
    _gtr_session_sync, _gtr_get_clip_sync
)
from cogs.games.flags import run_flags_game
from cogs.games.crafting import run_crafting_game
from cogs.games.cars import run_cars_game
from cogs.games.tea import run_blacktea_game, run_greentea_game, run_redtea_game
from cogs.games.unscramble import run_unscramble_game
from cogs.games.trivia import run_trivia_game
from cogs.games.typeracer import run_typeracer_game
from cogs.games.geoguessr import run_geoguessr_game
from cogs.games.buckshot import BuckshotGameView, BuckshotChallengeView
from cogs.games.impostor import run_impostor_game


class Minigames(commands.Cog, name="Minigames"):
    def __init__(self, bot):
        self.bot = bot
        self.words_db_path = WORDS_DB_PATH
        self.active_unscrambles = set()
        self.active_akinator_users = set()
        self.active_akinator_channels = {}

    def _get_cursor(self):
        return get_dictionary_cursor()

    def is_english_word(self, word: str) -> bool:
        return is_english_word(word)

    def get_combo(self, difficulty: str = "medium", exclude: Optional[set] = None) -> str:
        return get_combo(difficulty, exclude=exclude)

    def get_typeracer_text(self) -> str:
        return get_typeracer_text()

    def get_word(self, difficulty: str = "medium", min_length: int = 4, max_length: int = 10) -> str:
        return get_word(difficulty, min_length, max_length)

    def get_words_batch(self, difficulty: str = "medium", count: int = 5, min_length: int = 4, max_length: int = 10) -> list[str]:
        return get_words_batch(difficulty, count, min_length, max_length)

    def get_unscramble_word(self, difficulty: str = "medium") -> str:
        return get_unscramble_word(difficulty)

    def get_wordle_secret(self, difficulty: str = "medium") -> str:
        return get_wordle_secret(difficulty)

    def get_hangman_secret(self, difficulty: str = "medium") -> str:
        return get_hangman_secret(difficulty)

    async def record_minigame_win(self, guild_id: Optional[int], user_id: int, game: str, earnings: int = 0):
        await record_minigame_win(self.bot, guild_id, user_id, game, earnings)

    async def ensure_user_free(self, ctx: commands.Context, target_user: Optional[Union[discord.Member, discord.User]] = None) -> bool:
        """Returns True if author (and optional target_user) is free, else sends notice and returns False."""
        busy = is_user_in_game(self.bot, ctx.author.id)
        if busy:
            await ctx.send(f"❌ 3ndek deja game khddama (**{busy}**)! Kemmelha wla tsennaha tsali 9bel matbda w7da khra.")
            return False
        if target_user and not getattr(target_user, "bot", False):
            target_busy = is_user_in_game(self.bot, target_user.id)
            if target_busy:
                await ctx.send(f"❌ **{target_user.display_name}** 3ndo deja game khddama (**{target_busy}**).")
                return False
        return True


    @commands.command(name="flags", aliases=["gtf"], help="N3tik flag o goul lia chno smit dawla.")
    async def flags(self, ctx, *args):
        await run_flags_game(self, ctx, *args)

    @commands.command(name="craftingtable", aliases=["crafting", "craft", "recipe"], help="N3tik recipe ta3 minecraf o 9dder chno l item li katsawb.")
    async def craftingtable(self, ctx, *args):
        await run_crafting_game(self, ctx, *args)

    @commands.command(name="guessthecar", aliases=["guesscar", "cars", "carguess", "carmodels", "models", "tomobil", "tomobila"], help="N3tik tswira ta3 tomobila o 9dder smyt l model.")
    async def guessthecar(self, ctx, *args):
        await run_cars_game(self, ctx, *args)

    @commands.command(aliases=["jklm"], help="Kteb kelma fiha l7orof li ghan3tik.")
    async def blacktea(self, ctx, *args):
        await run_blacktea_game(self, ctx, *args)

    @commands.command(aliases=["gt", "green"], help="Kteb kelma fiha l7orof li ghan3tik bzerba.")
    async def greentea(self, ctx, *args):
        await run_greentea_game(self, ctx, *args)

    @commands.command(aliases=["rt", "red"], help="Kteb atwal kelma fiha l7orof li ghan3tik.")
    async def redtea(self, ctx, *args):
        await run_redtea_game(self, ctx, *args)

    @commands.command(name="unscramble", aliases=["scramble", "moliniks", "molinix", "chlada", "shlada"], help="An3tik kelma mkhrb9a o nta 9adha.")
    async def unscramble(self, ctx, *args):
        await run_unscramble_game(self, ctx, *args)

    @commands.command(name="tictactoe", aliases=["ttt"], help="X/O las9 3 bach trbe7 (sat ttt @user [bet:500]).")
    @not_fraud()
    async def tictactoe(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return
        bet, _ = parse_bet_argument(*args)
        if member is None:
            view = TicTacToeView(ctx.author, ctx.bot.user, is_bot_game=True, cog=self)
            set_user_in_game(self.bot, ctx.author.id, "Tic-Tac-Toe", view)
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
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
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
        if not await self.ensure_user_free(ctx, member):
            return
        bet, _ = parse_bet_argument(*args)
        if member is None:
            view = ConnectFourView(ctx.author, ctx.bot.user, is_bot_game=True, cog=self)
            set_user_in_game(self.bot, ctx.author.id, "Connect 4", view)
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
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
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
    async def akinator_cmd(self, ctx: commands.Context, *args):
        if not await self.ensure_user_free(ctx):
            return

        if ctx.author.id in self.active_akinator_users:
            await ctx.send("❌ Rak deja katl3eb Akinator! Kamel lgame dialk wla dir 🛑 Stop.")
            return

        if ctx.channel.id in self.active_akinator_channels:
            active_player = self.active_akinator_channels[ctx.channel.id]
            await ctx.send(f"❌ **{active_player.display_name}** deja la3b Akinator db f had lchannel, tsna 7ta ysali!")
            return

        self.active_akinator_users.add(ctx.author.id)
        self.active_akinator_channels[ctx.channel.id] = ctx.author

        async with ctx.typing():
            view = AkinatorView(ctx.author, timeout=60.0, cog=self, channel_id=ctx.channel.id)
            set_user_in_game(self.bot, ctx.author.id, "Akinator", view)

            # Setup dynamic callbacks for buttons
            for child in view.children:
                if isinstance(child, Button):
                    child.callback = view.button_callback

            try:
                embed = await view.start_game()
                message = await ctx.send(embed=embed, view=view)
                view.message = message
            except Exception as e:
                self.active_akinator_users.discard(ctx.author.id)
                self.active_akinator_channels.pop(ctx.channel.id, None)
                clear_user_game(self.bot, ctx.author.id)
                print(f"[Akinator Command Error]: {e}")
                await ctx.send("❌ Makhdamach Akinator daba, 7awel mn be3d.")


    @commands.command(name="playchess", aliases=["shitranj", "chessgame"], help="L3eb chess (sat playchess @user [bet:500]).")
    @not_fraud()
    async def playchess(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return
        bet, _ = parse_bet_argument(*args)
        # Single Player vs Bot
        if member is None:
            game_view = ChessView(ctx.author, ctx.bot.user, is_bot_game=True, cog=self)
            set_user_in_game(self.bot, ctx.author.id, "Chess", game_view)
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
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Challenge dial Chess!**\n"
            f"**{ctx.author.display_name}** challenga {member.mention} f match dial Chess!"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        await ctx.send(content=content, view=challenge_view)


    @commands.command(name="rockpaperscissors", aliases=["rps", "zdimbomba7", "zba7"], help="7ajar wara9 mi9as (sat rps [bet] wla sat rps @user [bet]).")
    @not_fraud()
    async def rps(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return
        economy_cog = self.bot.get_cog("Economy")
        w = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}

        msg_parts = ctx.message.content.split()
        cmd_words = msg_parts[1:] if len(msg_parts) > 1 else []
        parsed_bet, _ = parse_bet_argument(*cmd_words, user_balance=w.get("balance", 0))

        if not ctx.message.mentions and member is not None:
            first_val, _ = parse_bet_argument(cmd_words[0] if cmd_words else None, user_balance=w.get("balance", 0))
            if first_val is not None:
                member = None
                bet = first_val
            else:
                bet = parsed_bet
        else:
            bet = parsed_bet

        if member is None:
            if bet and bet > 0 and economy_cog:
                if w["balance"] < bet:
                    await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w['balance'])}.")
                    return
                await economy_cog.deduct_balance(ctx.author.id, bet, context="RPS Bot Bet")

            view = RPSBotView(ctx.author, cog=self, bet=bet or 0)
            set_user_in_game(self.bot, ctx.author.id, "Rock Paper Scissors", view)
            stake_str = f"\n💰 Stake: {format_tad(bet)}" if bet and bet > 0 else ""
            embed = discord.Embed(
                title="🪨 Rock Paper Scissors",
                description=f"⚔️ {ctx.author.mention} vs 🤖 Bot\n\nKhtar choice dialk:{stake_str}",
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
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
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


    @commands.command(name="minesweeper", aliases=["ms", "demineur"], help="Hreb mn l9nabl (solo) wla l9a l9nabl (1v1) (sat ms @user [bet:500]).")
    @not_fraud()
    async def minesweeper(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return
        bet, _ = parse_bet_argument(*args)
        if member is None:
            view = MinesweeperSoloView(ctx.author, cog=self)
            set_user_in_game(self.bot, ctx.author.id, "Minesweeper", view)
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

        challenge_view = MinesweeperChallengeView(ctx.author, member, self, bet=bet or 0)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Challenge dial Minesweeper!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message


    @commands.command(name="wordle", aliases=["klma", "kelma"], help="9edder kelmat bach tl9a lkelma fach kanfkr (sat wordle [@user] [easy|medium|hard] [bet:500]).")
    @not_fraud()
    async def wordle(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return
        bet, _ = parse_bet_argument(*args)
        _, difficulty = parse_minigame_args(*args, default_duration=0, default_difficulty="easy")
        if member is None:
            secret = self.get_wordle_secret(difficulty)
            view = WordleSoloView(ctx.author, secret, self, difficulty=difficulty)
            set_user_in_game(self.bot, ctx.author.id, "Wordle", view)
            message = await ctx.send(content=view.get_content(), view=view)
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

        challenge_view = WordleChallengeView(ctx.author, member, self, bet=bet or 0, difficulty=difficulty)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Challenge dial Wordle 1v1!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message


    @commands.command(name="hangman", aliases=["hm", "michna9a"], help="l9a lkelma 9bel matchne9 (sat hangman [@user] [easy|medium|hard] [bet:500]).")
    @not_fraud()
    async def hangman(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return
        bet, _ = parse_bet_argument(*args)
        _, difficulty = parse_minigame_args(*args, default_duration=0, default_difficulty="easy")
        if member is None:
            secret = self.get_hangman_secret(difficulty)
            view = HangmanSoloView(ctx.author, secret, self, difficulty=difficulty)
            set_user_in_game(self.bot, ctx.author.id, "Hangman", view)
            message = await ctx.send(content=view.get_content(), view=view)
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

        challenge_view = HangmanChallengeView(ctx.author, member, self, bet=bet or 0, difficulty=difficulty)
        wager_str = ""
        if bet and bet > 0:
            w_payout, burned, d_split = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"**2% Tax:** `{burned:,}` {TAD_EMOJI} TAD\n"
                f"🤝 **Draw Split:** {format_tad(d_split)} each"
            )

        content = (
            f"⚔️ **Challenge dial Hangman 1v1!**\n"
            f"**{ctx.author.display_name}** vs {member.mention}"
            f"{wager_str}\n\n"
            f"{member.mention}, t accepti?"
        )
        message = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = message


    @commands.command(name="trivia", aliases=["quiz", "as2ila"], help="Man sayarba7 2 drahm.")
    @not_fraud()
    async def trivia(self, ctx, *args):
        await run_trivia_game(self, ctx, *args)

    @commands.command(name="typeracer", aliases=["tr", "type", "monkeytype"], help="Kteb text li ghan3tik bzerba bach trb7.")
    @not_fraud()
    async def typeracer(self, ctx, rounds: int = 3):
        await run_typeracer_game(self, ctx, rounds)

    @commands.command(name="geoguessr", aliases=["geo", "geoguesser", "geoguess"], help="Khssk t3rf dawla mn tswira.")
    async def geoguessr(self, ctx: commands.Context, *args):
        await run_geoguessr_game(self, ctx, *args)

    @commands.command(name="guesstherank", aliases=["gtr", "guessrank"], help="9edder rank dial clip f games bhal Rocket League, Valorant, CS2, etc.")
    async def guesstherank(self, ctx: commands.Context, *, game: str = None):
        if not await self.ensure_user_free(ctx):
            return

        if not game:
            embed = discord.Embed(
                title="🎖️ Guess The Rank — Khtar Game",
                description=(
                    "Khtar lgame li bghiti tguessi fiha rank mn dropdown lte7t! 🎮\n\n"
                    "• Exact Guess: **+100 TAD**\n"
                    "• 1 Rank Off: **+50 TAD**"
                ),
                color=0x000000
            )
            embed.set_footer(text="GuessTheRank.org • Khtar game bach tbda")
            select_view = GuessTheRankSelectView(ctx.author, self)
            msg = await ctx.send(embed=embed, view=select_view)
            select_view.message = msg
            return

        g_clean = game.strip().lower().replace(" ", "").replace("-", "")
        target_game = None

        for gid, gdata in GTR_GAMES.items():
            if g_clean == gid or g_clean in gdata["aliases"] or g_clean == gdata["name"].lower().replace(" ", ""):
                target_game = gdata
                break

        if not target_game:
            valid_list = " • ".join([f"{g['emoji']} `{g['id']}` ({g['name']})" for g in GTR_GAMES.values()])
            await ctx.send(f"❌ Makaynch had lgame. Games li kaynin:\n{valid_list}")
            return

        wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))

        try:
            opener = await asyncio.to_thread(_gtr_session_sync, target_game["id"])
            clip = await asyncio.to_thread(_gtr_get_clip_sync, opener, target_game["id"])
        except Exception as e:
            await wait_msg.edit(content=f"❌ Tra chy mochkil f fetching dial clip: `{e}`")
            return

        if not clip:
            await wait_msg.edit(content="❌ Mal9itch clip f had lwe9t. 3awed jereb mn b3d.")
            return

        view = GuessTheRankView(ctx.author, self, target_game, opener, clip)
        set_user_in_game(self.bot, ctx.author.id, "Guess The Rank", view)
        content = (
            f"**{target_game['emoji']} Guess The Rank — {target_game['name']}**\n"
            f"https://www.youtube.com/watch?v={clip['youtubeId']}\n\n"
            f"-# Exact Guess: **+100** TAD, 1 Rank Off: **+50** TAD"
        )
        await wait_msg.edit(content=content, embed=None, view=view)
        view.message = wait_msg


    @commands.command(name="chesspuzzle", aliases=["puzzle", "chessquiz", "lichess"], help="7ell puzzle dial chess.")
    async def chesspuzzle(self, ctx: commands.Context):
        if not await self.ensure_user_free(ctx):
            return
        pool = _load_chess_puzzles()
        if not pool:
            await ctx.send("❌ Mal9itch puzzles f had lwe9t, 7awel mn be3d.")
            return

        view = ChessPuzzleView(ctx.author, self)
        set_user_in_game(self.bot, ctx.author.id, "Chess Puzzle", view)
        board_file = await view.generate_board_file()
        embed = view.build_puzzle_embed()
        msg = await ctx.send(embed=embed, file=board_file, view=view)
        view.message = msg


    @commands.command(name="buckshot", aliases=["bsr", "buckshotroulette", "shotgun"], help="Buckshot Roulette (sat buckshot [bet] wla sat buckshot @user [bet]).")
    @not_fraud()
    async def buckshot_cmd(self, ctx: commands.Context, member: Optional[FuzzyMember] = None, *args):
        if not await self.ensure_user_free(ctx, member):
            return

        economy_cog = self.bot.get_cog("Economy")
        w_author = await economy_cog.get_wallet(ctx.author.id) if economy_cog else {"balance": 0}

        msg_parts = ctx.message.content.split()
        cmd_words = msg_parts[1:] if len(msg_parts) > 1 else []
        parsed_bet, _ = parse_bet_argument(*cmd_words, user_balance=w_author.get("balance", 0))

        if not ctx.message.mentions and member is not None:
            first_val, _ = parse_bet_argument(cmd_words[0] if cmd_words else None, user_balance=w_author.get("balance", 0))
            if first_val is not None:
                member = None
                bet = first_val
            else:
                bet = parsed_bet
        else:
            bet = parsed_bet

        bet = bet or 0

        # Solo against Dealer AI
        if member is None:
            if bet > 0 and economy_cog:
                if w_author["balance"] < bet:
                    await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w_author['balance'])}.")
                    return
                await economy_cog.deduct_balance(ctx.author.id, bet, context="Buckshot Roulette Bet")

            game_view = BuckshotGameView(
                player_1=ctx.author,
                player_2=ctx.bot.user,
                is_bot_game=True,
                bet=bet,
                cog=self
            )
            set_user_in_game(self.bot, ctx.author.id, "Buckshot Roulette", game_view)
            game_view.refresh_components()
            game_view.reset_turn_timer()

            msg = await ctx.send(
                content=game_view.get_turn_content(),
                embed=game_view.build_embed(),
                view=game_view
            )
            game_view.message = msg
            return

        # PvP Against another user
        if member.bot:
            await ctx.send("❌ Mat9edch tchallengi bot (Ila bghiti tl3eb m3a l-Dealer, dir ghir `sat buckshot [bet]`).")
            return

        if member == ctx.author:
            await ctx.send("❌ Mat9edch tchallengi rask..")
            return

        if bet > 0 and economy_cog:
            w_target = await economy_cog.get_wallet(member.id)
            if w_author["balance"] < bet:
                await ctx.send(f"❌ Flousk makafyinch! Balance: {format_tad(w_author['balance'])}.")
                return
            if w_target["balance"] < bet:
                await ctx.send(f"❌ **{member.display_name}** ma 3ndo kafi dial flous ({format_tad(w_target['balance'])} / {format_tad(bet)})!")
                return

        challenge_view = BuckshotChallengeView(ctx.author, member, self, bet=bet)
        wager_str = ""
        if bet > 0:
            w_payout, burned, _ = calculate_pvp_payout(bet)
            wager_str = (
                f"\n\n🚨 **ACTIVE WAGER: {format_tad(bet)}** 🚨\n"
                f"💰 **Total Pot:** {format_tad(bet*2)} (Winner Takes: **{format_tad(w_payout)}**)\n"
                f"🔥 **2% Tax:** `{burned:,}` {TAD_EMOJI} TAD"
            )

        content = (
            f"⚔️ **Buckshot Roulette Challenge!**\n"
            f"**{ctx.author.display_name}** challenga {member.mention} l-duel d Buckshot Roulette!"
            f"{wager_str}\n\n"
            f"{member.mention}, t-accepti?"
        )
        msg = await ctx.send(content=content, view=challenge_view)
        challenge_view.message = msg


    @commands.command(name="impostor", aliases=["imposter", "amongus"], help="Minigame dial l'Impostor (minimum 3 players) (sat impostor [bet:500]).")
    @not_fraud()
    async def impostor_cmd(self, ctx: commands.Context, *args):
        await run_impostor_game(self, ctx, *args)


    @commands.command(name="quit", aliases=["forfeit", "leavegame", "cancelgame", "surrender"], help="Khroj mn ay minigame session rak la3bha daba.")
    async def quit_game_cmd(self, ctx: commands.Context):
        busy = is_user_in_game(self.bot, ctx.author.id)
        if not busy:
            await ctx.send("❌ Ma 3ndek 7ta chi game khddama daba bach t-quittiha.")
            return

        session = get_user_game_session(self.bot, ctx.author.id)
        quit_msg = None

        if session and hasattr(session, "handle_user_quit"):
            try:
                quit_msg = await session.handle_user_quit(ctx.author)
            except Exception as e:
                print(f"[quit_game_cmd error]: {e}")

        clear_user_game(self.bot, ctx.author.id)
        if hasattr(self, "active_akinator_users"):
            self.active_akinator_users.discard(ctx.author.id)

        if quit_msg:
            await ctx.send(quit_msg)
        else:
            await ctx.send(f"🚪 **{ctx.author.mention}**, kherjti mn lgame dial **{busy}** o t-cleara l-session dialk!")



async def setup(bot):
    await bot.add_cog(Minigames(bot))
