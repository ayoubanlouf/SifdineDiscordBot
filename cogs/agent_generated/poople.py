import discord
from discord.ext import commands
import random

class GuessModal(discord.ui.Modal, title='Guess l-kelma'):
    guess = discord.ui.TextInput(label='Kteb l-kelma dyalek (5 hrouf)', min_length=5, max_length=5)

    def __init__(self, game_instance):
        super().__init__()
        self.game = game_instance

    async def on_submit(self, interaction: discord.Interaction):
        await self.game.process_guess(interaction, self.guess.value.lower())

class PoopleView(discord.ui.View):
    def __init__(self, game_instance):
        super().__init__(timeout=300)
        self.game = game_instance

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary)
    async def guess_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuessModal(self.game))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.game.author_id

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        await self.game.message.edit(view=self)

class PoopleGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.words = ["darja", "khobz", "tmaro", "mraya", "korsa", "tbiba"]

    @commands.command(name="poople")
    async def poople(self, ctx):
        self.secret = random.choice(self.words)
        self.attempts = 6
        self.history = []
        self.author_id = ctx.author.id
        
        embed = discord.Embed(title="Poople - L3ba dyal l-kelmat", description="Kteb kelma dyal 5 hrouf!", color=0x000000)
        embed.add_field(name="L-halat", value="Bda l-l3b!", inline=False)
        
        self.message = await ctx.send(embed=embed, view=PoopleView(self))

    async def process_guess(self, interaction, guess):
        if len(guess) != 5:
            return await interaction.response.send_message("Kteb 5 hrouf a khay!", ephemeral=True)

        self.attempts -= 1
        result = self.check_word(guess)
        self.history.append(f"`{guess.upper()}`: {result}")
        
        embed = discord.Embed(title="Poople", color=0x000000)
        embed.description = "\n".join(self.history)
        
        if guess == self.secret:
            reward = (self.attempts + 1) * 10
            economy = self.bot.get_cog('Economy')
            if economy:
                await economy.apply_tax_and_add_balance(interaction.user.id, reward, context='Poople Win')
            embed.add_field(name="Mabrouk!", value=f"Rbehti {reward} TAD!")
            await interaction.response.edit_message(embed=embed, view=None)
        elif self.attempts <= 0:
            embed.add_field(name="Salaw l-mohawalat", value=f"L-kelma kant: {self.secret}")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed)

    def check_word(self, guess):
        res = []
        for i, char in enumerate(guess):
            if char == self.secret[i]:
                res.append("🟩")
            elif char in self.secret:
                res.append("🟨")
            else:
                res.append("⬛")
        return "".join(res)

async def setup(bot):
    await bot.add_cog(PoopleGame(bot))