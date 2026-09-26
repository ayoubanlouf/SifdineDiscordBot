from __future__ import annotations
import unicodedata
import re
import difflib
import asyncio
import time
import random
from typing import Optional

import aiohttp
import discord

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    attach_game_session, MultiplayerGameSession, record_minigame_win
)
from cogs.games.common import (
    DIFFICULTY_STAKES, parse_minigame_args,
    countdown_reactions, MinigameDifficultyView
)

EASY_COUNTRIES = {
    "ma", "fr", "es", "it", "de", "jp", "br", "ar", "us", "gb", "ca", "ru", "cn",
    "pt", "nl", "eg", "sa", "tr", "dz", "tn", "mx", "be", "ch", "se", "kr",
    "in", "au", "ps", "qa", "ae", "sn", "ng", "za", "cl", "co", "pl", "gr", "no"
}

MEDIUM_COUNTRIES = EASY_COUNTRIES | {
    "pe", "uy", "ec", "py", "bo", "ve", "cr", "pa", "cu", "jm", "at", "dk",
    "fi", "ie", "is", "cz", "hu", "ro", "bg", "rs", "hr", "si", "sk", "ua",
    "gh", "ci", "cm", "ke", "et", "tz", "ug", "ao", "mz", "mg", "id", "th",
    "vn", "my", "ph", "sg", "nz", "pk", "bd", "lk", "np", "iq", "ir", "sy",
    "lb", "jo", "kw", "om", "ye", "kz", "uz", "az", "ge", "am", "cy", "mt"
}

# ============ FLAGS HELPERS ============

FLAG_ALIASES = {
    "ae": ["uae", "emirates", "united arab emirates"],
    "us": ["usa", "us", "america", "united states", "united states of america"],
    "gb": ["uk", "britain", "great britain", "england", "united kingdom"],
    "cd": ["dr congo", "drc", "democratic republic of the congo", "congo"],
    "cg": ["congo", "republic of the congo", "congo brazzaville"],
    "kr": ["south korea", "korea"],
    "kp": ["north korea"],
    "sa": ["saudi", "saudi arabia", "ksa"],
    "ru": ["russia", "russian federation"],
    "cz": ["czechia", "czech republic"],
    "tz": ["tanzania", "united republic of tanzania"],
    "va": ["vatican", "vatican city", "holy see"],
    "ps": ["palestine", "state of palestine"],
    "sy": ["syria", "syrian arab republic"],
    "la": ["laos", "lao"],
    "ci": ["ivory coast", "cote d ivoire", "cote divoire", "cote d'ivoire"],
    "cf": ["car", "central african republic"],
    "nz": ["nz", "new zealand"],
    "do": ["dominican republic", "dominican rep"],
    "tt": ["trinidad", "trinidad and tobago"],
    "st": ["sao tome", "sao tome and principe"],
    "pg": ["png", "papua new guinea"],
    "ba": ["bosnia", "bosnia and herzegovina"],
    "cv": ["cape verde", "cabo verde"],
    "kn": ["saint kitts", "st kitts", "st kitts and nevis", "saint kitts and nevis"],
    "lc": ["saint lucia", "st lucia"],
    "vc": ["saint vincent", "st vincent", "saint vincent and the grenadines", "st vincent and the grenadines"],
    "fm": ["micronesia", "federated states of micronesia"],
    "ir": ["iran", "islamic republic of iran"],
    "mm": ["myanmar", "burma"],
    "mk": ["north macedonia", "macedonia"],
    "sz": ["eswatini", "swaziland"],
    "tl": ["east timor", "timor leste", "timor"],
    "nl": ["netherlands", "holland"],
    "tr": ["turkey", "turkiye"],
}

