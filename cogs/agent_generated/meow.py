import discord
from discord.ext import commands

class MeowCog(commands.Cog):
    """
    A simple command that makes the bot say Meow meow.
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="meow")
    async def meow(self, ctx: commands.Context):
        """
        Returns 'Meow meow' literally.
        """
        await ctx.send("Meow meow")

async def setup(bot):
    await bot.add_cog(MeowCog(bot))