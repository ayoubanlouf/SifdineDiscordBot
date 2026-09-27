from __future__ import annotations
import io
import math
import os
import asyncio
import random
import time
from typing import Optional, List, Dict, Any, Union, Tuple
import discord
from discord.ui import View, Button
from PIL import Image, ImageDraw, ImageFont

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import is_user_in_game, set_user_in_game, clear_user_game

ITEMS_INFO = {
    "glass": {
        "name": "Glass",
        "emoji": "🔍",
        "desc": "Peek at the current chamber secretly"
    },
    "saw": {
        "name": "Saw",
        "emoji": "🪚",
        "desc": "Double damage of next shot (2 DMG)"
    },
    "cigs": {
        "name": "Cigarettes",
        "emoji": "🚬",
        "desc": "Restore +1 Health charge (max 4)"
    },
    "beer": {
        "name": "Beer",
        "emoji": "🍺",
        "desc": "Eject current shell unspent"
    },
    "cuffs": {
        "name": "Handcuffs",
        "emoji": "⛓️",
        "desc": "Opponent skips their next turn"
    }
}

ALL_ITEM_KEYS = ["glass", "saw", "cigs", "beer", "cuffs"]

# ==============================================================================
# FONT & DRAWING HELPERS (LOW-RAM / CACHED)
# ==============================================================================
_BS_FONTS_CACHE: Dict[Tuple[int, bool], Any] = {}

def _get_bs_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _BS_FONTS_CACHE:
        return _BS_FONTS_CACHE[key]
    font_names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for name in font_names:
        for folder in ["assets/fonts", "C:/Windows/Fonts"]:
            p = os.path.join(folder, name)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    _BS_FONTS_CACHE[key] = f
                    return f
                except Exception:
                    pass
    f = ImageFont.load_default()
    _BS_FONTS_CACHE[key] = f
    return f


def _draw_smooth_heart(draw, cx, cy, radius, fill_color, outline_color=None):
    pts = []
    steps = 40
    for i in range(steps):
        t = math.pi * 2 * (i / steps)
        x = 16 * (math.sin(t) ** 3)
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * (radius / 16.0), cy + y * (radius / 16.0) - 2))
    draw.polygon(pts, fill=fill_color)
    if outline_color:
        draw.line(pts + [pts[0]], fill=outline_color, width=2)


def _draw_item_icon(draw, icon_name: str, x: int, y: int, size: int = 36):
    """Draws high-contrast, clean stylized vector icons for Buckshot items."""
    cx = x + size // 2
    cy = y + size // 2

    if icon_name == "beer":
        # Amber Beer Mug with Foaming Head and Glass Handle
        draw.rounded_rectangle([(cx - 8, cy - 6), (cx + 6, cy + 13)], radius=2, fill=(235, 160, 30), outline=(150, 95, 15), width=1)
        draw.rectangle([(cx - 6, cy - 3), (cx + 4, cy + 11)], fill=(255, 185, 45))
        draw.arc([(cx + 3, cy - 2), (cx + 14, cy + 9)], start=270, end=90, fill=(180, 215, 235), width=2)
        draw.ellipse([(cx - 10, cy - 12), (cx - 2, cy - 5)], fill=(255, 255, 255))
        draw.ellipse([(cx - 4, cy - 14), (cx + 4, cy - 5)], fill=(255, 255, 255))
        draw.ellipse([(cx + 2, cy - 12), (cx + 8, cy - 5)], fill=(255, 255, 255))

    elif icon_name in ("cig", "cigs"):
        # Crisp Cigarette Pack with red flip-top and visible protruding filter cigarettes
        draw.rectangle([(cx - 6, cy - 15), (cx - 3, cy - 5)], fill=(255, 255, 255))
        draw.rectangle([(cx - 6, cy - 15), (cx - 3, cy - 11)], fill=(225, 150, 70))
        draw.rectangle([(cx - 1, cy - 13), (cx + 2, cy - 5)], fill=(255, 255, 255))
        draw.rectangle([(cx - 1, cy - 13), (cx + 2, cy - 9)], fill=(225, 150, 70))
        draw.polygon([(cx - 10, cy - 6), (cx + 10, cy - 6), (cx + 8, cy - 12), (cx - 8, cy - 12)], fill=(215, 35, 45), outline=(130, 20, 25))
        draw.line([(cx - 9, cy - 5), (cx + 9, cy - 5)], fill=(215, 180, 60), width=1)
        draw.rounded_rectangle([(cx - 9, cy - 4), (cx + 9, cy + 13)], radius=2, fill=(245, 245, 250), outline=(120, 125, 135), width=1)
        draw.polygon([(cx - 8, cy - 2), (cx + 8, cy - 2), (cx, cy + 5)], fill=(215, 35, 45))

    elif icon_name == "glass":
        # Magnifying glass with metallic rim and reflective lens
        draw.ellipse([(cx - 11, cy - 12), (cx + 5, cy + 4)], fill=(125, 195, 245), outline=(230, 235, 245), width=2)
        draw.arc([(cx - 9, cy - 10), (cx + 3, cy + 2)], start=200, end=270, fill=(255, 255, 255), width=2)
        draw.ellipse([(cx + 3, cy + 3), (cx + 7, cy + 7)], fill=(185, 190, 200))
        draw.line([(cx + 5, cy + 5), (cx + 14, cy + 14)], fill=(150, 75, 35), width=4)
        draw.line([(cx + 12, cy + 12), (cx + 14, cy + 14)], fill=(210, 175, 60), width=4)

    elif icon_name == "saw":
        # Detailed Handsaw with steel blade, cutting teeth, and ergonomic wooden handle
        blade_pts = [(cx - 6, cy - 9), (cx + 16, cy - 2), (cx + 16, cy + 7), (cx - 6, cy + 7)]
        draw.polygon(blade_pts, fill=(210, 215, 225), outline=(110, 115, 125))
        draw.line([(cx - 4, cy - 4), (cx + 14, cy + 1)], fill=(245, 250, 255), width=1)
        for tx in range(cx - 4, cx + 15, 3):
            draw.polygon([(tx, cy + 7), (tx + 2, cy + 7), (tx + 1, cy + 10)], fill=(90, 95, 105))
        draw.rounded_rectangle([(cx - 15, cy - 9), (cx - 5, cy + 8)], radius=3, fill=(160, 60, 30), outline=(80, 25, 15), width=1)
        draw.rounded_rectangle([(cx - 12, cy - 4), (cx - 8, cy + 4)], radius=2, fill=(20, 22, 28))
        draw.ellipse([(cx - 7, cy - 6), (cx - 5, cy - 4)], fill=(230, 190, 60))
        draw.ellipse([(cx - 7, cy + 3), (cx - 5, cy + 5)], fill=(230, 190, 60))

    elif icon_name == "cuffs":
        # Handcuffs: Two polished steel shackles with ratchets and chain
        draw.ellipse([(cx - 13, cy - 7), (cx - 2, cy + 7)], fill=(24, 28, 36), outline=(215, 220, 230), width=2)
        draw.ellipse([(cx + 2, cy - 7), (cx + 13, cy + 7)], fill=(24, 28, 36), outline=(215, 220, 230), width=2)
        draw.rectangle([(cx - 3, cy - 2), (cx + 3, cy + 2)], fill=(170, 175, 185), outline=(110, 115, 125))
        draw.line([(cx - 10, cy - 7), (cx - 10, cy - 9)], fill=(235, 240, 245), width=1)
        draw.line([(cx + 10, cy - 7), (cx + 10, cy - 9)], fill=(235, 240, 245), width=1)


