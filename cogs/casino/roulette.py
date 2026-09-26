from __future__ import annotations
import random
from typing import Optional, Tuple, Set

import discord

from cogs.economy import format_tad, TAD_EMOJI

ROULETTE_RED_NUMS: Set[int] = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_BLACK_NUMS: Set[int] = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}


def parse_roulette_choice(val: str) -> Optional[Tuple[str, str, str]]:
    """Returns (choice_type, choice_val, display_label) or None."""
    if not val:
        return None
    s = str(val).strip().lower()
    if s.isdigit():
        num = int(s)
        if 0 <= num <= 36:
            return ("number", str(num), f"Ra9m {num}")
        return None
    if s in ("zero",):
        return ("number", "0", "Ra9m 0")
    if s in ("red", "r", "7mer", "7mr"):
        return ("red", "red", "Red 🔴")
    if s in ("black", "b", "k7el", "k7l", "k7al"):
        return ("black", "black", "Black ⚫")
    if s in ("green", "g", "khder"):
        return ("green", "green", "Green 🟢")
    if s in ("even", "zawji"):
        return ("even", "even", "Even (Zawji)")
    if s in ("odd", "fardi"):
        return ("odd", "odd", "Odd (Fardi)")
    if s in ("1-18", "low", "fo9"):
        return ("low", "1-18", "1-18 (Low)")
    if s in ("19-36", "high", "ta7t", "t7t"):
        return ("high", "19-36", "19-36 (High)")
    return None


def spin_roulette(choice_type: str, choice_val: str) -> Tuple[int, str, str, bool, float]:
    landed_num = random.randint(0, 36)
    if landed_num == 0:
        color_emoji = "🟢"
        color_name = "green"
    elif landed_num in ROULETTE_RED_NUMS:
        color_emoji = "🔴"
        color_name = "red"
    elif landed_num in ROULETTE_BLACK_NUMS:
        color_emoji = "⚫"
        color_name = "black"
    else:
        color_emoji = "🟢"
        color_name = "green"

    won = False
    mult = 0.0

    if choice_type == "number":
        if landed_num == int(choice_val):
            won = True
            mult = 36.0
    elif choice_type == "red" and color_name == "red":
        won = True
        mult = 2.0
    elif choice_type == "black" and color_name == "black":
        won = True
        mult = 2.0
    elif choice_type == "green" and color_name == "green":
        won = True
        mult = 36.0
    elif choice_type == "even" and landed_num > 0 and landed_num % 2 == 0:
        won = True
        mult = 2.0
    elif choice_type == "odd" and landed_num % 2 != 0:
        won = True
        mult = 2.0
    elif choice_type == "low" and 1 <= landed_num <= 18:
        won = True
        mult = 2.0
    elif choice_type == "high" and 19 <= landed_num <= 36:
        won = True
        mult = 2.0

    return landed_num, color_emoji, color_name, won, mult
