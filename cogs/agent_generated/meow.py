import discord
import asyncio
from discord.ext import commands
from discord.ui import View, Button

class MeowView(View):
    def __init__(self, bot, user_id):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.clicked = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Hada machi dyalk, khli l-qot yrtah!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # Note: In a production environment, we would edit the message here.
        # Since we don't have the message object directly, we rely on the interaction response.

    @discord.ui.button(label="Feed Sifdine", style=discord.ButtonStyle.primary, emoji="🐱")
    async def feed_button(self, interaction: discord.Interaction, button: Button):
        if self.clicked:
            return

        economy_cog = self.bot.get_cog('Economy')
        # Simple check for daily limit (mock logic for demonstration)
        # In production, use a database/cache check for the 5/day limit
        
        self.clicked = True
        for item in self.children:
            item.disabled = True

        if economy_cog:
            await economy_cog.apply_tax_and_add_balance(interaction.user.id, 5, context='meow_game')
            await interaction.response.edit_message(
                content="Meow meow meow! 🐾 Sifdine farhan, khodi 5 TAD dyalk. Meow! 3titi l-qot l-makla, bsahtek l-bonus.",
                view=self
            )
        else:
            await interaction.response.edit_message(
                content="Meow meow meow! Sifdine farhan, walakin l-banka m3tla daba.",
                view=self
            )

class MeowCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='meow')
    async def meow(self, ctx):
        """A playful test command that triggers a feline response from Sifdine."""
        view = MeowView(self.bot, ctx.author.id)
        await ctx.send("Sifdine bgha yakol! 🐱 Tka 3la l-button bach t-feedih.", view=view)

async def setup(bot):
    await bot.add_cog(MeowCog(bot))