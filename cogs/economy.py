import time
import re
from typing import Optional, Tuple
import discord
from discord.ext import commands


TAD_EMOJI = "<:TAD:1543808845728710686>"
TAX_RATE = 0.05  # 5% anti-inflation transaction burn


def format_tad(amount: int) -> str:
    return f"**{amount:,}** {TAD_EMOJI} TAD"


def parse_bet_argument(*args) -> Tuple[Optional[int], list]:
    """
    Intelligently parses explicit bet setters (e.g. bet:500, b:250, 100tad, 500t, bet=300, 500drhm)
    out of variable command arguments so betting never conflicts with other integer inputs.
    """
    remaining = []
    found_bet = None

    pattern = re.compile(r"^(?:bet:|b:|bet=)?([0-9]+)(?:tad|t|drhm|drhem)?$", re.IGNORECASE)

    for arg in args:
        if arg is None:
            continue
        s_arg = str(arg).strip()
        
        # Check explicit prefixes/suffixes or standalone positive int if startswith prefix
        if (s_arg.lower().startswith(("bet:", "b:", "bet=")) or 
            s_arg.lower().endswith(("tad", "t", "drhm", "drhem"))):
            m = pattern.match(s_arg)
            if m and found_bet is None:
                try:
                    val = int(m.group(1))
                    if val > 0:
                        found_bet = val
                        continue
                except ValueError:
                    pass

        remaining.append(arg)

    return found_bet, remaining


def calculate_pvp_payout(bet_per_player: int) -> Tuple[int, int, int]:
    """
    Returns (winner_payout, burned_amount, draw_split_per_player)
    Total pot = 2 * bet
    Tax burn = round(Total pot * 5%)
    Winner payout = Total pot - Tax burn
    Draw split = floor((Total pot - Tax burn) / 2) -> (each player recovers 95% of their stake)
    """
    total_pot = bet_per_player * 2
    burned = round(total_pot * TAX_RATE)
    winner_payout = total_pot - burned
    draw_split = (total_pot - burned) // 2
    return winner_payout, burned, draw_split


