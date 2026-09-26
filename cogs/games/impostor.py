from __future__ import annotations
import asyncio
import os
import random
import time
from typing import Optional, List, Dict, Any, Union

import discord
from discord.ui import View, Button, Modal, TextInput, Select

from cogs.economy import parse_bet_argument, format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import is_user_in_game, set_user_in_game, clear_user_game, attach_game_session


CURATED_WORDS: List[Dict[str, Any]] = [
    # Food & Drinks
    {"word": "Pizza", "aliases": ["pizza", "piza", "بيتزا", "pizzat"]},
    {"word": "Coffee", "aliases": ["coffee", "cafe", "qahwa", "9ahwa", "قهوة", "café"]},
    {"word": "Chocolate", "aliases": ["chocolate", "chocolat", "chokla", "شكلاط", "chocola"]},
    {"word": "Burger", "aliases": ["burger", "hamburger", "برغر", "sandwitch"]},
    {"word": "Ice Cream", "aliases": ["ice cream", "glace", "la glace", "ايس كريم", "مثلجات"]},
    {"word": "Water", "aliases": ["water", "lma", "ma", "eau", "ماء"]},
    {"word": "Tea", "aliases": ["tea", "atay", "ataye", "شاي", "thé"]},
    {"word": "Tajine", "aliases": ["tajine", "tagine", "طاجين", "tjin"]},
    {"word": "Banana", "aliases": ["banana", "banane", "banan", "بنان", "موز"]},
    {"word": "Apple", "aliases": ["apple", "pomme", "tefa7", "تفاح", "tfa7"]},

    # Tech & Objects
    {"word": "iPhone", "aliases": ["iphone", "apple", "ayfon", "smartphone", "telephone", "ايفون"]},
    {"word": "Television", "aliases": ["television", "tv", "tele", "tilifizyoun", "تلفاز", "tilifisyou"]},
    {"word": "Headphones", "aliases": ["headphones", "casque", "ecouteur", "écouter", "سماعات", "kask"]},
    {"word": "Watch", "aliases": ["watch", "montre", "magana", "sa3a", "ساعة"]},
    {"word": "Car", "aliases": ["car", "tomobil", "voiture", "sayara", "سيارة", "auto"]},
    {"word": "Airplane", "aliases": ["airplane", "plane", "tayara", "avion", "طائرة"]},
    {"word": "Bicycle", "aliases": ["bicycle", "bike", "pikala", "velo", "دراجة", "bicyclette"]},
    {"word": "Camera", "aliases": ["camera", "kamira", "appareil photo", "كاميرا"]},
    {"word": "Money", "aliases": ["money", "flous", "flos", "argent", "درهم", "نقود", "flousse"]},
    {"word": "Book", "aliases": ["book", "livre", "ktab", "kitab", "كتاب"]},

    # Places
    {"word": "Cinema", "aliases": ["cinema", "sinima", "سينما", "movie theater"]},
    {"word": "Hospital", "aliases": ["hospital", "hopital", "sbitar", "mustashfa", "مستشفى"]},
    {"word": "School", "aliases": ["school", "ecole", "madrasa", "lycee", "مدرسة"]},
    {"word": "Mosque", "aliases": ["mosque", "mosquee", "jame3", "jami3", "مسجد", "جامع"]},
    {"word": "Airport", "aliases": ["airport", "aeroport", "matar", "مطار"]},
    {"word": "Beach", "aliases": ["beach", "plage", "b7ar", "bahr", "شاطئ", "بحر"]},
    {"word": "Gym", "aliases": ["gym", "salle", "la salle", "musculation", "sport", "جيم"]},
    {"word": "Supermarket", "aliases": ["supermarket", "marjane", "carrefour", "supermarche", "hanout", "سوبرماركت"]},
    {"word": "Desert", "aliases": ["desert", "sahra", "sa7ra", "صحراء"]},
    {"word": "Hotel", "aliases": ["hotel", "foundouq", "otel", "فندق"]},

    # Famous / Characters / Roles
    {"word": "Spider-Man", "aliases": ["spiderman", "spider-man", "spider man", "سبايدرمان", "rajol l3ankabout"]},
    {"word": "Batman", "aliases": ["batman", "باتمان", "rajol lwatwat"]},
    {"word": "Lionel Messi", "aliases": ["messi", "lionel messi", "ميسي", "leo messi"]},
    {"word": "Cristiano Ronaldo", "aliases": ["ronaldo", "cristiano", "cr7", "رونالدو"]},
    {"word": "Police", "aliases": ["police", "boulis", "chorta", "بوليس", "شرطة"]},
    {"word": "Doctor", "aliases": ["doctor", "medecin", "tbib", "tabib", "طبيب", "doc"]},
    {"word": "Teacher", "aliases": ["teacher", "prof", "ostad", "oustad", "professeur", "معلم", "استاذ"]},
    {"word": "Firefighter", "aliases": ["firefighter", "pompiya", "pompier", "مطافئ", "rejal lmatani2"]},

    # Nature & Living
    {"word": "Moon", "aliases": ["moon", "lune", "gmar", "qamar", "قمر"]},
    {"word": "Sun", "aliases": ["sun", "soleil", "chems", "shams", "شمس"]},
    {"word": "Rain", "aliases": ["rain", "pluie", "chta", "chtat", "matar", "مطر", "شتة"]},
    {"word": "Mountain", "aliases": ["mountain", "montagne", "jbel", "jabal", "جبل"]},
    {"word": "Fire", "aliases": ["fire", "feu", "3afia", "nar", "عافية", "نار"]},
    {"word": "Cat", "aliases": ["cat", "chat", "moch", "moucha", "qitta", "قطة", "مش"]},
    {"word": "Dog", "aliases": ["dog", "chien", "kelb", "kalb", "كلب"]},
    {"word": "Lion", "aliases": ["lion", "sbe3", "assad", "أسد", "سبع"]},

    # Culture / Concepts
    {"word": "Ramadan", "aliases": ["ramadan", "ramdan", "رمضان", "remdan"]},
    {"word": "Football", "aliases": ["football", "soccer", "koora", "kora", "foot", "كرة القدم"]},
    {"word": "Birthday", "aliases": ["birthday", "anniversaire", "3id milad", "عيد ميلاد"]},
    {"word": "Music", "aliases": ["music", "musique", "mousi9a", "musiqa", "موسيقى", "aghani"]},
    {"word": "Sleep", "aliases": ["sleep", "dodo", "sommeil", "n3as", "no3as", "نوم", "نعاس"]},
    {"word": "Video Games", "aliases": ["video games", "gaming", "jeux", "al3ab", "العاب فيديو", "game"]}
]


