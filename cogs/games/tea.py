from __future__ import annotations
import time
import asyncio
import random
from typing import Optional

import discord

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    attach_game_session, MultiplayerGameSession, record_minigame_win,
    is_english_word, get_combo
)
from cogs.games.common import (
    DIFFICULTY_STAKES, parse_minigame_args,
    countdown_reactions, MinigameDifficultyView
)


async def run_blacktea_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "BlackTea")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=20, default_difficulty="easy")
        time_display = f"{round_duration}s"
        mult = DIFFICULTY_STAKES[difficulty]
        players = []

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="☕ BlackTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nDifficulty: **{difficulty.upper()}** (Stake: **{mult}x**)",
                color=0x000000
            )
            diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
            start = await ctx.send(embed=signup_embed, view=diff_view)
            await start.add_reaction(join_emoji)
            await asyncio.sleep(19)

            difficulty = diff_view.difficulty
            diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.5)
            diff_view.stop()

            signup_msg = await ctx.channel.fetch_message(start.id)
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
                await start.edit(embed=discord.Embed(
                    description="💨 7ta wa7d ma dkhel lgame ._.",
                    color=0x000000
                ), view=None)
                return

            active_players = list(players)
            session = MultiplayerGameSession("BlackTea", active_players, ctx.channel)
            for p in players:
                set_user_in_game(cog.bot, p.id, "BlackTea", session)

            single_player = len(players) == 1
            lives = {p.id: 3 for p in players}
            player_correct_words = {p.id: 0 for p in players}
            used_words = set()
            used_combos = set()

            if single_player:
                await start.edit(embed=discord.Embed(
                    description=f"▶️ Bdina! 3ndek **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                    color=0x000000
                ), view=None)
            else:
                await start.edit(embed=discord.Embed(
                    description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                    color=0x000000
                ), view=None)
            await asyncio.sleep(2)

            if single_player:
                player = active_players[0]
                while lives[player.id] > 0 and not session.stopped:
                    combo = cog.get_combo(difficulty, exclude=used_combos)
                    used_combos.add(combo)

                    round_msg = await ctx.send(f"❓ {player.mention} kteb kelma fiha: **{combo.upper()}** (HP: **{lives[player.id]}**)")
                    countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                    def check(message):
                        if message.author.id != player.id or message.channel.id != ctx.channel.id:
                            return False
                        word = message.content.strip().lower()
                        if word == "exitgame":
                            return True
                        if combo not in word or word in used_words:
                            return False
                        return cog.is_english_word(word)

                    try:
                        word_msg = await cog.bot.wait_for('message', check=check, timeout=round_duration)
                        if not countdown_task.done():
                            countdown_task.cancel()
                        if word_msg:
                            if word_msg.content.strip().lower() == "exitgame":
                                clear_user_game(cog.bot, player.id)
                                lives[player.id] = 0
                                await ctx.send(f"🚪 **{player.mention}** khrej mn lgame (**Game Over**).")
                                break
                            used_words.add(word_msg.content.strip().lower())
                            player_correct_words[player.id] = player_correct_words.get(player.id, 0) + 1
                            await word_msg.add_reaction('✅')
                    except asyncio.TimeoutError:
                        if not countdown_task.done():
                            countdown_task.cancel()
                        lives[player.id] -= 1
                        if lives[player.id] > 0:
                            await ctx.send(f"⌛ Sala lwe9t: -1 HP (Ba9i: **{lives[player.id]} HP**)")
                        else:
                            await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**)")

                economy_cog = cog.bot.get_cog("Economy")
                correct_count = len(used_words)
                gross = 50 + int(round((correct_count * 20) * diff_mult))
                eco_msg = ""
                if economy_cog and correct_count > 0:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context=f"BlackTea Solo ({difficulty.capitalize()})")
                    eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                    if ctx.guild:
                        await cog.record_minigame_win(ctx.guild.id, player.id, "blacktea", earnings=net)
                await ctx.send(embed=discord.Embed(
                    description=f"🎯 Game Over {player.mention}! L9iti **{correct_count} kelmat** (Difficulty: **{difficulty.upper()}**).{eco_msg}",
                    color=0x000000
                ))
            else:
                while len(active_players) > 1 and not session.stopped:
                    for player in list(active_players):
                        if player not in active_players:
                            continue
                        if len(active_players) <= 1:
                            break

                        combo = cog.get_combo(difficulty, exclude=used_combos)
                        used_combos.add(combo)

                        round_msg = await ctx.send(f"❓ {player.mention} kteb kelma fiha: **{combo.upper()}** (HP: **{lives[player.id]}**)")
                        countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                        def check(message):
                            if message.channel.id != ctx.channel.id:
                                return False
                            if message.content.strip().lower() == "exitgame" and any(p.id == message.author.id for p in active_players):
                                return True
                            if message.author.id != player.id:
                                return False
                            word = message.content.strip().lower()
                            if combo not in word or word in used_words:
                                return False
                            return cog.is_english_word(word)

                        start_turn = time.time()
                        answered = False
                        while time.time() - start_turn < round_duration:
                            rem = round_duration - (time.time() - start_turn)
                            if rem <= 0:
                                break
                            try:
                                word_msg = await cog.bot.wait_for('message', check=check, timeout=rem)
                                if word_msg.content.strip().lower() == "exitgame":
                                    leaver = next((p for p in active_players if p.id == word_msg.author.id), None)
                                    if leaver:
                                        clear_user_game(cog.bot, leaver.id)
                                        lives[leaver.id] = 0
                                        active_players.remove(leaver)
                                        await ctx.send(f"🚪 **{leaver.mention}** khrej mn lgame o t elimina.")
                                        if leaver.id == player.id:
                                            if not countdown_task.done():
                                                countdown_task.cancel()
                                            answered = True
                                            break
                                        elif len(active_players) <= 1:
                                            if not countdown_task.done():
                                                countdown_task.cancel()
                                            break
                                    continue

                                if word_msg.author.id == player.id:
                                    if not countdown_task.done():
                                        countdown_task.cancel()
                                    player_correct_words[player.id] = player_correct_words.get(player.id, 0) + 1
                                    used_words.add(word_msg.content.strip().lower())
                                    await word_msg.add_reaction('✅')
                                    answered = True
                                    break
                            except asyncio.TimeoutError:
                                break

                        if not countdown_task.done():
                            countdown_task.cancel()

                        if len(active_players) <= 1:
                            break

                        if not answered and player in active_players:
                            lives[player.id] -= 1
                            if lives[player.id] > 0:
                                await ctx.send(f"⌛ Sala lwe9t: -1 HP (Ba9i: **{lives[player.id]} HP**)")
                            else:
                                await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**)")
                                active_players.remove(player)

                if active_players:
                    winner = active_players[0]
                    economy_cog = cog.bot.get_cog("Economy")
                    player_earnings = {}
                    eco_msg = ""
                    w_words = player_correct_words.get(winner.id, 0)
                    if economy_cog:
                        gross = (len(players) * 50) + int(round((w_words * 20) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context=f"BlackTea Win ({difficulty.capitalize()})")
                        player_earnings[winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner.id, "blacktea", earnings=net)

                        # Other players get rewarded for what they guessed with no total_players*50 bonus
                        for pid, words_count in player_correct_words.items():
                            if pid != winner.id and words_count > 0:
                                p_gross = int(round((words_count * 10) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="BlackTea Words Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_words[pid]} words)" for pid, net in player_earnings.items() if pid != winner.id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {winner.mention} rbe7 lgame b **{w_words} kelmat**! (Majmou3 lkelmat: {len(used_words)}){eco_msg}{others_msg}",
                        color=0x000000
                    ))
        except Exception as e:
            print(f"[blacktea error]: {e}")
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)




