import io
import math
import os
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

_FONTS_CACHE = {}

def _get_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _FONTS_CACHE:
        return _FONTS_CACHE[key]

    if bold:
        font_names = ["segoeuib.ttf", "arialbd.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf"]
    else:
        font_names = ["segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf"]
    
    for name in font_names:
        try:
            f = ImageFont.truetype(name, size)
            _FONTS_CACHE[key] = f
            return f
        except Exception:
            p = os.path.join("C:/Windows/Fonts", name)
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
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
) -> io.BytesIO:
    """
    Renders a high-end, neutral monochrome glassmorphic & claymorphic level card.
    Uses 2x supersampling for razor-sharp anti-aliased curves and specular highlights.
    """
    scale = 2
    w_base, h_base = 820, 270
    w, h = w_base * scale, h_base * scale

    # Re-use cached pristine glass card canvas (zero memory churn)
    card = _get_base_card()
    draw = ImageDraw.Draw(card)

    # 4. Avatar (Circular with Monochrome Silver/Chrome Clay Ring)
    av_size = 142 * scale
    av_x = 44 * scale
    av_y = int((h - av_size) / 2)

    # Outer 3D Silver Clay Ring
    ring_thick = 4 * scale
    draw.ellipse([av_x - ring_thick, av_y - ring_thick, av_x + av_size + ring_thick, av_y + av_size + ring_thick],
                 fill=(12, 12, 14, 255), outline=(190, 192, 200, 230), width=int(3.5 * scale))
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
        # High quality monogram avatar fallback
        av_img = Image.new("RGBA", (av_size, av_size), (24, 24, 28, 255))
        m_draw = ImageDraw.Draw(av_img)
        m_draw.ellipse([av_size * 0.08, av_size * 0.08, av_size * 0.92, av_size * 0.92], fill=(36, 36, 42, 255))
        f_mono = _get_font(52 * scale, bold=True)
        initials = (username[:2] if len(username) >= 2 else username).upper()
        m_draw.text((av_size // 2, av_size // 2), initials, fill=(230, 230, 235, 255), font=f_mono, anchor="mm")

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

    # --- TOP ROW: Name + Rank Pill + Level Badge ---
    name_y = int(48 * scale)
    # Truncate overly long names cleanly
    display_name = username if len(username) <= 15 else username[:13] + ".."
    draw.text((content_x, name_y), display_name, fill=(255, 255, 255, 255), font=font_name)

    bbox_name = font_name.getbbox(display_name)
    name_w = bbox_name[2] - bbox_name[0]

    # Rank Pill (Frosted Glass with Charcoal/Silver Contrast)
    rank_str = f"RANK #{rank}" if rank > 0 else "UNRANKED"
    bbox_rank = font_rank.getbbox(rank_str)
    rank_w = (bbox_rank[2] - bbox_rank[0]) + (24 * scale)
    rank_h = 28 * scale
    rank_x = content_x + name_w + int(14 * scale)
    rank_box = [rank_x, name_y + int(3 * scale), rank_x + rank_w, name_y + int(3 * scale) + rank_h]

    draw.rounded_rectangle(rank_box, radius=rank_h // 2, fill=(28, 28, 34, 230), outline=(210, 212, 222, 190), width=scale)
    draw.text((rank_x + rank_w // 2, name_y + int(3 * scale) + rank_h // 2), rank_str, fill=(245, 246, 250, 255), font=font_rank, anchor="mm")

    # Level Badge (Claymorphic 3D Titanium Pill)
    lvl_str = f"LEVEL {level}"
    bbox_lvl = font_lvl_badge.getbbox(lvl_str)
    lvl_w = (bbox_lvl[2] - bbox_lvl[0]) + (32 * scale)
    lvl_h = 36 * scale
    lvl_x = int((w_base - 46) * scale) - lvl_w
    lvl_y = name_y - int(1 * scale)
    lvl_box = [lvl_x, lvl_y, lvl_x + lvl_w, lvl_y + lvl_h]

    # Base clay titanium pill
    draw.rounded_rectangle(lvl_box, radius=12 * scale, fill=(225, 228, 235, 255))
    # Specular shine curve on top
    draw.rounded_rectangle([lvl_x + int(4 * scale), lvl_y + int(2 * scale), lvl_x + lvl_w - int(4 * scale), lvl_y + int(6 * scale)],
                           radius=3 * scale, fill=(255, 255, 255, 240))
    # Bottom drop shadow bevel inside the pill
    draw.rounded_rectangle([lvl_x + int(3 * scale), lvl_y + lvl_h - int(5 * scale), lvl_x + lvl_w - int(3 * scale), lvl_y + lvl_h - int(2 * scale)],
                           radius=3 * scale, fill=(150, 152, 160, 240))
    draw.text((lvl_x + lvl_w // 2, lvl_y + lvl_h // 2), lvl_str, fill=(12, 12, 14, 255), font=font_lvl_badge, anchor="mm")

    # --- MIDDLE ROW: Progression Bar & XP Info ---
    bar_y = int(124 * scale)
    bar_w = int((w_base - 46) * scale) - content_x
    bar_h = 24 * scale
    bar_radius = bar_h // 2

    pct = min(1.0, max(0.0, current_xp / xp_needed)) if xp_needed > 0 else 1.0
    xp_str = f"{current_xp:,} / {xp_needed:,} XP"
    pct_str = f"{pct * 100:.1f}%"

    # Labels row above progress bar
    draw.text((content_x, bar_y - int(22 * scale)), "PROGRESSION", fill=(140, 140, 148, 255), font=font_label)
    draw.text((content_x + bar_w, bar_y - int(22 * scale)), f"{xp_str}  •  {pct_str}", fill=(235, 235, 242, 255), font=font_nums, anchor="ra")

    # Track Trough (Deep inset clay dark track)
    draw.rounded_rectangle([content_x, bar_y, content_x + bar_w, bar_y + bar_h], radius=bar_radius, fill=(12, 12, 15, 255), outline=(42, 42, 48, 220), width=scale)

    # Inner dark shadow at top of trough
    draw.rounded_rectangle([content_x + int(3 * scale), bar_y + scale, content_x + bar_w - int(3 * scale), bar_y + int(3.5 * scale)],
                           radius=int(2 * scale), fill=(3, 3, 5, 220))

    # Active Progress Fill (Neutral Platinum/Chrome Gradient with Clay depth)
    fill_len = int(bar_w * pct)
    if fill_len >= bar_radius:
        fill_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        f_draw = ImageDraw.Draw(fill_layer)

        # Volumetric active silver gradient
        f_draw.rounded_rectangle([content_x, bar_y, content_x + fill_len, bar_y + bar_h], radius=bar_radius, fill=(215, 218, 228, 255))
        # Top specular shine strip (pure white)
        f_draw.rounded_rectangle([content_x + int(6 * scale), bar_y + int(2.5 * scale), content_x + fill_len - int(6 * scale), bar_y + int(6.5 * scale)],
                                 radius=int(2 * scale), fill=(255, 255, 255, 240))
        # Bottom shadow bevel (dark metallic grey)
        f_draw.rounded_rectangle([content_x + int(4 * scale), bar_y + bar_h - int(4.5 * scale), content_x + fill_len - int(4 * scale), bar_y + bar_h - int(1.5 * scale)],
                                 radius=int(2 * scale), fill=(140, 144, 155, 230))

        card = Image.alpha_composite(card, fill_layer)
        draw = ImageDraw.Draw(card)

    # --- BOTTOM ROW: Lifetime XP + Milestone Glass Cards ---
    stat_y = int(168 * scale)
    stat_h = 58 * scale

    # Card 1: Lifetime Mined
    stat1_w = int(185 * scale)
    stat1_box = [content_x, stat_y, content_x + stat1_w, stat_y + stat_h]
    draw.rounded_rectangle(stat1_box, radius=12 * scale, fill=(16, 16, 18, 210), outline=(255, 255, 255, 22), width=scale)
    draw.text((content_x + int(14 * scale), stat_y + int(13 * scale)), "LIFETIME XP MINED", fill=(130, 130, 138, 255), font=font_label)
    draw.text((content_x + int(14 * scale), stat_y + int(36 * scale)), f"{total_xp:,} XP", fill=(255, 255, 255, 255), font=font_val)

    # Card 2: Next Milestone Reward (with Tails.png coin icon)
    stat2_x = content_x + stat1_w + int(14 * scale)
    stat2_w = int((w_base - 46) * scale) - stat2_x
    stat2_box = [stat2_x, stat_y, stat2_x + stat2_w, stat_y + stat_h]
    draw.rounded_rectangle(stat2_box, radius=12 * scale, fill=(16, 16, 18, 210), outline=(255, 255, 255, 30), width=scale)

    draw.text((stat2_x + int(14 * scale), stat_y + int(13 * scale)), f"NEXT MILESTONE (LVL {next_milestone_level})", fill=(195, 198, 208, 240), font=font_label)

    coin_size = int(24 * scale)
    coin_x = stat2_x + int(14 * scale)
    coin_y = stat_y + int(33 * scale)
    coin_img = _get_tails_coin(coin_size)
    if coin_img:
        card.paste(coin_img, (coin_x, coin_y), coin_img)

    text_x = coin_x + coin_size + int(8 * scale)
    draw.text((text_x, stat_y + int(36 * scale)), f"+{next_milestone_reward:,} TAD Cash Payout", fill=(245, 245, 250, 255), font=font_val)

    # Downsample 2x to 1x via Lanczos for pixel-perfect retina anti-aliasing
    final_card = card.resize((w_base, h_base), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    final_card.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    # Free all PIL Image objects and run GC to keep RAM strictly below 100MB
    try:
        card.close()
        final_card.close()
        del card, auras, glass, streak, final_card
        if 'fill_layer' in locals():
            fill_layer.close()
            del fill_layer
        if 'av_img' in locals():
            av_img.close()
            del av_img
        if 'mask' in locals():
            mask.close()
            del mask
        import gc
        gc.collect()
    except Exception:
        pass

    return buf


async def setup(bot):
    pass

