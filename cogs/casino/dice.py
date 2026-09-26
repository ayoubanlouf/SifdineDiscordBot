from __future__ import annotations
import io
import os
import random
from typing import Optional, List

import discord
from PIL import Image


def render_dice_composite(rolls: List[int]) -> Optional[io.BytesIO]:
    imgs = []
    for r in rolls:
        p = os.path.join('assets', 'dice', f'{r}.png')
        if os.path.exists(p):
            imgs.append(Image.open(p).convert('RGBA'))
    if not imgs:
        return None
    if len(imgs) == 1:
        buf = io.BytesIO()
        imgs[0].save(buf, format='PNG')
        buf.seek(0)
        return buf

    spacing = 15
    w, h = imgs[0].size
    total_w = len(imgs) * w + (len(imgs) - 1) * spacing
    canvas = Image.new('RGBA', (total_w, h), (0, 0, 0, 0))
    for i, im in enumerate(imgs):
        canvas.paste(im, (i * (w + spacing), 0), im)
    buf = io.BytesIO()
    canvas.save(buf, format='PNG')
    buf.seek(0)
    return buf


class DiceRollView(discord.ui.View):
    def __init__(self, author: discord.Member, cog, num_dice: int = 1, num_sides: int = 6):
        super().__init__(timeout=60)
        self.author = author
        self.cog = cog
        self.num_dice = num_dice
        self.num_sides = num_sides
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had lgame machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Roll Again", style=discord.ButtonStyle.primary, emoji="🎲")
    async def roll_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        rolls = [random.randint(1, self.num_sides) for _ in range(self.num_dice)]
        total = sum(rolls)
        rolls_str = " ".join(f"`{r}`" for r in rolls)

        embed = discord.Embed(
            title=f"🎲 Dice Roll ({self.num_dice}d{self.num_sides})",
            description=f"**Rolls:** {rolls_str}\n**Total Sum:** `{total}`",
            color=0x000000
        )
        if self.num_sides == 6:
            buf = render_dice_composite(rolls)
            if buf:
                file = discord.File(buf, filename="dice.png")
                if len(rolls) == 1:
                    embed.set_thumbnail(url="attachment://dice.png")
                else:
                    embed.set_image(url="attachment://dice.png")
                await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
                return

        await interaction.response.edit_message(embed=embed, view=self, attachments=[])
