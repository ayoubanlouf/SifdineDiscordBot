from __future__ import annotations
import re
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

STOPWORDS = {
    "the", "of", "and", "to", "a", "in", "that", "is", "was", "for", "on", "are",
    "with", "as", "by", "at", "from", "this", "an", "or", "which", "but", "not",
    "what", "all", "were", "we", "when", "your", "can", "said", "there", "each",
    "do", "their", "time", "if", "will", "how", "up", "other", "about", "out",
    "many", "then", "them", "these", "so", "some", "her", "would", "make", "like",
    "him", "into", "has", "look", "two", "more", "go", "see", "no", "way",
    "could", "my", "than", "first", "been", "call", "who", "its", "now", "find",
    "long", "down", "day", "did", "get", "come", "made", "may", "part", "it",
    "one", "such", "also", "most", "after", "used", "known", "being", "both", "between"
}

VITAL_ARTICLES = [
    "Coffee", "Internet", "Moon", "Oxygen", "Pyramid", "Cinema", "Guitar",
    "Football", "Electricity", "Computer", "Water", "Diamond", "Telescope",
    "Submarine", "Volcano", "Airplane", "Camera", "Chocolate", "Language",
    "Brain", "Sun", "Galaxy", "Mirror", "Compass", "Clock", "Bicycle",
    "Microscope", "Paper", "Printing_press", "Steam_engine", "Vaccine",
    "Antibiotic", "Battery", "Laser", "Robot", "Plastics", "Radio",
    "Television", "Telephone", "Automobile", "Train", "Bridge", "Castle",
    "Statue_of_Liberty", "Great_Wall_of_China", "Mount_Everest", "Amazon_River",
    "Sahara", "Pacific_Ocean", "Antarctica"
]