class ImpostorClueModal(Modal, title="Impostor — Kteb Sentence"):
    sentence_input = TextInput(
        label="Sentence / Clue 3la lkelma",
        style=discord.TextStyle.paragraph,
        placeholder="Kteb clue mzyan bla mat3iyeq bezaf...",
        required=True,
        min_length=2,
        max_length=150
    )

    def __init__(self, game_session: ImpostorGameSession, player: discord.Member):
        super().__init__()
        self.game_session = game_session
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        sentence = self.sentence_input.value.strip()
        await self.game_session.handle_clue_submission(interaction, self.player, sentence)


class ImpostorGuessModal(Modal, title="🎯 Guess Lkelma Siriya (Clutch)"):
    guess_input = TextInput(
        label="Chno kant lkelma siriya?",
        style=discord.TextStyle.short,
        placeholder="Kteb lkelma (mathalan: Pizza, Coffee, Spider-Man...)",
        required=True,
        min_length=2,
        max_length=50
    )

    def __init__(self, game_session: ImpostorGameSession):
        super().__init__()
        self.game_session = game_session

    async def on_submit(self, interaction: discord.Interaction):
        guess = self.guess_input.value.strip()
        await self.game_session.handle_impostor_guess_submit(interaction, guess)


class ImpostorRoleInspectorView(View):
    """Auxiliary view providing a persistent role inspection button."""
    def __init__(self, game_session: ImpostorGameSession):
        super().__init__(timeout=None)
        self.game_session = game_session

    @discord.ui.button(label="🤫 Chouf Dawr Dialek (Secret)", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="impostor_inspect_role")
    async def inspect_role_button(self, interaction: discord.Interaction, button: Button):
        await self.game_session.show_secret_role(interaction)


class ImpostorTurnView(View):
    def __init__(self, game_session: ImpostorGameSession):
        super().__init__(timeout=60)
        self.game_session = game_session

    @discord.ui.button(label="✍️ Kteb Sentence", style=discord.ButtonStyle.primary, emoji="📝", custom_id="impostor_submit_clue_btn")
    async def submit_clue_button(self, interaction: discord.Interaction, button: Button):
        current_player = self.game_session.get_current_turn_player()
        if not current_player or interaction.user.id != current_player.id:
            await interaction.response.send_message("❌ Mashi nouba dialk daba! Tsna 7ta tji noubtk.", ephemeral=True)
            return
        await interaction.response.send_modal(ImpostorClueModal(self.game_session, current_player))

    @discord.ui.button(label="🤫 Chouf Dawr Dialek", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="impostor_turn_role_btn")
    async def inspect_role(self, interaction: discord.Interaction, button: Button):
        await self.game_session.show_secret_role(interaction)

    @discord.ui.button(label="🚪 Khroj", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="impostor_turn_quit_btn")
    async def quit_button(self, interaction: discord.Interaction, button: Button):
        if not any(p.id == interaction.user.id for p in self.game_session.active_players):
            await interaction.response.send_message("❌ Nta mamcharkch f had lgame.", ephemeral=True)
            return
        await interaction.response.defer()
        msg = await self.game_session.handle_user_quit(interaction.user)
        clear_user_game(self.game_session.cog.bot, interaction.user.id)
        if msg:
            await interaction.followup.send(msg, ephemeral=True)