def _draw_shotgun(draw, x: int, y: int, sawed: bool = False):
    # Wood stock
    stock = [(x, y + 15), (x + 80, y + 11), (x + 95, y + 20), (x + 85, y + 42), (x + 6, y + 36)]
    draw.polygon(stock, fill=(90, 52, 32), outline=(45, 26, 16))
    draw.line([(x + 20, y + 16), (x + 75, y + 14)], fill=(115, 68, 42), width=1)

    # Steel Receiver
    draw.rounded_rectangle([(x + 90, y + 9), (x + 180, y + 36)], radius=4, fill=(52, 58, 66), outline=(28, 32, 38), width=2)
    draw.rectangle([(x + 120, y + 13), (x + 155, y + 22)], fill=(22, 25, 30))
    draw.line([(x + 125, y + 17), (x + 150, y + 17)], fill=(140, 145, 155), width=2)

    # Trigger guard & trigger
    draw.arc([(x + 115, y + 34), (x + 140, y + 50)], start=0, end=180, fill=(35, 40, 48), width=2)
    draw.line([(x + 128, y + 36), (x + 124, y + 44)], fill=(180, 185, 190), width=2)

    # Barrel & Magazine tube
    barrel_len = 160 if sawed else 280
    draw.rectangle([(x + 180, y + 11), (x + 180 + barrel_len, y + 21)], fill=(75, 82, 92), outline=(32, 36, 42))
    mag_len = barrel_len - 25 if not sawed else barrel_len
    draw.rectangle([(x + 180, y + 23), (x + 180 + mag_len, y + 31)], fill=(60, 66, 75), outline=(30, 34, 40))

    # Wooden pump action (forend)
    draw.rounded_rectangle([(x + 205, y + 20), (x + 275, y + 33)], radius=3, fill=(105, 62, 38), outline=(50, 28, 18))
    for gx in range(x + 215, x + 270, 7):
        draw.line([(gx, y + 21), (gx, y + 32)], fill=(55, 32, 20), width=1)

    if not sawed:
        draw.ellipse([(x + 180 + barrel_len - 6, y + 8), (x + 180 + barrel_len - 2, y + 12)], fill=(210, 180, 70))
    else:
        draw.line([(x + 180 + barrel_len, y + 8), (x + 180 + barrel_len, y + 33)], fill=(255, 80, 40), width=3)


