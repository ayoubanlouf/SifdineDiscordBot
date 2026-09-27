from __future__ import annotations
import io
import os
import math
import asyncio
import random
import time
from typing import Optional, List, Dict, Any, Union, Tuple
import discord
from discord.ui import View, Button, Select, Modal, TextInput
from PIL import Image, ImageDraw, ImageFont

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout, parse_bet_argument
from cogs.games.helpers import (
    is_user_in_game, set_user_in_game, clear_user_game,
    attach_game_session, MultiplayerGameSession
)

# ==============================================================================
# SECRET WORDS DATASET
# ==============================================================================
CURATED_WORDS = [
    {"word": "Pizza", "category": "Food", "aliases": ["pizza", "piza", "بيتزا"]},
    {"word": "Tajine", "category": "Food", "aliases": ["tajine", "tagine", "طاجين"]},
    {"word": "Couscous", "category": "Food", "aliases": ["couscous", "kouskous", "كسكس"]},
    {"word": "Burger", "category": "Food", "aliases": ["burger", "hamburger", "برجر"]},
    {"word": "Atay", "category": "Drink", "aliases": ["atay", "tea", "chay", "اتاي", "شاي"]},
    {"word": "Qahwa", "category": "Drink", "aliases": ["qahwa", "coffee", "قهوة", "cafe"]},
    {"word": "Tomobil", "category": "Vehicle", "aliases": ["tomobil", "car", "voiture", "سيارة", "طوموبيل"]},
    {"word": "Tayara", "category": "Vehicle", "aliases": ["tayara", "plane", "avion", "طائرة"]},
    {"word": "Train", "category": "Vehicle", "aliases": ["train", "qitar", "قطار"]},
    {"word": "Motor", "category": "Vehicle", "aliases": ["motor", "moto", "موطور"]},
    {"word": "Kora", "category": "Sport", "aliases": ["kora", "football", "soccer", "كرة"]},
    {"word": "Tennis", "category": "Sport", "aliases": ["tennis", "تنس"]},
    {"word": "Basket", "category": "Sport", "aliases": ["basket", "basketball", "كرة السلة"]},
    {"word": "PC", "category": "Tech", "aliases": ["pc", "computer", "ordinateur", "حاسوب"]},
    {"word": "Telephone", "category": "Tech", "aliases": ["telephone", "phone", "portable", "هاتف"]},
    {"word": "Clavier", "category": "Tech", "aliases": ["clavier", "keyboard", "كلافيي"]},
    {"word": "Casque", "category": "Tech", "aliases": ["casque", "headphones", "سماعات"]},
    {"word": "Cinema", "category": "Entertainment", "aliases": ["cinema", "film", "movie", "سينما"]},
    {"word": "B7ar", "category": "Nature", "aliases": ["b7ar", "sea", "beach", "plage", "بحر"]},
    {"word": "Jbel", "category": "Nature", "aliases": ["jbel", "mountain", "جبل"]},
    {"word": "Chjar", "category": "Nature", "aliases": ["chjar", "tree", "arbre", "شجرة"]},
    {"word": "Chmch", "category": "Nature", "aliases": ["chmch", "sun", "soleil", "شمس"]},
    {"word": "Gmr", "category": "Nature", "aliases": ["gmr", "moon", "lune", "قمر"]},
    {"word": "Mchich", "category": "Animal", "aliases": ["mchich", "cat", "chat", "قط"]},
    {"word": "Kelb", "category": "Animal", "aliases": ["kelb", "dog", "chien", "كلب"]},
    {"word": "Sb3", "category": "Animal", "aliases": ["sb3", "lion", "اسد", "سبع"]},
    {"word": "3awd", "category": "Animal", "aliases": ["3awd", "horse", "cheval", "عود", "حصان"]},
    {"word": "Ktab", "category": "Object", "aliases": ["ktab", "book", "livre", "كتاب"]},
    {"word": "Stylo", "category": "Object", "aliases": ["stylo", "pen", "قلم"]},
    {"word": "Magana", "category": "Object", "aliases": ["magana", "watch", "clock", "montre", "ساعة"]},
    {"word": "Nddar", "category": "Object", "aliases": ["nddar", "glasses", "lunettes", "نظارات"]},
    {"word": "Moussiqa", "category": "Art", "aliases": ["moussiqa", "music", "موسيقى"]},
    {"word": "Zwa9", "category": "Art", "aliases": ["zwa9", "art", "drawing", "رسم"]},
    {"word": "Spider-Man", "category": "Character", "aliases": ["spiderman", "spider-man", "سبايدرمان"]},
    {"word": "Batman", "category": "Character", "aliases": ["batman", "باتمان"]},
    {"word": "Messi", "category": "Celebrity", "aliases": ["messi", "ميسي"]},
    {"word": "Ronaldo", "category": "Celebrity", "aliases": ["ronaldo", "cr7", "رونالدو"]},
]

# ==============================================================================
# FONT & DRAWING HELPERS (LOW-RAM / CACHED)
# ==============================================================================
_IMP_FONTS_CACHE: Dict[Tuple[int, bool], Any] = {}

def _get_imp_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _IMP_FONTS_CACHE:
        return _IMP_FONTS_CACHE[key]
    font_names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for name in font_names:
        for folder in ["assets/fonts", "C:/Windows/Fonts"]:
            p = os.path.join(folder, name)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    _IMP_FONTS_CACHE[key] = f
                    return f
                except Exception:
                    pass
    f = ImageFont.load_default()
    _IMP_FONTS_CACHE[key] = f
    return f


