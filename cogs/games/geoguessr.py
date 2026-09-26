from __future__ import annotations
import os
import io
import json
import math
import asyncio
import time
import random
from typing import Optional

import aiohttp
import discord
from PIL import Image

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    attach_game_session, MultiplayerGameSession, record_minigame_win
)
from cogs.games.common import (
    DIFFICULTY_STAKES, parse_minigame_args,
    countdown_reactions, MinigameDifficultyView
)

# ============ GEOGUESSR & GUESS THE RANK HELPERS ============

GEOGUESSR_LOCATIONS = []
COUNTRY_CENTROIDS = {}

def _load_geoguessr_assets():
    global GEOGUESSR_LOCATIONS, COUNTRY_CENTROIDS
    if not GEOGUESSR_LOCATIONS:
        loc_path = os.path.join("assets", "geoguessr_locations.json")
        if os.path.exists(loc_path):
            with open(loc_path, "r", encoding="utf-8") as f:
                GEOGUESSR_LOCATIONS = json.load(f)
    if not COUNTRY_CENTROIDS:
        cent_path = os.path.join("assets", "country_centroids.json")
        if os.path.exists(cent_path):
            with open(cent_path, "r", encoding="utf-8") as f:
                COUNTRY_CENTROIDS = json.load(f)

def get_country_flag_emoji(code: str) -> str:
    if not code or len(code) != 2:
        return "🌍"
    return "".join(chr(ord(c) + 127397) for c in code.upper())

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def resolve_country_guess(guess: str):
    _load_geoguessr_assets()
    if not guess:
        return None
    clean = normalize_country_text(guess)
    if not clean:
        return None
    clean_no_spaces = clean.replace(" ", "")

    # 1. Exact match on ISO code, country name, or alias
    for code, data in COUNTRY_CENTROIDS.items():
        if clean == code:
            return {"code": code, "name": data["name"], "lat": data["lat"], "lon": data["lon"]}
        c_name_norm = normalize_country_text(data.get("name", ""))
        if clean == c_name_norm or clean_no_spaces == c_name_norm.replace(" ", ""):
            return {"code": code, "name": data["name"], "lat": data["lat"], "lon": data["lon"]}
        for alias in data.get("aliases", []):
            norm_a = normalize_country_text(alias)
            if clean == norm_a or clean_no_spaces == norm_a.replace(" ", ""):
                return {"code": code, "name": data["name"], "lat": data["lat"], "lon": data["lon"]}

    # 2. Fuzzy match across all country names and aliases
    candidates = {}
    for code, data in COUNTRY_CENTROIDS.items():
        candidates[normalize_country_text(data["name"])] = code
        for a in data.get("aliases", []):
            candidates[normalize_country_text(a)] = code

    best_match = None
    best_ratio = 0.0
    for cand_text, cand_code in candidates.items():
        ratio = difflib.SequenceMatcher(None, clean, cand_text).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = cand_code

    if best_ratio >= 0.82 and best_match:
        cdata = COUNTRY_CENTROIDS[best_match]
        return {"code": best_match, "name": cdata["name"], "lat": cdata["lat"], "lon": cdata["lon"]}

    return None

def calculate_geoguessr_proximity(target_code: str, target_lat: float, target_lon: float,
                                   guessed_code: str, guessed_lat: float, guessed_lon: float):
    if target_code.lower() == guessed_code.lower():
        return 1.0, 0.0
    dist = haversine_distance(target_lat, target_lon, guessed_lat, guessed_lon)
    proximity = max(0.0, 1.0 - (dist / 5000.0))
    return round(proximity, 4), dist

async def get_geoguessr_round_location(pool: list, difficulty: str = None) -> dict:
    if pool:
        return pool.pop()
    _load_geoguessr_assets()
    filtered = [loc for loc in GEOGUESSR_LOCATIONS if difficulty is None or loc.get("difficulty") == difficulty]
    if not filtered:
        filtered = GEOGUESSR_LOCATIONS
    return random.choice(filtered) if filtered else None