def render_buckshot_table(
    p1_name: str,
    p1_hp: int,
    p1_items: List[str],
    p2_name: str,
    p2_hp: int,
    p2_items: List[str],
    round_num: int,
    is_p1_turn: bool,
    saw_active: bool,
    initial_live: int,
    initial_blank: int
) -> io.BytesIO:
    """Renders the dark-mode Buckshot table graphic in ~10ms using PIL."""
    W, H = 960, 460
    canvas = Image.new("RGBA", (W, H), (12, 13, 16, 255))
    draw = ImageDraw.Draw(canvas)

    # Outer table border with dark red accent line
    draw.rounded_rectangle([(8, 8), (W - 8, H - 8)], radius=14, fill=(18, 20, 25, 255), outline=(42, 46, 56, 255), width=2)
    draw.line([(30, 9), (W - 30, 9)], fill=(215, 45, 55, 255), width=2)

    f_title = _get_bs_font(18, bold=True)
    f_bold = _get_bs_font(14, bold=True)
    f_badge = _get_bs_font(11, bold=True)
    f_name = _get_bs_font(10, bold=True)

    # Header
    draw.text((36, 26), "BUCKSHOT ROULETTE", font=f_title, fill=(245, 245, 250))
    draw.rounded_rectangle([(235, 28), (320, 50)], radius=4, fill=(45, 18, 24), outline=(210, 45, 55))
    draw.text((245, 31), f"ROUND {round_num}", font=f_badge, fill=(255, 120, 130))

    # Right side: Loadout Memory Notice (Only live/blank count given on reload!)
    draw.rounded_rectangle([(W - 310, 24), (W - 36, 54)], radius=6, fill=(24, 27, 34), outline=(45, 50, 62))
    draw.text((W - 296, 32), f"LOADOUT: {initial_live} LIVE  |  {initial_blank} BLANK", font=f_badge, fill=(255, 200, 80))

    ITEM_LABELS = {
        "beer": "BEER",
        "cig": "CIGS",
        "cigs": "CIGS",
        "glass": "GLASS",
        "saw": "SAW",
        "cuffs": "CUFFS",
        "empty": "EMPTY"
    }

    # ================= TOP SECTION: DEALER / OPPONENT =================
    draw.rounded_rectangle([(36, 68), (W - 36, 162)], radius=8, fill=(22, 24, 30), outline=(36, 40, 50), width=1)

    # Identity
    display_p2 = (p2_name[:16] + "..") if len(p2_name) > 18 else p2_name
    draw.text((56, 78), display_p2.upper(), font=f_bold, fill=(225, 230, 240))
    draw.text((56, 98), "CHARGES", font=f_badge, fill=(120, 130, 145))
    for i in range(4):
        f_col = (235, 45, 55) if p2_hp > i else (40, 42, 50)
        o_col = (255, 130, 140) if p2_hp > i else (60, 65, 75)
        _draw_smooth_heart(draw, 64 + i * 28, 130, 20, fill_color=f_col, outline_color=o_col)

    # Items Tray
    draw.line([(210, 76), (210, 154)], fill=(34, 38, 48), width=1)
    draw.text((230, 78), "ITEM TRAY", font=f_badge, fill=(120, 130, 145))

    p2_slots = (p2_items + ["empty"] * 8)[:8]
    for idx, item in enumerate(p2_slots):
        ix = 230 + idx * 82
        is_empty = item == "empty"
        bg = (18, 20, 26) if is_empty else (28, 32, 42)
        border = (30, 34, 44) if is_empty else (50, 60, 78)
        draw.rounded_rectangle([(ix, 96), (ix + 72, 150)], radius=6, fill=bg, outline=border, width=1)
        if not is_empty:
            _draw_item_icon(draw, item, ix + 18, 99, size=36)
            lbl = ITEM_LABELS.get(item, item[:5].upper())
            draw.rounded_rectangle([(ix + 12, 134), (ix + 60, 147)], radius=3, fill=(18, 20, 26))
            draw.text((ix + 17, 134), lbl, font=f_name, fill=(210, 215, 225))
        else:
            draw.text((ix + 18, 120), "EMPTY", font=f_name, fill=(60, 65, 78))

    # ================= CENTER: SHOTGUN TABLE MAT =================
    mat_x1, mat_y1, mat_x2, mat_y2 = 36, 178, W - 36, 308
    draw.rounded_rectangle([(mat_x1, mat_y1), (mat_x2, mat_y2)], radius=8, fill=(17, 19, 24), outline=(36, 40, 52), width=1)
    draw.line([(mat_x1 + 30, 243), (mat_x2 - 30, 243)], fill=(24, 28, 36), width=1)

    _draw_shotgun(draw, 220, 220, sawed=saw_active)

    # Turn status pill
    if is_p1_turn:
        draw.rounded_rectangle([(mat_x1 + 24, mat_y1 + 18), (mat_x1 + 185, mat_y1 + 52)], radius=6, fill=(20, 38, 30), outline=(46, 204, 113), width=2)
        draw.text((mat_x1 + 36, mat_y1 + 27), "YOUR TURN TO ACT", font=f_badge, fill=(60, 230, 130))
    else:
        draw.rounded_rectangle([(mat_x1 + 24, mat_y1 + 18), (mat_x1 + 185, mat_y1 + 52)], radius=6, fill=(38, 24, 28), outline=(220, 50, 60), width=2)
        draw.text((mat_x1 + 36, mat_y1 + 27), "OPPONENT'S TURN", font=f_badge, fill=(255, 120, 130))

    if saw_active:
        draw.rounded_rectangle([(mat_x2 - 195, mat_y1 + 18), (mat_x2 - 24, mat_y1 + 52)], radius=6, fill=(45, 20, 22), outline=(235, 60, 50), width=2)
        draw.text((mat_x2 - 180, mat_y1 + 27), "🪚 SAWED-OFF (2X DMG)", font=f_badge, fill=(255, 110, 100))

    # ================= BOTTOM SECTION: PLAYER 1 =================
    draw.rounded_rectangle([(36, 324), (W - 36, 418)], radius=8, fill=(22, 24, 30), outline=(36, 40, 50), width=1)

    display_p1 = (p1_name[:16] + "..") if len(p1_name) > 18 else p1_name
    draw.text((56, 334), display_p1.upper(), font=f_bold, fill=(225, 230, 240))
    draw.text((56, 354), "CHARGES", font=f_badge, fill=(120, 130, 145))
    for i in range(4):
        f_col = (235, 45, 55) if p1_hp > i else (40, 42, 50)
        o_col = (255, 130, 140) if p1_hp > i else (60, 65, 75)
        _draw_smooth_heart(draw, 64 + i * 28, 386, 20, fill_color=f_col, outline_color=o_col)

    # Items Tray
    draw.line([(210, 332), (210, 410)], fill=(34, 38, 48), width=1)
    draw.text((230, 334), "YOUR INVENTORY", font=f_badge, fill=(120, 130, 145))

    p1_slots = (p1_items + ["empty"] * 8)[:8]
    for idx, item in enumerate(p1_slots):
        ix = 230 + idx * 82
        is_empty = item == "empty"
        bg = (18, 20, 26) if is_empty else (28, 32, 42)
        border = (30, 34, 44) if is_empty else (50, 60, 78)
        draw.rounded_rectangle([(ix, 352), (ix + 72, 406)], radius=6, fill=bg, outline=border, width=1)
        if not is_empty:
            _draw_item_icon(draw, item, ix + 18, 355, size=36)
            lbl = ITEM_LABELS.get(item, item[:5].upper())
            draw.rounded_rectangle([(ix + 12, 390), (ix + 60, 403)], radius=3, fill=(18, 20, 26))
            draw.text((ix + 17, 390), lbl, font=f_name, fill=(210, 215, 225))
        else:
            draw.text((ix + 18, 376), "EMPTY", font=f_name, fill=(60, 65, 78))

    draw.text((38, 432), "NOTE: Shell counts are only revealed when the shotgun is reloaded.", font=f_name, fill=(110, 115, 130))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", compress_level=1, optimize=False)
    buf.seek(0)
    canvas.close()
    return buf