def not_fraud():
    """Custom command check that suspends frauded users from the economy."""
    async def predicate(ctx: commands.Context):
        if not hasattr(ctx.bot, 'db') or not ctx.bot.db:
            return True
        async with ctx.bot.db.execute("SELECT is_fraud FROM user_wallets WHERE user_id = ?", (ctx.author.id,)) as cursor:
            row = await cursor.fetchone()
        if row and row[0] == 1:
            embed = discord.Embed(
                title="🚫 Economy Suspended",
                description=(
                    "⚠️ 7sabek f l'economy mbloqui 7it mssjl ka **Fraud**.\n"
                    "Ma9adch tsta3mel commands dial flous, t9emmer, wla tched chat rewards."
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return False
        return True
    return commands.check(predicate)


class WalletView(discord.ui.View):
    def __init__(self, target_user: discord.Member, author: discord.Member, cog):
        super().__init__(timeout=90)
        self.target_user = target_user
        self.author = author
        self.cog = cog
        self.showing_transactions = False
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l bouton machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Recent Transactions", style=discord.ButtonStyle.secondary, emoji="📜")
    async def toggle_view(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.showing_transactions = not self.showing_transactions
        if self.showing_transactions:
            button.label = "Back to Wallet"
            button.emoji = "🔙"
            button.style = discord.ButtonStyle.primary
            embed = await self.cog.get_transactions_embed(self.target_user)
        else:
            button.label = "Recent Transactions"
            button.emoji = "📜"
            button.style = discord.ButtonStyle.secondary
            embed = await self.cog.get_wallet_embed(self.target_user)

        await interaction.response.edit_message(embed=embed, view=self)


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_wallet(self, user_id: int) -> dict:
        async with self.bot.db.execute(
            "SELECT balance, total_activity_rewards, is_fraud FROM user_wallets WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            # Default starting balance = 100 TAD
            await self.bot.db.execute(
                "INSERT INTO user_wallets (user_id, balance, total_activity_rewards, is_fraud) VALUES (?, 100, 0, 0)",
                (user_id,)
            )
            await self.bot.db.commit()
            return {"balance": 100, "total_activity_rewards": 0, "is_fraud": 0}

        return {
            "balance": int(row[0]),
            "total_activity_rewards": int(row[1]),
            "is_fraud": int(row[2])
        }

    async def add_balance(self, user_id: int, amount: int, context: str = "") -> int:
        if amount <= 0:
            return 0
        w = await self.get_wallet(user_id)
        new_bal = w["balance"] + amount

        if context == "chat_activity":
            await self.bot.db.execute(
                "UPDATE user_wallets SET balance = ?, total_activity_rewards = total_activity_rewards + ? WHERE user_id = ?",
                (new_bal, amount, user_id)
            )
        else:
            await self.bot.db.execute(
                "UPDATE user_wallets SET balance = ? WHERE user_id = ?",
                (new_bal, user_id)
            )

        if context and context != "chat_activity":
            now_ts = int(time.time())
            await self.bot.db.execute(
                "INSERT INTO user_transactions (user_id, amount, context, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, context, now_ts)
            )

        await self.bot.db.commit()
        return new_bal

    async def deduct_balance(self, user_id: int, amount: int, context: str = "") -> bool:
        if amount <= 0:
            return True
        w = await self.get_wallet(user_id)
        if w["balance"] < amount:
            return False

        new_bal = w["balance"] - amount
        await self.bot.db.execute(
            "UPDATE user_wallets SET balance = ? WHERE user_id = ?",
            (new_bal, user_id)
        )

        if context:
            now_ts = int(time.time())
            await self.bot.db.execute(
                "INSERT INTO user_transactions (user_id, amount, context, created_at) VALUES (?, ?, ?, ?)",
                (user_id, -amount, context, now_ts)
            )

        await self.bot.db.commit()
        return True

    async def get_wallet_embed(self, user: discord.Member) -> discord.Embed:
        w = await self.get_wallet(user.id)
        status_str = "🔒 **Frozen (Fraud)**" if w["is_fraud"] == 1 else "✅ **Active (Legit)**"

        embed = discord.Embed(
            title=f"💼 {user.display_name}'s Wallet",
            color=0x000000
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="💰 Balance", value=format_tad(w['balance']), inline=True)
        embed.add_field(name="📊 Status", value=status_str, inline=True)
        embed.add_field(
            name="💬 Chat Activity Rewards",
            value=f"{format_tad(w['total_activity_rewards'])} *(Lifetime)*",
            inline=False
        )
        embed.set_footer(text="Click 'Recent Transactions' bach tchouf akher 3amaliyat.")
        return embed

    async def get_transactions_embed(self, user: discord.Member) -> discord.Embed:
        w = await self.get_wallet(user.id)
        async with self.bot.db.execute(
            "SELECT amount, context, created_at FROM user_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 6",
            (user.id,)
        ) as cursor:
            rows = await cursor.fetchall()

        embed = discord.Embed(
            title=f"📜 Recent Transactions — {user.display_name}",
            color=0x000000
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        if not rows:
            embed.description = "*Walo transactions msjlin 7ta l daba.*"
        else:
            lines = []
            for amt, ctx_desc, ts in rows:
                sign = "🟢 +" if amt > 0 else "🔴 "
                lines.append(f"{sign}**{abs(amt):,}** {TAD_EMOJI} — *{ctx_desc}* (<t:{ts}:R>)")
            embed.description = "\n".join(lines)

        embed.add_field(
            name="💬 Passive Chat Mining",
            value=f"Total rbe7ti mn chat: {format_tad(w['total_activity_rewards'])}",
            inline=False
        )
        return embed

    # ============ USER COMMANDS ============

    @commands.command(name="wallet", aliases=["bstam", "money", "flous", "bztam", "balance", "bank", "cash"], help="Chouf ch7al 3ndek tlflous.")
    @not_fraud()
    async def wallet(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target = member or ctx.author
        embed = await self.get_wallet_embed(target)
        view = WalletView(target, ctx.author, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="daily", aliases=["day"], help="Ched chy baraka tlflous kola nhar.")
    @not_fraud()
    async def daily(self, ctx: commands.Context):
        now = int(time.time())
        user_id = ctx.author.id

        async with self.bot.db.execute(
            "SELECT last_daily, daily_streak FROM economy_cooldowns WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        last_daily = row[0] if row else 0
        streak = row[1] if row else 0

        # Cooldown: 24h = 86400s
        diff = now - last_daily
        if diff < 86400:
            remaining = 86400 - diff
            hours = remaining // 3600
            mins = (remaining % 3600) // 60
            await ctx.send(embed=discord.Embed(
                description=f"⏳ Mazal ma wsslat 24h 3la daily ta3k!\nRje3 mn hna **{hours}h {mins}m**.",
                color=0x000000
            ))
            return

        # Streak calculation (lost if > 48h)
        if diff <= 172800:
            streak = min(streak + 1, 7)
        else:
            streak = 1

        reward = 250 + (streak * 25)
        new_bal = await self.add_balance(user_id, reward, context=f"Daily Reward (Streak {streak}x)")

        await self.bot.db.execute(
            "INSERT INTO economy_cooldowns (user_id, last_daily, daily_streak) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_daily = ?, daily_streak = ?",
            (user_id, now, streak, now, streak)
        )
        await self.bot.db.commit()

        embed = discord.Embed(
            title="🎁 Daily Reward Claimed!",
            description=(
                f"🎉 Chediti {format_tad(reward)}!\n\n"
                f"🔥 **Daily Streak:** `{streak}/7` (Bonus: `+{streak*25} TAD`)\n"
                f"💰 **New Balance:** {format_tad(new_bal)}"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="weekly", aliases=["week"], help="Ched chy baraka ta3 lflous kola simana.")
    @not_fraud()
    async def weekly(self, ctx: commands.Context):
        now = int(time.time())
        user_id = ctx.author.id

        async with self.bot.db.execute(
            "SELECT last_weekly FROM economy_cooldowns WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        last_weekly = row[0] if row else 0
        diff = now - last_weekly
        # 7 days = 604800s
        if diff < 604800:
            remaining = 604800 - diff
            days = remaining // 86400
            hours = (remaining % 86400) // 3600
            await ctx.send(embed=discord.Embed(
                description=f"⏳ Mazal ma wssl weekly ta3k!\nRje3 mn hna **{days}d {hours}h**.",
                color=0x000000
            ))
            return

        reward = 1500
        new_bal = await self.add_balance(user_id, reward, context="Weekly Reward")

        await self.bot.db.execute(
            "INSERT INTO economy_cooldowns (user_id, last_weekly) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_weekly = ?",
            (user_id, now, now)
        )
        await self.bot.db.commit()

        embed = discord.Embed(
            title="👑 Weekly Reward Claimed!",
            description=(
                f"🎉 Chediti {format_tad(reward)}!\n\n"
                f"💰 **New Balance:** {format_tad(new_bal)}"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="pay", aliases=["transfer", "versi"], help="Sift flous l chy wa7d.")
    @not_fraud()
    async def pay(self, ctx: commands.Context, target: discord.Member, amount: int):
        if target.id == ctx.author.id:
            await ctx.send("❌ Ma ymkench tsift flous l rasek.")
            return
        if target.bot:
            await ctx.send("❌ Ma ymkench tsift flous l bots.")
            return
        if amount <= 0:
            await ctx.send("❌ Khassek tsift amount kber mn 0.")
            return

        author_wallet = await self.get_wallet(ctx.author.id)
        if author_wallet["balance"] < amount:
            await ctx.send(f"❌ Flousk makafyinch! Balance ta3k: {format_tad(author_wallet['balance'])}.")
            return

        target_wallet = await self.get_wallet(target.id)
        if target_wallet["is_fraud"] == 1:
            await ctx.send(f"❌ **{target.display_name}** 7sabo mbloqui ka Fraud, ma ymkench yst9bel flous.")
            return

        # 5% tax burn
        tax = round(amount * TAX_RATE)
        received = amount - tax

        await self.deduct_balance(ctx.author.id, amount, context=f"Sent to {target.display_name}")
        await self.add_balance(target.id, received, context=f"Received from {ctx.author.display_name}")

        embed = discord.Embed(
            title="💸 Payment Successful",
            description=(
                f"✅ **{ctx.author.mention}** sifti {format_tad(received)} l **{target.mention}**!\n\n"
                f"🔥 **5% Tax Burned:** `{tax:,}` {TAD_EMOJI} TAD"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    # ============ MODERATOR COMMANDS ============

    @commands.command(name="tax", help="[Admin] N9ess flous mn wallet dial user.")
    @commands.is_owner()
    async def tax_user(self, ctx: commands.Context, target: discord.Member, amount: int):
        if amount <= 0:
            await ctx.send("❌ Amount khas ykoun kber mn 0.")
            return

        await self.deduct_balance(target.id, amount, context=f"Taxed by Admin {ctx.author.display_name}")
        w = await self.get_wallet(target.id)

        embed = discord.Embed(
            title="🏛️ Economy Tax Applied",
            description=f"📉 N9ssna **{amount:,}** {TAD_EMOJI} TAD mn wallet dial **{target.mention}**.\nNew Balance: {format_tad(w['balance'])}",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="reward", help="[Admin] zid flous l wallet dial user.")
    @commands.is_owner()
    async def reward_user(self, ctx: commands.Context, target: discord.Member, amount: int):
        if amount <= 0:
            await ctx.send("❌ Amount khas ykoun kber mn 0.")
            return

        new_bal = await self.add_balance(target.id, amount, context=f"Admin Reward by {ctx.author.display_name}")

        embed = discord.Embed(
            title="🎁 Admin Reward Granted",
            description=f"📈 Zdna {format_tad(amount)} f wallet dial **{target.mention}**!\nNew Balance: {format_tad(new_bal)}",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="fraud", aliases=["nssab"], help="[Admin] Blocki user mn l economy system.")
    @commands.is_owner()
    async def fraud_user(self, ctx: commands.Context, target: discord.Member):
        await self.get_wallet(target.id)
        await self.bot.db.execute("UPDATE user_wallets SET is_fraud = 1 WHERE user_id = ?", (target.id,))
        await self.bot.db.commit()

        embed = discord.Embed(
            title="🚨 Economy Fraud Suspension",
            description=f"🔒 **{target.mention}** tmarka **Fraud**.\nWallet dialo tjmdat o ma 9adch ysta3mel l'economy wla ycharek f games.",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="legit", aliases=["n9i"], help="[Admin] Unblocki user mn l economy system.")
    @commands.is_owner()
    async def legit_user(self, ctx: commands.Context, target: discord.Member):
        await self.get_wallet(target.id)
        await self.bot.db.execute("UPDATE user_wallets SET is_fraud = 0 WHERE user_id = ?", (target.id,))
        await self.bot.db.commit()

        embed = discord.Embed(
            title="✅ Economy Standing Restored",
            description=f"🔓 **{target.mention}** rje3 **Legit**! Wallet dialo t7llat o progress dialo kaml b9a kifma kan.",
            color=0x000000
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Economy(bot))
