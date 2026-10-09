from __future__ import annotations
import os
import re
import json
import time
import random
import asyncio
from typing import Optional

import aiohttp
import discord

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    MultiplayerGameSession, record_minigame_win
)
from cogs.games.common import (
    DIFFICULTY_STAKES, parse_minigame_args,
    countdown_reactions, MinigameDifficultyView
)

_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
_PRODUCTS_CACHE: dict = {}


def _load_products():
    global _PRODUCTS_CACHE
    if not _PRODUCTS_CACHE:
        path = os.path.join(_ASSETS_DIR, "price_products.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _PRODUCTS_CACHE = json.load(f)


async def fetch_dummyjson_products(session: aiohttp.ClientSession, count: int = 5) -> list[dict]:
    # Dynamic limitless catalog fetcher
    url = f"https://dummyjson.com/products?limit=100&skip={random.randint(0, 80)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("products", [])
                random.shuffle(items)
                converted = []
                for item in items[:count]:
                    img = item.get("thumbnail") or (item.get("images", [None])[0] if item.get("images") else None)
                    converted.append({
                        "title": item.get("title", "Product"),
                        "category": item.get("category", "General").title(),
                        "price": float(item.get("price", 10.0)),
                        "image_url": img,
                        "description": item.get("description", "")
                    })
                return converted
    except Exception as e:
        print(f"[DummyJSON fetch error]: {e}")
    return []


def parse_price(text: str) -> Optional[float]:
    t = text.strip().lower().replace(",", "").replace("$", "").replace(" ", "")
    try:
        val = float(t)
        if val > 0:
            return val
    except ValueError:
        pass

    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)(k|m|b)?$", t)
    if match:
        val = float(match.group(1))
        suffix = match.group(2)
        if suffix == "k":
            val *= 1_000
        elif suffix == "m":
            val *= 1_000_000
        elif suffix == "b":
            val *= 1_000_000_000
        if val > 0:
            return val
    return None


def format_price_number(val: float | int) -> str:
    if val is None:
        return "$0"
    val = float(val)
    abs_val = abs(val)
    if abs_val >= 1e9:
        num = val / 1e9
        return f"${num:.2f}".rstrip("0").rstrip(".") + "B"
    elif abs_val >= 1e6:
        num = val / 1e6
        return f"${num:.2f}".rstrip("0").rstrip(".") + "M"
    else:
        if val.is_integer() and val >= 1000:
            return f"${int(val):,}"
        return f"${val:,.2f}"


def calculate_price_score(guess: float, actual: float) -> tuple[int, float]:
    if guess <= 0 or actual <= 0:
        return 0, 1.0
    diff_ratio = abs(guess - actual) / actual
    if diff_ratio <= 0.03:
        return 100, diff_ratio  # 🎯 Exact price match (within 3%)
    ratio = max(guess, actual) / min(guess, actual)
    proximity = max(0.0, 1.0 - (ratio - 1.0) / 3.0)
    score = round(proximity * 100)
    return score, diff_ratio


