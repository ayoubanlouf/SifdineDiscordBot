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

# ============ MINECRAFT CRAFTING HELPERS ============

_MC_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "minecraft"))
_MC_TEXTURES_DIR = os.path.join(_MC_BASE_DIR, "textures")
_MC_GUI_PATH = os.path.join(_MC_BASE_DIR, "crafting_table_gui.png")
_MC_RECIPES_PATH = os.path.join(_MC_BASE_DIR, "recipes.json")

_mc_recipes = None
_mc_texture_cache = {}
_mc_gui_image = None

def _load_minecraft_recipes():
    global _mc_recipes
    if _mc_recipes is None:
        if os.path.exists(_MC_RECIPES_PATH):
            try:
                with open(_MC_RECIPES_PATH, "r", encoding="utf-8") as f:
                    _mc_recipes = json.load(f)
            except Exception:
                _mc_recipes = []
        else:
            _mc_recipes = []
    return _mc_recipes

def _get_mc_texture(name: str):
    if not name:
        return None
    if name not in _mc_texture_cache:
        path = os.path.join(_MC_TEXTURES_DIR, f"{name}.png")
        if os.path.exists(path):
            try:
                _mc_texture_cache[name] = Image.open(path).convert("RGBA")
            except Exception:
                _mc_texture_cache[name] = None
        else:
            _mc_texture_cache[name] = None
    return _mc_texture_cache[name]

def render_crafting_table(grid: list) -> io.BytesIO:
    global _mc_gui_image
    if _mc_gui_image is None:
        if os.path.exists(_MC_GUI_PATH):
            try:
                _mc_gui_image = Image.open(_MC_GUI_PATH).convert("RGBA")
            except Exception:
                _mc_gui_image = Image.new("RGBA", (256, 256), (198, 198, 198, 255))
        else:
            _mc_gui_image = Image.new("RGBA", (256, 256), (198, 198, 198, 255))

    # Crop the active crafting interface: x=24 to x=155, y=11 to y=72
    craft_box = (24, 11, 155, 72)
    base_gui = _mc_gui_image.crop(craft_box)

    scale = 4
    scaled_w = base_gui.width * scale
    scaled_h = base_gui.height * scale
    canvas = base_gui.resize((scaled_w, scaled_h), Image.Resampling.NEAREST)

    for r in range(3):
        for c in range(3):
            if r < len(grid) and c < len(grid[r]):
                item_name = grid[r][c]
                if item_name:
                    tex = _get_mc_texture(item_name)
                    if tex:
                        item_scaled = tex.resize((16 * scale, 16 * scale), Image.Resampling.NEAREST)
                        item_x = (6 + c * 18) * scale
                        item_y = (6 + r * 18) * scale
                        canvas.paste(item_scaled, (item_x, item_y), item_scaled)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf

def normalize_crafting_text(text: str) -> str:
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

def is_crafting_guess_correct(guess: str, recipe: dict) -> bool:
    norm_guess = normalize_crafting_text(guess)
    if not norm_guess:
        return False

    targets = [recipe.get("displayName", ""), recipe.get("name", "")] + recipe.get("aliases", [])
    for target in targets:
        if not target:
            continue
        norm_target = normalize_crafting_text(target)
        if norm_guess == norm_target:
            return True
        if len(norm_guess) >= 4 and difflib.SequenceMatcher(None, norm_guess, norm_target).ratio() >= 0.85:
            return True

    return False



