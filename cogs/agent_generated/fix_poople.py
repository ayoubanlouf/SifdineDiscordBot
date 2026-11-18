import discord
from discord.ext import commands

class FixPoople(commands.Cog):
    """
    Command to re-initialize or debug the Poople listener to ensure it reacts to user messages.
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="fix_poople")
    async def fix_poople(self, ctx):
        """
        Resets the internal state of the Poople listener and verifies permissions.
        """
        channel = ctx.channel
        permissions = channel.permissions_for(ctx.guild.me)
        
        # Check for necessary permissions to interact
        has_read = permissions.read_messages
        has_send = permissions.send_messages
        has_react = permissions.add_reactions

        # Logic to "reset" the listener (assuming a hypothetical listener state)
        # In a production environment, this would trigger a state refresh in the Poople Cog
        poople_cog = self.bot.get_cog('Poople')
        status = "Poople listener mreset-a b njah."
        
        if poople_cog and hasattr(poople_cog, 'reset_state'):
            await poople_cog.reset_state()
        else:
            status = "Poople listener ma-tlaqach, walakin checkit permissions dyal l-bot."

        # Build the embed response
        embed = discord.Embed(
            title="🛠️ Poople Debugger",
            description=f"**Status:** {status}\n\n"
                        f"**Permissions Check:**\n"
                        f"• Read Messages: {'✅' if has_read else '❌'}\n"
                        f"• Send Messages: {'✅' if has_send else '❌'}\n"
                        f"• Add Reactions: {'✅' if has_react else '❌'}",
            color=0x000000
        )

        if not (has_read and has_send and has_react):
            embed.add_field(
                name="⚠️ Warning",
                value="Khassk t-verifyi permissions dyal l-bot f had l-channel bach y-reacti.",
                inline=False
            )

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(FixPoople(bot))