def render_impostor_turn_card(
    player_name: str = "@Player",
    round_num: int = 1,
    avatar_bytes: Optional[bytes] = None
) -> io.BytesIO:
    """Renders the Black & Red turn banner card in ~8ms."""
    W, H = 840, 220
    canvas = Image.new("RGBA", (W, H), (10, 10, 14, 255))
    draw = ImageDraw.Draw(canvas)

    # Black & Red Outline & Accent Bar
    draw.rounded_rectangle([(6, 6), (W - 6, H - 6)], radius=12, fill=(16, 17, 22, 255), outline=(48, 24, 30, 255), width=2)
    draw.line([(24, 7), (W - 24, 7)], fill=(225, 35, 55, 255), width=3)

    f_huge = _get_imp_font(26, bold=True)
    f_sub = _get_imp_font(13, bold=False)
    f_bold = _get_imp_font(15, bold=True)
    f_badge = _get_imp_font(12, bold=True)

    # Circular Avatar Frame (Crimson glowing ring)
    av_cx, av_cy, av_r = 95, 115, 52
    draw.ellipse([(av_cx - av_r - 4, av_cy - av_r - 4), (av_cx + av_r + 4, av_cy + av_r + 4)], fill=(32, 20, 26), outline=(225, 45, 60), width=2)
    draw.ellipse([(av_cx - av_r, av_cy - av_r), (av_cx + av_r, av_cy + av_r)], fill=(20, 22, 28))

    avatar_drawn = False
    if avatar_bytes:
        try:
            with Image.open(io.BytesIO(avatar_bytes)) as av_raw:
                av_raw = av_raw.convert("RGBA").resize((av_r * 2, av_r * 2), Image.Resampling.BILINEAR)
                mask = Image.new("L", (av_r * 2, av_r * 2), 0)
                mdraw = ImageDraw.Draw(mask)
                mdraw.ellipse([(0, 0), (av_r * 2, av_r * 2)], fill=255)
                canvas.paste(av_raw, (av_cx - av_r, av_cy - av_r), mask)
                avatar_drawn = True
        except Exception:
            avatar_drawn = False

    if not avatar_drawn:
        # Gamer Silhouette Fallback
        draw.ellipse([(av_cx - 15, av_cy - 26), (av_cx + 15, av_cy + 4)], fill=(220, 55, 70))
        draw.chord([(av_cx - 30, av_cy + 6), (av_cx + 30, av_cy + 60)], start=0, end=180, fill=(220, 55, 70))
        draw.arc([(av_cx - 22, av_cy - 32), (av_cx + 22, av_cy + 2)], start=180, end=0, fill=(245, 245, 250), width=3)
        draw.rounded_rectangle([(av_cx - 25, av_cy - 20), (av_cx - 18, av_cy - 2)], radius=2, fill=(245, 245, 250))
        draw.rounded_rectangle([(av_cx + 18, av_cy - 20), (av_cx + 25, av_cy - 2)], radius=2, fill=(245, 245, 250))

    # Phase Badge
    draw.rounded_rectangle([(180, 34), (370, 60)], radius=4, fill=(45, 18, 24), outline=(220, 45, 60), width=1)
    draw.text((192, 39), f"ROUND {round_num} • CLUE PHASE", font=f_badge, fill=(255, 140, 155))

    # Main Headline
    disp_name = (player_name[:16] + "..") if len(player_name) > 18 else player_name
    draw.text((180, 74), f"IT'S YOUR TURN: {disp_name}", font=f_huge, fill=(255, 255, 255))
    draw.text((180, 116), "• Write 1 short sentence to describe the secret word.", font=f_sub, fill=(200, 205, 215))
    draw.text((180, 138), "• If you are the Impostor, bluff carefully without giving it away!", font=f_sub, fill=(140, 145, 160))

    # Timer Badge
    draw.rounded_rectangle([(W - 130, 34), (W - 36, 70)], radius=6, fill=(35, 18, 24), outline=(225, 45, 60), width=1)
    draw.text((W - 115, 43), "TIME: 45s", font=f_bold, fill=(255, 110, 125))

    # Footer
    draw.line([(180, 178), (W - 36, 178)], fill=(34, 38, 48), width=1)
    draw.text((180, 190), "Click [✍️ Kteb Sentence] below to submit your clue", font=f_badge, fill=(120, 130, 145))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", compress_level=1, optimize=False)
    buf.seek(0)
    canvas.close()
    return buf


_STATIC_VOTE_CARD_BYTES: Optional[bytes] = None

