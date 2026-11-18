import discord
import random
from discord.ext import commands

class PoopleGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.max_guesses = 10
        # Word pool sourced from internal game assets
        self.word_pool = ["comp", "loop", "pool", "pomp", "coop", "boop", "moop", "poop"]
        self.active_games = {}

    def is_valid_transition(self, word1, word2):
        if len(word1) != 4 or len(word2) != 4:
            return False
        diff = sum(1 for a, b in zip(word1, word2) if a != b)
        return diff == 1

    @commands.command(name="poople")
    async def poople(self, ctx, guess: str = None):
        """Transform a 4-letter word into 'poop' one letter at a time."""
        user_id = ctx.author.id

        # Start new game
        if user_id not in self.active_games:
            start_word = random.choice([w for w in self.word_pool if w != "poop"])
            self.active_games[user_id] = {
                "current_word": start_word,
                "guesses": 0,
                "history": [start_word]
            }
            embed = discord.Embed(
                title="Poople Game",
                description=f"Welcome! Transform **{start_word.upper()}** into **POOP**.\nChange one letter at a time. Valid 4-letter words only.",
                color=0x000000
            )
            return await ctx.send(embed=embed)

        # Process guess
        game = self.active_games[user_id]
        guess = guess.lower()

        if len(guess) != 4 or guess not in self.word_pool:
            return await ctx.send("Invalid word. Please use a valid 4-letter word from the dictionary.")

        if not self.is_valid_transition(game["current_word"], guess):
            return await ctx.send(f"Invalid move! You must change exactly one letter from **{game['current_word'].upper()}**.")

        game["guesses"] += 1
        game["current_word"] = guess
        game["history"].append(guess)

        if guess == "poop":
            # Calculate Reward
            economy = self.bot.get_cog('Economy')
            reward = 0
            if game["guesses"] <= self.max_guesses:
                reward = 50 + (10 * (self.max_guesses - game["guesses"]))
                if economy:
                    await economy.apply_tax_and_add_balance(user_id, reward, context='Poople Game Win')

            embed = discord.Embed(
                title="You won!",
                description=f"You reached POOP in {game['guesses']} guesses!\nReward: {reward} TAD.",
                color=0x000000
            )
            del self.active_games[user_id]
            return await ctx.send(embed=embed)

        if game["guesses"] >= self.max_guesses:
            embed = discord.Embed(title="Game Over", description="You ran out of guesses!", color=0x000000)
            del self.active_games[user_id]
            return await ctx.send(embed=embed)

        embed = discord.Embed(
            title="Poople",
            description=f"Current word: **{guess.upper()}**\nGuesses left: {self.max_guesses - game['guesses']}",
            color=0x000000
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PoopleGame(bot))