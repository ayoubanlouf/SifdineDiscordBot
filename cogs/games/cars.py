from __future__ import annotations
import os
import io
import json
import re
import difflib
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

# ============ CAR MODEL GUESSING HELPERS ============

_CARS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "cars.json"))
_cars_dataset = None

def _load_cars_dataset():
    global _cars_dataset
    if _cars_dataset is None:
        if os.path.exists(_CARS_PATH):
            try:
                with open(_CARS_PATH, "r", encoding="utf-8") as f:
                    _cars_dataset = json.load(f)
            except Exception:
                _cars_dataset = []
        else:
            _cars_dataset = []
    return _cars_dataset

def normalize_car_text(text: str) -> str:
    t = text.lower().strip()
    if t.startswith("the "):
        t = t[4:].strip()
    elif t.startswith("a "):
        t = t[2:].strip()
    elif t.startswith("an "):
        t = t[3:].strip()
    t = re.sub(r"[.,'\-_/&]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def is_car_guess_correct(guess: str, car: dict) -> bool:
    norm_guess = normalize_car_text(guess)
    if not norm_guess:
        return False

    targets = [
        car.get("model", ""),
        car.get("displayName", ""),
        f"{car.get('make', '')} {car.get('model', '')}"
    ] + car.get("aliases", [])

    guess_compact = norm_guess.replace(" ", "")
    guess_digits = re.findall(r"\d+", norm_guess)
    guess_tokens = set(norm_guess.split())

    for target in targets:
        if not target:
            continue
        norm_target = normalize_car_text(target)
        target_compact = norm_target.replace(" ", "")
        target_digits = re.findall(r"\d+", norm_target)
        target_tokens = set(norm_target.split())

        # 1. Exact matches (standard or compact)
        if norm_guess == norm_target or guess_compact == target_compact:
            return True

        # 2. Strict digit check: If digits exist in either target or guess, digits MUST match exactly
        if target_digits or guess_digits:
            if target_digits != guess_digits:
                continue

        # 3. Critical short model tokens (<= 2 chars, like 'y', '3', 'e', 'c', 's', 'x', 'm3')
        # These are distinguishing model grades/letters that must never be mismatched
        critical_tokens = {tok for tok in target_tokens if len(tok) <= 2 and tok not in ("de", "la", "le", "di", "el", "un")}
        if critical_tokens:
            tokens_ok = True
            for ctok in critical_tokens:
                if ctok not in guess_tokens:
                    # check if ends or starts with token in compact form (e.g. 'modely' ending with 'y')
                    if not (guess_compact.endswith(ctok) or guess_compact.startswith(ctok)):
                        tokens_ok = False
                        break
            if not tokens_ok:
                continue

        # 4. Fuzzy typo matching:
        # Only allowed for long model names (>= 5 chars).
        # Differing tokens cannot be short model identifiers (<= 2 chars like 'z' vs 'y')
        if len(norm_guess) >= 5 and len(norm_target) >= 5:
            ratio = difflib.SequenceMatcher(None, norm_guess, norm_target).ratio()
            compact_ratio = difflib.SequenceMatcher(None, guess_compact, target_compact).ratio()

            if ratio >= 0.88 or compact_ratio >= 0.88:
                diff_tokens = (target_tokens - guess_tokens) | (guess_tokens - target_tokens)
                if any(len(dt) <= 2 for dt in diff_tokens):
                    continue
                return True

    return False

def _clean_car_image_url(url: str) -> str:
    if not url:
        return ""
    m = re.search(r'/wikipedia/commons/(?:thumb/)?([0-9a-f]/[0-9a-f]{2})/([^/]+)', url)
    if m:
        return f"https://upload.wikimedia.org/wikipedia/commons/{m.group(1)}/{m.group(2)}"
    if "Special:FilePath/" in url:
        return url.split("?")[0].replace("http://", "https://")
    return url


def _get_car_image_url(car: dict) -> str:
    if not isinstance(car, dict):
        return ""
    images = car.get("images")
    if isinstance(images, list) and images:
        return random.choice(images)
    return car.get("image_url", "")


async def _get_compressed_car_image(session: Optional[aiohttp.ClientSession], image_url: str) -> Optional[io.BytesIO]:
    if not image_url:
        return None

    headers = {
        "User-Agent": "SifdineDiscordBot/1.0 (https://github.com/ayoubanlouf/SifdineDiscordBot; contact@sifdine.bot) aiohttp/3.9"
    }

    urls_to_try = []
    if "upload.wikimedia.org" in image_url or "Special:FilePath" in image_url:
        filename = image_url.split("/")[-1].split("?")[0]
        filename_decoded = urllib.parse.unquote(filename)
        urls_to_try.append(f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(filename_decoded)}?width=960")
    urls_to_try.append(image_url)

    raw_data = None
    close_session = False
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        for url in urls_to_try:
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4), allow_redirects=True) as resp:
                    if resp.status == 200:
                        cl = resp.headers.get("Content-Length")
                        if cl and int(cl) > 2500000:
                            # Skip raw multi-megabyte originals to protect container RAM
                            continue
                        data = await resp.read()
                        if data and len(data) > 500:
                            raw_data = data
                            break
            except Exception:
                continue
    finally:
        if close_session:
            await session.close()

    if not raw_data:
        return None

    # If already a compact thumbnail (< 600 KB, which Wikimedia 960px thumbnails always are ~150 KB),
    # bypass Pillow entirely! This prevents memory spikes and saves 100% of image decode RAM.
    if len(raw_data) <= 600 * 1024:
        buf = io.BytesIO(raw_data)
        buf.seek(0)
        return buf

    # For rare images > 600 KB, use memory-efficient draft scaling
    try:
        def _quick_downscale():
            with Image.open(io.BytesIO(raw_data)) as img:
                img.draft("RGB", (960, 650))
                img = img.convert("RGB")
                img.thumbnail((960, 650), Image.Resampling.BILINEAR)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=82, optimize=True)
                return out.getvalue()

        compressed_bytes = await asyncio.to_thread(_quick_downscale)
        buf = io.BytesIO(compressed_bytes)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"[_get_compressed_car_image downscale error]: {e}")
        buf = io.BytesIO(raw_data)
        buf.seek(0)
        return buf