async def run_greentea_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "GreenTea")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=20, default_difficulty="easy")
        time_display = f"{round_duration}s"
        mult = DIFFICULTY_STAKES[difficulty]
        players = []

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="🍵 GreenTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nMin Players: **2**\nDifficulty: **{difficulty.upper()}** (Stake: **{mult}x**)",
                color=0x000000
            )
            diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
            start = await ctx.send(embed=signup_embed, view=diff_view)
            await start.add_reaction(join_emoji)
            await asyncio.sleep(19)

            difficulty = diff_view.difficulty
            diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.5)
            diff_view.stop()

            signup_msg = await ctx.channel.fetch_message(start.id)
            reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

            players = []
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        if user.id != ctx.author.id and is_user_in_game(cog.bot, user.id):
                            continue
                        players.append(user)

            if len(players) < 2:
                clear_user_game(cog.bot, ctx.author.id)
                for p in players:
                    clear_user_game(cog.bot, p.id)
                await start.edit(embed=discord.Embed(
                    description="❌ Khass minimum **2 players** bach tl3bo GreenTea.",
                    color=0x000000
                ), view=None)
                return

            active_players = list(players)
            player_ids = {p.id for p in players}
            session = MultiplayerGameSession("GreenTea", active_players, ctx.channel, on_quit=lambda u: player_ids.discard(u.id))
            for p in players:
                set_user_in_game(cog.bot, p.id, "GreenTea", session)

            await start.edit(embed=discord.Embed(
                description="▶️ **Bdina!** (10 Rounds)\nPlayers: " + ", ".join(p.mention for p in players) + f"\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                color=0x000000
            ), view=None)

            points = {p.id: 0 for p in players}
            used_words = set()
            used_combos = set()
            await asyncio.sleep(2)

            for round_num in range(1, 11):
                if len(player_ids) <= 1 or session.stopped:
                    break

                combo = cog.get_combo(difficulty, exclude=used_combos)
                if not combo:
                    continue
                used_combos.add(combo)

                round_msg = await ctx.send(embed=discord.Embed(
                    description=f"Kteb kelma fiha: **{combo.upper()}**\n⏱️ Round **{round_num}/10**",
                    color=0x000000
                ))
                countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                def check(message):
                    if message.author.id not in player_ids or message.channel.id != ctx.channel.id:
                        return False
                    word = message.content.strip().lower()
                    if word == "exitgame":
                        return True
                    if combo not in word or word in used_words:
                        return False
                    return cog.is_english_word(word)

                round_start = time.time()
                round_won = False
                while time.time() - round_start < round_duration:
                    rem = round_duration - (time.time() - round_start)
                    if rem <= 0:
                        break
                    try:
                        word_msg = await cog.bot.wait_for('message', check=check, timeout=rem)
                        if word_msg.content.strip().lower() == "exitgame":
                            fast = word_msg.author
                            clear_user_game(cog.bot, fast.id)
                            player_ids.discard(fast.id)
                            players = [p for p in players if p.id != fast.id]
                            await ctx.send(f"🚪 **{fast.mention}** khrej mn lgame.")
                            if len(player_ids) <= 1:
                                break
                            continue
                        else:
                            if not countdown_task.done():
                                countdown_task.cancel()
                            fast = word_msg.author
                            used_words.add(word_msg.content.strip().lower())
                            points[fast.id] += 1
                            await word_msg.add_reaction('✅')
                            await asyncio.sleep(1.5)
                            await ctx.send(embed=discord.Embed(
                                description=f"✅ {fast.mention} 5da 1 point. (Total: **{points[fast.id]} pts**)",
                                color=0x000000
                            ))
                            round_won = True
                            break
                    except asyncio.TimeoutError:
                        break

                if not countdown_task.done():
                    countdown_task.cancel()

                if len(player_ids) <= 1:
                    break

                if not round_won:
                    await ctx.send(embed=discord.Embed(
                        description="⌛ Sala lwe9t. 7ta wa7d ma 5da lpoint.",
                        color=0x000000
                    ))
                await asyncio.sleep(1.5)

            if points:
                maxpoints = max(points.values())
                winners = [pid for pid, pts in points.items() if pts == maxpoints]
                economy_cog = cog.bot.get_cog("Economy")
                player_earnings = {}

                if len(winners) == 1:
                    winner_id = winners[0]
                    winner = cog.bot.get_user(winner_id)
                    winner_str = winner.mention if winner else f"<@{winner_id}>"
                    eco_msg = ""
                    if economy_cog and maxpoints > 0:
                        gross = (len(players) * 50) + int(round((maxpoints * 20) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner_id, gross, context="GreenTea Win")
                        player_earnings[winner_id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner_id, "greentea", earnings=net)

                    # Reward other players based on their points (without len(players)*50 bonus)
                    if economy_cog:
                        for pid, pts in points.items():
                            if pid != winner_id and pts > 0:
                                p_gross = int(round((pts * 20) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="GreenTea Points Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({points[pid]} pts)" for pid, net in player_earnings.items() if pid != winner_id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {winner_str} rbe7 lgame b **{maxpoints} pts**!{eco_msg}{others_msg}",
                        color=0x000000
                    ))
                else:
                    if economy_cog:
                        for pid, pts in points.items():
                            if pts > 0:
                                p_gross = int(round((pts * 20) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="GreenTea Points Reward")
                                    player_earnings[pid] = p_net

                    mention_str = " o ".join(f"<@{wid}>" for wid in winners)
                    others_msg = ""
                    rewards_list = [f"<@{pid}>: **+{net}** TAD ({points[pid]} pts)" for pid, net in player_earnings.items()]
                    if rewards_list:
                        others_msg = "\n\n💰 **Rewards:**\n" + " • ".join(rewards_list)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {mention_str} ta3adlo b **{maxpoints} pts**!{others_msg}",
                        color=0x000000
                    ))
        except Exception as e:
            print(f"[greentea error]: {e}")
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)