_geoguessr_image_cache: dict[str, bytes] = {}

async def _get_geoguessr_image(session: Optional[aiohttp.ClientSession], image_url: str) -> Optional[io.BytesIO]:
    if not image_url:
        return None

    cached = _geoguessr_image_cache.get(image_url)
    if cached:
        buf = io.BytesIO(cached)
        buf.seek(0)
        return buf

    headers = {
        "User-Agent": "SifdineDiscordBot/1.0 (https://github.com/ayoubanlouf/SifdineDiscordBot; contact@sifdine.bot) aiohttp/3.9"
    }

    raw_data = None
    close_session = False
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        async with session.get(image_url, headers=headers, timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True) as resp:
            if resp.status == 200:
                raw_data = await resp.read()
    except Exception as e:
        print(f"[_get_geoguessr_image fetch error]: {e}")
    finally:
        if close_session:
            await session.close()

    if not raw_data:
        return None

    try:
        def _compress():
            with Image.open(io.BytesIO(raw_data)) as img:
                img = img.convert("RGB")
                img.thumbnail((1280, 800), Image.Resampling.BILINEAR)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=85, optimize=True)
                return out.getvalue()

        compressed_bytes = await asyncio.to_thread(_compress)
    except Exception as e:
        print(f"[_get_geoguessr_image compress error]: {e}")
        compressed_bytes = raw_data

    if len(_geoguessr_image_cache) > 25:
        _geoguessr_image_cache.pop(next(iter(_geoguessr_image_cache)))
    _geoguessr_image_cache[image_url] = compressed_bytes

    buf = io.BytesIO(compressed_bytes)
    buf.seek(0)
    return buf



