from __future__ import annotations
import os
import re
import json
import math
import time
import random
import asyncio
from typing import Optional

import discord

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    MultiplayerGameSession, record_minigame_win
)
from cogs.games.common import countdown_reactions

_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
_FERMI_CURATED: list[dict] = []
_COUNTRY_CENTROIDS: dict = {}


def _load_fermi_assets():
    global _FERMI_CURATED, _COUNTRY_CENTROIDS
    if not _FERMI_CURATED:
        path = os.path.join(_ASSETS_DIR, "fermi_questions.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _FERMI_CURATED = json.load(f)
    if not _COUNTRY_CENTROIDS:
        cpath = os.path.join(_ASSETS_DIR, "country_centroids.json")
        if os.path.exists(cpath):
            with open(cpath, "r", encoding="utf-8") as f:
                _COUNTRY_CENTROIDS = json.load(f)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_random_fermi_questions(count: int = 5) -> list[dict]:
    _load_fermi_assets()
    questions = []

    # Take 2-3 from curated pool
    curated_pool = list(_FERMI_CURATED)
    random.shuffle(curated_pool)
    for q in curated_pool[:min(3, len(curated_pool))]:
        questions.append(q)

    # Procedurally generate distance questions from country centroids (infinite pool)
    if _COUNTRY_CENTROIDS and len(_COUNTRY_CENTROIDS) >= 2:
        keys = list(_COUNTRY_CENTROIDS.keys())
        while len(questions) < count:
            c1_key, c2_key = random.sample(keys, 2)
            c1 = _COUNTRY_CENTROIDS[c1_key]
            c2 = _COUNTRY_CENTROIDS[c2_key]
            dist = _haversine(c1["lat"], c1["lon"], c2["lat"], c2["lon"])
            if dist > 500:
                dist_rounded = round(dist, -1)  # round to nearest 10 km
                questions.append({
                    "question": f"How many kilometers is the straight-line distance between {c1['name']} and {c2['name']}?",
                    "answer": int(dist_rounded),
                    "unit": "km",
                    "explanation": f"Using great-circle distance coordinates, {c1['name']} to {c2['name']} is approximately {int(dist):,} km."
                })

    random.shuffle(questions)
    return questions[:count]


def parse_estimate(text: str) -> Optional[float]:
    t = text.strip().lower().replace(",", "").replace("$", "").replace(" ", "")
    # Check scientific / power format: 10^9 or 10**9
    pow_match = re.match(r"^10(?:\^|\*\*)(\d+)$", t)
    if pow_match:
        try:
            exp = int(pow_match.group(1))
            if 0 <= exp <= 30:
                return float(10 ** exp)
        except Exception:
            pass

    try:
        val = float(t)
        if val > 0:
            return val
    except ValueError:
        pass

    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)(k|m|b|t|mil|million|billion|trillion)?$", t)
    if match:
        val = float(match.group(1))
        suffix = match.group(2)
        if suffix in ("k",):
            val *= 1_000
        elif suffix in ("m", "mil", "million"):
            val *= 1_000_000
        elif suffix in ("b", "billion"):
            val *= 1_000_000_000
        elif suffix in ("t", "trillion"):
            val *= 1_000_000_000_000
        if val > 0:
            return val
    return None


def format_fermi_number(val: float | int) -> str:
    if val is None:
        return ""
    val = float(val)
    abs_val = abs(val)
    if abs_val >= 1e12:
        num = val / 1e12
        return f"{num:.2f}".rstrip("0").rstrip(".") + "T"
    elif abs_val >= 1e9:
        num = val / 1e9
        return f"{num:.2f}".rstrip("0").rstrip(".") + "B"
    elif abs_val >= 1e6:
        num = val / 1e6
        return f"{num:.2f}".rstrip("0").rstrip(".") + "M"
    else:
        if val.is_integer():
            return f"{int(val):,}"
        return f"{val:,.2f}".rstrip("0").rstrip(".")


def calculate_fermi_score(guess: float, actual: float) -> tuple[int, float]:
    if guess <= 0 or actual <= 0:
        return 0, 99.0
    error = abs(math.log10(guess) - math.log10(actual))
    if error <= 0.12:  # very close (within ~30%)
        return 100, error
    score = max(0, round(100 - (35 * error)))
    return score, error


