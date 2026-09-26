from __future__ import annotations
import asyncio
import time
import random
import html
from typing import Optional

import aiohttp
import discord
from discord.ui import Button, View

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    attach_game_session, MultiplayerGameSession, record_minigame_win
)
from cogs.games.common import DIFFICULTY_STAKES, parse_minigame_args, MinigameDifficultyView

# ============ TRIVIA HELPERS & UI CLASSES ============

async def fetch_trivia_batch(session: aiohttp.ClientSession, amount: int = 15, difficulty: str = "easy") -> list[dict]:
    diff_param = f"&difficulty={difficulty.lower()}" if difficulty and difficulty.lower() in ("easy", "medium", "hard") else ""
    url = f"https://opentdb.com/api.php?amount={amount}&type=multiple{diff_param}"
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




async def run_trivia_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "Trivia Quiz")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=30, default_difficulty="easy")
        if round_duration < 5:
            round_duration = 5
            time_display = "5s (Minimum)"
        else:
            time_display = f"{round_duration}s"

        mult = DIFFICULTY_STAKES.get(difficulty, 1.5)
        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🧠 Trivia Quiz!",
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nDifficulty: **{difficulty.upper()}** (Stake: **{mult}x**)",
            color=0x000000
        )
        diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
        signup_msg = await ctx.send(embed=signup_embed, view=diff_view)
        await signup_msg.add_reaction(join_emoji)
        await asyncio.sleep(19)

        difficulty = diff_view.difficulty
        diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.5)
        diff_view.stop()

        signup_msg = await ctx.channel.fetch_message(signup_msg.id)
        reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

        players = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    if user.id != ctx.author.id and is_user_in_game(cog.bot, user.id):
                        continue
                    players.append(user)

        if not players:
            clear_user_game(cog.bot, ctx.author.id)
            await signup_msg.edit(embed=discord.Embed(
                description="💨 7ta wa7d ma dkhel lgame ._.",
                color=0x000000
            ), view=None)
            return

        active_players = list(players)
        session = MultiplayerGameSession("Trivia Quiz", active_players, ctx.channel)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Trivia Quiz", session)

        try:
            single_player = len(players) == 1
            hp = {p.id: 3 for p in players}
            scores = {p.id: 0 for p in players}

            if single_player:
                await signup_msg.edit(embed=discord.Embed(
                    description=f"▶️ Bdina! 3ndek **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                    color=0x000000
                ), view=None)
            else:
                await signup_msg.edit(embed=discord.Embed(
                    description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                    color=0x000000
                ), view=None)
            await asyncio.sleep(2)

            question_pool = await fetch_trivia_batch(cog.bot.session, amount=20, difficulty=difficulty)

            while len(active_players) > 0 and not session.stopped:
                if not single_player and len(active_players) == 1:
                    winner = active_players[0]
                    economy_cog = cog.bot.get_cog("Economy")
                    player_earnings = {}
                    eco_msg = ""
                    if economy_cog:
                        winner_answers = scores.get(winner.id, 0)
                        gross = (len(players) * 50) + int(round((winner_answers * 25) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Trivia Win")
                        player_earnings[winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner.id, "trivia", earnings=net)

                        for pid, p_score in scores.items():
                            if pid != winner.id and p_score > 0:
                                p_gross = int(round((p_score * 50) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Trivia Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({scores[pid]} answers)" for pid, net in player_earnings.items() if pid != winner.id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {winner.mention} rbe7 lgame b **{scores[winner.id]} answers correct**!{eco_msg}{others_msg}",
                        color=0x000000
                    ))
                    return

                for player in list(active_players):
                    if not single_player and len(active_players) == 1:
                        break

                    if not question_pool:
                        question_pool = await fetch_trivia_batch(cog.bot.session, amount=20, difficulty=difficulty)

                    q_data = question_pool.pop(0) if question_pool else {
                        "category": "General", "difficulty": difficulty.capitalize(),
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
                economy_cog = cog.bot.get_cog("Economy")
                gross = 50 + int(round((scores.get(player.id, 0) * 25) * diff_mult))
                eco_msg = ""
                if economy_cog:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context="Trivia Solo")
                    eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                await ctx.send(embed=discord.Embed(
                    description=f"🎯 Game Over {player.mention}! Score dialk: **{scores[player.id]} questions correct**.{eco_msg}",
                    color=0x000000
                ))
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