async def run_geoguessr_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "GeoGuessr")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=40, default_difficulty="easy")
        diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.5)

        _load_geoguessr_assets()
        if not GEOGUESSR_LOCATIONS:
            await ctx.send("❌ Mal9itch locations database dial GeoGuessr.")
            return

        pool = [loc for loc in GEOGUESSR_LOCATIONS if loc.get("difficulty") == difficulty]
        if not pool:
            pool = list(GEOGUESSR_LOCATIONS)
        random.shuffle(pool)
        if pool:
            asyncio.create_task(_get_geoguessr_image(cog.bot.session, pool[-1].get("image_url")))

        join_emoji = "✅"
        start_ts = int(time.time() + 21)
        signup_embed = discord.Embed(
            title="🌍 GeoGuessr Challenge!",
            description=(
                f"Clicki 3la {join_emoji} bach tdkhel lgame.\n"
                f"• Guess the **Country**: Kteb smit dawla f chat!\n\n"
                f"Starts: <t:{start_ts}:R>\n"
                f"Time per round: **{round_duration}s**\n"
                f"Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)"
            ),
            color=0x000000
        )
        diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
        signup_msg = await ctx.send(embed=signup_embed, view=diff_view)
        await signup_msg.add_reaction(join_emoji)

        await asyncio.sleep(19)

        difficulty = diff_view.difficulty
        diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.5)
        diff_view.stop()

        pool = [loc for loc in GEOGUESSR_LOCATIONS if loc.get("difficulty") == difficulty]
        if not pool:
            pool = list(GEOGUESSR_LOCATIONS)
        random.shuffle(pool)
        if pool:
            asyncio.create_task(_get_geoguessr_image(cog.bot.session, pool[-1].get("image_url")))

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
            await ctx.send("❌ 7ed madkhel l GeoGuessr. Game t'annula.")
            return

        session = MultiplayerGameSession("GeoGuessr", players, ctx.channel)
        for p in players:
            set_user_in_game(cog.bot, p.id, "GeoGuessr", session)

        try:
            total_rounds = min(5, len(pool))
            is_solo = (len(players) == 1)

            if is_solo:
                player = players[0]
                await ctx.send(f"🎮 **Solo GeoGuessr:** {player.mention} 3ndek **{total_rounds} rounds**! Kteb smit **dawla** f chat!.")
                total_round_stakes = 0.0
                round_history = []

                for r in range(1, total_rounds + 1):
                    if session.stopped:
                        break
                    loc = await get_geoguessr_round_location(pool, difficulty)
                    if not loc:
                        break
                    target_code = loc.get("code", "")
                    target_country = loc["country"]
                    target_lat = loc["lat"]
                    target_lon = loc["lon"]
                    target_flag = get_country_flag_emoji(target_code)

                    round_embed = discord.Embed(
                        title=f"🌍 Round {r}/{total_rounds} — Guess the country!",
                        description=(
                            f"Kteb smit **dawla** f chat!\n"
                            f"⌛ Time: **{round_duration}s**"
                        ),
                        color=0x000000
                    )
                    round_embed.set_footer(text=f"GeoGuessr Solo • Round {r}/{total_rounds} • Difficulty: {difficulty.upper()}")
                    img_buf = await _get_geoguessr_image(cog.bot.session, loc.get("image_url"))
                    if img_buf:
                        geo_file = discord.File(img_buf, filename="geoguessr.jpg")
                        round_embed.set_image(url="attachment://geoguessr.jpg")
                        round_msg = await ctx.send(embed=round_embed, file=geo_file)
                    else:
                        round_embed.set_image(url=loc["image_url"])
                        round_msg = await ctx.send(embed=round_embed)

                    # Prefetch next location's image in background while player is guessing
                    if pool:
                        asyncio.create_task(_get_geoguessr_image(cog.bot.session, pool[-1].get("image_url")))

                    def check(m):
                        return m.author.id == player.id and m.channel.id == ctx.channel.id

                    countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))
                    start_time = time.time()
                    guessed = False
                    guess_data = None

                    while time.time() - start_time < round_duration:
                        time_left = round_duration - (time.time() - start_time)
                        if time_left <= 0:
                            break
                        try:
                            m = await cog.bot.wait_for("message", check=check, timeout=time_left)
                            if m.content.strip().lower() == "exitgame":
                                if not countdown_task.done():
                                    countdown_task.cancel()
                                await ctx.send(f"🚪 {player.mention} khrej mn lgame.")
                                return

                            cguess = resolve_country_guess(m.content)
                            if cguess:
                                proximity, dist = calculate_geoguessr_proximity(
                                    target_code, target_lat, target_lon,
                                    cguess["code"], cguess["lat"], cguess["lon"]
                                )
                                round_stake = proximity * 50
                                guess_data = (cguess, proximity, dist, round_stake)
                                guessed = True
                                if not countdown_task.done():
                                    countdown_task.cancel()
                                try:
                                    await m.add_reaction("📍")
                                except Exception:
                                    pass
                                break
                        except asyncio.TimeoutError:
                            break

                    if not countdown_task.done():
                        countdown_task.cancel()

                    maps_link = f"https://www.google.com/maps/@{target_lat},{target_lon},6z"
                    if guessed and guess_data:
                        cguess, proximity, dist, round_stake = guess_data
                        total_round_stakes += round_stake
                        round_history.append((loc, cguess["name"], proximity, dist, round_stake))

                        if proximity >= 1.0:
                            header = "🎯 **EXACT COUNTRY! Nta Naadi!**"
                            prox_str = "**100%** (Dawla s7i7a!)"
                        else:
                            header = f"📍 **Target: {target_flag} {target_country}**"
                            prox_str = f"**{proximity*100:.1f}%** (b3id b {dist:,.0f} km)"

                        res_embed = discord.Embed(
                            title=f"{target_flag} Round {r} Result — {target_country}",
                            description=(
                                f"{header}\n\n"
                                f"🎮 Lkhtiyar dialek: **{cguess['name']}**\n"
                                f"📏 Proximity: {prox_str}\n\n"
                                f"🗺️ **[Choufha f Google Maps]({maps_link})**"
                            ),
                            color=0x000000
                        )
                    else:
                        round_history.append((loc, "None", 0.0, 20000, 0.0))
                        res_embed = discord.Embed(
                            title=f"⏰ Time Out! Real Country: {target_flag} {target_country}",
                            description=(
                                f"Majawbtich f lwe9t! Proximity: **0%**\n\n"
                                f"🗺️ **[Choufha f Google Maps]({maps_link})**"
                            ),
                            color=0x000000
                        )
                    reveal_buf = await _get_geoguessr_image(cog.bot.session, loc.get("image_url"))
                    if reveal_buf:
                        rev_file = discord.File(reveal_buf, filename="reveal.jpg")
                        res_embed.set_thumbnail(url="attachment://reveal.jpg")
                        await ctx.send(embed=res_embed, file=rev_file)
                    else:
                        res_embed.set_thumbnail(url=loc["image_url"])
                        await ctx.send(embed=res_embed)
                    await asyncio.sleep(4)

                economy_cog = cog.bot.get_cog("Economy")
                eco_msg = ""
                gross = int(round(50 * 1 + total_round_stakes * diff_mult))
                if economy_cog and total_round_stakes > 0:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context="GeoGuessr Solo")
                    eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                    if ctx.guild:
                        await cog.record_minigame_win(ctx.guild.id, player.id, "geoguessr", earnings=net)

                summary_embed = discord.Embed(
                    title="🏆 GeoGuessr Solo Results!",
                    description=(
                        f"Bravo {player.mention}! Difficulty: **{difficulty.upper()} ({diff_mult}x)**!{eco_msg}\n\n"
                        f"**Round Breakdown:**\n"
                        + "\n".join([
                            f"• Round {idx+1}: **{h[0]['country']}** — {h[1]} (`{h[3]:,.0f} km` • `{h[2]*100:.0f}%`)"
                            if h[1] != "None" else
                            f"• Round {idx+1}: **{h[0]['country']}** — Time Out (`0%`)"
                            for idx, h in enumerate(round_history)
                        ])
                    ),
                    color=0x000000
                )
                await ctx.send(embed=summary_embed)

            else:
                player_stakes = {p.id: 0.0 for p in players}

                for r in range(1, total_rounds + 1):
                    if len(players) == 0 or session.stopped:
                        break
                    loc = await get_geoguessr_round_location(pool, difficulty)
                    if not loc:
                        break
                    target_code = loc.get("code", "")
                    target_country = loc["country"]
                    target_lat = loc["lat"]
                    target_lon = loc["lon"]
                    target_flag = get_country_flag_emoji(target_code)

                    round_embed = discord.Embed(
                        title=f"🌍 Round {r}/{total_rounds} — Guess the country!",
                        description=(
                            f"Kul wa7ed 3ndo **1 guess**! Kteb smit **dawla** f chat.\n"
                            f"⚠️ Dawla li tgalt makat3awdch!\n"
                            f"⌛ Time: **{round_duration}s**"
                        ),
                        color=0x000000
                    )
                    round_embed.set_footer(text=f"GeoGuessr Multi • Round {r}/{total_rounds} • Difficulty: {difficulty.upper()}")
                    img_buf = await _get_geoguessr_image(cog.bot.session, loc.get("image_url"))
                    if img_buf:
                        geo_file = discord.File(img_buf, filename="geoguessr.jpg")
                        round_embed.set_image(url="attachment://geoguessr.jpg")
                        round_msg = await ctx.send(embed=round_embed, file=geo_file)
                    else:
                        round_embed.set_image(url=loc["image_url"])
                        round_msg = await ctx.send(embed=round_embed)

                    # Prefetch next location's image in background while players are guessing
                    if pool:
                        asyncio.create_task(_get_geoguessr_image(cog.bot.session, pool[-1].get("image_url")))

                    guesses = {}
                    taken_codes = set()
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

                            cguess = resolve_country_guess(m.content)
                            if cguess:
                                if cguess["code"] in taken_codes:
                                    continue
                                taken_codes.add(cguess["code"])
                                proximity, dist = calculate_geoguessr_proximity(
                                    target_code, target_lat, target_lon,
                                    cguess["code"], cguess["lat"], cguess["lon"]
                                )
                                round_stake = proximity * 50
                                guesses[m.author.id] = (cguess, proximity, dist, round_stake)
                                player_stakes[m.author.id] += round_stake
                                try:
                                    await m.add_reaction("📍")
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

                    maps_link = f"https://www.google.com/maps/@{target_lat},{target_lon},6z"

                    if guesses:
                        sorted_guesses = sorted(guesses.items(), key=lambda item: item[1][1], reverse=True)
                        round_winner_id, round_winner_data = sorted_guesses[0]
                        round_winner_user = discord.utils.get(players, id=round_winner_id)

                        guess_lines = []
                        for rank_num, (pid, pdata) in enumerate(sorted_guesses, 1):
                            puser = discord.utils.get(players, id=pid)
                            pname = puser.display_name if puser else f"Player {pid}"
                            cguess, prox, dist, stake = pdata
                            if prox >= 1.0:
                                guess_lines.append(f"**#{rank_num}** {pname}: **{cguess['name']}** (🎯 Exact • `100%`)")
                            else:
                                guess_lines.append(f"**#{rank_num}** {pname}: **{cguess['name']}** (`{dist:,.0f} km` • `{prox*100:.1f}%`)")

                        res_embed = discord.Embed(
                            title=f"{target_flag} Round {r} Target: {target_country}",
                            description=(
                                f"🏆 **Top Guess f had round:** {round_winner_user.mention if round_winner_user else 'Unknown'}!\n\n"
                                f"📊 **All Guesses:**\n" + "\n".join(guess_lines) + f"\n\n🗺️ **[Google Maps]({maps_link})**"
                            ),
                            color=0x000000
                        )
                    else:
                        res_embed = discord.Embed(
                            title=f"⏰ Round {r} Real Country: {target_flag} {target_country}",
                            description=f"7ed majawb f had round!\n\n🗺️ **[Google Maps]({maps_link})**",
                            color=0x000000
                        )
                    reveal_buf = await _get_geoguessr_image(cog.bot.session, loc.get("image_url"))
                    if reveal_buf:
                        rev_file = discord.File(reveal_buf, filename="reveal.jpg")
                        res_embed.set_thumbnail(url="attachment://reveal.jpg")
                        await ctx.send(embed=res_embed, file=rev_file)
                    else:
                        res_embed.set_thumbnail(url=loc["image_url"])
                        await ctx.send(embed=res_embed)
                    await asyncio.sleep(4)

                sorted_stakes = sorted(player_stakes.items(), key=lambda x: x[1], reverse=True)
                top_winner_id, top_stake = sorted_stakes[0]
                top_winner = discord.utils.get(players, id=top_winner_id)

                economy_cog = cog.bot.get_cog("Economy")
                eco_msg = ""
                player_earnings = {}

                if economy_cog:
                    # 1st place: 50 * len(players) + top_stake * diff_mult
                    if top_stake > 0 and top_winner:
                        gross = int(round(50 * len(players) + top_stake * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(top_winner.id, gross, context="GeoGuessr Multiplayer Win")
                        player_earnings[top_winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, top_winner.id, "geoguessr", earnings=net)

                    # Other places: pstake * diff_mult (without the 50 * len(players) bonus)
                    for pid, pstake in sorted_stakes[1:]:
                        if pstake > 0:
                            p_gross = int(round(pstake * diff_mult))
                            if p_gross > 0:
                                p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="GeoGuessr Points Reward")
                                player_earnings[pid] = p_net

                leaderboard_lines = []
                for rank_pos, (pid, pstake) in enumerate(sorted_stakes, 1):
                    puser = discord.utils.get(players, id=pid)
                    pname = puser.display_name if puser else f"Player {pid}"
                    earn_str = f" (+{format_tad(player_earnings[pid])})" if pid in player_earnings else ""
                    leaderboard_lines.append(f"**#{rank_pos}** {pname}{earn_str}")

                final_embed = discord.Embed(
                    title="🏆 GeoGuessr Match Ended!",
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


