from __future__ import annotations
import os
import json
import time
import asyncio
import difflib
from typing import Optional

import discord

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    MultiplayerGameSession, record_minigame_win
)
from cogs.games.common import countdown_reactions

_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
_KRILLION_CACHE: list[dict] = []


def _load_krillion_categories() -> list[dict]:
    global _KRILLION_CACHE
    if not _KRILLION_CACHE:
        path = os.path.join(_ASSETS_DIR, "krillion_categories.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _KRILLION_CACHE = json.load(f)
    return _KRILLION_CACHE


TIER_DISPLAY = {
    "krillion": ("🦈 One in a Krillion", 100, "Deepest dive! Rarest answer!"),
    "deep_cut": ("🦑 Deep Cut", 75, "Very obscure!"),
    "rare": ("🐠 Rare", 50, "Uncommon answer."),
    "schooler": ("🦐 Schooler", 25, "Decent pick."),
    "plankton": ("🫧 Plankton", 10, "Very common answer.")
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    import unicodedata
    n = unicodedata.normalize("NFKD", text)
    cleaned = "".join(c for c in n if not unicodedata.combining(c))
    for ch in [".", ",", "-", "'", "\"", "!", "?", "(", ")", "/", ":"]:
        cleaned = cleaned.replace(ch, " ")
    return " ".join(cleaned.lower().split())


def match_answer_in_category(category: dict, guess: str) -> Optional[dict]:
    norm_guess = normalize_text(guess)
    if not norm_guess or len(norm_guess) < 2:
        return None

    # 1. Exact match on name or any alias
    for item in category["items"]:
        norm_name = normalize_text(item.get("name", ""))
        if norm_guess == norm_name or norm_guess.replace(" ", "") == norm_name.replace(" ", ""):
            return item
        for alias in item.get("aliases", []):
            norm_a = normalize_text(alias)
            if norm_guess == norm_a or norm_guess.replace(" ", "") == norm_a.replace(" ", ""):
                return item

    # 2. Fuzzy match (allow minor typos)
    best_item = None
    best_ratio = 0.0
    for item in category["items"]:
        candidates = [item.get("name", "")] + item.get("aliases", [])
        for name in candidates:
            norm_name = normalize_text(name)
            ratio = difflib.SequenceMatcher(None, norm_guess, norm_name).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_item = item

    if best_ratio >= 0.85 and best_item:
        return best_item

    return None


async def run_krillion_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    categories = _load_krillion_categories()
    if not categories:
        await ctx.send("❌ Mal9itch data ta3 Krillion.")
        return

    set_user_in_game(cog.bot, ctx.author.id, "Krillion")
    round_duration = 25
    total_rounds = 5
    players = []

    try:
        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🌊 Krillion · The Deep Dive",
            description=(
                f"Ljawab li ghaaaber kayrbe7.\n\n"
                f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\n"
                f"Starts: <t:{int(time.time() + 21)}:R>\n"
                f"Rounds: **{total_rounds}** • Time/Round: **{round_duration}s**"
            ),
            color=0x000000
        )
        start_msg = await ctx.send(embed=signup_embed)
        await start_msg.add_reaction(join_emoji)
        await asyncio.sleep(19)

        signup_msg = await ctx.channel.fetch_message(start_msg.id)
        reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    if user.id != ctx.author.id and is_user_in_game(cog.bot, user.id):
                        continue
                    players.append(user)

        if not players:
            clear_user_game(cog.bot, ctx.author.id)
            await start_msg.edit(embed=discord.Embed(
                description="💨 7ta wa7d ma dkhel lgame ._.",
                color=0x000000
            ))
            return

        def _on_krillion_quit(u):
            quitter = next((p for p in players if p.id == u.id), None)
            if quitter:
                players.remove(quitter)

        session = MultiplayerGameSession("Krillion", list(players), ctx.channel, on_quit=_on_krillion_quit)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Krillion", session)

        player_scores = {p.id: 0 for p in players}
        player_depths = {p.id: [] for p in players}

        import random
        pool = list(categories)
        random.shuffle(pool)
        selected_categories = pool[:total_rounds]

        await start_msg.edit(embed=discord.Embed(
            description=f"▶️ **Bdina Krillion!** {len(players)} players.",
            color=0x000000
        ))
        await asyncio.sleep(2)

        for r_idx, category in enumerate(selected_categories, start=1):
            if len(players) == 0 or session.stopped:
                break

            round_embed = discord.Embed(
                title=f"🌊 Round {r_idx}/{total_rounds}: {category['title']}",
                description=(
                    f"💡 *{category.get('hint', 'Kteb chy jawab ghaaaaaaber!')}*"
                ),
                color=0x000000
            )
            round_msg = await ctx.send(embed=round_embed)

            guesses = {}
            taken_answers = set()
            countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))
            start_time = time.time()

            while time.time() - start_time < round_duration:
                time_left = round_duration - (time.time() - start_time)
                if time_left <= 0:
                    break
                try:
                    def check_m(msg):
                        return (
                            any(msg.author.id == p.id for p in players)
                            and (msg.author.id not in guesses or msg.content.strip().lower() == "exitgame")
                            and msg.channel.id == ctx.channel.id
                        )

                    m = await cog.bot.wait_for("message", check=check_m, timeout=time_left)
                    if m.content.strip().lower() == "exitgame":
                        quitter = next((p for p in players if p.id == m.author.id), None)
                        if quitter:
                            clear_user_game(cog.bot, quitter.id)
                            players.remove(quitter)
                            await ctx.send(f"🚪 **{quitter.mention}** khrej mn lgame.")
                            if len(players) <= 1:
                                if not countdown_task.done():
                                    countdown_task.cancel()
                                break
                        continue

                    matched_item = match_answer_in_category(category, m.content)
                    if matched_item:
                        canon_name = matched_item.get("name", m.content.strip().title())
                        if canon_name.lower() in taken_answers:
                            try:
                                await m.add_reaction("❌")
                            except Exception:
                                pass
                            continue

                        taken_answers.add(canon_name.lower())
                        tier = matched_item.get("tier", "plankton")
                        tier_label, pts, _ = TIER_DISPLAY.get(tier, ("🫧 Plankton", 10, ""))
                        guesses[m.author.id] = {
                            "answer": canon_name,
                            "tier_label": tier_label,
                            "tier": tier,
                            "pts": pts
                        }
                        player_scores[m.author.id] += pts
                        player_depths[m.author.id].append(tier_label)
                        try:
                            await m.add_reaction("🤿")
                        except Exception:
                            pass

                        if len(guesses) >= len(players):
                            if not countdown_task.done():
                                countdown_task.cancel()
                            break
                except asyncio.TimeoutError:
                    break

            if not countdown_task.done():
                countdown_task.cancel()

            # Results of this round
            if guesses:
                sorted_guesses = sorted(guesses.items(), key=lambda x: x[1]["pts"], reverse=True)
                top_id, top_data = sorted_guesses[0]
                top_user = discord.utils.get(players, id=top_id)

                guess_lines = []
                for rank_num, (pid, gdata) in enumerate(sorted_guesses, start=1):
                    puser = discord.utils.get(players, id=pid)
                    pname = puser.display_name if puser else f"Player {pid}"
                    guess_lines.append(f"**#{rank_num}** {pname}: **{gdata['answer']}** — {gdata['tier_label']} (`+{gdata['pts']} pts`)")

                result_embed = discord.Embed(
                    title=f"🌊 Round {r_idx} Results: {category['title']}",
                    description=(
                        f"🏆 **Deepest Dive:** {top_user.mention if top_user else 'Unknown'}!\n\n"
                        f"📊 **Guesses:**\n" + "\n".join(guess_lines)
                    ),
                    color=0x000000
                )
            else:
                result_embed = discord.Embed(
                    title=f"🌊 Round {r_idx} Ended: {category['title']}",
                    description="⏰ Ta7ed majawb jawb s7i7 f had round!",
                    color=0x000000
                )
            await ctx.send(embed=result_embed)
            await asyncio.sleep(4)

        if not players:
            return

        sorted_players = sorted(player_scores.items(), key=lambda x: x[1], reverse=True)
        top_winner_id, top_score = sorted_players[0]
        top_winner = discord.utils.get(players, id=top_winner_id)

        economy_cog = cog.bot.get_cog("Economy")
        eco_msg = ""
        player_earnings = {}

        if economy_cog:
            if top_score > 0 and top_winner:
                gross = int(round((50 * len(players)) + (top_score / 10)))
                net, tax = await economy_cog.apply_tax_and_add_balance(top_winner.id, gross, context="Krillion Multiplayer Win")
                player_earnings[top_winner.id] = net
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                if ctx.guild:
                    await cog.record_minigame_win(ctx.guild.id, top_winner.id, "krillion", earnings=net)

            for pid, pscore in sorted_players[1:]:
                if pscore > 0:
                    p_gross = int(round(pscore / 10))
                    if p_gross > 0:
                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Krillion Points Reward")
                        player_earnings[pid] = p_net

        leaderboard_lines = []
        for rank_pos, (pid, pscore) in enumerate(sorted_players, 1):
            puser = discord.utils.get(players, id=pid)
            pname = puser.display_name if puser else f"Player {pid}"
            earn_str = f" (+{format_tad(player_earnings[pid])})" if pid in player_earnings else ""
            leaderboard_lines.append(f"**#{rank_pos}** {pname}: **{pscore} pts**{earn_str}")

        final_embed = discord.Embed(
            title="🏆 Krillion Match Ended!",
            description=(
                f"🥇 **Deepest Diver:** {top_winner.mention if top_winner else 'Unknown'}!{eco_msg}\n\n"
                f"📈 **Final Scoreboard:**\n" + "\n".join(leaderboard_lines)
            ),
            color=0x000000
        )
        await ctx.send(embed=final_embed)
    finally:
        clear_user_game(cog.bot, ctx.author.id)
        for p in players:
            clear_user_game(cog.bot, p.id)