async def run_fermi_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    _load_fermi_assets()
    set_user_in_game(cog.bot, ctx.author.id, "Fermi")
    round_duration = 25
    total_rounds = 5
    players = []

    try:
        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🎯 Fermi · Make an educated guess",
            description=(
                f"9edder chy ra9m, 3ink mizank hh.\n\n"
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

        def _on_fermi_quit(u):
            quitter = next((p for p in players if p.id == u.id), None)
            if quitter:
                players.remove(quitter)

        session = MultiplayerGameSession("Fermi", list(players), ctx.channel, on_quit=_on_fermi_quit)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Fermi", session)

        player_scores = {p.id: 0 for p in players}
        questions = get_random_fermi_questions(total_rounds)

        await start_msg.edit(embed=discord.Embed(
            description=f"▶️ **Bdina Fermi!** {len(players)} players.",
            color=0x000000
        ))
        await asyncio.sleep(2)

        round_history = []  # For solo mode breakdown
        for r_idx, qdata in enumerate(questions, start=1):
            if len(players) == 0 or session.stopped:
                break

            round_embed = discord.Embed(
                title=f"📐 Round {r_idx}/{total_rounds}: Estimation",
                description=(
                    f"❓ **{qdata['question']}**\n\n"
                    f"Unit: `{qdata['unit']}`"
                ),
                color=0x000000
            )
            round_embed.set_footer(text=f"Fermi • Round {r_idx}/{total_rounds}")
            round_msg = await ctx.send(embed=round_embed)

            guesses = {}
            taken_estimates = set()
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

                    estimate = parse_estimate(m.content)
                    if estimate is not None:
                        # Prevent duplicate identical estimates in same round
                        if estimate in taken_estimates:
                            try:
                                await m.add_reaction("❌")
                            except Exception:
                                pass
                            continue

                        taken_estimates.add(estimate)
                        pts, error = calculate_fermi_score(estimate, qdata["answer"])
                        guesses[m.author.id] = {
                            "estimate": estimate,
                            "pts": pts,
                            "error": error
                        }
                        player_scores[m.author.id] += pts
                        try:
                            await m.add_reaction("📐")
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

            actual_val = qdata["answer"]
            actual_str = format_fermi_number(actual_val)

            if len(players) == 1:
                p_id = players[0].id
                if p_id in guesses:
                    round_history.append((qdata, format_fermi_number(guesses[p_id]["estimate"]), guesses[p_id]["pts"], actual_str))
                else:
                    round_history.append((qdata, None, 0, actual_str))

            if guesses:
                sorted_guesses = sorted(guesses.items(), key=lambda x: x[1]["pts"], reverse=True)
                top_id, top_data = sorted_guesses[0]
                top_user = discord.utils.get(players, id=top_id)

                guess_lines = []
                for rank_num, (pid, gdata) in enumerate(sorted_guesses, start=1):
                    puser = discord.utils.get(players, id=pid)
                    pname = puser.display_name if puser else f"Player {pid}"
                    est = gdata["estimate"]
                    est_str = format_fermi_number(est)
                    if gdata["pts"] >= 100:
                        guess_lines.append(f"**#{rank_num}** {pname}: `{est_str} {qdata['unit']}` (🎯 Exact • `100%`)")
                    else:
                        guess_lines.append(f"**#{rank_num}** {pname}: `{est_str} {qdata['unit']}` (`{gdata['pts']}%`)")

                res_embed = discord.Embed(
                    title=f"🎯 Round {r_idx} Real Answer: {actual_str} {qdata['unit']}",
                    description=(
                        f"🏆 **Top Estimate:** {top_user.mention if top_user else 'Unknown'}!\n\n"
                        f"📊 **Guesses:**\n" + "\n".join(guess_lines) + f"\n\n"
                        f"💡 **Explanation:**\n{qdata.get('explanation', '')}"
                    ),
                    color=0x000000
                )
            else:
                res_embed = discord.Embed(
                    title=f"⏰ Round {r_idx} Real Answer: {actual_str} {qdata['unit']}",
                    description=(
                        f"Ta 7ed majawb f had round!\n\n"
                        f"💡 **Explanation:**\n{qdata.get('explanation', '')}"
                    ),
                    color=0x000000
                )

            await ctx.send(embed=res_embed)
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
                gross = int(round((40 * len(players)) + (top_score / 8)))
                net, tax = await economy_cog.apply_tax_and_add_balance(top_winner.id, gross, context="Fermi Multiplayer Win")
                player_earnings[top_winner.id] = net
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                if ctx.guild:
                    await cog.record_minigame_win(ctx.guild.id, top_winner.id, "fermi", earnings=net)

            for pid, pscore in sorted_players[1:]:
                if pscore > 0:
                    p_gross = int(round(pscore / 10))
                    if p_gross > 0:
                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Fermi Points Reward")
                        player_earnings[pid] = p_net

        if len(players) == 1:
            solo_user = players[0]
            avg_prox = top_score / total_rounds
            round_lines = []
            for idx, (q, est_txt, pts, act_txt) in enumerate(round_history, start=1):
                if est_txt:
                    if pts >= 100:
                        round_lines.append(f"• Round {idx}: Real `{act_txt} {q['unit']}` — `{est_txt} {q['unit']}` (🎯 Exact • `100%`)")
                    else:
                        round_lines.append(f"• Round {idx}: Real `{act_txt} {q['unit']}` — `{est_txt} {q['unit']}` (`{pts}%`)")
                else:
                    round_lines.append(f"• Round {idx}: Real `{act_txt} {q['unit']}` — Time Out (`0%`)")

            final_embed = discord.Embed(
                title="🏆 Fermi Solo Results!",
                description=(
                    f"Bravo {solo_user.mention}! Average Proximity: **{avg_prox:.1f}%**{eco_msg}\n\n"
                    f"**Round Breakdown:**\n" + "\n".join(round_lines)
                ),
                color=0x000000
            )
            await ctx.send(embed=final_embed)
        else:
            leaderboard_lines = []
            for rank_pos, (pid, pscore) in enumerate(sorted_players, 1):
                puser = discord.utils.get(players, id=pid)
                pname = puser.display_name if puser else f"Player {pid}"
                earn_str = f" (+{format_tad(player_earnings[pid])})" if pid in player_earnings else ""
                avg_prox = pscore / total_rounds
                leaderboard_lines.append(f"**#{rank_pos}** {pname}: **{avg_prox:.1f}%**{earn_str}")

            final_embed = discord.Embed(
                title="🏆 Fermi Match Ended!",
                description=(
                    f"🥇 **Winner:** {top_winner.mention if top_winner else 'Unknown'}!{eco_msg}\n\n"
                    f"📈 **Final Scoreboard:**\n" + "\n".join(leaderboard_lines)
                ),
                color=0x000000
            )
            await ctx.send(embed=final_embed)
    finally:
        clear_user_game(cog.bot, ctx.author.id)
        for p in players:
            clear_user_game(cog.bot, p.id)