async def run_redtea_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "RedTea")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=20, default_difficulty="easy")
        time_display = f"{round_duration}s"
        mult = DIFFICULTY_STAKES[difficulty]
        players = []

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="🔴 RedTea",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nAkbar kelma fiha lcombo katrbe7!\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nMin Players: **2**\nDifficulty: **{difficulty.upper()}** (Stake: **{mult}x**)",
                color=0x000000
            )
            diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
            start = await ctx.send(embed=signup_embed, view=diff_view)
            await start.add_reaction(join_emoji)
            await asyncio.sleep(19)

            difficulty = diff_view.difficulty
            diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.5)
            diff_view.stop()

            signup_msg = await ctx.channel.fetch_message(start.id)
            reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)

            players = []
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        if user.id != ctx.author.id and is_user_in_game(cog.bot, user.id):
                            continue
                        players.append(user)

            if len(players) < 2:
                clear_user_game(cog.bot, ctx.author.id)
                for p in players:
                    clear_user_game(cog.bot, p.id)
                await start.edit(embed=discord.Embed(
                    description="❌ Khass minimum **2 players** bach tl3bo RedTea.",
                    color=0x000000
                ), view=None)
                return

            active_players = list(players)
            player_ids = {p.id for p in players}
            session = MultiplayerGameSession("RedTea", active_players, ctx.channel, on_quit=lambda u: player_ids.discard(u.id))
            for p in players:
                set_user_in_game(cog.bot, p.id, "RedTea", session)

            await start.edit(embed=discord.Embed(
                description="▶️ **Bdina!** (10 Rounds)\nPlayers: " + ", ".join(p.mention for p in players) + f"\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)\n*Akbar kelma fiha lcombo katrbe7!*",
                color=0x000000
            ), view=None)

            points = {p.id: 0 for p in players}
            used_words = set()
            used_combos = set()
            await asyncio.sleep(2)

            for round_num in range(1, 11):
                if len(player_ids) <= 1 or session.stopped:
                    break

                combo = cog.get_combo(difficulty, exclude=used_combos)
                if not combo:
                    continue
                used_combos.add(combo)

                longest_word = ""
                longest_len = 0
                longest_player = None

                round_msg = await ctx.send(embed=discord.Embed(
                    description=f"🔴 Kteb **atwel kelma** fiha: **{combo.upper()}**\n⏱️ Round **{round_num}/10** ({time_display})",
                    color=0x000000
                ))
                countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                round_start = time.time()
                while True:
                    remaining = round_duration - (time.time() - round_start)
                    if remaining <= 0:
                        break

                    def check(message):
                        if message.channel.id != ctx.channel.id:
                            return False
                        if message.author.id not in player_ids:
                            return False
                        return True

                    try:
                        word_msg = await cog.bot.wait_for('message', check=check, timeout=remaining)
                    except asyncio.TimeoutError:
                        break

                    word_clean = word_msg.content.strip().lower()
                    if word_clean == "exitgame":
                        clear_user_game(cog.bot, word_msg.author.id)
                        player_ids.discard(word_msg.author.id)
                        players = [p for p in players if p.id != word_msg.author.id]
                        if longest_player and longest_player.id == word_msg.author.id:
                            longest_player = None
                            longest_word = ""
                            longest_len = 0
                        await ctx.send(f"🚪 **{word_msg.author.mention}** khrej mn lgame.")
                        if len(player_ids) <= 1:
                            break
                        continue

                    if combo in word_clean and word_clean not in used_words and len(word_clean) > longest_len:
                        if cog.is_english_word(word_clean):
                            used_words.add(word_clean)
                            longest_len = len(word_clean)
                            longest_word = word_clean
                            longest_player = word_msg.author
                            asyncio.create_task(word_msg.add_reaction("📏"))

                if not countdown_task.done():
                    countdown_task.cancel()

                if longest_player and longest_player.id in player_ids:
                    points[longest_player.id] += 1
                    await ctx.send(embed=discord.Embed(
                        description=f"📏 {longest_player.mention} 5da lpoint b **{longest_word.upper()}** ({longest_len} letters)!\nTotal: **{points[longest_player.id]} pts**",
                        color=0x000000
                    ))
                else:
                    await ctx.send(embed=discord.Embed(
                        description="⌛ Sala lwe9t. 7ta wa7d ma 3ta kelma s7i7a.",
                        color=0x000000
                    ))
                await asyncio.sleep(1.5)

            if points:
                maxpoints = max(points.values())
                winners = [pid for pid, pts in points.items() if pts == maxpoints]
                economy_cog = cog.bot.get_cog("Economy")
                player_earnings = {}

                if len(winners) == 1:
                    winner_id = winners[0]
                    winner = cog.bot.get_user(winner_id)
                    winner_str = winner.mention if winner else f"<@{winner_id}>"
                    eco_msg = ""
                    if economy_cog and maxpoints > 0:
                        gross = (len(players) * 50) + int(round((maxpoints * 20) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner_id, gross, context="RedTea Win")
                        player_earnings[winner_id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner_id, "redtea", earnings=net)

                    # Reward other players based on their points (without len(players)*50 bonus)
                    if economy_cog:
                        for pid, pts in points.items():
                            if pid != winner_id and pts > 0:
                                p_gross = int(round((pts * 20) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="RedTea Points Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({points[pid]} pts)" for pid, net in player_earnings.items() if pid != winner_id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {winner_str} rbe7 lgame b **{maxpoints} pts**!{eco_msg}{others_msg}",
                        color=0x000000
                    ))
                else:
                    if economy_cog:
                        for pid, pts in points.items():
                            if pts > 0:
                                p_gross = int(round((pts * 20) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="RedTea Points Reward")
                                    player_earnings[pid] = p_net

                    mention_str = " o ".join(f"<@{wid}>" for wid in winners)
                    others_msg = ""
                    rewards_list = [f"<@{pid}>: **+{net}** TAD ({points[pid]} pts)" for pid, net in player_earnings.items()]
                    if rewards_list:
                        others_msg = "\n\n💰 **Rewards:**\n" + " • ".join(rewards_list)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {mention_str} ta3adlo b **{maxpoints} pts**!{others_msg}",
                        color=0x000000
                    ))
        except Exception as e:
            print(f"[redtea error]: {e}")
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


