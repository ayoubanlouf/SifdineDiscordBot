from __future__ import annotations
import io
import time
import asyncio
import random
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    attach_game_session, MultiplayerGameSession, record_minigame_win,
    get_typeracer_text
)

# ============ TYPERACER HELPERS ============

def render_typeracer_image(text: str) -> io.BytesIO:
    width = 1000
    height = 260
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_w = width - 60
    max_h = height - 40

    best_font = None
    best_lines = []
    best_line_spacing = 10

    # Dynamically find the largest bold font size that fits the text on 1 or 2 lines
    for size in range(85, 24, -2):
        try:
            font = ImageFont.truetype("arialbd.ttf", size)
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", size)
            except Exception:
                font = ImageFont.load_default()

        words = text.split()
        lines = []
        cur_line = []
        for w in words:
            cur_line.append(w)
            bbox = draw.textbbox((0, 0), " ".join(cur_line), font=font)
            if (bbox[2] - bbox[0]) > max_w:
                cur_line.pop()
                if cur_line:
                    lines.append(" ".join(cur_line))
                cur_line = [w]
        if cur_line:
            lines.append(" ".join(cur_line))

        if len(lines) > 2:
            continue

        line_heights = [draw.textbbox((0, 0), l, font=font)[3] - draw.textbbox((0, 0), l, font=font)[1] for l in lines]
        line_spacing = int(size * 0.25)
        total_text_h = sum(line_heights) + (len(lines) - 1) * line_spacing

        if total_text_h <= max_h:
            best_font = font
            best_lines = lines
            best_line_spacing = line_spacing
            break

    if not best_lines:
        best_font = ImageFont.load_default()
        best_lines = [text]
        best_line_spacing = 10

    total_text_h = sum([draw.textbbox((0, 0), l, font=best_font)[3] - draw.textbbox((0, 0), l, font=best_font)[1] for l in best_lines]) + (len(best_lines) - 1) * best_line_spacing
    y_cursor = (height - total_text_h) // 2

    for line in best_lines:
        bbox = draw.textbbox((0, 0), line, font=best_font)
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        x = (width - line_w) // 2
        draw.text((x, y_cursor - bbox[1]), line, fill=(255, 255, 255), font=best_font)
        y_cursor += line_h + best_line_spacing

    output = io.BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output