def get_static_impostor_vote_card_buf() -> io.BytesIO:
    """Returns the static voting phase card buffer in 0.01ms (cached in RAM)."""
    global _STATIC_VOTE_CARD_BYTES
    if _STATIC_VOTE_CARD_BYTES is None:
        W, H = 840, 200
        canvas = Image.new("RGBA", (W, H), (10, 10, 14, 255))
        draw = ImageDraw.Draw(canvas)

        # Black & Red Outline
        draw.rounded_rectangle([(6, 6), (W - 6, H - 6)], radius=12, fill=(16, 17, 22, 255), outline=(50, 24, 30, 255), width=2)
        draw.line([(24, 7), (W - 24, 7)], fill=(225, 35, 55, 255), width=3)

        f_title = _get_imp_font(22, bold=True)
        f_sub = _get_imp_font(14, bold=False)
        f_bold = _get_imp_font(15, bold=True)
        f_badge = _get_imp_font(12, bold=True)

        # Header
        draw.text((36, 30), "VOTING PHASE • ACCUSATION TIME", font=f_title, fill=(255, 255, 255))
        draw.text((36, 64), "Review the clues and select who you believe is the Impostor.", font=f_sub, fill=(190, 195, 210))
        draw.text((36, 90), "Or choose 'Skip Vote' to proceed to another round of clues.", font=f_sub, fill=(140, 145, 160))

        # Timer Pill
        draw.rounded_rectangle([(W - 130, 28), (W - 36, 66)], radius=6, fill=(40, 18, 24), outline=(225, 45, 60), width=1)
        draw.text((W - 115, 38), "TIME: 30s", font=f_bold, fill=(255, 110, 125))

        # Notice Pill (Static - explains that live votes are tracked in text embed below!)
        draw.rounded_rectangle([(36, 130), (W - 36, 172)], radius=6, fill=(26, 18, 24), outline=(75, 30, 40), width=1)
        draw.text((52, 142), "LIVE VOTES: Tracked in real-time in the message below  •  Anonymous until reveal", font=f_badge, fill=(255, 170, 180))

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", compress_level=1, optimize=False)
        _STATIC_VOTE_CARD_BYTES = buf.getvalue()
        buf.close()
        canvas.close()

    return io.BytesIO(_STATIC_VOTE_CARD_BYTES)


# ==============================================================================
# UI COMPONENTS (MODALS & VIEWS)
# ==============================================================================

class ImpostorClueModal(Modal, title="✍️ Enter Your Clue"):
    sentence_input = TextInput(
        label="Clue Sentence",
        style=discord.TextStyle.paragraph,
        placeholder="Write a sentence describing the secret word without giving it away...",
        required=True,
        min_length=3,
        max_length=150
    )

    def __init__(self, game_session: ImpostorGameSession, player: discord.Member):
        super().__init__()
        self.game_session = game_session
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        sentence = self.sentence_input.value.strip()
        await self.game_session.handle_clue_submission(interaction, self.player, sentence)


class ImpostorGuessModal(Modal, title="🎯 Guess the Secret Word (Clutch)"):
    guess_input = TextInput(
        label="What was the secret word?",
        style=discord.TextStyle.short,
        placeholder="Enter your guess (e.g. Pizza, Coffee, Spider-Man...)",
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
    def __init__(self, game_session: ImpostorGameSession):
        super().__init__(timeout=None)
        self.game_session = game_session

    @discord.ui.button(label="🤫 View Secret Role", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="impostor_inspect_role")
    async def inspect_role_button(self, interaction: discord.Interaction, button: Button):
        await self.game_session.show_secret_role(interaction)


class ImpostorTurnView(View):
    def __init__(self, game_session: ImpostorGameSession):
        super().__init__(timeout=60)
        self.game_session = game_session

    @discord.ui.button(label="✍️ Submit Clue", style=discord.ButtonStyle.primary, emoji="📝", custom_id="impostor_submit_clue_btn")
    async def submit_clue_button(self, interaction: discord.Interaction, button: Button):
        current_player = self.game_session.get_current_turn_player()
        if not current_player or interaction.user.id != current_player.id:
            await interaction.response.send_message("❌ It is not your turn yet! Please wait.", ephemeral=True)
            return
        await interaction.response.send_modal(ImpostorClueModal(self.game_session, current_player))

    @discord.ui.button(label="🤫 My Secret Role", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="impostor_turn_role_btn")
    async def inspect_role(self, interaction: discord.Interaction, button: Button):
        await self.game_session.show_secret_role(interaction)

    @discord.ui.button(label="🚪 Leave Match", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="impostor_turn_quit_btn")
    async def quit_button(self, interaction: discord.Interaction, button: Button):
        if not any(p.id == interaction.user.id for p in self.game_session.active_players):
            await interaction.response.send_message("❌ You are not participating in this game.", ephemeral=True)
            return
        msg = await self.game_session.handle_user_quit(interaction.user)
        clear_user_game(self.game_session.cog.bot, interaction.user.id)
        if msg:
            await interaction.response.send_message(msg, ephemeral=True)


# ==============================================================================
# STEP 1: READINESS VIEW (PROCEED VS SKIP VOTE)
# ==============================================================================
class ImpostorReadinessView(View):
    def __init__(self, game_session: ImpostorGameSession):
        super().__init__(timeout=30)
        self.game_session = game_session

    @discord.ui.button(label="Proceed to Vote", style=discord.ButtonStyle.success, emoji="🗳️", custom_id="imp_readiness_vote")
    async def proceed_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if not any(p.id == user.id for p in self.game_session.active_players):
            await interaction.response.send_message("❌ You are not participating in this game.", ephemeral=True)
            return
        await self.game_session.record_readiness_vote(interaction, user.id, "proceed")

    @discord.ui.button(label="Skip Vote", style=discord.ButtonStyle.danger, emoji="⏭️", custom_id="imp_readiness_skip")
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if not any(p.id == user.id for p in self.game_session.active_players):
            await interaction.response.send_message("❌ You are not participating in this game.", ephemeral=True)
            return
        await self.game_session.record_readiness_vote(interaction, user.id, "skip")

    @discord.ui.button(label="Secret Role", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="imp_readiness_role")
    async def role_button(self, interaction: discord.Interaction, button: Button):
        await self.game_session.show_secret_role(interaction)


# ==============================================================================
# STEP 2: ACCUSATION VOTING VIEW
# ==============================================================================
class ImpostorVotingView(View):
    def __init__(self, game_session: ImpostorGameSession, eligible_players: List[discord.Member]):
        super().__init__(timeout=35)
        self.game_session = game_session
        self.eligible_players = eligible_players
        self.voted_users: set[int] = set()

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
            description="Proceed to next clue round without eliminating anyone",
            value="skip",
            emoji="⏭️"
        ))

        self.select_menu = Select(
            placeholder="🗳️ Select who you think is the Impostor or Skip...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="impostor_vote_select"
        )
        self.select_menu.callback = self.vote_callback
        self.add_item(self.select_menu)

        role_btn = Button(label="🤫 My Secret Role", style=discord.ButtonStyle.secondary, emoji="🔍")
        role_btn.callback = self.inspect_role_callback
        self.add_item(role_btn)

    async def inspect_role_callback(self, interaction: discord.Interaction):
        await self.game_session.show_secret_role(interaction)

    async def vote_callback(self, interaction: discord.Interaction):
        user = interaction.user
        if not any(p.id == user.id for p in self.game_session.active_players):
            await interaction.response.send_message("❌ You are not participating in this game.", ephemeral=True)
            return
        if user.id in self.voted_users:
            await interaction.response.send_message("⚠️ You already voted! You cannot vote again.", ephemeral=True)
            return

        choice = self.select_menu.values[0]
        self.voted_users.add(user.id)
        self.game_session.record_vote(user.id, choice)

        voted_name = "Skip Vote" if choice == "skip" else next((p.display_name for p in self.eligible_players if str(p.id) == choice), "Unknown")
        await interaction.response.send_message(f"✅ You voted for: **{voted_name}**.", ephemeral=True)

        # Update live ballot tracker in embed text
        await self.game_session.refresh_live_votes_display()

        if len(self.voted_users) >= len(self.game_session.active_players):
            self.stop()
            await self.game_session.finalize_voting()


