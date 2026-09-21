import io
import math
import os
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, ImageColor


def parse_color_input(color_str: str) -> Optional[Tuple[int, int, int]]:
    """
    Parses a user color string (named color e.g. 'orange', 'cyan', 'pink' or hex code '#ff2a85').
    Returns (R, G, B) tuple or None if invalid.
    """
    if not color_str:
        return None
    cleaned = color_str.strip().lower()

    # Common custom aliases
    aliases = {
        "emerald": (16, 185, 129),
        "darkblue": (15, 23, 42),
        "hotpink": (255, 42, 133),
        "gold": (245, 158, 11),
    }
    if cleaned in aliases:
        return aliases[cleaned]

    # Pillow ImageColor parser (supports 148 standard CSS/W3C names + #rgb, #rrggbb, #rgba)
    try:
        rgb = ImageColor.getrgb(cleaned)
        return (rgb[0], rgb[1], rgb[2])
    except Exception:
        pass

    # Try prepending '#' if 6 or 3 hex characters
    if not cleaned.startswith("#"):
        try:
            rgb = ImageColor.getrgb(f"#{cleaned}")
            return (rgb[0], rgb[1], rgb[2])
        except Exception:
            pass

    return None


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


_FONTS_CACHE = {}

def _get_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _FONTS_CACHE:
        return _FONTS_CACHE[key]

    target_file = "segoeuib.ttf" if bold else "segoeui.ttf"
    local_path = os.path.join("assets", "fonts", target_file)
    if os.path.exists(local_path):
        try:
            f = ImageFont.truetype(local_path, size)
            _FONTS_CACHE[key] = f
            return f
        except Exception:
            pass

    font_names = ["segoeuib.ttf", "arialbd.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf"]
    
    # Check assets directory
    for name in font_names:
        p = os.path.join("assets", "fonts", name)
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _FONTS_CACHE[key] = f
                return f
            except Exception:
                pass

    # Check standard system font directories (Windows & Linux)
    search_dirs = [
        "C:/Windows/Fonts",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype",
        "/usr/share/fonts"
    ]
    for d in search_dirs:
        for name in font_names:
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    _FONTS_CACHE[key] = f
                    return f
                except Exception:
                    pass

    for name in font_names:
        try:
            f = ImageFont.truetype(name, size)
            _FONTS_CACHE[key] = f
            return f
        except Exception:
            pass

    f = ImageFont.load_default()
    _FONTS_CACHE[key] = f
    return f


_COIN_CACHE = {}

def _get_tails_coin(size: int) -> Optional[Image.Image]:
    if size in _COIN_CACHE:
        return _COIN_CACHE[size].copy()
    coin_path = os.path.join("assets", "coin", "Tails.png")
    if os.path.exists(coin_path):
        try:
            img = Image.open(coin_path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            _COIN_CACHE[size] = img
            return img.copy()
        except Exception:
            pass
    return None


_BASE_CARD_CACHE: Optional[Image.Image] = None

def _get_base_card() -> Image.Image:
    global _BASE_CARD_CACHE
    if _BASE_CARD_CACHE is not None:
        return _BASE_CARD_CACHE.copy()

    scale = 2
    w_base, h_base = 820, 270
    w, h = w_base * scale, h_base * scale

    card = Image.new("RGBA", (w, h), (0, 0, 0, 255))

    auras = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    adraw = ImageDraw.Draw(auras)
    adraw.ellipse([int(15 * scale), int(15 * scale), int(250 * scale), int(250 * scale)], fill=(255, 255, 255, 18))
    adraw.ellipse([int(580 * scale), int(15 * scale), int(840 * scale), int(240 * scale)], fill=(255, 255, 255, 14))
    auras = auras.filter(ImageFilter.GaussianBlur(30 * scale))
    card = Image.alpha_composite(card, auras)
    auras.close()

    glass = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glass)
    c_box = [int(16 * scale), int(16 * scale), int((w_base - 16) * scale), int((h_base - 16) * scale)]
    radius_c = 24 * scale

    gdraw.rounded_rectangle(c_box, radius=radius_c, fill=(18, 18, 20, 160))
    gdraw.rounded_rectangle(c_box, radius=radius_c, outline=(255, 255, 255, 42), width=int(1.5 * scale))

    streak = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(streak)
    sdraw.polygon([
        (int(170 * scale), int(16 * scale)),
        (int(280 * scale), int(16 * scale)),
        (int(90 * scale), int((h_base - 16) * scale)),
        (int(20 * scale), int((h_base - 16) * scale)),
    ], fill=(255, 255, 255, 8))
    glass = Image.alpha_composite(glass, streak)
    streak.close()

    card = Image.alpha_composite(card, glass)
    glass.close()

    _BASE_CARD_CACHE = card
    import gc
    gc.collect()
    return _BASE_CARD_CACHE.copy()


