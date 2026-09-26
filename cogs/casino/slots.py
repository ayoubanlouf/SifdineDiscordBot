from __future__ import annotations
import random
from typing import Tuple, Dict, Any

import discord

from cogs.economy import format_tad, TAD_EMOJI

SLOT_ITEMS = ["💎", "7️⃣", "🔔", "🍇", "🍒", "🍋", "🍊"]
SLOT_WEIGHTS = [5, 10, 15, 20, 25, 30, 35]


def spin_slots() -> Tuple[str, str, str, float, str]:
    r1 = random.choices(SLOT_ITEMS, weights=SLOT_WEIGHTS, k=1)[0]
    r2 = random.choices(SLOT_ITEMS, weights=SLOT_WEIGHTS, k=1)[0]
    r3 = random.choices(SLOT_ITEMS, weights=SLOT_WEIGHTS, k=1)[0]

    payout_mult = 0.0
    outcome_title = "Khesrti!"
    if r1 == r2 == r3:
        if r1 == "💎":
            payout_mult = 15.0
            outcome_title = "JACKPOT! Triple Diamonds!"
        elif r1 == "7️⃣":
            payout_mult = 10.0
            outcome_title = "MEGA WIN! Triple Sevens!"
        elif r1 == "🔔":
            payout_mult = 6.0
            outcome_title = "SUPER WIN! Triple Bells!"
        elif r1 == "🍇":
            payout_mult = 5.0
            outcome_title = "BIG WIN! Triple Grapes!"
        elif r1 == "🍒":
            payout_mult = 4.0
            outcome_title = "WIN! Triple Cherries!"
        else:
            payout_mult = 3.0
            outcome_title = f"WIN! Triple {r1}!"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        payout_mult = 1.5
        outcome_title = "Small Win! Double Match!"

    return r1, r2, r3, payout_mult, outcome_title


def build_slots_embed(r1: str, r2: str, r3: str, payout_mult: float, outcome_title: str) -> discord.Embed:
    embed = discord.Embed(
        title="🎰 Slots Machine",
        description=(
            f"**[ {r1} | {r2} | {r3} ]**\n\n"
            f"**{outcome_title}**\n"
            f"📊 Multiplier: **{payout_mult:.1f}x**"
        ),
        color=0x000000
    )
    return embed