async def run_guesstheprice_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    _load_products()
    set_user_in_game(cog.bot, ctx.author.id, "GuessThePrice")
    round_duration, difficulty = parse_minigame_args(*args, default_duration=25, default_difficulty="easy")
    mult = DIFFICULTY_STAKES.get(difficulty, 1.0)
    total_rounds = 5
    players = []

    try:
        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🏷️ Guess The Price · The Price Is Right",
            description=(
                f"9edder taman tl product b dollar $.\n\n"
                f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\n"
                f"Starts: <t:{int(time.time() + 21)}:R>\n"
                f"Rounds: **{total_rounds}** • Time/Round: **{round_duration}s**\n"
                f"Difficulty: **{difficulty.upper()}** (Stake: **{mult}x**)"
            ),
            color=0x000000
        )
        diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
        start_msg = await ctx.send(embed=signup_embed, view=diff_view)
        await start_msg.add_reaction(join_emoji)
        await asyncio.sleep(19)

        difficulty = diff_view.difficulty
        diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.0)
        diff_view.stop()

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
            ), view=None)
            return

        def _on_price_quit(u):
            quitter = next((p for p in players if p.id == u.id), None)
            if quitter:
                players.remove(quitter)

        session = MultiplayerGameSession("GuessThePrice", list(players), ctx.channel, on_quit=_on_price_quit)
        for p in players:
            set_user_in_game(cog.bot, p.id, "GuessThePrice", session)

        player_scores = {p.id: 0 for p in players}

        # Select items based on difficulty
        tier_items = _PRODUCTS_CACHE.get(difficulty, [])
        selected_items = list(tier_items)
        random.shuffle(selected_items)

        if len(selected_items) < total_rounds and difficulty == "easy":
            extra = await fetch_dummyjson_products(cog.bot.session, total_rounds - len(selected_items))
            selected_items.extend(extra)

        round_items = selected_items[:total_rounds]

        await start_msg.edit(embed=discord.Embed(
            description=f"▶️ **Bdina Guess The Price!** {len(players)} players.",
            color=0x000000
        ), view=None)
        await asyncio.sleep(2)

        round_history = []  # For solo mode breakdown
        for r_idx, item in enumerate(round_items, start=1):
            if len(players) == 0 or session.stopped:
                break

            round_embed = discord.Embed(
                title=f"🏷️ Round {r_idx}/{total_rounds}: {item['title']}",
                description=(
                    f"📦 **Category:** `{item['category']}`\n"
                    f"📝 **Description:** {item.get('description', '')}"),
                color=0x000000
            )
            if item.get("image_url"):
                round_embed.set_image(url=item["image_url"])

            round_msg = await ctx.send(embed=round_embed)

            guesses = {}
            taken_prices = set()
            countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))
            start_time = time.time()

            while time.time() - start_time < round_duration:
                time_left = round_duration - (time.time() - start_time)
                if time_left <= 0:
                    break
                try:
                    def check_m(m):
                        return (
                            any(m.author.id == p.id for p in players)
                            and (m.author.id not in guesses or m.content.strip().lower() == "exitgame")
                            and m.channel.id == ctx.channel.id
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

                    val = parse_price(m.content)
                    if val is not None:
                        if val in taken_prices:
                            try:
                                await m.add_reaction("❌")
                            except Exception:
                                pass
                            continue

                        taken_prices.add(val)
                        pts, diff_ratio = calculate_price_score(val, item["price"])
                        guesses[m.author.id] = {
                            "guess": val,
                            "pts": pts,
                            "diff_ratio": diff_ratio
                        }
                        player_scores[m.author.id] += pts
                        try:
                            await m.add_reaction("🏷️")
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

            actual_price = item["price"]
            actual_str = format_price_number(actual_price)

            if len(players) == 1:
                p_id = players[0].id
                if p_id in guesses:
                    round_history.append((item, format_price_number(guesses[p_id]["guess"]), guesses[p_id]["pts"], actual_str))
                else:
                    round_history.append((item, None, 0, actual_str))

            if guesses:
                sorted_guesses = sorted(guesses.items(), key=lambda x: x[1]["pts"], reverse=True)
                top_id, top_data = sorted_guesses[0]
                top_user = discord.utils.get(players, id=top_id)

                guess_lines = []
                for rank_num, (pid, gdata) in enumerate(sorted_guesses, start=1):
                    puser = discord.utils.get(players, id=pid)
                    pname = puser.display_name if puser else f"Player {pid}"
                    gval = gdata["guess"]
                    g_str = format_price_number(gval)
                    if gdata["pts"] >= 100:
                        guess_lines.append(f"**#{rank_num}** {pname}: `{g_str}` (🎯 Exact • `100%`)")
                    else:
                        guess_lines.append(f"**#{rank_num}** {pname}: `{g_str}` (`{gdata['pts']}%`)")

                res_embed = discord.Embed(
                    title=f"🏷️ Round {r_idx} Real Price: {actual_str}",
                    description=(
                        f"🏆 **Top Guess:** {top_user.mention if top_user else 'Unknown'}!\n\n"
                        f"📊 **Guesses:**\n" + "\n".join(guess_lines)
                    ),
                    color=0x000000
                )
            else:
                res_embed = discord.Embed(
                    title=f"⏰ Round {r_idx} Real Price: {actual_str}",
                    description="Ta7ed majawb f had round!",
                    color=0x000000
                )

            if item.get("image_url"):
                res_embed.set_thumbnail(url=item["image_url"])
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
                gross = int(round((50 * len(players)) + ((top_score / 5) * diff_mult)))
                net, tax = await economy_cog.apply_tax_and_add_balance(top_winner.id, gross, context="Guess The Price Win")
                player_earnings[top_winner.id] = net
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                if ctx.guild:
                    await cog.record_minigame_win(ctx.guild.id, top_winner.id, "guesstheprice", earnings=net)

            for pid, pscore in sorted_players[1:]:
                if pscore > 0:
                    p_gross = int(round((pscore / 6) * diff_mult))
                    if p_gross > 0:
                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Guess The Price Points")
                        player_earnings[pid] = p_net

        if len(players) == 1:
            solo_user = players[0]
            avg_prox = top_score / total_rounds
            round_lines = []
            for idx, (p_item, g_txt, pts, act_txt) in enumerate(round_history, start=1):
                if g_txt:
                    if pts >= 100:
                        round_lines.append(f"• Round {idx}: **{p_item['title']}** — Real `{act_txt}` | Guess `{g_txt}` (🎯 Exact • `100%`)")
                    else:
                        round_lines.append(f"• Round {idx}: **{p_item['title']}** — Real `{act_txt}` | Guess `{g_txt}` (`{pts}%`)")
                else:
                    round_lines.append(f"• Round {idx}: **{p_item['title']}** — Real `{act_txt}` | Time Out (`0%`)")

            final_embed = discord.Embed(
                title="🏆 Guess The Price Solo Results!",
                description=(
                    f"Bravo {solo_user.mention}! Difficulty: **{difficulty.upper()} ({diff_mult}x)**\n"
                    f"Average Proximity: **{avg_prox:.1f}%**{eco_msg}\n\n"
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
                title="🏆 Guess The Price Ended!",
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
