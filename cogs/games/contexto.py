from __future__ import annotations
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
from cogs.games.common import countdown_reactions

BASE_CONTEXTO_URL = "https://api.contexto.me/machado/en"


async def fetch_contexto_secret(session: aiohttp.ClientSession, game_id: int) -> Optional[str]:
    url = f"{BASE_CONTEXTO_URL}/giveup/{game_id}"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("word")
    except Exception as e:
        print(f"[Contexto fetch_secret error]: {e}")
    return None


async def check_contexto_word(session: aiohttp.ClientSession, game_id: int, word: str) -> Optional[int]:
    clean_word = word.strip().lower()
    url = f"{BASE_CONTEXTO_URL}/game/{game_id}/{clean_word}"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("distance")
    except Exception as e:
        print(f"[Contexto check_word error]: {e}")
    return None


def get_warmth_info(rank: int) -> tuple[str, str]:
    if rank == 0:
        return "🎯 Target Word", "🏆"
    elif rank <= 300:
        return "🔥 Super Hot", "🔥"
    elif rank <= 1500:
        return "🟧 Warm", "🟧"
    elif rank <= 5000:
        return "🟦 Cold", "🟦"
    else:
        return "❄️ Freezing", "❄️"


async def run_contexto_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    set_user_in_game(cog.bot, ctx.author.id, "Contexto")
    round_duration = 22
    players = []

    try:
        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🧠 Contexto · Semantic Warmth",
            description=(
                f"9edder lkelmat 7ta tl9a lkelma s7i7a.\n\n"
                f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\n"
                f"Starts: <t:{int(time.time() + 21)}:R>\n"
                f"Time: **{round_duration}s**"
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

        # Pick random game_id from available Contexto pool (1 to 850)
        game_id = random.randint(10, 850)
        secret_word = await fetch_contexto_secret(cog.bot.session, game_id)
        if not secret_word:
            # Retry with another ID
            game_id = random.randint(100, 750)
            secret_word = await fetch_contexto_secret(cog.bot.session, game_id)

        if not secret_word:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)
            await ctx.send("❌ Mouchkil f connexion m3a server dial Contexto. 3awd jereb mn be3d.")
            return

        active_players = list(players)
        random.shuffle(active_players)

        def _on_contexto_quit(u):
            if u in active_players:
                active_players.remove(u)

        session = MultiplayerGameSession("Contexto", active_players, ctx.channel, on_quit=_on_contexto_quit)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Contexto", session)

        player_best_rank = {p.id: 999999 for p in players}
        all_guesses_history = []  # list of (rank, word, user)
        best_overall_rank = 999999
        best_overall_word = None
        winner = None

        is_solo = (len(players) == 1)
        max_solo_guesses = 20
        solo_guesses_left = max_solo_guesses

        start_desc = f"▶️ **Bdina Contexto!** Solo Mode: 3ndek **{max_solo_guesses} guesses** bach tl9a lkelma." if is_solo else f"▶️ **Bdina Contexto!** {len(players)} players."
        await start_msg.edit(embed=discord.Embed(
            description=start_desc,
            color=0x000000
        ))
        await asyncio.sleep(2)

        turn_idx = 0
        while not session.stopped and active_players and not winner:
            if is_solo and solo_guesses_left <= 0:
                await ctx.send("⌛ **Salaw lik ga3 l guesses (Game Over)!**")
                break

            current_player = active_players[turn_idx % len(active_players)]

            # Construct message without bolding "kteb chy klma"
            extra_solo = f" (Ba9i lik: **{solo_guesses_left} guesses**)" if is_solo else ""
            if best_overall_word:
                warmth_label, warmth_ico = get_warmth_info(best_overall_rank)
                prompt_text = (
                    f"❓ {current_player.mention} a9rab kelma: **{best_overall_word} #{best_overall_rank:,} ({warmth_label} {warmth_ico})**. "
                    f"kteb chy klma{extra_solo}."
                )
            else:
                prompt_text = f"❓ {current_player.mention} kteb chy klma{extra_solo}."

            turn_msg = await ctx.send(prompt_text)
            countdown_task = asyncio.create_task(countdown_reactions(turn_msg, round_duration))

            def check_turn(m):
                return m.author.id == current_player.id and m.channel.id == ctx.channel.id

            try:
                m = await cog.bot.wait_for("message", check=check_turn, timeout=round_duration)
                if not countdown_task.done():
                    countdown_task.cancel()

                word_content = m.content.strip().lower()
                if word_content == "exitgame":
                    active_players.remove(current_player)
                    clear_user_game(cog.bot, current_player.id)
                    await ctx.send(f"🚪 **{current_player.mention}** khrej mn lgame.")
                    if len(active_players) == 0:
                        break
                    continue

                # Query Contexto API
                distance = await check_contexto_word(cog.bot.session, game_id, word_content)

                if distance is None:
                    # Word not found in dictionary or API timeout
                    await ctx.send(f"⚠️ `{word_content}` ma l9ithach f dictionary ta3 Contexto.")
                else:
                    warmth_label, warmth_ico = get_warmth_info(distance)
                    all_guesses_history.append((distance, word_content, current_player))

                    if distance < player_best_rank[current_player.id]:
                        player_best_rank[current_player.id] = distance

                    if distance == 0 or word_content == secret_word.lower():
                        # WINNER!
                        winner = current_player
                        try:
                            await m.add_reaction("🏆")
                        except Exception:
                            pass
                        break
                    else:
                        if distance < best_overall_rank:
                            best_overall_rank = distance
                            best_overall_word = word_content
                            try:
                                await m.add_reaction("🔥" if distance <= 300 else "📍")
                            except Exception:
                                pass
                        else:
                            try:
                                await m.add_reaction(warmth_ico)
                            except Exception:
                                pass

                    if is_solo:
                        solo_guesses_left -= 1

            except asyncio.TimeoutError:
                if not countdown_task.done():
                    countdown_task.cancel()
                if is_solo:
                    solo_guesses_left -= 1
                await ctx.send(f"⏰ {current_player.mention} sala lik lw9t!." if not is_solo else f"⏰ {current_player.mention} sala lik lw9t (-1 guess)!")

            turn_idx += 1
            await asyncio.sleep(1.5)

        # Game ended: reveal secret word and Top 5 closest words
        economy_cog = cog.bot.get_cog("Economy")
        eco_msg = ""
        player_earnings = {}

        if economy_cog:
            if winner:
                # Winner gets: (50 * len(players)) + 100 gross TAD
                gross = (50 * len(players)) + 100
                net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Contexto Victory (Word Found)")
                player_earnings[winner.id] = net
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                if ctx.guild:
                    await cog.record_minigame_win(ctx.guild.id, winner.id, "contexto", earnings=net)

            # Rewarding other participants (or solo player if lost) for how close their best guess got
            for p in players:
                if p.id != (winner.id if winner else None):
                    p_best = player_best_rank.get(p.id, 999999)
                    if p_best <= 50:
                        p_gross = 40
                    elif p_best <= 300:
                        p_gross = 25
                    elif p_best <= 1500:
                        p_gross = 15
                    else:
                        p_gross = 10 if is_solo else 0

                    if p_gross > 0:
                        p_net, _ = await economy_cog.apply_tax_and_add_balance(p.id, p_gross, context="Contexto Proximity Reward")
                        player_earnings[p.id] = p_net

        # Sort all guesses to get top 5 closest
        sorted_history = sorted(all_guesses_history, key=lambda x: x[0])
        top_5_lines = []
        seen_words = set()
        for dist, w, user in sorted_history:
            if w in seen_words:
                continue
            seen_words.add(w)
            lbl, ico = get_warmth_info(dist)
            user_tag = user.display_name if user else "Player"
            top_5_lines.append(f"**#{dist:,}** `{w}` ({lbl} {ico}) — by **{user_tag}**")
            if len(top_5_lines) >= 5:
                break

        top_5_str = "\n".join(top_5_lines) if top_5_lines else "*Walou ma t7ettat 7ta kelma 9riba.*"

        participants_rewards = []
        for p in players:
            if p.id != (winner.id if winner else None) and p.id in player_earnings:
                p_best = player_best_rank.get(p.id, 999999)
                best_str = f"#{p_best:,}" if p_best < 999999 else "None"
                participants_rewards.append(f"{p.mention}: **+{format_tad(player_earnings[p.id])}** (Best: `{best_str}`)")

        part_str = ("\n\n🎖️ **Other Rewards:**\n" + " • ".join(participants_rewards)) if participants_rewards else ""

        final_embed = discord.Embed(
            title="🏆 Contexto Match Ended!",
            description=(
                f"🎯 **Secret Word:** **{secret_word.upper()}**\n\n"
                f"🥇 **Winner:** {winner.mention if winner else 'Ta7ed ma l9aha'}{eco_msg}{part_str}\n\n"
                f"📊 **Top 5 Closest Words Found:**\n{top_5_str}"
            ),
            color=0x000000
        )
        await ctx.send(embed=final_embed)

    finally:
        clear_user_game(cog.bot, ctx.author.id)
        for p in players:
            clear_user_game(cog.bot, p.id)
