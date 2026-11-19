import discord
from discord.ext import commands

class MeowCog(commands.Cog):
    """
    A simple command that makes the bot say meow.
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="meow")
    async def meow(self, ctx):
        """
        Responds with 'Meow meow'.
        """
        # Using an embed with the required black color as per global standards
        embed = discord.Embed(
            description="Meow meow",
            color=0x000000
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(MeowCog(bot))