class ImpostorVotingView(View):
    def __init__(self, game_session: ImpostorGameSession, eligible_players: List[discord.Member]):
        super().__init__(timeout=40)
        self.game_session = game_session
        self.eligible_players = eligible_players
        self.voted_users: set[int] = set()

        # Build dropdown options: each player + Skip option
        options = []
        for p in eligible_players:
            options.append(discord.SelectOption(
                label=p.display_name[:25],
                description=f"Vote out {p.display_name}",
                value=str(p.id),
                emoji="🎯"
            ))
        options.append(discord.SelectOption(
            label="Skip Vote",
            description="Dowz l round jdid dial clues bla elimination",
            value="skip",
            emoji="⏭️"
        ))

        self.select_menu = Select(
            placeholder="🗳️ Khtar chkoun ban lik Impostor wla Skip...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="impostor_vote_select"
        )
        self.select_menu.callback = self.vote_callback
        self.add_item(self.select_menu)

        role_btn = Button(label="🤫 Chouf Dawr Dialek", style=discord.ButtonStyle.secondary, emoji="🔍")
        role_btn.callback = self.inspect_role_callback
        self.add_item(role_btn)

    async def inspect_role_callback(self, interaction: discord.Interaction):
        await self.game_session.show_secret_role(interaction)

    async def vote_callback(self, interaction: discord.Interaction):
        user = interaction.user
        if not any(p.id == user.id for p in self.game_session.active_players):
            await interaction.response.send_message("❌ Nta mamcharkch f had lgame.", ephemeral=True)
            return
        if user.id in self.voted_users:
            await interaction.response.send_message("⚠️ Votiti deja! Mat9derch t3awed.", ephemeral=True)
            return

        choice = self.select_menu.values[0]
        self.voted_users.add(user.id)
        self.game_session.record_vote(user.id, choice)

        voted_name = "Skip" if choice == "skip" else next((p.display_name for p in self.eligible_players if str(p.id) == choice), "Unknown")
        await interaction.response.send_message(f"✅ Votiti 3la: **{voted_name}**.", ephemeral=True)

        if len(self.voted_users) >= len(self.game_session.active_players):
            self.stop()
            await self.game_session.finalize_voting()


class ImpostorClutchGuessView(View):
    def __init__(self, game_session: ImpostorGameSession, impostor: discord.Member):
        super().__init__(timeout=30)
        self.game_session = game_session
        self.impostor = impostor

    @discord.ui.button(label="🎯 Guess Lkelma Daba!", style=discord.ButtonStyle.danger, emoji="💥", custom_id="impostor_clutch_btn")
    async def clutch_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.impostor.id:
            await interaction.response.send_message("❌ Had l chance dial L'Impostor bo7do!", ephemeral=True)
            return
        await interaction.response.send_modal(ImpostorGuessModal(self.game_session))

    async def on_timeout(self):
        if not self.game_session.game_over:
            await self.game_session.resolve_clutch_timeout()


