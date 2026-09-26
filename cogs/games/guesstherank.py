from __future__ import annotations
import http.cookiejar
import urllib.request
import urllib.parse
import json
import asyncio
import time
from typing import Optional, Union

import discord
from discord.ui import Button, View, Select

from cogs.economy import format_tad, TAD_EMOJI
from cogs.games.helpers import (
    record_minigame_win,
    is_user_in_game, set_user_in_game, clear_user_game
)

GTR_GET_CLIP_ACTION = "65e6f3218d954bd1d692d0aeb3d80af7eb610f50"
GTR_SUBMIT_ACTION = "a73bc4de975f06219fea583ec2d272901533bd75"

GTR_GAMES = {
    "rocketleague": {
        "id": "rocketleague",
        "name": "Rocket League",
        "aliases": ["rl", "rocket"],
        "emoji": "🚗",
        "img_ext": "webp",
        "rank_names": ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Champion", "Grand Champ", "SSL"]
    },
    "valorant": {
        "id": "valorant",
        "name": "Valorant",
        "aliases": ["val", "valo"],
        "emoji": "🎯",
        "img_ext": "webp",
        "rank_names": ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ascendant", "Immortal", "Radiant"]
    },
    "cs2": {
        "id": "cs2",
        "name": "Counter-Strike 2",
        "aliases": ["cs", "csgo", "counterstrike"],
        "emoji": "🔫",
        "img_ext": "png",
        "rank_names": ["0-4.9k", "5k-9.9k", "10k-14.9k", "15k-19.9k", "20k-24.9k", "25k-29.9k", "30k+"]
    },
    "leagueoflegends": {
        "id": "leagueoflegends",
        "name": "League of Legends",
        "aliases": ["lol", "league"],
        "emoji": "⚔️",
        "img_ext": "webp",
        "rank_names": ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond", "Master", "Grandmaster"]
    },
    "overwatch": {
        "id": "overwatch",
        "name": "Overwatch",
        "aliases": ["ow", "ow2"],
        "emoji": "🛡️",
        "img_ext": "webp",
        "rank_names": ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master", "Grandmaster", "Champion"]
    },
    "apexlegends": {
        "id": "apexlegends",
        "name": "Apex Legends",
        "aliases": ["apex"],
        "emoji": "⚡",
        "img_ext": "webp",
        "rank_names": ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master", "Predator"]
    },
    "r6": {
        "id": "r6",
        "name": "Rainbow Six Siege",
        "aliases": ["siege", "rainbowsix"],
        "emoji": "💣",
        "img_ext": "png",
        "rank_names": ["Copper", "Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond", "Champion"]
    },
    "fortnite": {
        "id": "fortnite",
        "name": "Fortnite",
        "aliases": ["fn"],
        "emoji": "🪂",
        "img_ext": "webp",
        "rank_names": ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Elite", "Champion", "Unreal"]
    }
}

def _gtr_session_sync(game_id: str):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req0 = urllib.request.Request(
        f"https://guesstherank.org/{game_id}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    opener.open(req0, timeout=10)
    return opener

def _gtr_get_clip_sync(opener, game_id: str):
    req = urllib.request.Request(
        f"https://guesstherank.org/{game_id}",
        data=json.dumps([game_id]).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Next-Action": GTR_GET_CLIP_ACTION,
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "text/x-component"
        }
    )
    with opener.open(req, timeout=10) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
        for line in text.split("\n"):
            if line.startswith("1:"):
                return json.loads(line[2:])
    return None

def _gtr_submit_guess_sync(opener, game_id: str, clip_id: str, guessed_rank: int, time_taken: int = 10):
    payload = [{
        "clipId": clip_id,
        "guessedRank": guessed_rank,
        "gameId": game_id,
        "timeTaken": time_taken
    }]
    req = urllib.request.Request(
        f"https://guesstherank.org/{game_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Next-Action": GTR_SUBMIT_ACTION,
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "text/x-component"
        }
    )
    with opener.open(req, timeout=10) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
        for line in text.split("\n"):
            if line.startswith("1:"):
                return json.loads(line[2:])
    return None