def normalize_country_text(text: str) -> str:
    if not text:
        return ""
    # Strip accents / diacritics
    t = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    t = t.lower().strip()
    if t.startswith("the "):
        t = t[4:].strip()
    t = re.sub(r"[.,'\-/&]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def is_flag_guess_correct(guess: str, code: str, country_name: str) -> bool:
    norm_guess = normalize_country_text(guess)
    norm_target = normalize_country_text(country_name)

    if not norm_guess or not norm_target:
        return False

    # 1. Direct exact match
    if norm_guess == norm_target:
        return True

    # 2. Check predefined aliases
    code_aliases = FLAG_ALIASES.get(code.lower(), [])
    for alias in code_aliases:
        norm_alias = normalize_country_text(alias)
        if norm_guess == norm_alias:
            return True
        if len(norm_guess) >= 4 and difflib.SequenceMatcher(None, norm_guess, norm_alias).ratio() >= 0.85:
            return True

    # 3. Fuzzy match on country name (e.g. minor typos: "philipines" -> "philippines")
    similarity = difflib.SequenceMatcher(None, norm_guess, norm_target).ratio()
    if similarity >= 0.82:
        return True

    return False



async def run_flags_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "Guess the Flag")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=20, default_difficulty="easy")
        time_display = f"{round_duration}s"
        mult = DIFFICULTY_STAKES[difficulty]

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
            await signup_msg.edit(embed=discord.Embed(description="💨 7ta wa7d ma dkhel lgame ._.", color=0x000000), view=None)
            return

        active_players = list(players)
        session = MultiplayerGameSession("Guess the Flag", active_players, ctx.channel)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Guess the Flag", session)

        try:
            single_player = len(players) == 1
            hp = {player.id: 3 for player in players}

            # Apply difficulty filter to flag pool
            if difficulty == "easy":
                country_pool = [c for c in country_pool if c["code"] in EASY_COUNTRIES] or country_pool
            elif difficulty == "medium":
                country_pool = [c for c in country_pool if c["code"] in MEDIUM_COUNTRIES] or country_pool

            # Match pool to prevent any repeated flags in the same match
            match_pool = list(country_pool)
            random.shuffle(match_pool)

            start_embed = discord.Embed(
                description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                color=0x000000
            )
            await signup_msg.edit(embed=start_embed, view=None)
            await asyncio.sleep(2)

            player_correct_flags = {p.id: 0 for p in players}

            while len(active_players) > 0 and not session.stopped:
                if not single_player and len(active_players) == 1:
                    winner = active_players[0]
                    economy_cog = cog.bot.get_cog("Economy")
                    eco_msg = ""
                    player_earnings = {}
                    winner_flags = player_correct_flags.get(winner.id, 0)
                    if economy_cog:
                        gross = (len(players) * 50) + int(round((winner_flags * 15) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Flags Win")
                        player_earnings[winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner.id, "flags", earnings=net)

                        for pid, p_flags in player_correct_flags.items():
                            if pid != winner.id and p_flags > 0:
                                p_gross = int(round((p_flags * 15) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Flags Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_flags[pid]} flags)" for pid, net in player_earnings.items() if pid != winner.id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    win_embed = discord.Embed(
                        description=f"🏆 {winner.mention} rbe7 lgame b **{winner_flags} flags**!{eco_msg}{others_msg}",
                        color=0x000000
                    )
                    await ctx.send(embed=win_embed)
                    return

                if not match_pool:
                    await ctx.send(embed=discord.Embed(
                        description="🏁 **Flags pool salaw kamlin! Game sala.**",
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
                    correct_name = target["name"]
                    target_code = target["code"]
                    flag_url = f"https://flagcdn.com/w640/{target_code}.png"

                    game_embed = discord.Embed(
                        description=f"❓ Chno smit had dawla?\n⌛ Time: {round_duration}s\n❤️ HP: {hp[player.id]}\n🚩 Flags left: **{len(match_pool) + 1}**",
                        color=0x000000
                    )
                    game_embed.set_image(url=flag_url)
                    round_msg = await ctx.send(player.mention, embed=game_embed)

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
                                    clear_user_game(cog.bot, leaver.id)
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

                            if msg.author.id == player.id and is_flag_guess_correct(msg.content, target_code, correct_name):
                                if not countdown_task.done():
                                    countdown_task.cancel()
                                try:
                                    await msg.add_reaction("✅")
                                except Exception:
                                    pass
                                guessed_correctly = True
                                player_correct_flags[player.id] = player_correct_flags.get(player.id, 0) + 1
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
                p_flags = player_correct_flags.get(player.id, 0)
                gross = 50 + int(round((p_flags * 15) * diff_mult))
                eco_msg = ""
                if economy_cog and p_flags > 0:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context="Flags Solo")
                    eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                    if ctx.guild:
                        await cog.record_minigame_win(ctx.guild.id, player.id, "flags", earnings=net)
                await ctx.send(embed=discord.Embed(
                    description=f"🎯 Game Over {player.mention}! L9iti **{p_flags} flags**.{eco_msg}",
                    color=0x000000
                ))
            elif not single_player:
                max_guesses = max(player_correct_flags.values()) if player_correct_flags else 0
                if max_guesses > 0:
                    top_players = [p for p in players if player_correct_flags.get(p.id, 0) == max_guesses]
                    economy_cog = cog.bot.get_cog("Economy")
                    player_earnings = {}
                    if len(top_players) == 1:
                        winner = top_players[0]
                        eco_msg = ""
                        if economy_cog:
                            gross = (len(players) * 50) + int(round((max_guesses * 15) * diff_mult))
                            net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Flags Win")
                            player_earnings[winner.id] = net
                            eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                            if ctx.guild:
                                await cog.record_minigame_win(ctx.guild.id, winner.id, "flags", earnings=net)

                            for pid, p_flags in player_correct_flags.items():
                                if pid != winner.id and p_flags > 0:
                                    p_gross = int(round((p_flags * 15) * diff_mult))
                                    if p_gross > 0:
                                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Flags Reward")
                                        player_earnings[pid] = p_net

                        others_msg = ""
                        other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_flags[pid]} flags)" for pid, net in player_earnings.items() if pid != winner.id]
                        if other_rewards:
                            others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                        await ctx.send(embed=discord.Embed(
                            description=f"🏆 {winner.mention} 3ndo a3la score b **{max_guesses} flags** o rbe7 lgame!{eco_msg}{others_msg}",
                            color=0x000000
                        ))
                    else:
                        winners_mention = " o ".join(p.mention for p in top_players)
                        if economy_cog:
                            for pid, p_flags in player_correct_flags.items():
                                if p_flags > 0:
                                    p_gross = int(round((p_flags * 15) * diff_mult))
                                    if p_gross > 0:
                                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Flags Reward (Tie)")
                                        player_earnings[pid] = p_net

                        others_msg = ""
                        rewards_list = [f"<@{pid}>: **+{net}** TAD ({player_correct_flags[pid]} flags)" for pid, net in player_earnings.items()]
                        if rewards_list:
                            others_msg = "\n\n💰 **Rewards:**\n" + " • ".join(rewards_list)

                        await ctx.send(embed=discord.Embed(
                            description=f"🤝 Ta3adol bin {winners_mention} b **{max_guesses} flags**!{others_msg}",
                            color=0x000000
                        ))
                else:
                    await ctx.send(embed=discord.Embed(
                        description="🎯 Game Over! Ta wa7d ma jab chy raya s7i7a.",
                        color=0x000000
                    ))
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