# ==============================================================================
# BUCKSHOT GAME VIEW
# ==============================================================================
class BuckshotGameView(View):
    def __init__(
        self,
        player_1: Union[discord.Member, discord.User],
        player_2: Union[discord.Member, discord.User],
        is_bot_game: bool = False,
        bet: int = 0,
        cog = None
    ):
        super().__init__(timeout=180)
        self.p1 = player_1
        self.p2 = player_2
        self.is_bot_game = is_bot_game
        self.bet = bet
        self.cog = cog

        self.max_hp = 4
        self.p1_hp = 4
        self.p2_hp = 4

        self.p1_items: List[str] = []
        self.p2_items: List[str] = []

        self.shells: List[str] = []
        self.initial_live: int = 0
        self.initial_blank: int = 0
        self.round_num = 1
        self.current_turn = self.p1

        # Modifiers
        self.saw_active = False
        self.cuffed_user_id: Optional[int] = None
        self.ai_known_shell: Optional[str] = None
        self.last_action_log = "The game begins. Shotgun is loaded."

        self.game_over = False
        self.message: Optional[discord.Message] = None
        self._turn_task: Optional[asyncio.Task] = None
        self.turn_timeout_seconds = 75
        self.last_turn_timestamp = time.time()

        # Initial shell load and item distribution
        self._setup_new_loadout(first_round=True)

    def _setup_new_loadout(self, first_round: bool = False):
        """Generates random shells and deals items."""
        presets = [
            (1, 2), (2, 1), (2, 2),
            (3, 2), (2, 3), (3, 3),
            (4, 2), (3, 4), (4, 4)
        ]
        live_c, blank_c = random.choice(presets)
        self.initial_live = live_c
        self.initial_blank = blank_c
        self.shells = ["live"] * live_c + ["blank"] * blank_c
        random.shuffle(self.shells)

        # Distribute items (2 items each, max 8)
        items_to_deal = 2
        for _ in range(items_to_deal):
            if len(self.p1_items) < 8:
                self.p1_items.append(random.choice(ALL_ITEM_KEYS))
            if len(self.p2_items) < 8:
                self.p2_items.append(random.choice(ALL_ITEM_KEYS))

        self.saw_active = False
        self.ai_known_shell = None

        load_msg = (
            f"🔄 **RELOAD:** **{live_c} Live 🔴** - **{blank_c} Blank ⚪**"
        )
        if not first_round:
            self.last_action_log = load_msg
        else:
            self.last_action_log = (
                f"🎲 **ROUND 1:** **{live_c} Live 🔴** - **{blank_c} Blank ⚪**"
            )

    def render_table_image(self) -> io.BytesIO:
        p2_name = "Dealer [AI]" if self.is_bot_game else self.p2.display_name
        return render_buckshot_table(
            p1_name=self.p1.display_name,
            p1_hp=self.p1_hp,
            p1_items=self.p1_items,
            p2_name=p2_name,
            p2_hp=self.p2_hp,
            p2_items=self.p2_items,
            round_num=self.round_num,
            is_p1_turn=(self.current_turn == self.p1),
            saw_active=self.saw_active,
            initial_live=self.initial_live,
            initial_blank=self.initial_blank
        )

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎲 BUCKSHOT ROULETTE — ROUND {self.round_num}",
            color=0x000000
        )
        embed.set_image(url="attachment://buckshot_table.png")

        lines = [f"📜 **Last Action:** {self.last_action_log}"]
        if self.saw_active:
            lines.append("🪚 **Sawed-Off Barrel:** Next shot deals **2x Damage**!")
        if self.cuffed_user_id:
            cuffed_name = self.p1.display_name if self.cuffed_user_id == self.p1.id else ("Dealer" if self.is_bot_game else self.p2.display_name)
            lines.append(f"⛓️ **Handcuffed:** {cuffed_name} will skip their next turn.")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Shells revealed at reload only • Turn timer: {self.turn_timeout_seconds}s")
        return embed

    def get_turn_content(self) -> str:
        if self.game_over:
            return ""
        if self.is_bot_game and self.current_turn == self.p2:
            return "🤖 **Dealer is calculating odds and thinking...**"
        ends_time = int(self.last_turn_timestamp + self.turn_timeout_seconds)
        return f"👉 {self.current_turn.mention}, it's your turn! • Ends <t:{ends_time}:R>"

    def refresh_components(self):
        self.clear_items()
        if self.game_over:
            return

        is_human_turn = not (self.is_bot_game and self.current_turn == self.p2)
        if not is_human_turn:
            return

        # Row 0: Primary Combat Actions
        opp = self.get_opponent()
        opp_name = "Dealer" if self.is_bot_game else opp.display_name
        if len(opp_name) > 12:
            opp_name = opp_name[:11] + ".."

        btn_opp = Button(
            label=f"Shoot {opp_name}",
            style=discord.ButtonStyle.danger,
            emoji="💥",
            custom_id="shoot_opp",
            row=0
        )
        btn_opp.callback = self.shoot_opponent_callback
        self.add_item(btn_opp)

        btn_self = Button(
            label="Shoot Yourself",
            style=discord.ButtonStyle.secondary,
            emoji="🎯",
            custom_id="shoot_self",
            row=0
        )
        btn_self.callback = self.shoot_self_callback
        self.add_item(btn_self)

        btn_quit = Button(
            label="Quit",
            style=discord.ButtonStyle.secondary,
            emoji="🚪",
            custom_id="quit_match",
            row=2
        )
        btn_quit.callback = self.quit_game_callback
        self.add_item(btn_quit)

        # Row 1: Direct 1-Tap Item Buttons (One button per unique item possessed)
        current_items = self.p1_items if self.current_turn == self.p1 else self.p2_items
        counts: Dict[str, int] = {}
        for it in current_items:
            counts[it] = counts.get(it, 0) + 1

        for it, count in counts.items():
            info = ITEMS_INFO.get(it, {"emoji": "📦", "name": it.title()})
            lbl = f"{info['name']} ({count})" if count > 1 else info["name"]
            item_btn = Button(
                label=lbl,
                style=discord.ButtonStyle.primary,
                emoji=info["emoji"],
                custom_id=f"use_{it}",
                row=1
            )
            # Closure callback
            item_btn.callback = self._make_item_callback(it)
            self.add_item(item_btn)

    def _make_item_callback(self, item_key: str):
        async def _callback(interaction: discord.Interaction):
            if interaction.user != self.current_turn:
                await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
                return
            await self.handle_item_use(interaction, item_key)
        return _callback

    def get_opponent(self) -> Union[discord.Member, discord.User]:
        return self.p2 if self.current_turn == self.p1 else self.p1

    def reset_turn_timer(self):
        self.last_turn_timestamp = time.time()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._turn_task = asyncio.create_task(self._watchdog_timeout())

    async def _watchdog_timeout(self):
        try:
            await asyncio.sleep(self.turn_timeout_seconds)
            if self.game_over:
                return

            timed_out_user = self.current_turn
            if self.is_bot_game and timed_out_user == self.p2:
                return

            winner = self.get_opponent()
            self.game_over = True
            self.clear_items()
            payout_msg = await self._handle_game_payout(winner)

            buf = await asyncio.to_thread(self.render_table_image)
            file = discord.File(buf, filename="buckshot_table.png")
            end_embed = self.build_embed()
            end_embed.title = "⏰ BUCKSHOT ROULETTE — FORFEIT"
            end_embed.description += (
                f"\n\n⏰ **{timed_out_user.mention} ran out of time!**\n"
                f"🏆 **{winner.mention}** wins by forfeit!{payout_msg}"
            )
            if self.message:
                try:
                    await self.message.edit(content="", embed=end_embed, attachments=[file], view=None)
                except Exception:
                    pass
            buf.close()
            self._cleanup_game_locks()
        except asyncio.CancelledError:
            pass

    async def start(self, target: Union[discord.ext.commands.Context, discord.Interaction]):
        """Starts the game and sends the initial PIL table image cleanly."""
        self.refresh_components()
        self.reset_turn_timer()
        buf = await asyncio.to_thread(self.render_table_image)
        file = discord.File(buf, filename="buckshot_table.png")
        try:
            if isinstance(target, discord.Interaction):
                await target.response.edit_message(
                    content=self.get_turn_content(),
                    embed=self.build_embed(),
                    attachments=[file],
                    view=self
                )
                self.message = target.message
            else:
                msg = await target.send(
                    content=self.get_turn_content(),
                    embed=self.build_embed(),
                    file=file,
                    view=self
                )
                self.message = msg
        finally:
            buf.close()

    async def update_game_message(self, interaction: Optional[discord.Interaction] = None):
        if not self.message and not interaction:
            return
        self.refresh_components()
        buf = await asyncio.to_thread(self.render_table_image)
        file = discord.File(buf, filename="buckshot_table.png")
        try:
            if interaction and not interaction.response.is_done():
                await interaction.response.edit_message(
                    content=self.get_turn_content(),
                    embed=self.build_embed(),
                    attachments=[file],
                    view=self if not self.game_over else None
                )
            elif self.message:
                await self.message.edit(
                    content=self.get_turn_content(),
                    embed=self.build_embed(),
                    attachments=[file],
                    view=self if not self.game_over else None
                )
        except Exception:
            pass
        finally:
            buf.close()

    async def handle_item_use(self, interaction: discord.Interaction, item_key: str):
        current_items = self.p1_items if self.current_turn == self.p1 else self.p2_items
        if item_key not in current_items:
            await interaction.response.send_message("❌ You don't have this item!", ephemeral=True)
            return

        actor_name = self.current_turn.display_name

        if item_key == "glass":
            current_shell = self.shells[0]
            shell_desc = "**🔴 LIVE**" if current_shell == "live" else "**⚪ BLANK**"
            current_items.remove("glass")
            self.last_action_log = f"🔍 **{actor_name}** inspected the chamber with a Magnifying Glass."
            await interaction.response.send_message(
                f"🔍 **Chamber Intel:** The loaded shell is {shell_desc}!",
                ephemeral=True
            )
            await self.update_game_message()
            return

        elif item_key == "saw":
            if self.saw_active:
                await interaction.response.send_message("❌ Shotgun is already sawed-off!", ephemeral=True)
                return
            current_items.remove("saw")
            self.saw_active = True
            self.last_action_log = f"🪚 **{actor_name}** sawed off the barrel! The next shot deals **2x Damage**."
            await self.update_game_message(interaction)
            return

        elif item_key == "cigs":
            hp_attr = "p1_hp" if self.current_turn == self.p1 else "p2_hp"
            curr_hp = getattr(self, hp_attr)
            if curr_hp >= self.max_hp:
                await interaction.response.send_message("❌ Health charges already full (4/4)!", ephemeral=True)
                return
            setattr(self, hp_attr, curr_hp + 1)
            current_items.remove("cigs")
            self.last_action_log = f"🚬 **{actor_name}** smoked a Cigarette (+1 ⚡ Health)."
            await self.update_game_message(interaction)
            return

        elif item_key == "beer":
            current_items.remove("beer")
            ejected = self.shells.pop(0)
            e_str = "🔴 LIVE" if ejected == "live" else "⚪ BLANK"
            self.ai_known_shell = None
            self.last_action_log = f"🍺 **{actor_name}** racked the slide! Ejected a unspent **{e_str}** shell."

            if len(self.shells) == 0:
                self.round_num += 1
                self._setup_new_loadout(first_round=False)

            await self.update_game_message(interaction)
            return

        elif item_key == "cuffs":
            opp = self.get_opponent()
            if self.cuffed_user_id == opp.id:
                await interaction.response.send_message("❌ Opponent is already handcuffed!", ephemeral=True)
                return
            current_items.remove("cuffs")
            self.cuffed_user_id = opp.id
            opp_name = "Dealer" if self.is_bot_game else opp.display_name
            self.last_action_log = f"⛓️ **{actor_name}** locked **{opp_name}** in Handcuffs! (Skips next turn)"
            await self.update_game_message(interaction)
            return

    async def shoot_opponent_callback(self, interaction: discord.Interaction):
        if interaction.user != self.current_turn:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return
        shooter_name = self.current_turn.display_name
        opp = self.get_opponent()
        opp_name = "Dealer" if self.is_bot_game else opp.display_name
        try:
            await interaction.response.edit_message(
                content=f"⏳ **{shooter_name}** aims the shotgun at **{opp_name}**... *pulling the trigger...*",
                view=None
            )
        except Exception:
            pass
        await asyncio.sleep(1.2)
        await self._process_shot(target_self=False)

    async def shoot_self_callback(self, interaction: discord.Interaction):
        if interaction.user != self.current_turn:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return
        shooter_name = self.current_turn.display_name
        try:
            await interaction.response.edit_message(
                content=f"⏳ **{shooter_name}** turns the shotgun on **THEMSELVES**... *pulling the trigger...*",
                view=None
            )
        except Exception:
            pass
        await asyncio.sleep(1.2)
        await self._process_shot(target_self=True)

    async def quit_game_callback(self, interaction: discord.Interaction):
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("❌ You are not a player in this match!", ephemeral=True)
            return
        await interaction.response.defer()
        await self.handle_user_quit(interaction.user)

    async def handle_user_quit(self, quitter: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""

        winner = self.p2 if quitter == self.p1 else self.p1
        self.game_over = True
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

        self.clear_items()
        payout_msg = await self._handle_game_payout(winner)

        buf = await asyncio.to_thread(self.render_table_image)
        file = discord.File(buf, filename="buckshot_table.png")
        end_embed = self.build_embed()
        end_embed.title = "🚪 BUCKSHOT ROULETTE — FORFEIT"

        p2_display = "Dealer [AI]" if self.is_bot_game else self.p2.display_name
        winner_display = p2_display if winner == self.p2 else winner.display_name

        end_embed.description += (
            f"\n\n🚪 **{quitter.mention} has left the match!**\n"
            f"🏆 **{winner_display}** wins by forfeit!{payout_msg}"
        )

        if self.message:
            try:
                await self.message.edit(content="", embed=end_embed, attachments=[file], view=None)
            except Exception:
                pass
        buf.close()
        self._cleanup_game_locks()
        return "🚪 You left the **Buckshot Roulette** match and forfeited!"

    async def _process_shot(self, target_self: bool):
        if self.game_over:
            return

        shooter = self.current_turn
        opponent = self.get_opponent()
        shell = self.shells.pop(0)
        self.ai_known_shell = None

        damage = 2 if self.saw_active else 1
        was_sawed = self.saw_active
        self.saw_active = False

        shooter_name = "Dealer" if (self.is_bot_game and shooter == self.p2) else shooter.display_name
        opp_name = "Dealer" if (self.is_bot_game and opponent == self.p2) else opponent.display_name

        if target_self:
            if shell == "live":
                if shooter == self.p1:
                    self.p1_hp = max(0, self.p1_hp - damage)
                else:
                    self.p2_hp = max(0, self.p2_hp - damage)

                saw_text = " (2x Sawed-Off Damage)" if was_sawed else ""
                self.last_action_log = f"💥 **{shooter_name}** shot THEMSELVES with a **🔴 LIVE** shell! -{damage} ⚡{saw_text}"

                if self._check_game_over():
                    await self._finish_game(winner=opponent)
                    return

                if len(self.shells) == 0:
                    self.round_num += 1
                    self._setup_new_loadout(first_round=False)

                self._advance_turn(switched=True)
            else:
                self.last_action_log = f"💨 *Click!* **{shooter_name}** shot themselves with a **⚪ BLANK** shell! **(Extra Turn!)**"
                if len(self.shells) == 0:
                    self.round_num += 1
                    self._setup_new_loadout(first_round=False)
                self._advance_turn(switched=False)
        else:
            if shell == "live":
                if opponent == self.p1:
                    self.p1_hp = max(0, self.p1_hp - damage)
                else:
                    self.p2_hp = max(0, self.p2_hp - damage)

                saw_text = " (2x Sawed-Off Damage)" if was_sawed else ""
                self.last_action_log = f"💥 **{shooter_name}** shot **{opp_name}** with a **🔴 LIVE** shell! -{damage} ⚡{saw_text}"

                if self._check_game_over():
                    await self._finish_game(winner=shooter)
                    return
            else:
                self.last_action_log = f"💨 *Click!* **{shooter_name}** aimed at **{opp_name}**, but it was a **⚪ BLANK** shell!"

            if len(self.shells) == 0:
                self.round_num += 1
                self._setup_new_loadout(first_round=False)

            self._advance_turn(switched=True)

        await self.update_game_message()

        # Bot AI trigger
        if not self.game_over and self.is_bot_game and self.current_turn == self.p2:
            asyncio.create_task(self._run_bot_ai_turn())

    def _advance_turn(self, switched: bool):
        if not switched:
            self.reset_turn_timer()
            return

        opp = self.get_opponent()
        opp_name = "Dealer" if (self.is_bot_game and opp == self.p2) else opp.display_name
        if self.cuffed_user_id == opp.id:
            self.cuffed_user_id = None
            self.last_action_log += f"\n⛓️ **{opp_name}** is handcuffed and skips their turn!"
        else:
            self.current_turn = opp

        self.reset_turn_timer()

    def _check_game_over(self) -> bool:
        return self.p1_hp <= 0 or self.p2_hp <= 0

    async def _finish_game(self, winner: Union[discord.Member, discord.User]):
        self.game_over = True
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

        self.clear_items()
        payout_msg = await self._handle_game_payout(winner)

        buf = await asyncio.to_thread(self.render_table_image)
        file = discord.File(buf, filename="buckshot_table.png")
        end_embed = self.build_embed()
        end_embed.title = "🏆 BUCKSHOT ROULETTE — MATCH OVER"

        p2_display = "Dealer [AI]" if self.is_bot_game else self.p2.display_name
        winner_display = p2_display if winner == self.p2 else winner.display_name

        end_embed.description += f"\n\n💀 **{winner_display}** has survived the match!{payout_msg}"

        if self.message:
            try:
                await self.message.edit(content="", embed=end_embed, attachments=[file], view=None)
            except Exception:
                pass
        buf.close()
        self._cleanup_game_locks()

    async def _handle_game_payout(self, winner: Union[discord.Member, discord.User]) -> str:
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if not economy_cog:
            return ""

        guild_id = self.message.guild.id if self.message and self.message.guild else None

        if self.is_bot_game:
            if winner == self.p1:
                claimed, net, tax, msg = await economy_cog.claim_daily_bot_bounty(
                    self.p1.id, 5000, context="Buckshot Bot Win"
                )
                if guild_id and self.cog:
                    await self.cog.record_minigame_win(guild_id, self.p1.id, "buckshot", earnings=net if claimed else 0)
                if claimed:
                    return f"\n\n💰 **{self.p1.mention}** beat the Dealer and claimed the Daily Bot Bounty: **+{net:,}** {TAD_EMOJI} TAD! (`{tax:,}` TAD tax • 1/1 daily cap)"
                return f"\n\n🏆 **{self.p1.mention}** beat the Dealer!\n{msg}"
            else:
                if guild_id and self.cog:
                    await self.cog.record_minigame_loss(guild_id, self.p1.id, "buckshot", loss_amount=0)
                return ""
        else:
            if self.bet <= 0:
                return ""
            w_payout, burned, _ = calculate_pvp_payout(self.bet)
            loser = self.p2 if winner == self.p1 else self.p1

            if burned > 0:
                await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="Buckshot PvP Wager")
            await economy_cog.add_balance(winner.id, w_payout, context="Buckshot PvP Win")

            if guild_id and self.cog:
                await self.cog.record_minigame_win(guild_id, winner.id, "buckshot", earnings=w_payout)
                await self.cog.record_minigame_loss(guild_id, loser.id, "buckshot", loss_amount=self.bet)

            return f"\n\n💰 **{winner.mention}** won {format_tad(w_payout)}! (`{burned:,}` {TAD_EMOJI} tax)"

    def _cleanup_game_locks(self):
        if self.cog and hasattr(self.cog, "bot"):
            clear_user_game(self.cog.bot, self.p1.id)
            if not self.is_bot_game:
                clear_user_game(self.cog.bot, self.p2.id)

    # ============ MINIMAX / PROBABILISTIC DEALER AI ============

    async def _run_bot_ai_turn(self):
        """Executes the Dealer's turn with mathematical precision and pacing."""
        while self.current_turn == self.p2 and not self.game_over:
            await asyncio.sleep(1.8)

            if self.p2_hp < self.max_hp and "cigs" in self.p2_items:
                self.p2_items.remove("cigs")
                self.p2_hp += 1
                self.last_action_log = "🤖 **Dealer** used 🚬 Cigarettes (+1 ⚡ Health)."
                await self.update_game_message()
                await asyncio.sleep(1.4)
                continue

            live_rem = self.shells.count("live")
            blank_rem = self.shells.count("blank")
            total_rem = len(self.shells)

            if total_rem == 0:
                self.round_num += 1
                self._setup_new_loadout(first_round=False)
                await self.update_game_message()
                continue

            if live_rem == 0:
                known = "blank"
            elif blank_rem == 0:
                known = "live"
            else:
                known = self.ai_known_shell

            if known is None and "glass" in self.p2_items:
                self.p2_items.remove("glass")
                known = self.shells[0]
                self.ai_known_shell = known
                self.last_action_log = "🤖 **Dealer** peered into the chamber with a 🔍 Magnifying Glass."
                await self.update_game_message()
                await asyncio.sleep(1.4)
                continue

            if known == "blank":
                self.last_action_log = "🤖 **Dealer** calculated a 0% danger rate and aimed at themselves."
                await self._process_shot(target_self=True)
                break
            elif known == "live":
                if "saw" in self.p2_items and not self.saw_active and self.p1_hp > 1:
                    self.p2_items.remove("saw")
                    self.saw_active = True
                    self.last_action_log = "🤖 **Dealer** sawed off the shotgun with a 🪚 Handsaw!"
                    await self.update_game_message()
                    await asyncio.sleep(1.4)
                    continue

                if "cuffs" in self.p2_items and self.cuffed_user_id != self.p1.id and self.p1_hp > (2 if self.saw_active else 1):
                    self.p2_items.remove("cuffs")
                    self.cuffed_user_id = self.p1.id
                    self.last_action_log = f"🤖 **Dealer** snapped ⛓️ Handcuffs on {self.p1.display_name}!"
                    await self.update_game_message()
                    await asyncio.sleep(1.4)
                    continue

                await self._process_shot(target_self=False)
                break

            p_live = live_rem / total_rem
            p_blank = blank_rem / total_rem

            if "beer" in self.p2_items and p_live < 0.35 and blank_rem > 1:
                self.p2_items.remove("beer")
                ejected = self.shells.pop(0)
                e_str = "🔴 LIVE" if ejected == "live" else "⚪ BLANK"
                self.ai_known_shell = None
                self.last_action_log = f"🤖 **Dealer** chugged a 🍺 Beer! Ejected a unspent **{e_str}** shell."
                if len(self.shells) == 0:
                    self.round_num += 1
                    self._setup_new_loadout(first_round=False)
                await self.update_game_message()
                await asyncio.sleep(1.4)
                continue

            if p_blank > 0.55 and self.p2_hp > 1:
                await self._process_shot(target_self=True)
                break
            else:
                if p_live >= 0.66 and "saw" in self.p2_items and not self.saw_active and self.p1_hp > 1:
                    self.p2_items.remove("saw")
                    self.saw_active = True
                    self.last_action_log = "🤖 **Dealer** evaluated high live probability and used a 🪚 Handsaw!"
                    await self.update_game_message()
                    await asyncio.sleep(1.4)
                    continue
                await self._process_shot(target_self=False)
                break