class ImpostorClutchGuessView(View):
    def __init__(self, game_session: ImpostorGameSession, impostor: discord.Member):
        super().__init__(timeout=25)
        self.game_session = game_session
        self.impostor = impostor

    @discord.ui.button(label="🎯 Guess Secret Word Now!", style=discord.ButtonStyle.danger, emoji="💥", custom_id="impostor_clutch_btn")
    async def clutch_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.impostor.id:
            await interaction.response.send_message("❌ Only the Impostor can attempt the clutch guess!", ephemeral=True)
            return
        await interaction.response.send_modal(ImpostorGuessModal(self.game_session))

    async def on_timeout(self):
        if not self.game_session.game_over:
            await self.game_session.resolve_clutch_timeout()


# ==============================================================================
# MAIN GAME SESSION
# ==============================================================================
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
        self.readiness_votes: Dict[int, str] = {}
        self.votes: Dict[str, str] = {}
        self.game_over = False
        self.stopped = False
        self.message: Optional[discord.Message] = None
        self.active_turn_view: Optional[ImpostorTurnView] = None
        self.active_readiness_view: Optional[ImpostorReadinessView] = None
        self.active_voting_view: Optional[ImpostorVotingView] = None
        self.turn_timeout_task: Optional[asyncio.Task] = None
        self.phase_timeout_task: Optional[asyncio.Task] = None

    async def start(self):
        if self.bet > 0:
            economy_cog = self.cog.bot.get_cog("Economy")
            if economy_cog:
                for p in self.active_players:
                    try:
                        await economy_cog.deduct_balance(p.id, self.bet, context=f"Impostor Wager Stake ({self.bet} TAD)")
                    except Exception:
                        pass

        for p in self.active_players:
            attach_game_session(self.cog.bot, p.id, self)
            try:
                if p.id == self.impostor.id:
                    dm_text = (
                        "🎭 **You are the IMPOSTOR in this match!**\n\n"
                        "❌ **You do not know the secret word!**\n"
                        "🎯 **Your Objective:** Read other players' clues, blend in with your own clue, "
                        "and try to deduce what the secret word is!\n"
                        "💡 If you get discovered, you still have a final clutch chance to guess the secret word and steal the victory!"
                    )
                else:
                    dm_text = (
                        f"🤫 **The Secret Word is:** **{self.secret_word.upper()}**\n\n"
                        f"🎯 **Your Objective:** Give a subtle clue about the word without giving it away to the Impostor!\n"
                        f"Watch for clues that don't match the word to vote out the Impostor."
                    )
                await p.send(dm_text)
            except discord.Forbidden:
                pass

        start_embed = discord.Embed(
            title="🕵️ IMPOSTOR — GAME STARTED!",
            description=(
                f"One player among you is the **Impostor** who has no clue what the word is!\n\n"
                f"🤫 Secret roles have been sent via **Private DMs** (or click the button below).\n\n"
                f"Each player will submit **one clue sentence** on their turn.\n\n"
                f"▶️ Starting now..."
            ),
            color=0x000000
        )
        inspector_view = ImpostorRoleInspectorView(self)
        self.message = await self.channel.send(embed=start_embed, view=inspector_view)
        await asyncio.sleep(3.5)

        await self.start_clue_round(round_number=1)

    async def show_secret_role(self, interaction: discord.Interaction):
        user = interaction.user
        if not any(p.id == user.id for p in self.active_players):
            await interaction.response.send_message("❌ You are not participating in this match.", ephemeral=True)
            return

        if user.id == self.impostor.id:
            await interaction.response.send_message(
                "🎭 **You are the IMPOSTOR!**\n\n"
                "❌ **You do not have the secret word.**\n"
                "🎯 Read everyone's clues, bluff convincingly, and deduce the secret word!",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"🤫 **The secret word is:** **{self.secret_word.upper()}**\n\n"
                f"🎯 Give a clever clue without making it too obvious for the Impostor!",
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
            return "*No clues submitted yet.*"
        return "\n".join(all_clues)

    async def prompt_next_clue_turn(self):
        if self.game_over or self.stopped:
            return

        if self.current_turn_idx >= len(self.turn_order):
            await self.start_readiness_phase()
            return

        current_player = self.turn_order[self.current_turn_idx]
        if current_player not in self.active_players:
            self.current_turn_idx += 1
            await self.prompt_next_clue_turn()
            return

        # Fetch avatar for turn banner card
        av_bytes = None
        try:
            av_bytes = await current_player.display_avatar.read()
        except Exception:
            pass

        buf = await asyncio.to_thread(
            render_impostor_turn_card,
            current_player.display_name,
            self.round_num,
            av_bytes
        )
        file = discord.File(buf, filename="impostor_turn.png")

        embed = discord.Embed(
            title=f"🕵️ IMPOSTOR — ROUND {self.round_num} (CLUES)",
            description=(
                f"📝 **Clues Board:**\n{self.build_clues_content()}\n\n"
                f"👉 Current turn: **{current_player.mention}**!\n"
                f"Click **[✍️ Submit Clue]** below to submit your sentence.\n"
                f"⏱️ You have **45s** to submit!"
            ),
            color=0x000000
        )
        embed.set_image(url="attachment://impostor_turn.png")
        embed.set_footer(text=f"Turn {self.current_turn_idx + 1}/{len(self.turn_order)} • Round {self.round_num}/{self.max_rounds}")

        self.active_turn_view = ImpostorTurnView(self)
        try:
            if self.message:
                await self.message.edit(content=current_player.mention, embed=embed, attachments=[file], view=self.active_turn_view)
            else:
                self.message = await self.channel.send(content=current_player.mention, embed=embed, file=file, view=self.active_turn_view)
        except Exception:
            self.message = await self.channel.send(content=current_player.mention, embed=embed, file=file, view=self.active_turn_view)
        finally:
            buf.close()

        if self.turn_timeout_task and not self.turn_timeout_task.done():
            self.turn_timeout_task.cancel()
        self.turn_timeout_task = asyncio.create_task(self._turn_timeout_watcher(self.current_turn_idx, current_player))

    async def _turn_timeout_watcher(self, turn_idx: int, player: discord.Member):
        await asyncio.sleep(45)
        if self.game_over or self.stopped:
            return
        if self.current_turn_idx == turn_idx:
            self.clues[self.round_num].append({
                "player": player.display_name,
                "text": "... (timed out without submitting)"
            })
            await self.channel.send(f"⏰ **{player.mention}** ran out of time and didn't submit a clue!")
            self.current_turn_idx += 1
            await self.prompt_next_clue_turn()

    async def handle_clue_submission(self, interaction: discord.Interaction, player: discord.Member, sentence: str):
        if self.current_turn_idx >= len(self.turn_order) or self.turn_order[self.current_turn_idx].id != player.id:
            await interaction.response.send_message("❌ It is not your turn!", ephemeral=True)
            return

        if self.turn_timeout_task and not self.turn_timeout_task.done():
            self.turn_timeout_task.cancel()

        self.clues[self.round_num].append({
            "player": player.display_name,
            "text": sentence
        })

        await interaction.response.send_message("✅ Your clue has been submitted!", ephemeral=True)
        self.current_turn_idx += 1
        await self.prompt_next_clue_turn()

    # ==========================================================================
    # STEP 1: READINESS PHASE (PROCEED VS SKIP VOTE)
    # ==========================================================================
    def _build_readiness_embed(self) -> discord.Embed:
        desc_lines = [
            f"📢 **All clues for Round {self.round_num} have been submitted!**\n",
            f"📝 **Clues Board:**\n{self.build_clues_content()}\n",
            "Vote whether to proceed to accusation or skip to another round of clues:\n",
            f"**Votes ({len(self.readiness_votes)}/{len(self.active_players)}):**"
        ]
        for p in self.active_players:
            choice = self.readiness_votes.get(p.id)
            if choice == "proceed":
                badge = "🗳️ Proceed to Vote"
            elif choice == "skip":
                badge = "⏭️ Skip Vote"
            else:
                badge = "⏳ Deciding..."
            desc_lines.append(f"• **{p.display_name}**: {badge}")

        desc_lines.append("\n⏱️ You have **25s** to decide!")

        embed = discord.Embed(
            title=f"🕵️ IMPOSTOR — DISCUSSION & READINESS (ROUND {self.round_num})",
            description="\n".join(desc_lines),
            color=0x000000
        )
        embed.set_footer(text="Majority decides: Proceed to Vote vs Skip Vote")
        return embed

    async def start_readiness_phase(self):
        if self.game_over or self.stopped:
            return

        self.readiness_votes = {}
        embed = self._build_readiness_embed()
        self.active_readiness_view = ImpostorReadinessView(self)

        try:
            await self.message.edit(content=None, embed=embed, attachments=[], view=self.active_readiness_view)
        except Exception:
            self.message = await self.channel.send(embed=embed, view=self.active_readiness_view)

        if self.phase_timeout_task and not self.phase_timeout_task.done():
            self.phase_timeout_task.cancel()
        self.phase_timeout_task = asyncio.create_task(self._readiness_timeout_watcher())

    async def record_readiness_vote(self, interaction: discord.Interaction, user_id: int, choice: str):
        self.readiness_votes[user_id] = choice
        choice_str = "Proceed to Vote" if choice == "proceed" else "Skip Vote"
        await interaction.response.send_message(f"✅ You voted: **{choice_str}**.", ephemeral=True)

        # Update text-only embed in real-time (instant ~30ms, 0 RAM)
        embed = self._build_readiness_embed()
        try:
            await self.message.edit(embed=embed)
        except Exception:
            pass

        if len(self.readiness_votes) >= len(self.active_players):
            if self.phase_timeout_task and not self.phase_timeout_task.done():
                self.phase_timeout_task.cancel()
            await self._resolve_readiness_outcome()

    async def _readiness_timeout_watcher(self):
        await asyncio.sleep(25)
        if not self.game_over and not self.stopped:
            await self._resolve_readiness_outcome()

    async def _resolve_readiness_outcome(self):
        if self.game_over or self.stopped:
            return

        if self.active_readiness_view:
            self.active_readiness_view.stop()

        skip_count = sum(1 for v in self.readiness_votes.values() if v == "skip")
        proceed_count = sum(1 for v in self.readiness_votes.values() if v == "proceed")

        # Skip ties or majority skip
        if skip_count >= proceed_count and skip_count > 0:
            if self.round_num < self.max_rounds:
                skip_embed = discord.Embed(
                    title="⏭️ VOTE SKIPPED!",
                    description=(
                        f"📊 **Discussion Results:**\n"
                        f"• ⏭️ Skip Vote: **{skip_count}**\n"
                        f"• 🗳️ Proceed to Vote: **{proceed_count}**\n\n"
                        f"ℹ️ The majority voted to **Skip Vote**!\n"
                        f"Moving to **Round 2** for more clues before voting."
                    ),
                    color=0x000000
                )
                try:
                    await self.message.edit(embed=skip_embed, view=None)
                except Exception:
                    await self.channel.send(embed=skip_embed)
                await asyncio.sleep(3.5)
                await self.start_clue_round(round_number=2)
                return
            else:
                # Max rounds reached with skip -> Impostor successfully survived!
                await self.finish_game(
                    winner_side="impostor",
                    reason="No accusation was made and all clue rounds finished! The Impostor escaped undetected!"
                )
                return

        # Majority voted to proceed with accusation
        await self.start_accusation_voting_phase()

    # ==========================================================================
    # STEP 2: ACCUSATION VOTING PHASE (STATIC PIL CARD + LIVE TEXT TRACKING)
    # ==========================================================================
    def _build_accusation_embed(self) -> discord.Embed:
        desc_lines = [
            f"📢 **Voting Phase Started for Round {self.round_num}!**\n",
            f"📝 **Clues Board:**\n{self.build_clues_content()}\n",
            "🤔 **Who is the Impostor?** Select your suspect from the dropdown below!\n",
            f"**Votes Cast ({len(self.votes)}/{len(self.active_players)}):**"
        ]
        for p in self.active_players:
            if str(p.id) in self.votes:
                badge = "✅ Voted"
            else:
                badge = "⏳ Deciding..."
            desc_lines.append(f"• **{p.display_name}**: {badge}")

        desc_lines.append("\n⏱️ You have **30s** to vote! (Ballots are anonymous until reveal)")

        embed = discord.Embed(
            title=f"🗳️ IMPOSTOR — ACCUSATION TIME (ROUND {self.round_num})",
            description="\n".join(desc_lines),
            color=0x000000
        )
        embed.set_image(url="attachment://impostor_vote.png")
        embed.set_footer(text="1 vote per player • Anonymous ballots until reveal")
        return embed
        embed.set_image(url="attachment://impostor_vote.png")
        embed.set_footer(text="1 vote per player • Anonymous ballots until reveal")
        return embed

    async def start_accusation_voting_phase(self):
        if self.game_over or self.stopped:
            return

        self.votes = {}
        embed = self._build_accusation_embed()
        buf = get_static_impostor_vote_card_buf()
        file = discord.File(buf, filename="impostor_vote.png")

        self.active_voting_view = ImpostorVotingView(self, self.active_players)
        try:
            await self.message.edit(content=None, embed=embed, attachments=[file], view=self.active_voting_view)
        except Exception:
            self.message = await self.channel.send(embed=embed, file=file, view=self.active_voting_view)
        finally:
            buf.close()

        if self.phase_timeout_task and not self.phase_timeout_task.done():
            self.phase_timeout_task.cancel()
        self.phase_timeout_task = asyncio.create_task(self._accusation_timeout_watcher())

    async def refresh_live_votes_display(self):
        """Updates text embed description in real-time when a vote is cast (~30ms, 0 RAM)."""
        embed = self._build_accusation_embed()
        try:
            await self.message.edit(embed=embed)
        except Exception:
            pass

    async def _accusation_timeout_watcher(self):
        await asyncio.sleep(30)
        if not self.game_over and not self.stopped:
            if self.active_voting_view:
                self.active_voting_view.stop()
            await self.finalize_voting()

    def record_vote(self, voter_id: int, target_choice: str):
        self.votes[str(voter_id)] = target_choice

    async def finalize_voting(self):
        if self.game_over or self.stopped:
            return

        if self.phase_timeout_task and not self.phase_timeout_task.done():
            self.phase_timeout_task.cancel()

        tally: Dict[str, int] = {}
        for target in self.votes.values():
            tally[target] = tally.get(target, 0) + 1

        if not tally:
            top_choice = "skip"
            top_count = 0
        else:
            sorted_tally = sorted(tally.items(), key=lambda x: x[1], reverse=True)
            top_choice, top_count = sorted_tally[0]
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
        breakdown_str = "\n".join(vote_breakdown_lines) if vote_breakdown_lines else "*No votes cast.*"

        # Case 1: Skip or Tie
        if top_choice in ("skip", "tie"):
            if self.round_num < self.max_rounds:
                reason = "Vote ended in a tie" if top_choice == "tie" else "Majority voted to skip"
                skip_embed = discord.Embed(
                    title="⏭️ VOTE SKIPPED!",
                    description=(
                        f"📊 **Voting Results:**\n{breakdown_str}\n\n"
                        f"ℹ️ **{reason}**!\n"
                        f"Advancing to **Round 2** for another round of clues!"
                    ),
                    color=0x000000
                )
                try:
                    await self.message.edit(embed=skip_embed, attachments=[], view=None)
                except Exception:
                    await self.channel.send(embed=skip_embed)
                await asyncio.sleep(4)
                await self.start_clue_round(round_number=2)
                return
            else:
                await self.finish_game(
                    winner_side="impostor",
                    reason="No one was eliminated and all clue rounds finished! The Impostor escaped undetected!"
                )
                return

        # Case 2: Someone was voted out!
        voted_id = int(top_choice)
        voted_player = next((p for p in self.active_players if p.id == voted_id), None)
        if not voted_player:
            await self.finish_game(winner_side="impostor", reason="The Impostor won due to an invalid ballot.")
            return

        if voted_player.id == self.impostor.id:
            # Caught Impostor -> Clutch guess opportunity
            clutch_embed = discord.Embed(
                title="🚨 IMPOSTOR UNMASKED!",
                description=(
                    f"📊 **Voting Results:**\n{breakdown_str}\n\n"
                    f"🎯 **Well done! The Impostor was {self.impostor.mention}!**\n\n"
                    f"⚠️ **BUT THEY HAVE ONE LAST CHANCE TO CLUTCH!** ⚠️\n"
                    f"{self.impostor.mention}, you have **25s** to guess the **Secret Word**!\n"
                    f"If you guess correctly, you steal the win from the Innocents!\n\n"
                    f"Click **[🎯 Guess Secret Word Now!]** below!"
                ),
                color=0x000000
            )
            clutch_view = ImpostorClutchGuessView(self, self.impostor)
            try:
                await self.message.edit(content=self.impostor.mention, embed=clutch_embed, attachments=[], view=clutch_view)
            except Exception:
                await self.channel.send(content=self.impostor.mention, embed=clutch_embed, view=clutch_view)
            return
        else:
            # Innocent was eliminated -> Impostor wins!
            await self.finish_game(
                winner_side="impostor",
                reason=(
                    f"📊 **Voting Results:**\n{breakdown_str}\n\n"
                    f"💥 Defeat! **{voted_player.mention}** was **INNOCENT**!\n"
                    f"🎭 The real Impostor was **{self.impostor.mention}**!"
                )
            )

    async def handle_impostor_guess_submit(self, interaction: discord.Interaction, guess_text: str):
        if self.game_over:
            return
        clean_guess = guess_text.strip().lower()

        is_correct = (
            clean_guess == self.secret_word.lower()
            or clean_guess in self.word_aliases
            or any(a in clean_guess for a in self.word_aliases if len(a) >= 4)
        )

        if is_correct:
            await self.finish_game(
                winner_side="impostor",
                reason=(
                    f"🔥 **SPECTACULAR CLUTCH!**\n"
                    f"The Impostor {self.impostor.mention} was unmasked but **guessed the secret word correctly**!\n"
                    f"Their Guess: **{guess_text}** (Word: **{self.secret_word.upper()}**)\n"
                    f"🎭 The Impostor steals the victory!"
                )
            )
        else:
            await self.finish_game(
                winner_side="innocents",
                reason=(
                    f"❌ **CLUTCH FAILED!**\n"
                    f"The Impostor {self.impostor.mention} attempted to guess the word and failed!\n"
                    f"Their Guess: *\"{guess_text}\"* | Secret Word was: **{self.secret_word.upper()}**\n"
                    f"🎉 The Innocents win and protected the secret!"
                )
            )

    async def resolve_clutch_timeout(self):
        if not self.game_over:
            await self.finish_game(
                winner_side="innocents",
                reason=(
                    f"⏰ Time ran out and {self.impostor.mention} could not guess the word!\n"
                    f"The secret word was: **{self.secret_word.upper()}**\n"
                    f"🎉 The Innocents win the match!"
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
            title = "🎭 THE IMPOSTOR WINS!"
            winners = [self.impostor]
            losers = innocents
            if self.bet > 0 and economy_cog:
                total_pot = self.bet * len(self.original_players)
                w_payout, burned, _ = calculate_pvp_payout(self.bet * (len(self.original_players) // 2))
                if not is_dev:
                    if burned > 0:
                        await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="Impostor Win Tax")
                    await economy_cog.add_balance(self.impostor.id, total_pot - burned, context="Impostor Solo Win")
                eco_msg = f"\n\n💰 {self.impostor.mention} won the pot: **+{format_tad(total_pot - burned)}**!"
            elif economy_cog and not is_dev:
                net, tax = await economy_cog.apply_tax_and_add_balance(self.impostor.id, 200, context="Impostor Win")
                eco_msg = f"\n\n💰 {self.impostor.mention} won: **+{net}** {TAD_EMOJI} TAD!"

            if self.channel.guild:
                await self.cog.record_minigame_win(self.channel.guild.id, self.impostor.id, "impostor")
                for innocent in innocents:
                    await self.cog.record_minigame_loss(self.channel.guild.id, innocent.id, "impostor")

        else:
            title = "🎉 THE INNOCENTS WIN!"
            winners = innocents
            losers = [self.impostor]
            if self.bet > 0 and economy_cog:
                share = int((self.bet * len(self.original_players)) / max(1, len(innocents)))
                if not is_dev:
                    for innocent in innocents:
                        await economy_cog.add_balance(innocent.id, share, context="Impostor Innocents Win")
                eco_msg = f"\n\n💰 Each innocent won: **+{format_tad(share)}**!"
            elif economy_cog and not is_dev:
                for innocent in innocents:
                    net, tax = await economy_cog.apply_tax_and_add_balance(innocent.id, 150, context="Impostor Win")
                eco_msg = f"\n\n💰 Each innocent won: **+150** {TAD_EMOJI} TAD!"

            if self.channel.guild:
                for innocent in innocents:
                    await self.cog.record_minigame_win(self.channel.guild.id, innocent.id, "impostor")
                await self.cog.record_minigame_loss(self.channel.guild.id, self.impostor.id, "impostor")

        final_embed = discord.Embed(
            title=title,
            description=(
                f"🤫 **Secret Word was:** **{self.secret_word.upper()}**\n"
                f"🎭 **The Impostor was:** {self.impostor.mention}\n\n"
                f"{reason}"
                f"{eco_msg}"
            ),
            color=0x000000
        )

        for p in self.original_players:
            clear_user_game(self.cog.bot, p.id)

        if self.message:
            try:
                await self.message.edit(content=None, embed=final_embed, attachments=[], view=None)
            except Exception:
                await self.channel.send(embed=final_embed)

    async def handle_user_quit(self, user: discord.Member) -> str:
        if self.game_over:
            return ""

        if user in self.active_players:
            self.active_players.remove(user)

        if user.id == self.impostor.id:
            await self.finish_game(
                winner_side="innocents",
                reason=f"🚪 The Impostor ({user.mention}) left the match! Innocents win by forfeit."
            )
            return "🚪 You left the **Impostor** match."

        if len(self.active_players) < 2:
            await self.finish_game(
                winner_side="impostor",
                reason="🚪 Too many players left and the game could not continue! Game ended."
            )
            return "🚪 You left the **Impostor** match and the match ended."

        current_p = self.get_current_turn_player()
        if current_p and current_p.id == user.id:
            if self.turn_timeout_task and not self.turn_timeout_task.done():
                self.turn_timeout_task.cancel()
            self.current_turn_idx += 1
            asyncio.create_task(self.prompt_next_clue_turn())

        try:
            await self.channel.send(
                f"🚪 **{user.mention}** left the **Impostor** match! "
                f"The game will continue with the remaining **{len(self.active_players)}** players."
            )
        except Exception:
            pass

        return f"🚪 You left the **Impostor** match! **{len(self.active_players)}** players remaining."


# ==============================================================================
# ENTRY POINT
# ==============================================================================
async def run_impostor_game(cog, ctx, *args):
    if not await cog.ensure_user_free(ctx):
        return

    bet, _ = parse_bet_argument(*args)
    bet = bet or 0

    economy_cog = cog.bot.get_cog("Economy")
    if bet > 0 and economy_cog:
        w = await economy_cog.get_wallet(ctx.author.id)
        if w["balance"] < bet:
            await ctx.send(f"❌ Insufficient balance for this wager ({format_tad(w['balance'])} / {format_tad(bet)})!")
            return

    set_user_in_game(cog.bot, ctx.author.id, "Impostor")

    join_emoji = "✅"
    wager_str = f"\nBet: **{format_tad(bet)}**" if bet > 0 else ""

    signup_embed = discord.Embed(
        title="🕵️ Impostor!",
        description=(
            f"Click {join_emoji} to join the match.\n\n"
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

    if bet > 0 and economy_cog:
        valid_players = []
        for p in players:
            w = await economy_cog.get_wallet(p.id)
            if w["balance"] >= bet:
                valid_players.append(p)
        players = valid_players

    if len(players) > 8:
        players = players[:8]

    if len(players) < 3:
        clear_user_game(cog.bot, ctx.author.id)
        for p in players:
            clear_user_game(cog.bot, p.id)
        await signup_msg.edit(embed=discord.Embed(
            description="❌ A minimum of **3 players** is required to play Impostor.",
            color=0x000000
        ))
        return

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
        description=f"▶️ **Impostor game started!** (Players: {len(players)})\n" + ", ".join(p.mention for p in players),
        color=0x000000
    ))

    await game_session.start()