async def run_typeracer_game(cog, ctx, rounds: int = 3):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "TypeRacer")
        rounds = max(1, min(10, rounds))
        join_emoji = "✅"

        signup_embed = discord.Embed(
            title="🏎️ TypeRacer!",
            description=f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\nStarts: <t:{int(time.time() + 21)}:R>\nRounds: **{rounds}**\nMin Players: **2**",
            color=0x000000
        )
        signup_msg = await ctx.send(embed=signup_embed)
        await signup_msg.add_reaction(join_emoji)
        await asyncio.sleep(19)

        signup_msg = await ctx.channel.fetch_message(signup_msg.id)
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
            await signup_msg.edit(embed=discord.Embed(
                description="❌ Khass minimum **2 players** bach tl3bo TypeRacer.",
                color=0x000000
            ))
            return

        active_players = list(players)
        session = MultiplayerGameSession("TypeRacer", active_players, ctx.channel)
        for p in players:
            set_user_in_game(cog.bot, p.id, "TypeRacer", session)

        try:
            scores = {p.id: 0 for p in players}
            wpm_records = {p.id: [] for p in players}

            await signup_msg.edit(embed=discord.Embed(
                description=f"▶️ **TypeRacer bda!** ({rounds} Rounds)\nPlayers: " + ", ".join(p.mention for p in players),
                color=0x000000
            ))
            await asyncio.sleep(2)

            for round_idx in range(1, rounds + 1):
                if len(active_players) < 2 or session.stopped:
                    break

                sentence = cog.get_typeracer_text()

                # Ready countdown
                countdown_msg = await ctx.send(embed=discord.Embed(
                    title=f"🏎️ Round {round_idx}/{rounds}",
                    description="3...",
                    color=0x000000
                ))
                await asyncio.sleep(1)
                await countdown_msg.edit(embed=discord.Embed(
                    title=f"🏎️ Round {round_idx}/{rounds}",
                    description="2...",
                    color=0x000000
                ))
                await asyncio.sleep(1)
                await countdown_msg.edit(embed=discord.Embed(
                    title=f"🏎️ Round {round_idx}/{rounds}",
                    description="1... **GO! 🚀**",
                    color=0x000000
                ))

                # Send image
                img_buf = render_typeracer_image(sentence)
                file = discord.File(img_buf, filename="typeracer.png")
                await ctx.send(file=file)

                start_time = time.perf_counter()
                round_winner = None
                round_elapsed = 0
                round_wpm = 0

                active_ids = {p.id for p in active_players}

                def check(m):
                    if m.channel.id != ctx.channel.id or m.author.id not in active_ids:
                        return False
                    content = m.content.strip()
                    if content.lower() == "exitgame":
                        return True
                    return content.lower() == sentence.lower()

                round_active = True
                while round_active and len(active_players) >= 2:
                    time_left = max(1.0, 45.0 - (time.perf_counter() - start_time))
                    try:
                        msg = await cog.bot.wait_for("message", check=check, timeout=time_left)
                    except asyncio.TimeoutError:
                        await ctx.send(embed=discord.Embed(
                            description="⌛ **Sala lwe9t!** 7ta wa7d ma kteb lkelma s7i7a f had round.",
                            color=0x000000
                        ))
                        round_active = False
                        break

                    if msg.content.strip().lower() == "exitgame":
                        quitter = next((p for p in active_players if p.id == msg.author.id), None)
                        if quitter:
                            active_players.remove(quitter)
                            active_ids.discard(quitter.id)
                            await ctx.send(f"🚪 {quitter.mention} khrej mn lgame.")
                            if len(active_players) < 2:
                                round_active = False
                                break
                        continue

                    # Correct sentence typed!
                    round_elapsed = time.perf_counter() - start_time
                    round_wpm = round(((len(sentence) / 5) / (round_elapsed / 60))) if round_elapsed > 0 else 0
                    round_winner = msg.author
                    scores[msg.author.id] += 1
                    wpm_records[msg.author.id].append(round_wpm)

                    await ctx.send(embed=discord.Embed(
                        description=f"🎉 {round_winner.mention} rbe7 **Round {round_idx}** f **{round_elapsed:.2f}s** (**{round_wpm} WPM**)!",
                        color=0x000000
                    ))
                    round_active = False
                    break

                await asyncio.sleep(3)

            # Game Over Leaderboard
            def player_rank_key(p):
                p_scores = scores.get(p.id, 0)
                avg_wpm = (sum(wpm_records[p.id]) / len(wpm_records[p.id])) if wpm_records[p.id] else 0
                return (p_scores, avg_wpm)

            ranked = sorted(players, key=player_rank_key, reverse=True)
            medals = ["🥇", "🥈", "🥉"] + [f"**#{i+1}**" for i in range(3, len(ranked))]

            lines = []
            for i, p in enumerate(ranked):
                p_scores = scores.get(p.id, 0)
                avg_wpm = round(sum(wpm_records[p.id]) / len(wpm_records[p.id])) if wpm_records[p.id] else 0
                lines.append(f"{medals[i]} {p.mention} — **{p_scores} wins** (Avg: **{avg_wpm} WPM**)")

            leaderboard_embed = discord.Embed(
                title="🏆 TypeRacer — Final Results",
                description="\n".join(lines),
                color=0x000000
            )
            if ranked:
                leaderboard_embed.set_footer(text=f"Winner: {ranked[0].display_name} 🎉")
                economy_cog = cog.bot.get_cog("Economy")
                if economy_cog:
                    top_player = ranked[0]
                    if scores.get(top_player.id, 0) > 0:
                        gross = (len(players) * 50) + (scores[top_player.id] * 50)
                        net, tax = await economy_cog.apply_tax_and_add_balance(top_player.id, gross, context="TypeRacer Win")
                        leaderboard_embed.add_field(
                            name="🏆 Winner Reward",
                            value=f"**{top_player.mention}** rbe7 **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!",
                            inline=False
                        )
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, top_player.id, "typeracer", earnings=net)

                    other_rewards = []
                    for p in ranked[1:]:
                        p_score = scores.get(p.id, 0)
                        if p_score > 0:
                            p_gross = p_score * 50
                            p_net, _ = await economy_cog.apply_tax_and_add_balance(p.id, p_gross, context="TypeRacer Rounds Reward")
                            other_rewards.append(f"**{p.mention}**: **+{p_net}** TAD ({p_score} wins)")

                    if other_rewards:
                        leaderboard_embed.add_field(
                            name="🎖️ Other Rewards",
                            value="\n".join(other_rewards),
                            inline=False
                        )
            await ctx.send(embed=leaderboard_embed)
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