class ImpostorLobbyView(View):
    def __init__(self, host: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=40)
        self.host = host
        self.cog = cog
        self.bet = bet
        self.players: List[discord.Member] = [host]
        self.started = False
        self.message: Optional[discord.Message] = None

    def build_lobby_embed(self, time_left: int = 30) -> discord.Embed:
        join_emoji = "✅"
        wager_str = f"\nBet: **{format_tad(self.bet)}**" if self.bet > 0 else ""
        return discord.Embed(
            title="🕵️ Impostor!",
            description=(
                f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\n"
                f"Starts: <t:{int(time.time() + time_left)}:R>\n"
                f"Min Players: **3**\n"
                f"Max Players: **8**"
                f"{wager_str}"
            ),
            color=0x000000
        )

    @discord.ui.button(label="Join / Leave", style=discord.ButtonStyle.primary, emoji="✅", custom_id="impostor_lobby_toggle")
    async def join_toggle(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if user in self.players:
            if user.id == self.host.id:
                await interaction.response.send_message("❌ Nta mol lgame (Host), mat9derch tkhroj mn lobby!", ephemeral=True)
                return
            self.players.remove(user)
            clear_user_game(self.cog.bot, user.id)
            await interaction.response.send_message("🚪 Kherjti mn lobby.", ephemeral=True)
        else:
            if len(self.players) >= 8:
                await interaction.response.send_message("❌ Lobby 3amra (Max 8 players).", ephemeral=True)
                return
            busy = is_user_in_game(self.cog.bot, user.id)
            if busy:
                await interaction.response.send_message(f"❌ 3ndek deja game khddama ({busy})!", ephemeral=True)
                return
            if self.bet > 0:
                economy_cog = self.cog.bot.get_cog("Economy")
                if economy_cog:
                    w = await economy_cog.get_wallet(user.id)
                    if w["balance"] < self.bet:
                        await interaction.response.send_message(f"❌ Flousk makafyinch l had wager ({format_tad(w['balance'])} / {format_tad(self.bet)})!", ephemeral=True)
                        return
            self.players.append(user)
            set_user_in_game(self.cog.bot, user.id, "Impostor")
            await interaction.response.send_message("✅ Dkhelti l lobby dial Impostor!", ephemeral=True)

        if self.message:
            try:
                await self.message.edit(embed=self.build_lobby_embed(), view=self)
            except Exception:
                pass

    @discord.ui.button(label="Start Now", style=discord.ButtonStyle.success, emoji="▶️", custom_id="impostor_lobby_start")
    async def start_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("❌ Gher l'Host li y9ed ybda lgame 9bel lwe9t.", ephemeral=True)
            return
        if len(self.players) < 3:
            await interaction.response.send_message(f"❌ Khass minimum 3 players! Ba9i gher {len(self.players)}.", ephemeral=True)
            return
        self.started = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", custom_id="impostor_lobby_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.host.id:
            await interaction.response.send_message("❌ Gher l'Host li y9ed y-annuli lobby.", ephemeral=True)
            return
        self.started = False
        self.stop()
        for p in self.players:
            clear_user_game(self.cog.bot, p.id)
        if self.message:
            try:
                await self.message.edit(content="❌ Lobby t'annulat mn taraf l'Host.", embed=None, view=None)
            except Exception:
                pass


class ImpostorGameSession:
    """Manages the full lifecycle of an active Impostor match."""
    def __init__(self, host: discord.Member, players: List[discord.Member], channel: discord.TextChannel, cog, bet: int = 0):
        self.host = host
        self.active_players = list(players)
        self.original_players = list(players)
        self.channel = channel
        self.cog = cog
        self.bet = bet
        self.game_name = "Impostor"

        # Secret setup
        self.target_data = random.choice(CURATED_WORDS)
        self.secret_word = self.target_data["word"]
        self.word_aliases = [a.lower() for a in self.target_data["aliases"]]
        self.impostor = random.choice(self.active_players)

        # Game state
        self.round_num = 1
        self.max_rounds = 2
        self.current_turn_idx = 0
        self.turn_order: List[discord.Member] = []
        self.clues: Dict[int, List[Dict[str, str]]] = {1: [], 2: []}
        self.votes: Dict[str, int] = {}
        self.game_over = False
        self.stopped = False
        self.message: Optional[discord.Message] = None
        self.active_turn_view: Optional[ImpostorTurnView] = None
        self.active_voting_view: Optional[ImpostorVotingView] = None
        self.turn_timeout_task: Optional[asyncio.Task] = None

    async def start(self):
        # Deduct bet if wager active
        if self.bet > 0:
            economy_cog = self.cog.bot.get_cog("Economy")
            if economy_cog:
                for p in self.active_players:
                    try:
                        await economy_cog.deduct_balance(p.id, self.bet, context=f"Impostor Wager Stake ({self.bet} TAD)")
                    except Exception:
                        pass

        # Send DMs with secret role
        for p in self.active_players:
            attach_game_session(self.cog.bot, p.id, self)
            try:
                if p.id == self.impostor.id:
                    dm_text = (
                        "🎭 **Nta hwa L'IMPOSTOR f had lgame!**\n\n"
                        "❌ **Ma 3ndekch lkelma siriya!**\n"
                        "🎯 **Hadaf dialk:** 9ra sentences dial nass f chat, bluffi f sentence dialk bach maychkoukch fik, "
                        "o 7awel tstentej chno hia lkelma siriya!\n"
                        "💡 Ila 7aslo bik, ba9i 3ndek chance t-guessi lkelma o tser9 l win!"
                    )
                else:
                    dm_text = (
                        f"🤫 **Lkelma Siriya hia:** **{self.secret_word.upper()}**\n\n"
                        f"🎯 **Hadaf dialk:** 3ti clue zwin 3la lkelma bla matfde7ha bzaf l'impostor!\n"
                        f"Chouf chkoun makatjiwch sentence dialo las9a m3a lkelma bach tvotiw 3lih."
                    )
                await p.send(dm_text)
            except discord.Forbidden:
                pass

        # Post game start announcement
        start_embed = discord.Embed(
            title="🕵️ L'IMPOSTOR — GAME BDAT!",
            description=(
                f"Wa7d fikom hwa **L'Impostor** li ma3ndo ta kelma, o lkhrin 3ndhom nafss lkelma!\n\n"
                f"🤫 Lkelmat tsifto f **Private DMs** (ila maselkch DM, clicki l bouton lte7t).\n\n"
                f"Kola wa7d ghadi ykteb **sentence wa7da** f noubtou b modal text input!\n\n"
                f"▶️ Ghadi نبداو daba..."
            ),
            color=0x000000
        )
        inspector_view = ImpostorRoleInspectorView(self)
        self.message = await self.channel.send(embed=start_embed, view=inspector_view)
        await asyncio.sleep(4)

        # Start Round 1
        await self.start_clue_round(round_number=1)

    async def show_secret_role(self, interaction: discord.Interaction):
        user = interaction.user
        if not any(p.id == user.id for p in self.active_players):
            await interaction.response.send_message("❌ Nta mamcharkch f had lmatch.", ephemeral=True)
            return

        if user.id == self.impostor.id:
            await interaction.response.send_message(
                "🎭 **Nta hwa L'IMPOSTOR!**\n\n"
                "❌ **Ma 3ndekch lkelma siriya.**\n"
                "🎯 Chouf clues dial lkhrin, bluffi f sentence dialk bach mayfe9souch bik, o 7awel t-3ref lkelma!",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"🤫 **Lkelma siriya hia:** **{self.secret_word.upper()}**\n\n"
                f"🎯 3ti sentence mzyana 3liha bla mat3iyeq bezaf bach l'impostor maystentejhach!",
                ephemeral=True
            )

    async def start_clue_round(self, round_number: int):
        if self.game_over or self.stopped:
            return
        self.round_num = round_number
        self.turn_order = list(self.active_players)
        random.shuffle(self.turn_order)
        self.current_turn_idx = 0
        await self.prompt_next_clue_turn()

    def get_current_turn_player(self) -> Optional[discord.Member]:
        if 0 <= self.current_turn_idx < len(self.turn_order):
            return self.turn_order[self.current_turn_idx]
        return None

    def build_clues_content(self) -> str:
        all_clues = []
        for r in range(1, self.round_num + 1):
            r_clues = self.clues.get(r, [])
            if r_clues:
                all_clues.append(f"**─── Round {r} Clues ───**")
                for c in r_clues:
                    all_clues.append(f"• **{c['player']}**: *\"{c['text']}\"*")
        if not all_clues:
            return "*7ta wa7d mazal ma 3ta clue.*"
        return "\n".join(all_clues)

    async def prompt_next_clue_turn(self):
        if self.game_over or self.stopped:
            return

        # Check if all turns in this round are done
        if self.current_turn_idx >= len(self.turn_order):
            await self.start_voting_phase()
            return

        current_player = self.turn_order[self.current_turn_idx]
        if current_player not in self.active_players:
            self.current_turn_idx += 1
            await self.prompt_next_clue_turn()
            return

        embed = discord.Embed(
            title=f"🕵️ IMPOSTOR — ROUND {self.round_num} (CLUES)",
            description=(
                f"📝 **Clues dial had lgame:**\n{self.build_clues_content()}\n\n"
                f"👉 Daba nouba dial: **{current_player.mention}**!\n"
                f"Clicki 3la **[✍️ Kteb Sentence]** lte7t bach t-submitti clue dialk.\n"
                f"⏱️ 3ndek **45s** bach t-submitti!"
            ),
            color=0x000000
        )
        embed.set_footer(text=f"Turn {self.current_turn_idx + 1}/{len(self.turn_order)} • Round {self.round_num}/{self.max_rounds}")

        self.active_turn_view = ImpostorTurnView(self)
        try:
            if self.message:
                await self.message.edit(content=current_player.mention, embed=embed, view=self.active_turn_view)
            else:
                self.message = await self.channel.send(content=current_player.mention, embed=embed, view=self.active_turn_view)
        except Exception:
            self.message = await self.channel.send(content=current_player.mention, embed=embed, view=self.active_turn_view)

        # Cancel previous turn timer and set 45s timer
        if self.turn_timeout_task and not self.turn_timeout_task.done():
            self.turn_timeout_task.cancel()
        self.turn_timeout_task = asyncio.create_task(self._turn_timeout_watcher(self.current_turn_idx, current_player))

    async def _turn_timeout_watcher(self, turn_idx: int, player: discord.Member):
        await asyncio.sleep(45)
        if self.game_over or self.stopped:
            return
        if self.current_turn_idx == turn_idx:
            # Player timed out
            self.clues[self.round_num].append({
                "player": player.display_name,
                "text": "... (sala lwe9t blama ykteb)"
            })
            await self.channel.send(f"⏰ **{player.mention}** sala lwe9t dialo o ma ktbch sentence!")
            self.current_turn_idx += 1
            await self.prompt_next_clue_turn()

    async def handle_clue_submission(self, interaction: discord.Interaction, player: discord.Member, sentence: str):
        if self.current_turn_idx >= len(self.turn_order) or self.turn_order[self.current_turn_idx].id != player.id:
            await interaction.response.send_message("❌ Mashi nouba dialk!", ephemeral=True)
            return

        if self.turn_timeout_task and not self.turn_timeout_task.done():
            self.turn_timeout_task.cancel()

        self.clues[self.round_num].append({
            "player": player.display_name,
            "text": sentence
        })

        await interaction.response.send_message("✅ Clue dialk tsifat!", ephemeral=True)
        self.current_turn_idx += 1
        await self.prompt_next_clue_turn()

    async def start_voting_phase(self):
        if self.game_over or self.stopped:
            return

        self.votes = {}
        embed = discord.Embed(
            title=f"🗳️ IMPOSTOR — VOTING TIME (ROUND {self.round_num})",
            description=(
                f"Salaw clues kamlin dial had l round!\n\n"
                f"📝 **Clues Summary:**\n{self.build_clues_content()}\n\n"
                f"🤔 **Chkouun ban likom l'Impostor?**\n"
                f"Khtaro mn dropdown lte7t: Votiw 3la chkoun t-eliminéw, wla **Skip Vote** bach tzidou round jdid dial clues!\n"
                f"⏱️ 3ndkom **35s** l voting!"
            ),
            color=0x000000
        )
        embed.set_footer(text="Kola wa7d 3ndo 1 vote bo7do")

        self.active_voting_view = ImpostorVotingView(self, self.active_players)
        try:
            await self.message.edit(content=None, embed=embed, view=self.active_voting_view)
        except Exception:
            self.message = await self.channel.send(embed=embed, view=self.active_voting_view)

        # Timeout after 35s
        await asyncio.sleep(35)
        if not self.game_over and self.active_voting_view:
            self.active_voting_view.stop()
            await self.finalize_voting()

    def record_vote(self, voter_id: int, target_choice: str):
        self.votes[str(voter_id)] = target_choice

    async def finalize_voting(self):
        if self.game_over or self.stopped:
            return

        tally: Dict[str, int] = {}
        for target in self.votes.values():
            tally[target] = tally.get(target, 0) + 1

        # Check who got max votes
        if not tally:
            # Nobody voted, default to skip
            top_choice = "skip"
            top_count = 0
        else:
            sorted_tally = sorted(tally.items(), key=lambda x: x[1], reverse=True)
            top_choice, top_count = sorted_tally[0]
            # Check for tie
            if len(sorted_tally) > 1 and sorted_tally[0][1] == sorted_tally[1][1]:
                top_choice = "tie"

        vote_breakdown_lines = []
        for choice, count in tally.items():
            if choice == "skip":
                name = "⏭️ Skip Vote"
            else:
                p = next((pl for pl in self.active_players if str(pl.id) == choice), None)
                name = p.display_name if p else choice
            vote_breakdown_lines.append(f"• **{name}**: {count} votes")
        breakdown_str = "\n".join(vote_breakdown_lines) if vote_breakdown_lines else "*Ta wa7d ma vota.*"

        # Case 1: Skip or Tie
        if top_choice in ("skip", "tie"):
            if self.round_num < self.max_rounds:
                reason = "Ta3adol f l'aswat" if top_choice == "tie" else "L'aghlabiya khtaro Skip"
                skip_embed = discord.Embed(
                    title="⏭️ VOTE SKIPPED!",
                    description=(
                        f"📊 **Nata2ij dial l vote:**\n{breakdown_str}\n\n"
                        f"ℹ️ **{reason}**!\n"
                        f"Ghadi ndowzo l **Round 2** dial clues bach tzido t-chkou f ba3diyatkom ktar!"
                    ),
                    color=0x000000
                )
                try:
                    await self.message.edit(embed=skip_embed, view=None)
                except Exception:
                    await self.channel.send(embed=skip_embed)
                await asyncio.sleep(4)
                await self.start_clue_round(round_number=2)
                return
            else:
                # Max rounds reached with skip/tie -> Impostor successfully survived!
                await self.finish_game(
                    winner_side="impostor",
                    reason="Ta wa7d ma t-elimina o salaw ga3 rounds dial clues! L'Impostor hreb b nja7!"
                )
                return

        # Case 2: Someone was voted out!
        voted_id = int(top_choice)
        voted_player = next((p for p in self.active_players if p.id == voted_id), None)
        if not voted_player:
            # Fallback
            await self.finish_game(winner_side="impostor", reason="L'Impostor rbe7 bsbab khalall f l voting.")
            return

        # Did they vote the IMPOSTOR?
        if voted_player.id == self.impostor.id:
            # Caught the impostor! BUT clutch guess chance!
            clutch_embed = discord.Embed(
                title="🚨 L'IMPOSTOR T-3ZEL!",
                description=(
                    f"📊 **Nata2ij dial l vote:**\n{breakdown_str}\n\n"
                    f"🎯 **Bravo! L'Impostor kan hwa {self.impostor.mention}!**\n\n"
                    f"⚠️ **WALAKIN BA9I 3NDO CHANCE DIAL CLUTCH!** ⚠️\n"
                    f"{self.impostor.mention}, 3ndek **25s** bach t-guessi chno hia **Lkelma Siriya**!\n"
                    f"Ila jebti lkelma s7i7a, ghadi t-ser9 l-win mn l'innocents!\n\n"
                    f"Clicki **[🎯 Guess Lkelma Daba!]** lte7t!"
                ),
                color=0x000000
            )
            clutch_view = ImpostorClutchGuessView(self, self.impostor)
            try:
                await self.message.edit(content=self.impostor.mention, embed=clutch_embed, view=clutch_view)
            except Exception:
                await self.channel.send(content=self.impostor.mention, embed=clutch_embed, view=clutch_view)
            return

        else:
            # Voted an innocent!
            await self.finish_game(
                winner_side="impostor",
                reason=(
                    f"📊 **Nata2ij:**\n{breakdown_str}\n\n"
                    f"💥 Khserto! **{voted_player.mention}** kan **INNOCENT** machi Impostor!\n"
                    f"🎭 L'Impostor l7a9i9i kan hwa **{self.impostor.mention}**!"
                )
            )

    async def handle_impostor_guess_submit(self, interaction: discord.Interaction, guess_text: str):
        if self.game_over:
            return
        clean_guess = guess_text.strip().lower()
        await interaction.response.defer()

        # Check match with word or aliases
        is_correct = (
            clean_guess == self.secret_word.lower()
            or clean_guess in self.word_aliases
            or any(a in clean_guess for a in self.word_aliases if len(a) >= 4)
        )

        if is_correct:
            await self.finish_game(
                winner_side="impostor",
                reason=(
                    f"🔥 **CLUTCH NAAAAADI!**\n"
                    f"L'Impostor {self.impostor.mention} tcheffef walakin **guessa lkelma siriya b nja7**!\n"
                    f"Lkhetyar dialo: **{guess_text}** (Lkelma: **{self.secret_word.upper()}**)\n"
                    f"🎭 L'Impostor ser9 l win!"
                )
            )
        else:
            await self.finish_game(
                winner_side="innocents",
                reason=(
                    f"❌ **CLUTCH FAILED!**\n"
                    f"L'Impostor {self.impostor.mention} 7awel y-guessi walakin majabhach!\n"
                    f"Guess dialo kan: *\"{guess_text}\"* | Lkelma kant: **{self.secret_word.upper()}**\n"
                    f"🎉 L'Innocents reb7o lgame o 7maw lkelma!"
                )
            )

    async def resolve_clutch_timeout(self):
        if not self.game_over:
            await self.finish_game(
                winner_side="innocents",
                reason=(
                    f"⏰ Sala lwe9t o {self.impostor.mention} ma guessach lkelma!\n"
                    f"Lkelma kant: **{self.secret_word.upper()}**\n"
                    f"🎉 L'Innocents reb7o lgame!"
                )
            )

    async def finish_game(self, winner_side: str, reason: str):
        if self.game_over:
            return
        self.game_over = True
        self.stopped = True

        economy_cog = self.cog.bot.get_cog("Economy")
        is_dev = os.getenv("ENVIRONMENT", "").lower() == "dev"
        eco_msg = ""

        innocents = [p for p in self.active_players if p.id != self.impostor.id]

        if winner_side == "impostor":
            title = "🎭 L'IMPOSTOR RBE7!"
            winners = [self.impostor]
            losers = innocents
            if self.bet > 0 and economy_cog:
                total_pot = self.bet * len(self.original_players)
                w_payout, burned, _ = calculate_pvp_payout(self.bet * (len(self.original_players) // 2))
                if not is_dev:
                    if burned > 0:
                        await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="Impostor Win Tax")
                    await economy_cog.add_balance(self.impostor.id, total_pot - burned, context="Impostor Solo Win")
                eco_msg = f"\n\n💰 {self.impostor.mention} rbe7 pot kaml: **+{format_tad(total_pot - burned)}**!"
            elif economy_cog and not is_dev:
                net, tax = await economy_cog.apply_tax_and_add_balance(self.impostor.id, 200, context="Impostor Win")
                eco_msg = f"\n\n💰 {self.impostor.mention} rbe7: **+{net}** {TAD_EMOJI} TAD!"

            if self.channel.guild:
                await self.cog.record_minigame_win(self.channel.guild.id, self.impostor.id, "impostor")
                for innocent in innocents:
                    await self.cog.record_minigame_loss(self.channel.guild.id, innocent.id, "impostor")

        else:
            title = "🎉 L'INNOCENTS REB7O!"
            winners = innocents
            losers = [self.impostor]
            if self.bet > 0 and economy_cog:
                # Return stakes + prize
                share = int((self.bet * len(self.original_players)) / max(1, len(innocents)))
                if not is_dev:
                    for innocent in innocents:
                        await economy_cog.add_balance(innocent.id, share, context="Impostor Innocents Win")
                eco_msg = f"\n\n💰 Kola innocent rbe7: **+{format_tad(share)}**!"
            elif economy_cog and not is_dev:
                for innocent in innocents:
                    await economy_cog.apply_tax_and_add_balance(innocent.id, 80, context="Impostor Innocent Win")
                eco_msg = f"\n\n💰 Kola innocent rbe7: **+80** {TAD_EMOJI} TAD!"

            if self.channel.guild:
                for innocent in innocents:
                    await self.cog.record_minigame_win(self.channel.guild.id, innocent.id, "impostor")
                await self.cog.record_minigame_loss(self.channel.guild.id, self.impostor.id, "impostor")

        # Clean locks
        for p in self.original_players:
            clear_user_game(self.cog.bot, p.id)

        result_embed = discord.Embed(
            title=title,
            description=(
                f"{reason}\n\n"
                f"🤫 **Lkelma siriya kant:** **{self.secret_word.upper()}**\n"
                f"🎭 **L'Impostor kan:** {self.impostor.mention}"
                f"{eco_msg}"
            ),
            color=0x000000
        )
        result_embed.set_footer(text="Sifdine Minigames • GG l jami3!")

        try:
            await self.channel.send(embed=result_embed)
        except Exception:
            pass

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""

        # Remove from active players
        quitter = next((p for p in self.active_players if p.id == user.id), None)
        if quitter:
            self.active_players.remove(quitter)

        # Did the IMPOSTOR quit?
        if quitter and quitter.id == self.impostor.id:
            await self.finish_game(
                winner_side="innocents",
                reason=f"🚪 L'Impostor ({user.mention}) kherj mn lgame! L'Innocents reb7o b forfeit!"
            )
            return "🚪 Kherjti mn lgame dial **Impostor** o reb7o l'innocents b forfeit!"

        # Did an innocent quit?
        if len(self.active_players) < 2:
            # Not enough players to continue
            await self.finish_game(
                winner_side="impostor",
                reason=f"🚪 Bzaf dial nass kherjo o mab9ach minimum dial players! Lgame salat."
            )
            return "🚪 Kherjti mn lgame dial **Impostor** o salat lgame."

        # If it was the quitter's turn to submit a clue, skip to next turn
        current_p = self.get_current_turn_player()
        if current_p and current_p.id == user.id:
            if self.turn_timeout_task and not self.turn_timeout_task.done():
                self.turn_timeout_task.cancel()
            self.current_turn_idx += 1
            asyncio.create_task(self.prompt_next_clue_turn())

        # Notify channel
        try:
            await self.channel.send(
                f"🚪 **{user.mention}** kherj mn lgame dial **Impostor**! "
                f"Lgame ghadi tkml m3a **{len(self.active_players)}** li b9aw."
            )
        except Exception:
            pass

        return f"🚪 Kherjti mn lgame dial **Impostor**! B9aw **{len(self.active_players)}** la3bin."


async def run_impostor_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    bet, _ = parse_bet_argument(*args)
    bet = bet or 0

    economy_cog = cog.bot.get_cog("Economy")
    if bet > 0 and economy_cog:
        w = await economy_cog.get_wallet(ctx.author.id)
        if w["balance"] < bet:
            await ctx.send(f"❌ Flousk makafyinch l had wager ({format_tad(w['balance'])} / {format_tad(bet)})!")
            return

    set_user_in_game(cog.bot, ctx.author.id, "Impostor")

    join_emoji = "✅"
    wager_str = f"\nBet: **{format_tad(bet)}**" if bet > 0 else ""

    signup_embed = discord.Embed(
        title="🕵️ Impostor!",
        description=(
            f"Clicki 3la {join_emoji} bach tdkhel lgame.\n\n"
            f"Starts: <t:{int(time.time() + 21)}:R>\n"
            f"Min Players: **3**\n"
            f"Max Players: **8**"
            f"{wager_str}"
        ),
        color=0x000000
    )

    signup_msg = await ctx.send(embed=signup_embed)
    await signup_msg.add_reaction(join_emoji)
    await asyncio.sleep(19)

    try:
        signup_msg = await ctx.channel.fetch_message(signup_msg.id)
        reaction = discord.utils.get(signup_msg.reactions, emoji=join_emoji)
    except Exception:
        reaction = None

    players = []
    if reaction:
        async for user in reaction.users():
            if not user.bot:
                if user.id != ctx.author.id and is_user_in_game(cog.bot, user.id):
                    continue
                players.append(user)

    if ctx.author not in players:
        players.insert(0, ctx.author)

    # Check wallet for wager
    if bet > 0 and economy_cog:
        valid_players = []
        for p in players:
            w = await economy_cog.get_wallet(p.id)
            if w["balance"] >= bet:
                valid_players.append(p)
        players = valid_players

    # Maximum 8 players
    if len(players) > 8:
        players = players[:8]

    if len(players) < 3:
        clear_user_game(cog.bot, ctx.author.id)
        for p in players:
            clear_user_game(cog.bot, p.id)
        await signup_msg.edit(embed=discord.Embed(
            description="❌ Khass minimum **3 players** bach tl3bo Impostor.",
            color=0x000000
        ))
        return

    # Start game session
    game_session = ImpostorGameSession(
        host=ctx.author,
        players=players,
        channel=ctx.channel,
        cog=cog,
        bet=bet
    )
    for p in players:
        set_user_in_game(cog.bot, p.id, "Impostor", game_session)

    await signup_msg.edit(embed=discord.Embed(
        description=f"▶️ **Impostor bda!** (Players: {len(players)})\n" + ", ".join(p.mention for p in players),
        color=0x000000
    ))

    await game_session.start()


