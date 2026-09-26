import asyncio
import discord
from discord.ui import Button, View
from cogs.games.helpers import DIFFICULTY_STAKES


def parse_minigame_args(*args, default_duration=20, default_difficulty="easy"):
    duration = default_duration
    difficulty = default_difficulty
    for arg in args:
        if arg is None:
            continue
        s = str(arg).strip().lower()
        if s in ("easy", "medium", "hard", "e", "m", "h", "sahel", "3adi", "s3ib"):
            if s in ("easy", "e", "sahel"):
                difficulty = "easy"
            elif s in ("medium", "m", "3adi"):
                difficulty = "medium"
            elif s in ("hard", "h", "s3ib"):
                difficulty = "hard"
        else:
            try:
                duration = int(s)
            except ValueError:
                pass
    return max(5, duration), difficulty


async def countdown_reactions(msg: discord.Message, total_duration: float):
    """Reacts with 3️⃣, 2️⃣, 1️⃣ when 3, 2, and 1 seconds remain on msg."""
    try:
        pre_wait = total_duration - 3.0
        if pre_wait > 0:
            await asyncio.sleep(pre_wait)
        for emoji in ("3️⃣", "2️⃣", "1️⃣"):
            try:
                await msg.add_reaction(emoji)
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                return
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass


class MinigameDifficultyView(View):
    def __init__(self, host_id: int, initial_difficulty: str = "easy"):
        super().__init__(timeout=25)
        self.host_id = host_id
        self.difficulty = initial_difficulty
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        configs = [
            ("easy", "🟢 Easy (1.0x)", discord.ButtonStyle.success),
            ("medium", "🟡 Medium (1.5x)", discord.ButtonStyle.primary),
            ("hard", "🔴 Hard (2.0x)", discord.ButtonStyle.danger)
        ]
        for diff_key, label, style in configs:
            btn = Button(
                label=label,
                style=style,
                custom_id=f"diff_{diff_key}",
                disabled=(self.difficulty == diff_key)
            )
            btn.callback = self._make_callback(diff_key)
            self.add_item(btn)

    def _make_callback(self, selected_diff: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.host_id:
                await interaction.response.send_message("Gher l host li y9ed ybdel difficulty.", ephemeral=True)
                return
            self.difficulty = selected_diff
            self._update_buttons()
            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0]
                mult = DIFFICULTY_STAKES.get(selected_diff, 1.0)
                lines = embed.description.split("\n")
                new_lines = []
                for line in lines:
                    if not line.startswith("Difficulty:"):
                        new_lines.append(line)
                new_lines.append(f"Difficulty: **{selected_diff.upper()}** (Stake: **{mult}x**)")
                embed.description = "\n".join(new_lines)
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.edit_message(view=self)
        return callback