async def run_cars_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "Guess The Car")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=20, default_difficulty="easy")
        time_display = f"{round_duration}s"
        mult = DIFFICULTY_STAKES[difficulty]

        all_cars = _load_cars_dataset()
        if not all_cars:
            await ctx.send(embed=discord.Embed(
                description="❌ Ma l9itch data dial tomobilat!",
                color=0x000000
            ))
            return

        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🚗 Guess The Car Model",
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nDifficulty: **{difficulty.upper()}** (Stake: **{mult}x**)",
            color=0x000000
        )
        diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
        signup_msg = await ctx.send(embed=signup_embed, view=diff_view)
        try:
            await signup_msg.add_reaction(join_emoji)
        except Exception:
            pass

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
            await signup_msg.edit(embed=discord.Embed(description="💨 7ta wa7d ma dkhel lgame ._.", color=0x000000), view=None)
            return

        active_players = list(players)
        session = MultiplayerGameSession("Guess The Car", active_players, ctx.channel)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Guess The Car", session)

        try:
            single_player = len(players) == 1
            hp = {player.id: 3 for player in players}

            # Apply difficulty filter
            if difficulty == "easy":
                pool = [c for c in all_cars if c.get("difficulty") == "easy"] or all_cars
            elif difficulty == "medium":
                pool = [c for c in all_cars if c.get("difficulty") in ("easy", "medium")] or all_cars
            else:
                pool = list(all_cars)

            match_pool = list(pool)
            random.shuffle(match_pool)

            # Prefetch first car so round 1 starts with 0ms delay
            if match_pool:
                asyncio.create_task(_get_compressed_car_image(cog.bot.session, _clean_car_image_url(_get_car_image_url(match_pool[-1]))))

            start_embed = discord.Embed(
                description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                color=0x000000
            )
            await signup_msg.edit(embed=start_embed, view=None)
            await asyncio.sleep(2)

            player_correct_cars = {p.id: 0 for p in players}

            while len(active_players) > 0 and not session.stopped:
                if not single_player and len(active_players) == 1:
                    winner = active_players[0]
                    economy_cog = cog.bot.get_cog("Economy")
                    eco_msg = ""
                    player_earnings = {}
                    winner_cars = player_correct_cars.get(winner.id, 0)
                    if economy_cog:
                        gross = (len(players) * 50) + int(round((winner_cars * 15) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Car Model Win")
                        player_earnings[winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner.id, "guessthecar", earnings=net)

                        for pid, p_cars in player_correct_cars.items():
                            if pid != winner.id and p_cars > 0:
                                p_gross = int(round((p_cars * 15) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Car Model Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_cars[pid]} cars)" for pid, net in player_earnings.items() if pid != winner.id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    win_embed = discord.Embed(
                        description=f"🏆 {winner.mention} rbe7 lgame b **{winner_cars} cars**!{eco_msg}{others_msg}",
                        color=0x000000
                    )
                    await ctx.send(embed=win_embed)
                    return

                if not match_pool:
                    await ctx.send(embed=discord.Embed(
                        description="🏁 **Cars pool salaw kamlin! Game sala.**",
                        color=0x000000
                    ))
                    break

                for player in list(active_players):
                    if player not in active_players:
                        continue
                    if not single_player and len(active_players) <= 1:
                        break

                    if not match_pool:
                        break

                    target = match_pool.pop()
                    correct_name = target.get("displayName", target.get("model", ""))

                    game_embed = discord.Embed(
                        description=f"🚗 Chno smit lmodel ta3 had tomobila?\n⌛ Time: {round_duration}s\n❤️ HP: {hp[player.id]}\n📦 Cars left: **{len(match_pool) + 1}**",
                        color=0x000000
                    )
                    car_img = _clean_car_image_url(_get_car_image_url(target))
                    compressed_buf = await _get_compressed_car_image(cog.bot.session, car_img)
                    if compressed_buf:
                        car_file = discord.File(compressed_buf, filename="car.jpg")
                        game_embed.set_image(url="attachment://car.jpg")
                        round_msg = await ctx.send(player.mention, embed=game_embed, file=car_file)
                    else:
                        game_embed.set_image(url=car_img)
                        round_msg = await ctx.send(player.mention, embed=game_embed)

                    # Prefetch next car in background while player is guessing
                    if match_pool:
                        next_target_url = _clean_car_image_url(_get_car_image_url(match_pool[-1]))
                        asyncio.create_task(_get_compressed_car_image(cog.bot.session, next_target_url))

                    def check(m):
                        if m.channel.id != ctx.channel.id:
                            return False
                        if m.author.id == player.id:
                            return True
                        if m.content.strip().lower() == "exitgame" and any(p.id == m.author.id for p in active_players):
                            return True
                        return False

                    start_time = time.time()
                    guessed_correctly = False
                    countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                    while time.time() - start_time < round_duration:
                        time_left = round_duration - (time.time() - start_time)
                        if time_left <= 0:
                            break

                        try:
                            msg = await cog.bot.wait_for("message", check=check, timeout=time_left)

                            if msg.content.strip().lower() == "exitgame":
                                leaver = next((p for p in active_players if p.id == msg.author.id), None)
                                if leaver:
                                    hp[leaver.id] = 0
                                    active_players.remove(leaver)
                                    await ctx.send(f"🚪 **{leaver.mention}** khrej mn lgame.")
                                    if leaver.id == player.id:
                                        if not countdown_task.done():
                                            countdown_task.cancel()
                                        guessed_correctly = True
                                        break
                                    elif not single_player and len(active_players) <= 1:
                                        if not countdown_task.done():
                                            countdown_task.cancel()
                                        break
                                continue

                            if msg.author.id == player.id and is_car_guess_correct(msg.content, target):
                                if not countdown_task.done():
                                    countdown_task.cancel()
                                try:
                                    await msg.add_reaction("✅")
                                except Exception:
                                    pass
                                guessed_correctly = True
                                player_correct_cars[player.id] = player_correct_cars.get(player.id, 0) + 1
                                break

                        except asyncio.TimeoutError:
                            break

                    if not countdown_task.done():
                        countdown_task.cancel()

                    if not single_player and len(active_players) <= 1:
                        break

                    if not guessed_correctly and player in active_players:
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

            if single_player:
                player = players[0]
                economy_cog = cog.bot.get_cog("Economy")
                p_cars = player_correct_cars.get(player.id, 0)
                gross = 50 + int(round((p_cars * 15) * diff_mult))
                eco_msg = ""
                if economy_cog and p_cars > 0:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context="Car Model Solo")
                    eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                    if ctx.guild:
                        await cog.record_minigame_win(ctx.guild.id, player.id, "guessthecar", earnings=net)
                await ctx.send(embed=discord.Embed(
                    description=f"🎯 Game Over {player.mention}! L9iti **{p_cars} cars**.{eco_msg}",
                    color=0x000000
                ))
            elif not single_player:
                max_guesses = max(player_correct_cars.values()) if player_correct_cars else 0
                if max_guesses > 0:
                    top_players = [p for p in players if player_correct_cars.get(p.id, 0) == max_guesses]
                    economy_cog = cog.bot.get_cog("Economy")
                    player_earnings = {}
                    if len(top_players) == 1:
                        winner = top_players[0]
                        eco_msg = ""
                        if economy_cog:
                            gross = (len(players) * 50) + int(round((max_guesses * 15) * diff_mult))
                            net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Car Model Win")
                            player_earnings[winner.id] = net
                            eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                            if ctx.guild:
                                await cog.record_minigame_win(ctx.guild.id, winner.id, "guessthecar", earnings=net)

                            for pid, p_cars in player_correct_cars.items():
                                if pid != winner.id and p_cars > 0:
                                    p_gross = int(round((p_cars * 15) * diff_mult))
                                    if p_gross > 0:
                                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Car Model Reward")
                                        player_earnings[pid] = p_net

                        others_msg = ""
                        other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_cars[pid]} cars)" for pid, net in player_earnings.items() if pid != winner.id]
                        if other_rewards:
                            others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                        await ctx.send(embed=discord.Embed(
                            description=f"🏆 {winner.mention} 3ndo a3la score b **{max_guesses} cars** o rbe7 lgame!{eco_msg}{others_msg}",
                            color=0x000000
                        ))
                    else:
                        winners_mention = " o ".join(p.mention for p in top_players)
                        if economy_cog:
                            for pid, p_cars in player_correct_cars.items():
                                if p_cars > 0:
                                    p_gross = int(round((p_cars * 15) * diff_mult))
                                    if p_gross > 0:
                                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Car Model Reward (Tie)")
                                        player_earnings[pid] = p_net

                        others_msg = ""
                        rewards_list = [f"<@{pid}>: **+{net}** TAD ({player_correct_cars[pid]} cars)" for pid, net in player_earnings.items()]
                        if rewards_list:
                            others_msg = "\n\n💰 **Rewards:**\n" + " • ".join(rewards_list)

                        await ctx.send(embed=discord.Embed(
                            description=f"🤝 Ta3adol bin {winners_mention} b **{max_guesses} cars**!{others_msg}",
                            color=0x000000
                        ))
                else:
                    await ctx.send(embed=discord.Embed(
                        description="🎯 Game Over! Ta wa7d ma jab chy car s7i7a.",
                        color=0x000000
                    ))
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


