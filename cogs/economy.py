import time
import re
from typing import Optional, Tuple
import discord
from discord.ext import commands


TAD_EMOJI = "<:TAD:1543808845728710686>"
TAX_RATE = 0.05  # 5% anti-inflation transaction burn


def format_tad(amount: int) -> str:
    return f"**{amount:,}** {TAD_EMOJI} TAD"


def parse_bet_argument(*args, user_balance: Optional[int] = None) -> Tuple[Optional[int], list]:
    """
    Intelligently parses explicit or natural bet inputs:
    - Keywords: 'all', 'max', 'half', '50%'
    - Magnitudes: '5k' (5000), '2.5k' (2500), '1m' (1000000)
    - Prefixes/Suffixes: 'bet:500', 'b:250', '500tad', '500drhm'
    - Plain numbers: 500
    """
    remaining = []
    found_bet = None
    max_cap = 50000  # Safe cap for 'all' / 'max'

    pattern = re.compile(r"^(?:bet:|b:|bet=)?([0-9]+(?:\.[0-9]+)?)(k|m|mil|kilo|tad|t|drhm|drhem)?$", re.IGNORECASE)

    for arg in args:
        if arg is None:
            continue
        s_arg = str(arg).strip().lower()

        if found_bet is None:
            # Check keywords
            if s_arg in ("all", "max", "kolchi"):
                if user_balance is not None and user_balance > 0:
                    found_bet = min(user_balance, max_cap)
                else:
                    found_bet = max_cap
                continue
            elif s_arg in ("half", "ness", "50%"):
                if user_balance is not None and user_balance > 0:
                    found_bet = max(1, user_balance // 2)
                continue

            m = pattern.match(s_arg)
            if m:
                num_str, suffix = m.group(1), m.group(2)
                try:
                    num_val = float(num_str)
                    if suffix:
                        suffix = suffix.lower()
                        if suffix in ("k", "kilo"):
                            num_val *= 1000
                        elif suffix in ("m", "mil"):
                            num_val *= 1000000
                    
                    val = int(round(num_val))
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


class WalletsPaginationView(discord.ui.View):
    def __init__(self, author: discord.Member, pages: list):
        super().__init__(timeout=90)
        self.author = author
        self.pages = pages
        self.current_page = 0
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= len(self.pages) - 1)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l menu machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


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

    def is_bot_user(self, user_id: int) -> bool:
        u = self.bot.get_user(user_id)
        return bool(u and u.bot)

    async def get_wallet(self, user_id: int) -> dict:
        if self.is_bot_user(user_id):
            return {"balance": 0, "total_activity_rewards": 0, "is_fraud": 1}

        async with self.bot.db.execute(
            "SELECT balance, total_activity_rewards, is_fraud FROM user_wallets WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            # Default starting balance = 100 TAD
            await self.bot.db.execute(
                "INSERT INTO user_wallets (user_id, balance, total_activity_rewards, is_fraud) VALUES (?, 100, 0, 0) "
                "ON CONFLICT(user_id) DO NOTHING",
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
        if amount <= 0 or self.is_bot_user(user_id):
            w = await self.get_wallet(user_id)
            return w["balance"]

        # Ensure user wallet exists
        await self.get_wallet(user_id)

        if context == "chat_activity":
            await self.bot.db.execute(
                "UPDATE user_wallets SET balance = balance + ?, total_activity_rewards = total_activity_rewards + ? WHERE user_id = ?",
                (amount, amount, user_id)
            )
        else:
            await self.bot.db.execute(
                "UPDATE user_wallets SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id)
            )

        if context and context != "chat_activity":
            now_ts = int(time.time())
            await self.bot.db.execute(
                "INSERT INTO user_transactions (user_id, amount, context, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, context, now_ts)
            )

        await self.bot.db.commit()
        w = await self.get_wallet(user_id)
        return w["balance"]

    async def apply_tax_and_add_balance(self, user_id: int, gross_payout: int, context: str = "") -> Tuple[int, int]:
        """
        Applies 5% anti-inflation tax burn on gross payout, adds after-tax balance, and logs transaction.
        Returns: (net_payout, tax_burned)
        """
        if gross_payout <= 0 or self.is_bot_user(user_id):
            return 0, 0

        tax = round(gross_payout * TAX_RATE)
        net_payout = gross_payout - tax
        if net_payout <= 0 and gross_payout > 0:
            net_payout = 1
            tax = gross_payout - 1

        ctx_desc = f"{context} (Tax: {tax} TAD)" if context else f"Payout (Tax: {tax} TAD)"
        await self.add_balance(user_id, net_payout, context=ctx_desc)
        return net_payout, tax

    async def deduct_balance(self, user_id: int, amount: int, context: str = "") -> bool:
        if amount <= 0:
            return True
        if self.is_bot_user(user_id):
            return False

        # Ensure user wallet exists
        await self.get_wallet(user_id)

        cursor = await self.bot.db.execute(
            "UPDATE user_wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ? AND is_fraud = 0",
            (amount, user_id, amount)
        )
        if not cursor or (hasattr(cursor, "rowcount") and cursor.rowcount <= 0):
            return False

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
        status_str = "🔒 **Frozen (Fraud)**" if w["is_fraud"] == 1 else "🟢 **Active**"

        embed = discord.Embed(
            title=f"💼 Bstam ta3 {user.display_name}",
            color=0x000000
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Balance", value=format_tad(w['balance']), inline=True)
        embed.add_field(name="Status", value=status_str, inline=True)
        embed.add_field(
            name="Total Activity Rewards",
            value=f"{format_tad(w['total_activity_rewards'])}",
            inline=False
        )
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
            embed.description = "*No Transactions yet.*"
        else:
            lines = []
            for amt, ctx_desc, ts in rows:
                sign = "🟢 +" if amt > 0 else "🔴 -"
                lines.append(f"{sign}**{abs(amt):,}** TAD — *{ctx_desc}* (<t:{ts}:R>)")
            embed.description = "\n".join(lines)

        embed.add_field(
            name="💬 Passive Chat Mining",
            value=f"Total: {format_tad(w['total_activity_rewards'])}",
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

    @commands.command(name="wallets", aliases=["bstams", "topwallets", "richest", "baltop"], help="Chouf top 50 richest members f had server.")
    @not_fraud()
    async def wallets(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("❌ Had l command khedama ghir f servers.")
            return

        guild_member_ids = [m.id for m in ctx.guild.members if not m.bot]
        if not guild_member_ids:
            await ctx.send("❌ Ta wa7d ma l9inah f server.")
            return

        placeholders = ",".join("?" for _ in guild_member_ids)
        query = f"""
            SELECT user_id, balance FROM user_wallets 
            WHERE user_id IN ({placeholders}) AND is_fraud = 0 
            ORDER BY balance DESC LIMIT 50
        """
        async with self.bot.db.execute(query, tuple(guild_member_ids)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("❌ Ba9i ta 7sab ma mssjl f l'economy.")
            return

        chunk_size = 10
        chunks = [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]
        total_pages = len(chunks)

        embeds = []
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}

        for page_idx, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"💰 Richest Wallets — {ctx.guild.name}",
                color=0x000000
            )
            if ctx.guild.icon:
                embed.set_thumbnail(url=ctx.guild.icon.url)

            lines = []
            for rank_offset, (u_id, bal) in enumerate(chunk):
                overall_rank = page_idx * chunk_size + rank_offset + 1
                rank_badge = medals.get(overall_rank, f"`#{overall_rank}`")
                member = ctx.guild.get_member(u_id)
                member_str = member.mention if member else f"<@{u_id}>"
                lines.append(f"{rank_badge} {member_str} • {format_tad(bal)}")

            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Page {page_idx + 1}/{total_pages} • Top {len(rows)} members")
            embeds.append(embed)

        view = WalletsPaginationView(ctx.author, embeds)
        msg = await ctx.send(embed=embeds[0], view=view)
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
                description=f"⏳ Mazal ma dazt 24h 3la daily ta3k!\nRje3 mn hna **{hours}h {mins}m**.",
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
            title="🎁 Daily Reward Claimed",
            description=(
                f"Chediti {format_tad(reward)}!\n\n"
                f"🔥 **Streak:** `{streak}/7` (+{streak*25} TAD)\n"
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
            title="👑 Weekly Reward Claimed",
            description=(
                f"Chediti {format_tad(reward)}!\n\n"
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
                f"Sifti {format_tad(received)} l **{target.mention}**.\n\n"
                f"🔥 **5% Tax Burned:** `{tax:,}` TAD"
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
            description=f"N9ssna **{amount:,}** TAD mn wallet dial **{target.mention}**.\n💰 **New Balance:** {format_tad(w['balance'])}",
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
            description=f"Zdna {format_tad(amount)} f wallet dial **{target.mention}**!\n💰 **New Balance:** {format_tad(new_bal)}",
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
            description=f"🟢 **{target.mention}** rje3 **Legit**! Wallet dialo t7llat o progress dialo kaml b9a kifma kan.",
            color=0x000000
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Economy(bot))
