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
    get_unscramble_word
)
from cogs.games.common import (
    DIFFICULTY_STAKES, parse_minigame_args,
    countdown_reactions, MinigameDifficultyView
)


async def run_unscramble_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "Word Unscramble")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=30, default_difficulty="easy")
        if round_duration < 5:
            round_duration = 5
            time_display = "5s (Minimum)"
        else:
            time_display = f"{round_duration}s"

        mult = DIFFICULTY_STAKES.get(difficulty, 1.0)
        players = []

        try:
            join_emoji = "✅"
            signup_embed = discord.Embed(
                title="🧩 Word Unscramble",
                description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nTime: **{time_display}**\nDifficulty: **{difficulty.upper()}** (Stake: **{mult}x**)",
                color=0x000000
            )
            diff_view = MinigameDifficultyView(ctx.author.id, initial_difficulty=difficulty)
            signup_msg = await ctx.send(embed=signup_embed, view=diff_view)
            await signup_msg.add_reaction(join_emoji)
            await asyncio.sleep(19)

            difficulty = diff_view.difficulty
            diff_mult = DIFFICULTY_STAKES.get(difficulty, 1.0)
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
            session = MultiplayerGameSession("Word Unscramble", active_players, ctx.channel)
            for p in players:
                set_user_in_game(cog.bot, p.id, "Word Unscramble", session)

            single_player = len(players) == 1
            lives = {p.id: 3 for p in players}
            player_correct_words = {p.id: 0 for p in players}
            used_secrets = set()

            if single_player:
                await signup_msg.edit(embed=discord.Embed(
                    description=f"▶️ Bdina! 3ndek **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                    color=0x000000
                ), view=None)
            else:
                await signup_msg.edit(embed=discord.Embed(
                    description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)\nPlayers: " + ", ".join(p.mention for p in players),
                    color=0x000000
                ), view=None)
            await asyncio.sleep(2)

            if single_player:
                player = active_players[0]
                while lives[player.id] > 0 and not session.stopped:
                    secret = cog.get_unscramble_word(difficulty).strip().lower()
                    if not secret or len(secret) < 3 or not secret.isalpha() or secret in used_secrets:
                        secret = cog.get_word(difficulty=difficulty, min_length=4, max_length=9).strip().lower()
                    used_secrets.add(secret)

                    letters = list(secret)
                    for _ in range(50):
                        random.shuffle(letters)
                        if "".join(letters) != secret:
                            break
                    scrambled_display = " ".join(letters).upper()

                    round_msg = await ctx.send(f"❓ {player.mention} 9ad had lkelma: **`{scrambled_display}`** ({len(secret)} letters) (HP: **{lives[player.id]}**)")
                    countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                    def check(message):
                        if message.author.id != player.id or message.channel.id != ctx.channel.id:
                            return False
                        w = message.content.strip().lower()
                        return w == secret or w == "exitgame"

                    try:
                        word_msg = await cog.bot.wait_for('message', check=check, timeout=round_duration)
                        if not countdown_task.done():
                            countdown_task.cancel()
                        if word_msg:
                            if word_msg.content.strip().lower() == "exitgame":
                                clear_user_game(cog.bot, player.id)
                                lives[player.id] = 0
                                await ctx.send(f"🚪 **{player.mention}** khrej mn lgame (**Game Over**).\n🧩 Lkelma kanet: **{secret.upper()}**")
                                break
                            player_correct_words[player.id] += 1
                            await word_msg.add_reaction('✅')
                    except asyncio.TimeoutError:
                        if not countdown_task.done():
                            countdown_task.cancel()
                        lives[player.id] -= 1
                        if lives[player.id] > 0:
                            await ctx.send(f"⌛ Sala lwe9t: -1 HP (Ba9i: **{lives[player.id]} HP**). Lkelma kanet: **{secret.upper()}**")
                        else:
                            await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**). Lkelma kanet: **{secret.upper()}**")
                    await asyncio.sleep(1.5)

                economy_cog = cog.bot.get_cog("Economy")
                correct_count = player_correct_words.get(player.id, 0)
                gross = (len(players) * 50) + int(round((correct_count * 20) * diff_mult))
                eco_msg = ""
                if economy_cog and correct_count > 0:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context=f"Unscramble Solo ({difficulty.capitalize()})")
                    eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                    if ctx.guild:
                        await cog.record_minigame_win(ctx.guild.id, player.id, "unscramble", earnings=net)
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

                        secret = cog.get_unscramble_word(difficulty).strip().lower()
                        if not secret or len(secret) < 3 or not secret.isalpha() or secret in used_secrets:
                            secret = cog.get_word(difficulty=difficulty, min_length=4, max_length=9).strip().lower()
                        used_secrets.add(secret)

                        letters = list(secret)
                        for _ in range(50):
                            random.shuffle(letters)
                            if "".join(letters) != secret:
                                break
                        scrambled_display = " ".join(letters).upper()

                        round_msg = await ctx.send(f"❓ {player.mention} 9ad had lkelma: **`{scrambled_display}`** ({len(secret)} letters) (HP: **{lives[player.id]}**)")
                        countdown_task = asyncio.create_task(countdown_reactions(round_msg, round_duration))

                        def check(message):
                            if message.channel.id != ctx.channel.id:
                                return False
                            if message.content.strip().lower() == "exitgame" and any(p.id == message.author.id for p in active_players):
                                return True
                            if message.author.id != player.id:
                                return False
                            w = message.content.strip().lower()
                            return w == secret

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
                                        await ctx.send(f"🚪 **{leaver.mention}** khrej mn lgame o t elimina.\n🧩 Lkelma kanet: **{secret.upper()}**")
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
                                    player_correct_words[player.id] += 1
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
                                await ctx.send(f"⌛ Sala lwe9t {player.mention}: -1 HP (Ba9i: **{lives[player.id]} HP**). Lkelma kanet: **{secret.upper()}**")
                            else:
                                await ctx.send(f"💥 **{player.mention}** t elimina (**0 HP**). Lkelma kanet: **{secret.upper()}**")
                                active_players.remove(player)
                        await asyncio.sleep(1.5)

                if active_players:
                    winner = active_players[0]
                    economy_cog = cog.bot.get_cog("Economy")
                    player_earnings = {}
                    eco_msg = ""
                    w_words = player_correct_words.get(winner.id, 0)
                    if economy_cog:
                        gross = (len(players) * 50) + int(round((w_words * 20) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context=f"Unscramble Win ({difficulty.capitalize()})")
                        player_earnings[winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner.id, "unscramble", earnings=net)

                        # Other players get 20 per correct word scaled by diff_mult
                        for pid, words_count in player_correct_words.items():
                            if pid != winner.id and words_count > 0:
                                p_gross = int(round((words_count * 20) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context=f"Unscramble Words Reward ({difficulty.capitalize()})")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_words[pid]} words)" for pid, net in player_earnings.items() if pid != winner.id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    await ctx.send(embed=discord.Embed(
                        description=f"🏆 {winner.mention} rbe7 lgame b **{w_words} kelmat** (Difficulty: **{difficulty.upper()}**)!{eco_msg}{others_msg}",
                        color=0x000000
                    ))
        except Exception as e:
            print(f"[unscramble error]: {e}")
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