async def fetch_wikipedia_article(session: aiohttp.ClientSession, topic: str) -> Optional[dict]:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{topic}"
    headers = {"User-Agent": "SifdineBot/1.0 (Discord Bot)"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                title = data.get("title", "")
                extract = data.get("extract", "")
                if title and extract and len(extract) > 100:
                    return {"title": title, "extract": extract}
    except Exception as e:
        print(f"[Redactle fetch error]: {e}")
    return None


def mask_article(text: str, revealed_words: set[str], reveal_all: bool = False) -> tuple[str, int, int]:
    # Returns (masked_text, total_content_words, revealed_content_words)
    tokens = re.findall(r"\w+|[^\w\s]|\s+", text)
    result = []
    total_words = 0
    revealed_count = 0

    for token in tokens:
        if token.isspace() or not token.isalnum():
            result.append(token)
        else:
            w_lower = token.lower()
            if w_lower in STOPWORDS:
                result.append(token)
            else:
                total_words += 1
                if w_lower in revealed_words or reveal_all:
                    result.append(token)
                    revealed_count += 1
                else:
                    # Redacted block of matching length
                    result.append("█" * len(token))

    return "".join(result), total_words, revealed_count


async def run_redactle_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    set_user_in_game(cog.bot, ctx.author.id, "Redactle")
    turn_duration = 20
    players = []

    try:
        join_emoji = "✅"
        signup_embed = discord.Embed(
            title="🕵️ Redactle · Wikipedia Un-redaction",
            description=(
                f"Guessi lkelmat li mkhebyin fl article bn-nouba.\n\n"
                f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\n"
                f"Starts: <t:{int(time.time() + 21)}:R>\n"
                f"Time/Turn: **{turn_duration}s**"
            ),
            color=0x000000
        )
        signup_embed.set_footer(text="Redactle • Turn-Based • Guess words to reveal text")
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

        # Fetch random article
        chosen_topic = random.choice(VITAL_ARTICLES)
        article_data = await fetch_wikipedia_article(cog.bot.session, chosen_topic)
        if not article_data:
            article_data = await fetch_wikipedia_article(cog.bot.session, "Coffee")

        if not article_data:
            clear_user_game(cog.bot, ctx.author.id)
            for p in players:
                clear_user_game(cog.bot, p.id)
            await ctx.send("❌ Mouchkil f connexion m3a Wikipedia API. 3awd jereb mn be3d.")
            return

        title = article_data["title"]
        extract = article_data["extract"]
        # Limit extract length to avoid embed character overflow
        if len(extract) > 1200:
            extract = extract[:1200].rsplit(" ", 1)[0] + "..."

        active_players = list(players)
        random.shuffle(active_players)

        def _on_redactle_quit(u):
            if u in active_players:
                active_players.remove(u)

        session = MultiplayerGameSession("Redactle", active_players, ctx.channel, on_quit=_on_redactle_quit)
        for p in players:
            set_user_in_game(cog.bot, p.id, "Redactle", session)

        revealed_words: set[str] = set()
        player_unique_words: dict[int, int] = {p.id: 0 for p in players}
        player_occurrences: dict[int, int] = {p.id: 0 for p in players}

        is_solo = (len(players) == 1)
        max_solo_guesses = 25
        solo_guesses_left = max_solo_guesses

        start_desc = f"▶️ **Bdina Redactle!** Solo Mode: 3ndek **{max_solo_guesses} guesses**." if is_solo else f"▶️ **Bdina Redactle!** {len(players)} players."
        await start_msg.edit(embed=discord.Embed(
            description=start_desc,
            color=0x000000
        ))
        await asyncio.sleep(2)

        masked_text, total_words, revealed_count = mask_article(extract, revealed_words)

        game_embed = discord.Embed(
            title="🕵️ Redactle · Article Text",
            description=(
                f"{masked_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Progress:** `{revealed_count}/{total_words} words ({round((revealed_count/max(1, total_words))*100)}%)`"
            ),
            color=0x000000
        )
        game_msg = await ctx.send(embed=game_embed)

        all_text_words = [w.lower() for w in re.findall(r"\w+", extract)]
        turn_idx = 0

        while not session.stopped and active_players:
            if total_words > 0 and revealed_count >= total_words:
                await ctx.send("🎉 **Kmel l'article kaml!** Ga3 lkelmat tl9aw!")
                break

            if is_solo and solo_guesses_left <= 0:
                await ctx.send("⌛ **Salaw lik ga3 l guesses (Game Over)!**")
                break

            current_player = active_players[turn_idx % len(active_players)]
            extra_solo = f" (Ba9i lik: **{solo_guesses_left} guesses**)" if is_solo else ""
            turn_msg = await ctx.send(f"❓ {current_player.mention} kteb kelma bach t-unredactiha{extra_solo}.")
            countdown_task = asyncio.create_task(countdown_reactions(turn_msg, turn_duration))

            def check_turn(m):
                return m.author.id == current_player.id and m.channel.id == ctx.channel.id

            try:
                m = await cog.bot.wait_for("message", check=check_turn, timeout=turn_duration)
                if not countdown_task.done():
                    countdown_task.cancel()

                raw_guess = m.content.strip().lower()
                if raw_guess == "exitgame":
                    active_players.remove(current_player)
                    clear_user_game(cog.bot, current_player.id)
                    await ctx.send(f"🚪 **{current_player.mention}** khrej mn lgame.")
                    if len(active_players) == 0:
                        break
                    continue

                clean_guess = re.sub(r"[^\w]", "", raw_guess)
                if not clean_guess or len(clean_guess) < 2:
                    await ctx.send("⚠️ Kteb kelma fiha au moins 2 7ourof.")
                elif clean_guess in STOPWORDS or clean_guess in revealed_words:
                    try:
                        await m.add_reaction("🔄")
                    except Exception:
                        pass
                    await ctx.send(f"🔄 `{clean_guess}` deja bayna wla stopword, turn daz!")
                    if is_solo:
                        solo_guesses_left -= 1
                else:
                    occ_count = all_text_words.count(clean_guess)
                    if occ_count > 0:
                        revealed_words.add(clean_guess)
                        player_unique_words[current_player.id] += 1
                        player_occurrences[current_player.id] += occ_count

                        try:
                            await m.add_reaction("🔍")
                        except Exception:
                            pass

                        masked_text, total_words, revealed_count = mask_article(extract, revealed_words)
                        game_embed.description = (
                            f"{masked_text}\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 **Progress:** `{revealed_count}/{total_words} words ({round((revealed_count/max(1, total_words))*100)}%)`"
                        )
                        try:
                            await game_msg.edit(embed=game_embed)
                        except Exception:
                            pass

                        await ctx.send(f"🔍 **{clean_guess.upper()}** kayna! (`{occ_count}` occurrences)")
                    else:
                        try:
                            await m.add_reaction("❌")
                        except Exception:
                            pass
                        await ctx.send(f"❌ `{clean_guess}` makaynach f had l'article.")

                    if is_solo:
                        solo_guesses_left -= 1

            except asyncio.TimeoutError:
                if not countdown_task.done():
                    countdown_task.cancel()
                if is_solo:
                    solo_guesses_left -= 1
                await ctx.send(f"⏰ {current_player.mention} sala lik lw9t!")

            turn_idx += 1
            await asyncio.sleep(1.5)

        # Calculate scores strictly based on words guessed: (unique * 8) + (occurrences * 2)
        player_scores = {}
        for p in players:
            unique_cnt = player_unique_words.get(p.id, 0)
            occ_cnt = player_occurrences.get(p.id, 0)
            base_score = (unique_cnt * 8) + (occ_cnt * 2)
            player_scores[p.id] = base_score

        sorted_players = sorted(player_scores.items(), key=lambda x: x[1], reverse=True)
        top_winner_id, top_score = sorted_players[0] if sorted_players else (None, 0)
        top_winner = discord.utils.get(players, id=top_winner_id) if top_winner_id else None

        economy_cog = cog.bot.get_cog("Economy")
        eco_msg = ""
        player_earnings = {}

        if economy_cog:
            if top_winner and top_score > 0:
                gross = int(round((50 * len(players)) + (top_score / 3)))
                net, tax = await economy_cog.apply_tax_and_add_balance(top_winner.id, gross, context="Redactle Top Contributor Win")
                player_earnings[top_winner.id] = net
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (Gross: {gross:,} TAD • 🔥 `{tax:,}` TAD tax burned)!"
                if ctx.guild:
                    await cog.record_minigame_win(ctx.guild.id, top_winner.id, "redactle", earnings=net)

            # Other players get their scaled score
            for pid, pscore in sorted_players[1:]:
                if pscore > 0:
                    p_gross = int(round(pscore / 3))
                    if p_gross > 0:
                        p_net, _ = await economy_cog.apply_tax_and_add_balance(pid, p_gross, context="Redactle Contribution Reward")
                        player_earnings[pid] = p_net

        leaderboard_lines = []
        for rank_pos, (pid, pscore) in enumerate(sorted_players, 1):
            puser = discord.utils.get(players, id=pid)
            pname = puser.display_name if puser else f"Player {pid}"
            u_words = player_unique_words.get(pid, 0)
            o_words = player_occurrences.get(pid, 0)
            earn_str = f" (+{format_tad(player_earnings[pid])})" if pid in player_earnings else ""
            leaderboard_lines.append(f"**#{rank_pos}** {pname}: **{pscore} pts** (`{u_words}` unique • `{o_words}` hits){earn_str}")

        wiki_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"

        final_embed = discord.Embed(
            title=f"📖 Redactle Revealed: {title}",
            description=(
                f"🔗 **[Wikipedia Article]({wiki_url})**\n\n"
                f"{extract}\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🥇 **Top Contributor:** {top_winner.mention if top_winner else 'None'}!{eco_msg}\n\n"
                f"📈 **Scoreboard:**\n" + "\n".join(leaderboard_lines if leaderboard_lines else ["*Walou*"])
            ),
            color=0x000000
        )
        await ctx.send(embed=final_embed)

    finally:
        clear_user_game(cog.bot, ctx.author.id)
        for p in players:
            clear_user_game(cog.bot, p.id)