class BuckshotChallengeView(View):
    """Handles the 1v1 PvP challenge acceptance."""
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.bet = bet
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Duel", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("❌ You were not challenged to this duel.", ephemeral=True)
            return

        if is_user_in_game(self.cog.bot, self.challenger.id):
            await interaction.response.send_message(f"❌ {self.challenger.mention} is already in a game!", ephemeral=True)
            return
        if is_user_in_game(self.cog.bot, self.challenged.id):
            await interaction.response.send_message("❌ You are already in a game!", ephemeral=True)
            return

        if self.bet > 0:
            economy_cog = self.cog.bot.get_cog("Economy")
            if economy_cog:
                w1 = await economy_cog.get_wallet(self.challenger.id)
                w2 = await economy_cog.get_wallet(self.challenged.id)
                if w1["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ {self.challenger.mention} no longer has sufficient funds!", ephemeral=True)
                    return
                if w2["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ Insufficient balance ({format_tad(w2['balance'])} / {format_tad(self.bet)})!", ephemeral=True)
                    return
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"Buckshot Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"Buckshot Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        game_view = BuckshotGameView(
            player_1=self.challenger,
            player_2=self.challenged,
            is_bot_game=False,
            bet=self.bet,
            cog=self.cog
        )
        set_user_in_game(self.cog.bot, self.challenger.id, "Buckshot Roulette", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Buckshot Roulette", game_view)
        await game_view.start(interaction)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("❌ You were not challenged to this duel.", ephemeral=True)
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=f"❌ {self.challenged.mention} declined the Buckshot Roulette duel.",
            view=self
        )

    async def on_timeout(self):
        if not self.accepted:
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ Challenge expired (not accepted in time).", view=self)
                except discord.NotFound:
                    pass