async def run_crafting_game(cog, ctx, *args):
        if not await cog.ensure_user_free(ctx):
            return
        set_user_in_game(cog.bot, ctx.author.id, "Crafting Table")
        round_duration, difficulty = parse_minigame_args(*args, default_duration=20, default_difficulty="easy")
        time_display = f"{round_duration}s"
        mult = DIFFICULTY_STAKES[difficulty]

        all_recipes = _load_minecraft_recipes()
        if not all_recipes:
            await ctx.send(embed=discord.Embed(
                description="❌ Ma l9itch recipes ta3 Minecraft!",
                color=0x000000
            ))
            return

        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🔨 Crafting Table",
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
        session = MultiplayerGameSession("Crafting Table", active_players, ctx.channel)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Crafting Table", session)

        try:
            single_player = len(players) == 1
            hp = {player.id: 3 for player in players}

            # Apply difficulty filter to recipe pool
            if difficulty == "easy":
                pool = [r for r in all_recipes if r.get("difficulty") == "easy"] or all_recipes
            elif difficulty == "medium":
                pool = [r for r in all_recipes if r.get("difficulty") in ("easy", "medium")] or all_recipes
            else:
                pool = list(all_recipes)

            match_pool = list(pool)
            random.shuffle(match_pool)

            start_embed = discord.Embed(
                description=f"▶️ Bdina! Kola wa7d 3ndo **3 HP**.\n🎯 Difficulty: **{difficulty.upper()}** (Stake: **{diff_mult}x**)",
                color=0x000000
            )
            await signup_msg.edit(embed=start_embed, view=None)
            await asyncio.sleep(2)

            player_correct_items = {p.id: 0 for p in players}

            while len(active_players) > 0 and not session.stopped:
                if not single_player and len(active_players) == 1:
                    winner = active_players[0]
                    economy_cog = cog.bot.get_cog("Economy")
                    eco_msg = ""
                    player_earnings = {}
                    winner_items = player_correct_items.get(winner.id, 0)
                    if economy_cog:
                        gross = (len(players) * 50) + int(round((winner_items * 15) * diff_mult))
                        net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Crafting Table Win")
                        player_earnings[winner.id] = net
                        eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                        if ctx.guild:
                            await cog.record_minigame_win(ctx.guild.id, winner.id, "craftingtable", earnings=net)

                        for pid, p_items in player_correct_items.items():
                            if pid != winner.id and p_items > 0:
                                p_gross = int(round((p_items * 15) * diff_mult))
                                if p_gross > 0:
                                    p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Crafting Table Reward")
                                    player_earnings[pid] = p_net

                    others_msg = ""
                    other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_items[pid]} items)" for pid, net in player_earnings.items() if pid != winner.id]
                    if other_rewards:
                        others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                    win_embed = discord.Embed(
                        description=f"🏆 {winner.mention} rbe7 lgame b **{winner_items} items**!{eco_msg}{others_msg}",
                        color=0x000000
                    )
                    await ctx.send(embed=win_embed)
                    return

                if not match_pool:
                    await ctx.send(embed=discord.Embed(
                        description="🏁 **Crafting pool salaw kamlin! Game sala.**",
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
                    correct_name = target["displayName"]

                    buf = render_crafting_table(target["grid"])
                    file = discord.File(buf, filename="crafting.png")

                    game_embed = discord.Embed(
                        description=f"🔨 Chno katsawb had recipe?\n⌛ Time: {round_duration}s\n❤️ HP: {hp[player.id]}\n📦 Recipes left: **{len(match_pool) + 1}**",
                        color=0x000000
                    )
                    game_embed.set_image(url="attachment://crafting.png")
                    round_msg = await ctx.send(player.mention, embed=game_embed, file=file)

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

                            if msg.author.id == player.id and is_crafting_guess_correct(msg.content, target):
                                if not countdown_task.done():
                                    countdown_task.cancel()
                                try:
                                    await msg.add_reaction("✅")
                                except Exception:
                                    pass
                                guessed_correctly = True
                                player_correct_items[player.id] = player_correct_items.get(player.id, 0) + 1
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
                p_items = player_correct_items.get(player.id, 0)
                gross = 50 + int(round((p_items * 15) * diff_mult))
                eco_msg = ""
                if economy_cog and p_items > 0:
                    net, tax = await economy_cog.apply_tax_and_add_balance(player.id, gross, context="Crafting Table Solo")
                    eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                    if ctx.guild:
                        await cog.record_minigame_win(ctx.guild.id, player.id, "craftingtable", earnings=net)
                await ctx.send(embed=discord.Embed(
                    description=f"🎯 Game Over {player.mention}! L9iti **{p_items} items**.{eco_msg}",
                    color=0x000000
                ))
            elif not single_player:
                max_guesses = max(player_correct_items.values()) if player_correct_items else 0
                if max_guesses > 0:
                    top_players = [p for p in players if player_correct_items.get(p.id, 0) == max_guesses]
                    economy_cog = cog.bot.get_cog("Economy")
                    player_earnings = {}
                    if len(top_players) == 1:
                        winner = top_players[0]
                        eco_msg = ""
                        if economy_cog:
                            gross = (len(players) * 50) + int(round((max_guesses * 15) * diff_mult))
                            net, tax = await economy_cog.apply_tax_and_add_balance(winner.id, gross, context="Crafting Table Win")
                            player_earnings[winner.id] = net
                            eco_msg = f"\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • 🔥 `{tax}` TAD 2% tax burned)!"
                            if ctx.guild:
                                await cog.record_minigame_win(ctx.guild.id, winner.id, "craftingtable", earnings=net)

                            for pid, p_items in player_correct_items.items():
                                if pid != winner.id and p_items > 0:
                                    p_gross = int(round((p_items * 15) * diff_mult))
                                    if p_gross > 0:
                                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Crafting Table Reward")
                                        player_earnings[pid] = p_net

                        others_msg = ""
                        other_rewards = [f"<@{pid}>: **+{net}** TAD ({player_correct_items[pid]} items)" for pid, net in player_earnings.items() if pid != winner.id]
                        if other_rewards:
                            others_msg = "\n\n🎖️ **Other Rewards:**\n" + " • ".join(other_rewards)

                        await ctx.send(embed=discord.Embed(
                            description=f"🏆 {winner.mention} 3ndo a3la score b **{max_guesses} items** o rbe7 lgame!{eco_msg}{others_msg}",
                            color=0x000000
                        ))
                    else:
                        winners_mention = " o ".join(p.mention for p in top_players)
                        if economy_cog:
                            for pid, p_items in player_correct_items.items():
                                if p_items > 0:
                                    p_gross = int(round((p_items * 15) * diff_mult))
                                    if p_gross > 0:
                                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Crafting Table Reward (Tie)")
                                        player_earnings[pid] = p_net

                        others_msg = ""
                        rewards_list = [f"<@{pid}>: **+{net}** TAD ({player_correct_items[pid]} items)" for pid, net in player_earnings.items()]
                        if rewards_list:
                            others_msg = "\n\n💰 **Rewards:**\n" + " • ".join(rewards_list)

                        await ctx.send(embed=discord.Embed(
                            description=f"🤝 Ta3adol bin {winners_mention} b **{max_guesses} items**!{others_msg}",
                            color=0x000000
                        ))
                else:
                    await ctx.send(embed=discord.Embed(
                        description="🎯 Game Over! Ta wa7d ma jab chy item s7i7.",
                        color=0x000000
                    ))
        finally:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)


