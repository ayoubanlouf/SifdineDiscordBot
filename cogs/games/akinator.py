from __future__ import annotations
import asyncio
from typing import Optional, Union

import discord
from discord.ui import Button, View

from cogs.games.akinator_client import ModernAsyncAkinator as AsyncAkinator
from cogs.games.helpers import clear_user_game

class AkinatorButton(Button):
    def __init__(self, label: str, custom_id: str, style: discord.ButtonStyle, emoji: str, row: int):
        super().__init__(label=label, custom_id=custom_id, style=style, emoji=emoji, row=row)


class AkinatorView(View):
    def __init__(self, player: Union[discord.Member, discord.User], timeout: float = 60.0, cog: Optional["Minigames"] = None, channel_id: Optional[int] = None):
        super().__init__(timeout=timeout)
        self.player = player
        self.cog = cog
        self.channel_id = channel_id
        self.aki = AsyncAkinator()
        self.message: Optional[discord.Message] = None
        self.game_over = False
        self.guessing = False

        # Row 0: Primary Answers
        self.add_item(AkinatorButton("Yes", "aki_y", discord.ButtonStyle.success, "✅", 0))
        self.add_item(AkinatorButton("No", "aki_n", discord.ButtonStyle.danger, "❌", 0))
        self.add_item(AkinatorButton("I don't know", "aki_idk", discord.ButtonStyle.secondary, "❓", 0))

        # Row 1: Secondary Answers
        self.add_item(AkinatorButton("Probably", "aki_p", discord.ButtonStyle.primary, "👍", 1))
        self.add_item(AkinatorButton("Probably Not", "aki_pn", discord.ButtonStyle.primary, "👎", 1))

        # Row 2: Controls
        self.add_item(AkinatorButton("Back", "aki_b", discord.ButtonStyle.secondary, "⬅️", 2))
        self.add_item(AkinatorButton("Stop", "aki_s", discord.ButtonStyle.danger, "🛑", 2))

    def cleanup_session(self):
        if self.cog:
            self.cog.active_akinator_users.discard(self.player.id)
            if self.channel_id:
                self.cog.active_akinator_channels.pop(self.channel_id, None)
            if hasattr(self.cog, "bot") and self.player:
                clear_user_game(self.cog.bot, self.player.id)
        if hasattr(self.aki, "close"):
            asyncio.create_task(self.aki.close())

    def stop(self):
        self.cleanup_session()
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        self.game_over = True
        self.disable_all_buttons()
        if self.message:
            try:
                await self.message.edit(content="🚪 **Akinator session salat.**", embed=None, view=None)
            except Exception:
                pass
        self.stop()
        return "🚪 Kherjti mn session dial **Akinator**!"

    async def start_game(self) -> discord.Embed:
        """Starts the Akinator session asynchronously with automatic retries."""
        last_err = None
        for attempt in range(3):
            try:
                self.aki = AsyncAkinator()
                await asyncio.wait_for(self.aki.start_game(), timeout=12.0)
                if self.aki.question:
                    return self.build_question_embed(self.aki.question)
            except Exception as e:
                last_err = e
                await asyncio.sleep(1.0)
        raise last_err or RuntimeError("Failed to start Akinator session.")

    def build_question_embed(self, question: str) -> discord.Embed:
        embed = discord.Embed(
            title="🔮 Akinator",
            description=f"**{question}**",
            color=0x000000
        )
        step_val = (self.aki.step or 0) + 1
        prog_val = int(self.aki.progression or 0)
        embed.set_footer(
            text=f"Player: {self.player.display_name} • Step {step_val} ({prog_val}%)"
        )
        return embed

    def disable_all_buttons(self):
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            self.cleanup_session()
            self.disable_all_buttons()
            if self.message:
                try:
                    embed = self.message.embeds[0]
                    embed.description = "⏰ **Match sala bsbab l inactivity!**"
                    await self.message.edit(embed=embed, view=self)
                except (discord.NotFound, discord.HTTPException):
                    pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr.", ephemeral=True)
            return False
        return True

    async def button_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")

        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat..", ephemeral=True)
            return

        await interaction.response.defer()

        # Stop Game
        if custom_id == "aki_s":
            self.game_over = True
            self.cleanup_session()
            self.disable_all_buttons()
            self.stop()
            embed = discord.Embed(description="🛑 **Lgame 7bsat!**", color=0x000000)
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            return

        # Undo Move
        if custom_id == "aki_b":
            try:
                await self.aki.back()
                embed = self.build_question_embed(self.aki.question)
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            except Exception:
                await interaction.followup.send("Man9drch nrje3..", ephemeral=True)
            return

        # Shortcode Mapping for akinator.py
        # Supported answers for akinator.py: "yes", "no", "i don't know", "probably", "probably not"
        answer_map = {
            "aki_y": "yes",
            "aki_n": "no",
            "aki_idk": "i don't know",
            "aki_p": "probably",
            "aki_pn": "probably not"
        }

        ans = answer_map.get(custom_id)
        if not ans:
            return

        # Handle Guess Confirmation Phase
        if self.guessing:
            if custom_id == "aki_y" or ans in ("yes", "probably"):
                self.game_over = True
                self.cleanup_session()
                self.disable_all_buttons()
                self.stop()
                try:
                    await self.aki.choose()
                except Exception as e:
                    print(f"[Akinator Choose Error]: {e}")
                economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                eco_msg = ""
                if economy_cog:
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, 50, context="Akinator Completion")
                    eco_msg = f"\n\n🎁 Rbe7ti **+{net}** {TAD_EMOJI} TAD cadeau mn 3endi!"
                name = getattr(self.aki, "name_proposition", "L Personnage dialk")
                desc = getattr(self.aki, "description_proposition", "")
                photo = getattr(self.aki, "photo", None)
                embed = discord.Embed(
                    title="🎉 Rbe7t! L9it l personnage dialk!",
                    description=f"**{name}**\n*{desc}*{eco_msg}",
                    color=0x000000
                )
                if photo:
                    embed.set_image(url=photo)
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
                return
            else:
                self.guessing = False
                try:
                    await self.aki.exclude()
                except Exception as e:
                    print(f"[Akinator Exclude Error]: {e}")

                # If Akinator reached high steps (>75) or finished after exclude, admit defeat
                if self.aki.step >= 75 or getattr(self.aki, "finished", False):
                    self.game_over = True
                    self.cleanup_session()
                    self.disable_all_buttons()
                    self.stop()
                    economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                    eco_msg = ""
                    if economy_cog:
                        net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, 50, context="Akinator Completion")
                        eco_msg = f"\n\n🎁 Rbe7ti **+{net}** {TAD_EMOJI} TAD cadeau mn 3endi!"
                    embed = discord.Embed(
                        title="🏆 Bravo! Ghlbtini!",
                        description=f"Ma9dertch n3ref chkoun f balek, 3refti tkhebiha 3lia!{eco_msg}",
                        color=0x000000
                    )
                    await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
                    return

                self.clear_items()
                self.add_item(AkinatorButton("Yes", "aki_y", discord.ButtonStyle.success, "✅", 0))
                self.add_item(AkinatorButton("No", "aki_n", discord.ButtonStyle.danger, "❌", 0))
                self.add_item(AkinatorButton("I don't know", "aki_idk", discord.ButtonStyle.secondary, "❓", 0))
                self.add_item(AkinatorButton("Probably", "aki_p", discord.ButtonStyle.primary, "👍", 1))
                self.add_item(AkinatorButton("Probably Not", "aki_pn", discord.ButtonStyle.primary, "👎", 1))
                self.add_item(AkinatorButton("Back", "aki_b", discord.ButtonStyle.secondary, "⬅️", 2))
                self.add_item(AkinatorButton("Stop", "aki_s", discord.ButtonStyle.danger, "🛑", 2))

                for child in self.children:
                    if isinstance(child, Button):
                        child.callback = self.button_callback

                embed = self.build_question_embed(self.aki.question)
                await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
                return

        # Turn Processing with Retries
        answered_ok = False
        for attempt in range(3):
            try:
                await self.aki.answer(ans)
                answered_ok = True
                break
            except Exception as e:
                print(f"[Akinator Turn Error - Attempt {attempt + 1}]: {e}")
                await asyncio.sleep(1.0)

        if not answered_ok:
            self.game_over = True
            self.cleanup_session()
            self.disable_all_buttons()
            self.stop()
            embed = discord.Embed(description="❌ Tra mochkil m3a Akinator API, 7awel mn b3d.", color=0x000000)
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            return

        # Win Check Logic
        if getattr(self.aki, "win", False) and getattr(self.aki, "name_proposition", None):
            self.guessing = True
            name = self.aki.name_proposition
            desc = getattr(self.aki, "description_proposition", "")
            photo = getattr(self.aki, "photo", None)
            embed = discord.Embed(
                title="🤔 Wach hada howa l personnage li f balek?",
                description=f"**{name}**\n*{desc}*",
                color=0x000000
            )
            if photo:
                embed.set_image(url=photo)

            self.clear_items()
            self.add_item(AkinatorButton("Yes", "aki_y", discord.ButtonStyle.success, "✅", 0))
            self.add_item(AkinatorButton("No", "aki_n", discord.ButtonStyle.danger, "❌", 0))

            for child in self.children:
                if isinstance(child, Button):
                    child.callback = self.button_callback

            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            return

        embed = self.build_question_embed(self.aki.question)
        await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)