class GuessTheRankView(discord.ui.View):
    def __init__(self, author: discord.User, cog, game_info: dict, opener, clip_data: dict):
        super().__init__(timeout=90)
        self.author = author
        self.cog = cog
        self.game_info = game_info
        self.opener = opener
        self.clip_data = clip_data
        self.message = None
        self.guessed = False
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        ranks = self.game_info["rank_names"]
        row_split = 4 if len(ranks) == 8 else 5
        for idx, rank_name in enumerate(ranks, start=1):
            row_idx = 0 if (idx - 1) < row_split else 1
            btn = discord.ui.Button(
                label=rank_name,
                style=discord.ButtonStyle.secondary,
                custom_id=f"gtr_{idx}",
                row=row_idx
            )
            btn.callback = self._make_guess_callback(idx, rank_name)
            self.add_item(btn)

    def _make_guess_callback(self, rank_idx: int, rank_label: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                await interaction.response.send_message("❌ Gher li lana had lgame li y9ed yguessi!", ephemeral=True)
                return
            if self.guessed:
                return
            self.guessed = True
            await interaction.response.defer()

            res = await asyncio.to_thread(
                _gtr_submit_guess_sync,
                self.opener,
                self.game_info["id"],
                self.clip_data["clipId"],
                rank_idx,
                10
            )

            if not res:
                await interaction.followup.send("❌ Tra chy mochkil f submit dial guess.", ephemeral=True)
                return

            actual_rank_idx = res.get("actualRank", 0)
            points_earned = res.get("pointsEarned", 0)
            ranks = self.game_info["rank_names"]
            actual_rank_name = ranks[actual_rank_idx - 1] if (1 <= actual_rank_idx <= len(ranks)) else f"Rank {actual_rank_idx}"

            eco_msg = ""
            economy_cog = self.cog.bot.get_cog("Economy")
            if points_earned > 0 and economy_cog:
                tad_payout = 100 if points_earned == 100 else 50
                net, tax = await economy_cog.apply_tax_and_add_balance(
                    self.author.id,
                    tad_payout,
                    context=f"GuessTheRank ({self.game_info['name']}) Win"
                )
                eco_msg = f"\n💰 Rbe7ti **+{format_tad(net)}** (🔥 `{tax:,}` TAD tax burned)!"
                if interaction.guild:
                    await self.cog.record_minigame_win(interaction.guild.id, self.author.id, "guesstherank", earnings=net)

            if points_earned == 100:
                outcome_header = "🎯 **EXACT GUESS! Mhyeeeb!**"
            elif points_earned == 50:
                outcome_header = "🤏 **CLOSE! Gher b rank w7da!**"
            else:
                outcome_header = "❌ **WRONG GUESS! Majbtihach.**"

            rank_icon_ext = self.game_info.get("img_ext", "webp")
            rank_icon_url = f"https://guesstherank.org/images/ranks/{self.game_info['id']}/{self.game_info['id']}_{actual_rank_idx}.{rank_icon_ext}"

            embed = discord.Embed(
                title=f"{self.game_info['emoji']} Guess The Rank — {self.game_info['name']}",
                description=(
                    f"{outcome_header}{eco_msg}\n\n"
                    f"🎮 Lkhtiyar dialek: **{rank_label}**\n"
                    f"🏆 Actual Rank: **{actual_rank_name}**"
                ),
                color=0x000000
            )
            embed.set_thumbnail(url=rank_icon_url)
            embed.set_footer(text="GuessTheRank.org • Clicki ▶️ Next Clip bach tkemmel")

            self.clear_items()
            next_btn = discord.ui.Button(label="▶️ Next Clip", style=discord.ButtonStyle.success)
            quit_btn = discord.ui.Button(label="❌ Quit", style=discord.ButtonStyle.danger)

            async def next_callback(next_interaction: discord.Interaction):
                if next_interaction.user.id != self.author.id:
                    await next_interaction.response.send_message("❌ Gher mol lgame li y9ed ykemmel!", ephemeral=True)
                    return
                await next_interaction.response.defer()
                new_clip = await asyncio.to_thread(_gtr_get_clip_sync, self.opener, self.game_info["id"])
                if not new_clip:
                    await next_interaction.followup.send("❌ Mal9itch clip jdid f had lwe9t.", ephemeral=True)
                    return
                new_view = GuessTheRankView(self.author, self.cog, self.game_info, self.opener, new_clip)
                new_content = (
                    f"**{self.game_info['emoji']} Guess The Rank — {self.game_info['name']}**\n"
                    f"https://www.youtube.com/watch?v={new_clip['youtubeId']}\n\n"
                    f"-# Exact Guess: **+100** TAD, 1 Rank Off: **+50** TAD"
                )
                msg = await next_interaction.edit_original_response(
                    content=new_content,
                    embed=None,
                    view=new_view
                )
                new_view.message = msg

            async def quit_callback(quit_interaction: discord.Interaction):
                if quit_interaction.user.id != self.author.id:
                    return
                self.clear_items()
                await quit_interaction.response.edit_message(view=None)
                self.stop()

            next_btn.callback = next_callback
            quit_btn.callback = quit_callback
            self.add_item(next_btn)
            self.add_item(quit_btn)

            await interaction.edit_original_response(content=None, embed=embed, view=self)

        return callback

    def stop(self):
        if self.cog and hasattr(self.cog, "bot") and self.author:
            clear_user_game(self.cog.bot, self.author.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        self.guessed = True
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass
        self.stop()
        return "🚪 Kherjti mn **Guess The Rank**!"

    async def on_timeout(self):
        self.stop()

class GuessTheRankSelectView(discord.ui.View):
    def __init__(self, author: discord.User, cog):
        super().__init__(timeout=60)
        self.author = author
        self.cog = cog
        self.message = None

        options = []
        for gid, gdata in GTR_GAMES.items():
            options.append(
                discord.SelectOption(
                    label=gdata["name"],
                    value=gid,
                    emoji=gdata["emoji"],
                    description=f"Guess the {gdata['name']} rank."
                )
            )

        select = discord.ui.Select(
            placeholder="🎮 Khtar game bach tl3b...",
            min_values=1,
            max_values=1,
            options=options
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def _select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Gher li lana had l'amr li y9ed yakhtar lgame!", ephemeral=True)
            return

        selected_gid = interaction.data["values"][0]
        target_game = GTR_GAMES.get(selected_gid)
        if not target_game:
            await interaction.response.send_message("❌ Game invalid.", ephemeral=True)
            return

        if is_user_in_game(self.cog.bot, self.author.id):
            await interaction.response.send_message("❌ 3ndek deja game khddama!", ephemeral=True)
            return

        self.clear_items()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Sber 3lia...", color=0x000000),
            view=None
        )

        try:
            opener = await asyncio.to_thread(_gtr_session_sync, target_game["id"])
            clip = await asyncio.to_thread(_gtr_get_clip_sync, opener, target_game["id"])
        except Exception as e:
            await interaction.followup.send(f"❌ Tra chy mochkil f fetching dial clip: `{e}`")
            return

        if not clip:
            await interaction.followup.send("❌ Mal9itch clip f had lwe9t. 3awed jereb mn b3d.")
            return

        set_user_in_game(self.cog.bot, self.author.id, "Guess The Rank")
        view = GuessTheRankView(self.author, self.cog, target_game, opener, clip)
        content = (
            f"**{target_game['emoji']} Guess The Rank — {target_game['name']}**\n"
            f"https://www.youtube.com/watch?v={clip['youtubeId']}\n\n"
            f"-# Exact Guess: **+100** TAD, 1 Rank Off: **+50** TAD"
        )
        msg = await interaction.edit_original_response(content=content, embed=None, view=view)
        view.message = msg