def render_level_card(
    username: str = "Player",
    level: int = 1,
    current_xp: int = 0,
    xp_needed: int = 75,
    total_xp: int = 0,
    rank: int = 1,
    avatar_bytes: Optional[bytes] = None,
    next_milestone_level: int = 10,
    next_milestone_reward: int = 10000,
    bg_bytes: Optional[bytes] = None,
    main_color_str: Optional[str] = None,
    accent_color_str: Optional[str] = None,
) -> io.BytesIO:
    """
    Renders a high-end, neutral monochrome or personalized custom glassmorphic level card.
    Uses 2x supersampling for razor-sharp anti-aliased curves and specular highlights.
    """
    scale = 2
    w_base, h_base = 820, 270
    w, h = w_base * scale, h_base * scale

    main_rgb = parse_color_input(main_color_str) if main_color_str else None
    accent_rgb = parse_color_input(accent_color_str) if accent_color_str else None

    # Determine background canvas
    if bg_bytes:
        try:
            raw_bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
            bg_fitted = ImageOps.fit(raw_bg, (w, h), Image.Resampling.LANCZOS)
            card = bg_fitted.filter(ImageFilter.GaussianBlur(8 * scale))
        except Exception:
            card = _get_base_card()
    elif main_rgb or accent_rgb:
        # Custom color on solid black base
        card = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        auras = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        adraw = ImageDraw.Draw(auras)
        adraw.ellipse([int(15 * scale), int(15 * scale), int(250 * scale), int(250 * scale)], fill=(255, 255, 255, 18))
        adraw.ellipse([int(580 * scale), int(15 * scale), int(840 * scale), int(240 * scale)], fill=(255, 255, 255, 14))
        auras = auras.filter(ImageFilter.GaussianBlur(30 * scale))
        card = Image.alpha_composite(card, auras)
        auras.close()
    else:
        card = _get_base_card()

    is_custom = bool(bg_bytes or main_rgb or accent_rgb)
    effective_main = main_rgb or (18, 18, 20)
    effective_accent = accent_rgb or (215, 218, 228)

    luminance = (0.299 * effective_main[0] + 0.587 * effective_main[1] + 0.114 * effective_main[2]) / 255.0
    is_light = is_custom and (luminance > 0.6)

    # Custom Glass Overlay
    if is_custom:
        glass = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glass)
        c_box = [int(16 * scale), int(16 * scale), int((w_base - 16) * scale), int((h_base - 16) * scale)]
        radius_c = 24 * scale

        glass_fill = (effective_main[0], effective_main[1], effective_main[2], 160)
        border_col = (effective_accent[0], effective_accent[1], effective_accent[2], 220)

        gdraw.rounded_rectangle(c_box, radius=radius_c, fill=glass_fill)
        gdraw.rounded_rectangle(c_box, radius=radius_c, outline=border_col, width=int(1.5 * scale))

        streak = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(streak)
        s_alpha = 25 if is_light else 10
        sdraw.polygon([
            (int(170 * scale), int(16 * scale)),
            (int(280 * scale), int(16 * scale)),
            (int(90 * scale), int((h_base - 16) * scale)),
            (int(20 * scale), int((h_base - 16) * scale)),
        ], fill=(255, 255, 255, s_alpha))
        glass = Image.alpha_composite(glass, streak)
        streak.close()
        card = Image.alpha_composite(card, glass)
        glass.close()

    draw = ImageDraw.Draw(card)

    # 4. Avatar (Circular with 3D Ring)
    av_size = 142 * scale
    av_x = 44 * scale
    av_y = int((h - av_size) / 2)
    ring_thick = 4 * scale

    av_ring_col = (effective_accent[0], effective_accent[1], effective_accent[2], 230) if is_custom else (190, 192, 200, 230)
    av_inner_bg = (effective_main[0], effective_main[1], effective_main[2], 255) if is_custom else (12, 12, 14, 255)

    draw.ellipse([av_x - ring_thick, av_y - ring_thick, av_x + av_size + ring_thick, av_y + av_size + ring_thick],
                 fill=av_inner_bg, outline=av_ring_col, width=int(3.5 * scale))
    draw.ellipse([av_x - 1, av_y - 1, av_x + av_size + 1, av_y + av_size + 1], outline=(255, 255, 255, 75), width=scale)

    av_loaded = False
    if avatar_bytes:
        try:
            raw_av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            av_img = raw_av.resize((av_size, av_size), Image.Resampling.LANCZOS)
            av_loaded = True
        except Exception:
            av_loaded = False

    if not av_loaded:
        av_img = Image.new("RGBA", (av_size, av_size), (24, 24, 28, 255))
        m_draw = ImageDraw.Draw(av_img)
        m_draw.ellipse([av_size * 0.08, av_size * 0.08, av_size * 0.92, av_size * 0.92], fill=av_inner_bg)
        f_mono = _get_font(52 * scale, bold=True)
        initials = (username[:2] if len(username) >= 2 else username).upper()
        init_col = (effective_accent[0], effective_accent[1], effective_accent[2], 255) if is_custom else (230, 230, 235, 255)
        m_draw.text((av_size // 2, av_size // 2), initials, fill=init_col, font=f_mono, anchor="mm")

    mask = Image.new("L", (av_size, av_size), 0)
    ImageDraw.Draw(mask).ellipse([(0, 0), (av_size, av_size)], fill=255)
    card.paste(av_img, (av_x, av_y), mask)

    # 5. Content Area
    content_x = int(216 * scale)

    # Fonts
    font_name = _get_font(29 * scale, bold=True)
    font_rank = _get_font(15 * scale, bold=True)
    font_lvl_badge = _get_font(18 * scale, bold=True)
    font_label = _get_font(12 * scale, bold=True)
    font_val = _get_font(15 * scale, bold=True)
    font_nums = _get_font(15 * scale, bold=True)

    text_primary = (20, 20, 25, 255) if is_light else (255, 255, 255, 255)
    text_muted = (100, 105, 115, 240) if is_light else (140, 140, 148, 255)

    # --- TOP ROW: Name + Rank Pill + Level Badge ---
    name_y = int(48 * scale)
    display_name = username if len(username) <= 15 else username[:13] + ".."
    draw.text((content_x, name_y), display_name, fill=text_primary, font=font_name)

    bbox_name = font_name.getbbox(display_name)
    name_w = bbox_name[2] - bbox_name[0]

    # Rank Pill
    rank_str = f"RANK #{rank}" if rank > 0 else "UNRANKED"
    bbox_rank = font_rank.getbbox(rank_str)
    rank_w = (bbox_rank[2] - bbox_rank[0]) + (24 * scale)
    rank_h = 28 * scale
    rank_x = content_x + name_w + int(14 * scale)
    rank_box = [rank_x, name_y + int(3 * scale), rank_x + rank_w, name_y + int(3 * scale) + rank_h]

    rank_pill_fill = (effective_main[0], effective_main[1], effective_main[2], 255) if is_custom else (28, 28, 34, 255)
    rank_pill_border = (effective_accent[0], effective_accent[1], effective_accent[2], 190) if is_custom else (210, 212, 222, 190)
    draw.rounded_rectangle(rank_box, radius=rank_h // 2, fill=rank_pill_fill, outline=rank_pill_border, width=scale)
    draw.text((rank_x + rank_w // 2, name_y + int(3 * scale) + rank_h // 2), rank_str, fill=text_primary, font=font_rank, anchor="mm")

    # Level Badge
    lvl_str = f"LEVEL {level}"
    bbox_lvl = font_lvl_badge.getbbox(lvl_str)
    lvl_w = (bbox_lvl[2] - bbox_lvl[0]) + (32 * scale)
    lvl_h = 36 * scale
    lvl_x = int((w_base - 46) * scale) - lvl_w
    lvl_y = name_y - int(1 * scale)
    lvl_box = [lvl_x, lvl_y, lvl_x + lvl_w, lvl_y + lvl_h]

    badge_fill = (effective_accent[0], effective_accent[1], effective_accent[2], 240) if is_custom else (225, 228, 235, 255)
    draw.rounded_rectangle(lvl_box, radius=12 * scale, fill=badge_fill)
    draw.rounded_rectangle([lvl_x + int(4 * scale), lvl_y + int(2 * scale), lvl_x + lvl_w - int(4 * scale), lvl_y + int(6 * scale)],
                           radius=3 * scale, fill=(255, 255, 255, 200))
    badge_lum = (0.299 * badge_fill[0] + 0.587 * badge_fill[1] + 0.114 * badge_fill[2]) / 255.0
    badge_text_col = (12, 12, 14, 255) if badge_lum > 0.5 else (255, 255, 255, 255)
    draw.text((lvl_x + lvl_w // 2, lvl_y + lvl_h // 2), lvl_str, fill=badge_text_col, font=font_lvl_badge, anchor="mm")

    # --- MIDDLE ROW: Progression Bar & XP Info ---
    bar_y = int(124 * scale)
    bar_w = int((w_base - 46) * scale) - content_x
    bar_h = 24 * scale
    bar_radius = bar_h // 2

    pct = min(1.0, max(0.0, current_xp / xp_needed)) if xp_needed > 0 else 1.0
    xp_str = f"{current_xp:,} / {xp_needed:,} XP"
    pct_str = f"{pct * 100:.1f}%"

    draw.text((content_x, bar_y - int(22 * scale)), "PROGRESSION", fill=text_muted, font=font_label)
    draw.text((content_x + bar_w, bar_y - int(22 * scale)), f"{xp_str}  •  {pct_str}", fill=text_primary, font=font_nums, anchor="ra")

    trough_fill = (effective_main[0], effective_main[1], effective_main[2], 255) if is_custom else (12, 12, 15, 255)
    trough_border = (effective_accent[0], effective_accent[1], effective_accent[2], 160) if is_custom else (42, 42, 48, 220)
    draw.rounded_rectangle([content_x, bar_y, content_x + bar_w, bar_y + bar_h], radius=bar_radius, fill=trough_fill, outline=trough_border, width=scale)

    fill_len = int(bar_w * pct)
    if fill_len >= bar_radius:
        fill_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        f_draw = ImageDraw.Draw(fill_layer)
        bar_col = (effective_accent[0], effective_accent[1], effective_accent[2], 255) if is_custom else (215, 218, 228, 255)
        f_draw.rounded_rectangle([content_x, bar_y, content_x + fill_len, bar_y + bar_h], radius=bar_radius, fill=bar_col)
        f_draw.rounded_rectangle([content_x + int(6 * scale), bar_y + int(2.5 * scale), content_x + fill_len - int(6 * scale), bar_y + int(6.5 * scale)],
                                 radius=int(2 * scale), fill=(255, 255, 255, 200))
        card = Image.alpha_composite(card, fill_layer)
        draw = ImageDraw.Draw(card)

    # --- BOTTOM ROW: Lifetime XP + Milestone Glass Cards ---
    stat_y = int(168 * scale)
    stat_h = 60 * scale

    if is_custom:
        tile_fill = (effective_main[0], effective_main[1], effective_main[2], 255)
        tile_border = (effective_accent[0], effective_accent[1], effective_accent[2], 140 if is_light else 160)
    else:
        tile_fill = (16, 16, 18, 255)
        tile_border = (255, 255, 255, 26)

    # Card 1: Lifetime Mined
    stat1_w = int(185 * scale)
    stat1_box = [content_x, stat_y, content_x + stat1_w, stat_y + stat_h]
    draw.rounded_rectangle(stat1_box, radius=12 * scale, fill=tile_fill, outline=tile_border, width=scale)
    draw.text((content_x + int(14 * scale), stat_y + int(11 * scale)), "LIFETIME XP MINED", fill=text_muted, font=font_label)
    draw.text((content_x + int(14 * scale), stat_y + int(32 * scale)), f"{total_xp:,} XP", fill=text_primary, font=font_val)

    # Card 2: Next Milestone Reward
    stat2_x = content_x + stat1_w + int(14 * scale)
    stat2_w = int((w_base - 46) * scale) - stat2_x
    stat2_box = [stat2_x, stat_y, stat2_x + stat2_w, stat_y + stat_h]
    draw.rounded_rectangle(stat2_box, radius=12 * scale, fill=tile_fill, outline=tile_border, width=scale)
    draw.text((stat2_x + int(14 * scale), stat_y + int(11 * scale)), f"NEXT MILESTONE (LVL {next_milestone_level})", fill=text_muted, font=font_label)

    coin_size = int(21 * scale)
    coin_x = stat2_x + int(14 * scale)
    coin_y = stat_y + int(31 * scale)
    coin_img = _get_tails_coin(coin_size)
    if coin_img:
        card.paste(coin_img, (coin_x, coin_y), coin_img)

    text_x = coin_x + coin_size + int(8 * scale)
    draw.text((text_x, stat_y + int(32 * scale)), f"+{next_milestone_reward:,} TAD Cash Payout", fill=text_primary, font=font_val)

    # Downsample 2x to 1x via Lanczos
    final_card = card.resize((w_base, h_base), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    final_card.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    try:
        card.close()
        final_card.close()
        del card, final_card
        if "fill_layer" in locals():
            fill_layer.close()
        if "av_img" in locals():
            av_img.close()
        if "mask" in locals():
            mask.close()
        import gc
        gc.collect()
    except Exception:
        pass

    return buf


def render_wallet_card(
    username: str = "Player",
    balance: int = 0,
    is_active: bool = True,
    is_private: bool = False,
    daily_streak: int = 0,
    daily_claimed: bool = False,
    daily_resets_in_str: str = "Resets at midnight",
    weekly_claimed: bool = False,
    weekly_resets_in_str: str = "Resets Monday",
    avatar_bytes: Optional[bytes] = None,
    bg_bytes: Optional[bytes] = None,
    main_color_str: Optional[str] = None,
    accent_color_str: Optional[str] = None,
) -> io.BytesIO:
    """
    Renders a high-end glassmorphic/claymorphic wallet card in Pillow.
    Supports custom background wallpapers, custom main and accent colors, and privacy indicators.
    """
    scale = 2
    w_base, h_base = 820, 320
    w, h = w_base * scale, h_base * scale

    main_rgb = parse_color_input(main_color_str) if main_color_str else None
    accent_rgb = parse_color_input(accent_color_str) if accent_color_str else None

    # Base background
    if bg_bytes:
        try:
            raw_bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
            bg_fitted = ImageOps.fit(raw_bg, (w, h), Image.Resampling.LANCZOS)
            card = bg_fitted.filter(ImageFilter.GaussianBlur(8 * scale))
        except Exception:
            card = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    else:
        # Default True Black Canvas with subtle ambient aura
        card = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        auras = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        adraw = ImageDraw.Draw(auras)
        adraw.ellipse([int(20 * scale), int(20 * scale), int(260 * scale), int(260 * scale)], fill=(255, 255, 255, 12))
        adraw.ellipse([int(560 * scale), int(20 * scale), int(800 * scale), int(260 * scale)], fill=(255, 255, 255, 8))
        auras = auras.filter(ImageFilter.GaussianBlur(35 * scale))
        card = Image.alpha_composite(card, auras)
        auras.close()

    effective_main = main_rgb or (18, 18, 22)
    effective_accent = accent_rgb or (255, 255, 255)

    luminance = (0.299 * effective_main[0] + 0.587 * effective_main[1] + 0.114 * effective_main[2]) / 255.0
    is_light = luminance > 0.6

    # Glass container panel
    glass = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glass)
    c_box = [int(16 * scale), int(16 * scale), int((w_base - 16) * scale), int((h_base - 16) * scale)]
    radius_c = 24 * scale

    glass_fill = (effective_main[0], effective_main[1], effective_main[2], 160 if (bg_bytes or main_rgb) else 150)
    border_col = (effective_accent[0], effective_accent[1], effective_accent[2], 200)

    gdraw.rounded_rectangle(c_box, radius=radius_c, fill=glass_fill)
    gdraw.rounded_rectangle(c_box, radius=radius_c, outline=border_col, width=int(1.5 * scale))

    # Frosted diagonal glass reflection
    streak = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(streak)
    s_alpha = 25 if is_light else 10
    sdraw.polygon([
        (int(190 * scale), int(16 * scale)),
        (int(320 * scale), int(16 * scale)),
        (int(100 * scale), int((h_base - 16) * scale)),
        (int(20 * scale), int((h_base - 16) * scale)),
    ], fill=(255, 255, 255, s_alpha))
    glass = Image.alpha_composite(glass, streak)
    streak.close()
    card = Image.alpha_composite(card, glass)
    glass.close()

    draw = ImageDraw.Draw(card)

    text_primary = (20, 20, 25, 255) if is_light else (255, 255, 255, 255)
    text_muted = (100, 105, 115, 240) if is_light else (160, 160, 170, 240)
    divider_color = (effective_accent[0], effective_accent[1], effective_accent[2], 80) if accent_rgb else ((0, 0, 0, 35) if is_light else (255, 255, 255, 30))

    # Avatar
    av_size = 110 * scale
    av_x = 44 * scale
    av_y = int(42 * scale)
    ring_thick = 3 * scale

    av_ring_col = border_col
    av_inner_bg = (effective_main[0], effective_main[1], effective_main[2], 255)
    draw.ellipse([av_x - ring_thick, av_y - ring_thick, av_x + av_size + ring_thick, av_y + av_size + ring_thick],
                 fill=av_inner_bg, outline=av_ring_col, width=int(2.5 * scale))
    draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], fill=av_inner_bg)

    av_loaded = False
    if avatar_bytes:
        try:
            raw_av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            av_img = raw_av.resize((av_size, av_size), Image.Resampling.LANCZOS)
            av_loaded = True
        except Exception:
            av_loaded = False

    if not av_loaded:
        av_img = Image.new("RGBA", (av_size, av_size), av_inner_bg)
        m_draw = ImageDraw.Draw(av_img)
        f_mono = _get_font(44 * scale, bold=True)
        initials = (username[:2] if len(username) >= 2 else username).upper()
        init_col = (effective_accent[0], effective_accent[1], effective_accent[2], 255) if accent_rgb else text_primary
        m_draw.text((av_size // 2, av_size // 2), initials, fill=init_col, font=f_mono, anchor="mm")

    mask = Image.new("L", (av_size, av_size), 0)
    ImageDraw.Draw(mask).ellipse([(0, 0), (av_size, av_size)], fill=255)
    card.paste(av_img, (av_x, av_y), mask)

    # User info
    content_x = av_x + av_size + int(24 * scale)
    f_name = _get_font(28 * scale, bold=True)
    f_label = _get_font(12 * scale, bold=True)
    f_bal = _get_font(34 * scale, bold=True)
    f_val = _get_font(15 * scale, bold=True)
    f_pill = _get_font(13 * scale, bold=True)

    display_name = username if len(username) <= 16 else username[:14] + ".."
    draw.text((content_x, av_y + int(10 * scale)), display_name, fill=text_primary, font=f_name)

    # Right pills
    right_x = int((w_base - 44) * scale)
    pill_w = int(106 * scale)
    pill_h = int(32 * scale)
    pill_box = [right_x - pill_w, av_y + int(10 * scale), right_x, av_y + int(10 * scale) + pill_h]

    status_fill = (16, 42, 24, 190) if is_active else (48, 18, 18, 190)
    status_border = (52, 211, 153, 200) if is_active else (239, 68, 68, 200)
    status_txt = "● ACTIVE" if is_active else "● SUSPENDED"
    draw.rounded_rectangle(pill_box, radius=16 * scale, fill=status_fill, outline=status_border, width=scale)
    draw.text((pill_box[0] + pill_w // 2, pill_box[1] + pill_h // 2), status_txt, fill=status_border, font=f_pill, anchor="mm")

    if is_private:
        priv_w = int(114 * scale)
        priv_box = [pill_box[0] - priv_w - int(10 * scale), pill_box[1], pill_box[0] - int(10 * scale), pill_box[3]]
        draw.rounded_rectangle(priv_box, radius=16 * scale, fill=(45, 30, 10, 200), outline=(245, 158, 11, 200), width=scale)
        draw.text((priv_box[0] + priv_w // 2, priv_box[1] + pill_h // 2), "PRIVATE", fill=(245, 158, 11, 255), font=f_pill, anchor="mm")

    draw.text((content_x, av_y + int(48 * scale)), "TOTAL BALANCE", fill=text_muted, font=f_label)

    # Balance with Tails coin
    coin_size = int(32 * scale)
    coin_img = _get_tails_coin(coin_size)
    coin_x = content_x
    coin_y = av_y + int(68 * scale)
    if coin_img:
        card.paste(coin_img, (coin_x, coin_y), coin_img)

    balance_str = f"{balance:,} TAD"
    draw.text((coin_x + coin_size + int(10 * scale), coin_y - int(2 * scale)), balance_str, fill=text_primary, font=f_bal)

    # Divider
    div_y = int(185 * scale)
    draw.line([(int(44 * scale), div_y), (int((w_base - 44) * scale), div_y)], fill=divider_color, width=scale)

    # Bottom Tiles
    box_w = int((w_base - 88 - 16) * scale // 2)
    box_h = int(90 * scale)
    box_y = div_y + int(18 * scale)

    # Main color with no transparency (100% solid opacity)
    tile_fill = (effective_main[0], effective_main[1], effective_main[2], 255)
    tile_border = (effective_accent[0], effective_accent[1], effective_accent[2], 140) if accent_rgb else ((255, 255, 255, 32) if not is_light else (0, 0, 0, 32))

    def _draw_tile_status_icon(center_x: int, center_y: int, is_ready: bool):
        badge_r = int(18 * scale)
        if is_ready:
            b_fill = (22, 101, 52, 190) if not is_light else (220, 252, 231, 230)
            b_outline = (34, 197, 94, 240) if not is_light else (22, 163, 74, 240)
            icon_col = (74, 222, 128, 255) if not is_light else (21, 128, 61, 255)
            draw.ellipse([center_x - badge_r, center_y - badge_r, center_x + badge_r, center_y + badge_r], fill=b_fill, outline=b_outline, width=max(1, int(1.5 * scale)))
            p1 = (center_x - int(7 * scale), center_y - int(1 * scale))
            p2 = (center_x - int(2 * scale), center_y + int(5 * scale))
            p3 = (center_x + int(8 * scale), center_y - int(5 * scale))
            draw.line([p1, p2, p3], fill=icon_col, width=max(2, int(2.5 * scale)), joint="curve")
        else:
            b_fill = (36, 36, 42, 180) if not is_light else (241, 245, 249, 220)
            b_outline = (100, 105, 115, 160) if not is_light else (148, 163, 184, 180)
            icon_col = (156, 163, 175, 255) if not is_light else (100, 116, 139, 255)
            draw.ellipse([center_x - badge_r, center_y - badge_r, center_x + badge_r, center_y + badge_r], fill=b_fill, outline=b_outline, width=max(1, int(1.5 * scale)))
            clock_r = int(8 * scale)
            draw.ellipse([center_x - clock_r, center_y - clock_r, center_x + clock_r, center_y + clock_r], outline=icon_col, width=max(1, int(1.5 * scale)))
            draw.line([(center_x, center_y), (center_x, center_y - int(4.5 * scale))], fill=icon_col, width=max(1, int(1.5 * scale)))
            draw.line([(center_x, center_y), (center_x + int(3.5 * scale), center_y)], fill=icon_col, width=max(1, int(1.5 * scale)))

    # Tile 1: Daily Reward
    b1_x = int(44 * scale)
    draw.rounded_rectangle([b1_x, box_y, b1_x + box_w, box_y + box_h], radius=14 * scale, fill=tile_fill, outline=tile_border, width=scale)
    draw.text((b1_x + int(16 * scale), box_y + int(14 * scale)), "DAILY REWARD", fill=text_muted, font=f_label)
    draw.text((b1_x + int(16 * scale), box_y + int(36 * scale)), f"Streak: {daily_streak}/7 Days", fill=text_primary, font=f_val)
    status_daily = "Ready to Claim" if not daily_claimed else (daily_resets_in_str if daily_resets_in_str.startswith("Resets") else f"Resets in {daily_resets_in_str}")
    daily_status_col = (22, 163, 74) if is_light else (52, 211, 153) if not daily_claimed else text_muted
    draw.text((b1_x + int(16 * scale), box_y + int(62 * scale)), status_daily, fill=daily_status_col, font=_get_font(13 * scale, bold=True if is_light else False))
    _draw_tile_status_icon(b1_x + box_w - int(34 * scale), box_y + box_h // 2, not daily_claimed)

    # Tile 2: Weekly Reward
    b2_x = b1_x + box_w + int(16 * scale)
    draw.rounded_rectangle([b2_x, box_y, b2_x + box_w, box_y + box_h], radius=14 * scale, fill=tile_fill, outline=tile_border, width=scale)
    draw.text((b2_x + int(16 * scale), box_y + int(14 * scale)), "WEEKLY REWARD", fill=text_muted, font=f_label)
    draw.text((b2_x + int(16 * scale), box_y + int(36 * scale)), "Reward: 5,000 TAD", fill=text_primary, font=f_val)
    status_weekly = "Ready to Claim" if not weekly_claimed else (weekly_resets_in_str if weekly_resets_in_str.startswith("Resets") else f"Resets in {weekly_resets_in_str}")
    weekly_status_col = (22, 163, 74) if is_light else (52, 211, 153) if not weekly_claimed else text_muted
    draw.text((b2_x + int(16 * scale), box_y + int(62 * scale)), status_weekly, fill=weekly_status_col, font=_get_font(13 * scale, bold=True if is_light else False))
    _draw_tile_status_icon(b2_x + box_w - int(34 * scale), box_y + box_h // 2, not weekly_claimed)

    final_card = card.resize((w_base, h_base), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    final_card.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    try:
        card.close()
        final_card.close()
        del card, final_card
        if "av_img" in locals():
            av_img.close()
        if "mask" in locals():
            mask.close()
        import gc
        gc.collect()
    except Exception:
        pass

    return buf


async def setup(bot):
    pass

