import time
import re
import json
import math
import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Union
import discord
from discord.ext import commands
from converters import FuzzyMember, AmountConverter


TAD_EMOJI = "<:TAD:1543808845728710686>"
TAX_RATE = 0.02  # 2% anti-inflation transaction burn
GLOBAL_BET_LIMIT = 50000  # Global limit for any gamble/wager
CASA_TZ = ZoneInfo("Africa/Casablanca")


def format_tad(amount: int) -> str:
    return f"**{amount:,}** {TAD_EMOJI} TAD"


def get_level_info(total_xp: int) -> Tuple[int, int, int, int]:
    """
    Curve A: Total XP(L) = 75 * (L - 1)^2
    Returns: (level, current_xp_in_level, xp_needed_for_next_level, base_xp_for_level)
    """
    total_xp = max(0, int(total_xp))
    lvl = 1 + int(math.isqrt(total_xp // 75))
    base_xp = 75 * ((lvl - 1) ** 2)
    next_xp = 75 * (lvl ** 2)
    needed = next_xp - base_xp
    current = total_xp - base_xp
    return lvl, current, needed, base_xp


def get_next_milestone_info(level: int) -> Tuple[int, int]:
    """Returns (next_milestone_level, reward_amount). Caps at 100,000 TAD at Level 100+."""
    next_lvl = ((level // 10) + 1) * 10
    reward = min(next_lvl * 1000, 100000)
    return next_lvl, reward


def get_current_week_start_ts() -> int:
    """Returns the Unix timestamp for the start of the current week (Monday 00:00 Casablanca time)."""
    now_casa = datetime.now(CASA_TZ)
    current_week_start = (now_casa - timedelta(days=now_casa.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(current_week_start.timestamp())


def get_next_week_start_ts() -> int:
    """Returns the Unix timestamp for the start of the next week (Next Monday 00:00 Casablanca time)."""
    now_casa = datetime.now(CASA_TZ)
    current_week_start = (now_casa - timedelta(days=now_casa.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    next_week_start = current_week_start + timedelta(days=7)
    return int(next_week_start.timestamp())


def parse_bet_argument(*args, user_balance: Optional[int] = None) -> Tuple[Optional[int], list]:
    """
    Intelligently parses explicit or natural bet inputs:
    - Keywords: 'all', 'max', 'half', '50%'
    - Magnitudes: '5k' (5000), '2.5k' (2500), '1m' (1000000)
    - Prefixes/Suffixes: 'bet:500', 'b:250', '500tad', '500drhm'
    - Plain numbers: 500
    Enforces GLOBAL_BET_LIMIT (50,000 TAD).
    """
    remaining = []
    found_bet = None
    max_cap = GLOBAL_BET_LIMIT

    pattern = re.compile(r"^(?:bet:|b:|bet=)?([0-9]+(?:\.[0-9]+)?)(k|m|mil|kilo|tad|t|drhm|drhem)?$", re.IGNORECASE)

    for arg in args:
        if arg is None:
            continue
        s_arg = str(arg).strip().lower().replace(",", "")

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
                    found_bet = min(max(1, user_balance // 2), max_cap)
                else:
                    found_bet = max_cap // 2
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

    if found_bet is not None:
        found_bet = min(found_bet, GLOBAL_BET_LIMIT)

    return found_bet, remaining


def calculate_pvp_payout(bet_per_player: int) -> Tuple[int, int, int]:
    """
    Returns (winner_payout, burned_amount, draw_split_per_player)
    Total pot = 2 * bet
    Tax burn = round(Total pot * 2%)
    Winner payout = Total pot - Tax burn
    Draw split = floor((Total pot - Tax burn) / 2) -> (each player recovers 98% of their stake)
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
                    "⚠️ Wallet ta3k mjemda 7it mssjl ka **Fraud**.\n"
                    "Mat9dch tsta3mel commands dial flous, t9emmer, wla tched chat rewards."
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return False
        return True
    return commands.check(predicate)


class LeaderboardFilterPaginationView(discord.ui.View):
    def __init__(self, author: Union[discord.Member, discord.User], server_pages: list, global_pages: list, initial_scope: str = "server"):
        super().__init__(timeout=90)
        self.author = author
        self.pages_dict = {
            "server": server_pages,
            "global": global_pages
        }
        self.scope = initial_scope if self.pages_dict.get(initial_scope) else "global"
        self.current_page = 0
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    @property
    def current_pages(self):
        return self.pages_dict.get(self.scope, [])

    def _update_buttons(self):
        pages = self.current_pages
        total = len(pages)
        self.prev_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= total - 1 or total <= 1)

        has_server = bool(self.pages_dict.get("server"))
        has_global = bool(self.pages_dict.get("global"))

        if self.scope == "server":
            self.toggle_scope_button.label = "🌐 Global"
            self.toggle_scope_button.style = discord.ButtonStyle.secondary
            self.toggle_scope_button.disabled = not has_global
        else:
            self.toggle_scope_button.label = "🏢 Server"
            self.toggle_scope_button.style = discord.ButtonStyle.primary
            self.toggle_scope_button.disabled = not has_server

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
            await interaction.response.edit_message(embed=self.current_pages[self.current_page], view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.current_pages) - 1:
            self.current_page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.current_pages[self.current_page], view=self)

    @discord.ui.button(label="🌐 Global", style=discord.ButtonStyle.secondary)
    async def toggle_scope_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.scope = "global" if self.scope == "server" else "server"
        self.current_page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.current_pages[self.current_page], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


WalletsPaginationView = LeaderboardFilterPaginationView


class WalletView(discord.ui.View):
    def __init__(self, target_user: Union[discord.Member, discord.User], author: Union[discord.Member, discord.User], cog):
        super().__init__(timeout=90)
        self.target_user = target_user
        self.author = author
        self.cog = cog
        self.current_page = "wallet"  # "wallet", "transactions", "summary"
        self.filter_mode = "all"  # "all", "plus", "minus"
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        if self.current_page == "wallet":
            btn_tx = discord.ui.Button(label="Recent Transactions", style=discord.ButtonStyle.secondary, emoji="📜")
            btn_tx.callback = self.show_transactions_callback
            self.add_item(btn_tx)

            btn_summary = discord.ui.Button(label="Financial Summary", style=discord.ButtonStyle.secondary, emoji="📊")
            btn_summary.callback = self.show_summary_callback
            self.add_item(btn_summary)

        elif self.current_page == "transactions":
            btn_wallet = discord.ui.Button(label="Wallet Overview", style=discord.ButtonStyle.primary, emoji="💼")
            btn_wallet.callback = self.show_wallet_callback
            self.add_item(btn_wallet)

            # 3-display toggle: default all, 2nd + (income), 3rd - (expense)
            if self.filter_mode == "all":
                btn_filter = discord.ui.Button(label="Filter: Recent", style=discord.ButtonStyle.secondary, emoji="🔄")
            elif self.filter_mode == "plus":
                btn_filter = discord.ui.Button(label="Filter: Added", style=discord.ButtonStyle.success, emoji="🟢")
            else:
                btn_filter = discord.ui.Button(label="Filter: Removed", style=discord.ButtonStyle.danger, emoji="🔴")
            btn_filter.callback = self.toggle_filter_callback
            self.add_item(btn_filter)

            btn_summary = discord.ui.Button(label="Financial Summary", style=discord.ButtonStyle.secondary, emoji="📊")
            btn_summary.callback = self.show_summary_callback
            self.add_item(btn_summary)

        elif self.current_page == "summary":
            btn_wallet = discord.ui.Button(label="Wallet Overview", style=discord.ButtonStyle.primary, emoji="💼")
            btn_wallet.callback = self.show_wallet_callback
            self.add_item(btn_wallet)

            btn_tx = discord.ui.Button(label="Recent Transactions", style=discord.ButtonStyle.secondary, emoji="📜")
            btn_tx.callback = self.show_transactions_callback
            self.add_item(btn_tx)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l bouton machi ta3k!", ephemeral=True)
            return False
        return True

    async def show_wallet_callback(self, interaction: discord.Interaction):
        self.current_page = "wallet"
        self._update_buttons()
        embed = await self.cog.get_wallet_embed(self.target_user)
        await interaction.response.edit_message(embed=embed, view=self)

    async def show_transactions_callback(self, interaction: discord.Interaction):
        self.current_page = "transactions"
        self.filter_mode = "all"
        self._update_buttons()
        embed = await self.cog.get_transactions_embed(self.target_user, self.filter_mode)
        await interaction.response.edit_message(embed=embed, view=self)

    async def show_summary_callback(self, interaction: discord.Interaction):
        self.current_page = "summary"
        self._update_buttons()
        embed = await self.cog.get_wallet_summary_embed(self.target_user)
        await interaction.response.edit_message(embed=embed, view=self)

    async def toggle_filter_callback(self, interaction: discord.Interaction):
        modes = ["all", "plus", "minus"]
        curr_idx = modes.index(self.filter_mode)
        self.filter_mode = modes[(curr_idx + 1) % len(modes)]
        self._update_buttons()
        embed = await self.cog.get_transactions_embed(self.target_user, self.filter_mode)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class VaultView(discord.ui.View):
    def __init__(self, vault_name: str, author: Union[discord.Member, discord.User], cog):
        super().__init__(timeout=90)
        self.vault_name = vault_name
        self.author = author
        self.cog = cog
        self.showing_transactions = False
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        if not self.showing_transactions:
            label = "Recent Tax Inflows" if self.vault_name == "bank" else "Recent Table Collections"
            btn_tx = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, emoji="📜")
            btn_tx.callback = self.toggle_view_callback
            self.add_item(btn_tx)
        else:
            btn_back = discord.ui.Button(label="Back to Overview", style=discord.ButtonStyle.primary, emoji="🔙")
            btn_back.callback = self.toggle_view_callback
            self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l bouton machi ta3k!", ephemeral=True)
            return False
        return True

    async def toggle_view_callback(self, interaction: discord.Interaction):
        self.showing_transactions = not self.showing_transactions
        self._update_buttons()
        if self.showing_transactions:
            embed = await self.cog.get_vault_transactions_embed(self.vault_name)
        else:
            embed = await self.cog.get_vault_embed(self.vault_name)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


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

        if context in ("chat_activity", "vc_activity"):
            await self.bot.db.execute(
                "UPDATE user_wallets SET balance = balance + ?, total_activity_rewards = total_activity_rewards + ? WHERE user_id = ?",
                (amount, amount, user_id)
            )
        else:
            await self.bot.db.execute(
                "UPDATE user_wallets SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id)
            )

        if context and context not in ("chat_activity", "vc_activity"):
            now_ts = int(time.time())
            await self.bot.db.execute(
                "INSERT INTO user_transactions (user_id, amount, context, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, context, now_ts)
            )

        await self.bot.db.commit()
        w = await self.get_wallet(user_id)
        return w["balance"]

    # ============ LEVELING DATABASE METHODS ============

    async def get_user_level(self, user_id: int) -> dict:
        if self.is_bot_user(user_id):
            return {
                "user_id": user_id, "level": 1, "current_xp": 0, "xp_needed": 75,
                "total_xp": 0, "rank": 0, "last_chat_xp": 0, "last_vc_xp": 0, "claimed_milestones": []
            }

        async with self.bot.db.execute(
            "SELECT level, current_xp, total_xp, last_chat_xp, last_vc_xp, claimed_milestones "
            "FROM user_levels WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return {
                "user_id": user_id, "level": 1, "current_xp": 0, "xp_needed": 75,
                "total_xp": 0, "rank": 0, "last_chat_xp": 0, "last_vc_xp": 0, "claimed_milestones": []
            }

        level, current_xp, total_xp, last_chat, last_vc, claimed_json = row
        try:
            claimed = json.loads(claimed_json) if claimed_json else []
        except Exception:
            claimed = []

        lvl, curr, needed, _ = get_level_info(total_xp)

        # Global rank calculation
        rank = 0
        if total_xp > 0:
            async with self.bot.db.execute(
                "SELECT COUNT(*) + 1 FROM user_levels WHERE total_xp > ?",
                (total_xp,)
            ) as cursor:
                rank_row = await cursor.fetchone()
                rank = rank_row[0] if rank_row else 1

        return {
            "user_id": user_id,
            "level": lvl,
            "current_xp": curr,
            "xp_needed": needed,
            "total_xp": total_xp,
            "rank": rank,
            "last_chat_xp": last_chat or 0,
            "last_vc_xp": last_vc or 0,
            "claimed_milestones": claimed
        }

    async def add_xp(self, user_id: int, xp_amount: int, channel: Optional[discord.TextChannel] = None, message: Optional[discord.Message] = None) -> dict:
        if xp_amount <= 0 or self.is_bot_user(user_id):
            return await self.get_user_level(user_id)

        user_data = await self.get_user_level(user_id)
        old_level = user_data["level"]
        new_total_xp = user_data["total_xp"] + xp_amount
        new_lvl, new_curr, new_needed, _ = get_level_info(new_total_xp)

        claimed = list(user_data["claimed_milestones"])
        milestones_awarded = []

        if new_lvl > old_level:
            # Check every milestone passed between old_level and new_lvl
            for m in range(10, new_lvl + 1, 10):
                if m > old_level and m not in claimed:
                    claimed.append(m)
                    milestone_reward = min(m * 1000, 100000)
                    await self.add_balance(user_id, milestone_reward, context=f"Level {m} Milestone Reward")
                    milestones_awarded.append((m, milestone_reward))

        claimed_json = json.dumps(claimed)

        await self.bot.db.execute(
            "INSERT INTO user_levels (user_id, level, current_xp, total_xp, claimed_milestones) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET level = ?, current_xp = ?, total_xp = ?, claimed_milestones = ?",
            (user_id, new_lvl, new_curr, new_total_xp, claimed_json,
             new_lvl, new_curr, new_total_xp, claimed_json)
        )
        await self.bot.db.commit()

        # Announce ONLY milestones (standard level-ups are silent!)
        if milestones_awarded and (channel or message):
            try:
                for m_lvl, m_rew in milestones_awarded:
                    user_name = None
                    if message and hasattr(message, "author") and message.author.id == user_id:
                        user_name = message.author.display_name
                    else:
                        u = self.bot.get_user(user_id)
                        user_name = u.display_name if u else "Player"

                    embed = discord.Embed(
                        title=f"Mbrok a {user_name}",
                        description=f"Wselti level {m_lvl}! Chediti `{m_rew:,}` TAD {TAD_EMOJI}",
                        color=0x000000
                    )

                    if message:
                        try:
                            await message.reply(embed=embed, mention_author=True)
                            continue
                        except Exception:
                            pass

                    target_channel = channel or (message.channel if message else None)
                    if target_channel:
                        await target_channel.send(embed=embed)
            except Exception:
                pass

        user_data["level"] = new_lvl
        user_data["current_xp"] = new_curr
        user_data["xp_needed"] = new_needed
        user_data["total_xp"] = new_total_xp
        user_data["claimed_milestones"] = claimed
        return user_data

    async def remove_xp(self, user_id: int, xp_amount: int) -> dict:
        if xp_amount <= 0 or self.is_bot_user(user_id):
            return await self.get_user_level(user_id)

        user_data = await self.get_user_level(user_id)
        new_total_xp = max(0, user_data["total_xp"] - xp_amount)
        new_lvl, new_curr, new_needed, _ = get_level_info(new_total_xp)

        await self.bot.db.execute(
            "UPDATE user_levels SET level = ?, current_xp = ?, total_xp = ? WHERE user_id = ?",
            (new_lvl, new_curr, new_total_xp, user_id)
        )
        await self.bot.db.commit()
        return await self.get_user_level(user_id)

    async def deposit_vault(self, vault_name: str, amount: int, source: str = "", context: str = "") -> int:
        if amount <= 0:
            v = await self.get_vault(vault_name)
            return v["balance"]

        now_ts = int(time.time())
        await self.bot.db.execute(
            "INSERT INTO economy_vaults (vault_name, balance, total_collected, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vault_name) DO UPDATE SET balance = balance + ?, total_collected = total_collected + ?, updated_at = ?",
            (vault_name, amount, amount, now_ts, amount, amount, now_ts)
        )
        await self.bot.db.execute(
            "INSERT INTO vault_transactions (vault_name, amount, source, context, created_at) VALUES (?, ?, ?, ?, ?)",
            (vault_name, amount, source or vault_name, context, now_ts)
        )
        await self.bot.db.commit()
        v = await self.get_vault(vault_name)
        return v["balance"]

    async def get_vault(self, vault_name: str) -> dict:
        async with self.bot.db.execute(
            "SELECT balance, total_collected, updated_at FROM economy_vaults WHERE vault_name = ?",
            (vault_name,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            now_ts = int(time.time())
            await self.bot.db.execute(
                "INSERT INTO economy_vaults (vault_name, balance, total_collected, updated_at) VALUES (?, 0, 0, ?) "
                "ON CONFLICT(vault_name) DO NOTHING",
                (vault_name, now_ts)
            )
            await self.bot.db.commit()
            return {"balance": 0, "total_collected": 0, "updated_at": now_ts}

        return {
            "balance": int(row[0]),
            "total_collected": int(row[1]),
            "updated_at": int(row[2])
        }

    async def get_vault_transactions(self, vault_name: str, limit: int = 6) -> list:
        async with self.bot.db.execute(
            "SELECT amount, source, context, created_at FROM vault_transactions WHERE vault_name = ? ORDER BY created_at DESC LIMIT ?",
            (vault_name, limit)
        ) as cursor:
            rows = await cursor.fetchall()
        return rows

    async def apply_lost_gamble_tax(self, bet: int, context: str = "Gamble Loss") -> int:
        """
        Applies tax on lost gambles: the player already lost the bet.
        Takes 2% total tax (1% sent to casino vault, 1% sent to bank vault).
        Returns the total tax amount.
        """
        if bet <= 0:
            return 0
        casino_tax = round(bet * 0.01)
        bank_tax = round(bet * 0.01)
        if bet >= 50:
            if casino_tax == 0:
                casino_tax = 1
            if bank_tax == 0:
                bank_tax = 1

        if casino_tax > 0:
            await self.deposit_vault("casino", casino_tax, source="gamble_loss", context=context)
        if bank_tax > 0:
            await self.deposit_vault("bank", bank_tax, source="gamble_loss", context=context)
        return casino_tax + bank_tax

    async def apply_tax_and_add_balance(self, user_id: int, gross_payout: int, context: str = "", vault: str = "bank") -> Tuple[int, int]:
        """
        Applies 2% tax on gross payout, adds after-tax balance to user, deposits tax to vault, and logs transaction.
        For gambling wins (vault == 'casino'), the 2% tax is split 50/50 between casino and central bank.
        For non-gambling rewards (vault == 'bank'), the 2% tax goes to central bank.
        Returns: (net_payout, tax)
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

        if tax > 0:
            if vault == "casino":
                casino_tax = tax // 2
                bank_tax = tax - casino_tax
                if casino_tax > 0:
                    await self.deposit_vault("casino", casino_tax, source="casino", context=context)
                if bank_tax > 0:
                    await self.deposit_vault("bank", bank_tax, source="casino_split", context=context)
            else:
                await self.deposit_vault(vault, tax, source=vault, context=context)

        return net_payout, tax

    async def deduct_balance(self, user_id: int, amount: int, context: str = "", force: bool = False) -> bool:
        if amount <= 0:
            return True
        if self.is_bot_user(user_id):
            return False

        # Ensure user wallet exists
        await self.get_wallet(user_id)

        fraud_clause = "" if force else "AND is_fraud = 0"
        cursor = await self.bot.db.execute(
            f"UPDATE user_wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ? {fraud_clause}",
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

    async def get_wallet_embed(self, user: Union[discord.Member, discord.User]) -> discord.Embed:
        w = await self.get_wallet(user.id)
        status_str = "🔒 **Frozen (Fraud)**" if w["is_fraud"] == 1 else "🟢 **Active**"

        # Check daily and weekly cooldown status
        async with self.bot.db.execute(
            "SELECT last_daily, daily_streak, last_weekly FROM economy_cooldowns WHERE user_id = ?",
            (user.id,)
        ) as cursor:
            cd_row = await cursor.fetchone()

        last_daily = cd_row[0] if cd_row and cd_row[0] else 0
        daily_streak = cd_row[1] if cd_row and cd_row[1] else 0
        last_weekly = cd_row[2] if cd_row and cd_row[2] else 0

        now_ts = int(time.time())
        now_casa = datetime.now(CASA_TZ)
        today_date = now_casa.date()

        if last_daily:
            last_daily_date = datetime.fromtimestamp(last_daily, tz=CASA_TZ).date()
            daily_claimed = (last_daily_date == today_date)
        else:
            daily_claimed = False

        next_midnight = (now_casa + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        next_midnight_ts = int(next_midnight.timestamp())

        if not daily_claimed:
            daily_val = f"✅ Available to claim\n🔥 Streak: `{daily_streak}/7`"
        else:
            daily_val = f"⏳ Resets <t:{next_midnight_ts}:R>\n🔥 Streak: `{daily_streak}/7`"

        current_week_start_ts = get_current_week_start_ts()
        next_week_start_ts = get_next_week_start_ts()

        if not last_weekly or last_weekly < current_week_start_ts:
            weekly_val = "✅ Available to claim"
        else:
            weekly_val = f"⏳ Resets <t:{next_week_start_ts}:R>"

        embed = discord.Embed(
            title=f"💼 Bstam ta3 {user.display_name}",
            color=0x000000
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Balance", value=format_tad(w['balance']), inline=True)
        embed.add_field(name="Status", value=status_str, inline=True)
        embed.add_field(name="Daily Reward", value=daily_val, inline=False)
        embed.add_field(name="Weekly Reward", value=weekly_val, inline=False)
        embed.set_footer(text=f"Wallet Overview • Page 1/3 • {user.display_name}")
        return embed

    async def get_transactions_embed(self, user: Union[discord.Member, discord.User], filter_mode: str = "all") -> discord.Embed:
        if filter_mode == "plus":
            query = "SELECT amount, context, created_at FROM user_transactions WHERE user_id = ? AND amount > 0 ORDER BY created_at DESC LIMIT 10"
            title = f"📈 Income Transactions — {user.display_name}"
            empty_msg = "*No income transactions yet.*"
        elif filter_mode == "minus":
            query = "SELECT amount, context, created_at FROM user_transactions WHERE user_id = ? AND amount < 0 ORDER BY created_at DESC LIMIT 10"
            title = f"📉 Expense Transactions — {user.display_name}"
            empty_msg = "*No expense transactions yet.*"
        else:
            query = "SELECT amount, context, created_at FROM user_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 10"
            title = f"📜 Recent Transactions — {user.display_name}"
            empty_msg = "*No transactions yet.*"

        async with self.bot.db.execute(query, (user.id,)) as cursor:
            rows = await cursor.fetchall()

        embed = discord.Embed(
            title=title,
            color=0x000000
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        if not rows:
            embed.description = empty_msg
        else:
            lines = []
            for amt, ctx_desc, ts in rows:
                sign = "🟢 +" if amt > 0 else "🔴 -"
                lines.append(f"{sign}**{abs(amt):,}** TAD — *{ctx_desc}* (<t:{ts}:R>)")
            embed.description = "\n".join(lines)

        embed.set_footer(text=f"Recent Transactions (Latest 10) • Page 2/3 • {user.display_name}")
        return embed

    async def get_wallet_summary_embed(self, user: Union[discord.Member, discord.User]) -> discord.Embed:
        w = await self.get_wallet(user.id)

        query = """
            SELECT 
                COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END), 0)
            FROM user_transactions
            WHERE user_id = ?
        """
        async with self.bot.db.execute(query, (user.id,)) as cursor:
            row = await cursor.fetchone()

        total_income = row[0] if row else 0
        total_expense = row[1] if row else 0

        embed = discord.Embed(
            title=f"📊 Financial Summary — {user.display_name}",
            color=0x000000
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="📈 Total Income", value=f"🟢 **+{total_income:,}** TAD", inline=False)
        embed.add_field(name="📉 Total Expense", value=f"🔴 **-{total_expense:,}** TAD", inline=False)
        embed.add_field(name="💬 Total Activity Rewards", value=format_tad(w['total_activity_rewards']), inline=False)

        embed.set_footer(text=f"Financial Summary • Page 3/3 • {user.display_name}")
        return embed

    async def get_vault_embed(self, vault_name: str) -> discord.Embed:
        v = await self.get_vault(vault_name)
        if vault_name == "bank":
            embed = discord.Embed(
                title="🏛️ Central Bank",
                description="Lkhazina l3amma hh.",
                color=0x000000)
            embed.add_field(name="Total Reserves", value=f"💰 {format_tad(v['balance'])}", inline=False)
            embed.add_field(name="Total Taxes Collected", value=f"📈 {format_tad(v['total_collected'])}", inline=False)
        else:
            embed = discord.Embed(
                title="🎰 Royal Casino Vault",
                description="Lkhzna tlcasino.",
                color=0x000000)
            embed.add_field(name="Vault Balance", value=f"💰 {format_tad(v['balance'])}", inline=False)
            embed.add_field(name="Total gambling Taxes", value=f"📈 {format_tad(v['total_collected'])}", inline=False)
        return embed

    async def get_vault_transactions_embed(self, vault_name: str) -> discord.Embed:
        v = await self.get_vault(vault_name)
        rows = await self.get_vault_transactions(vault_name, limit=6)

        if vault_name == "bank":
            title = "🏛️ Central Bank — Recent Inflow Receipts"
            color = 0xD4AF37
        else:
            title = "🎰 Royal Casino — Recent Table Tax Receipts"
            color = 0x2E0854

        embed = discord.Embed(title=title, color=color)
        embed.description = f"💰 **Current Balance:** {format_tad(v['balance'])}\n\n"

        if not rows:
            embed.description += "*Ba9i ta tax receipt ma tsjjlat hna.*"
        else:
            lines = []
            for amt, src, ctx_desc, ts in rows:
                sign = "🟢 +"
                lines.append(f"{sign}**{amt:,}** TAD — *{ctx_desc or src}* (<t:{ts}:R>)")
            embed.description += "\n".join(lines)

        embed.set_footer(text="Global Audit Log • Real-time tax inflow history")
        return embed

    # ============ USER COMMANDS ============

    @commands.command(name="bank", aliases=["banka", "centralbank", "treasury"], help="Lkhazina l3amma hh.")
    @not_fraud()
    async def bank_cmd(self, ctx: commands.Context):
        embed = await self.get_vault_embed("bank")
        view = VaultView("bank", ctx.author, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="casino", help="Lkhzna ta3 lcasino.")
    @not_fraud()
    async def casino_cmd(self, ctx: commands.Context):
        embed = await self.get_vault_embed("casino")
        view = VaultView("casino", ctx.author, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="wallet", aliases=["bstam", "money", "flous", "bztam", "balance", "bal", "cash", "wal"], help="Chouf ch7al 3ndek tlflous.")
    @not_fraud()
    async def wallet(self, ctx: commands.Context, member: Optional[FuzzyMember] = None):
        target = member or ctx.author
        embed = await self.get_wallet_embed(target)
        view = WalletView(target, ctx.author, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="wallets", aliases=["bsatm", "bzatm", "rich", "richest"], help="Chouf tertib tl flous ta3 bnadm.")
    @not_fraud()
    async def wallets(self, ctx: commands.Context):
        async with self.bot.db.execute(
            "SELECT user_id, balance FROM user_wallets WHERE is_fraud = 0 ORDER BY balance DESC LIMIT 250"
        ) as cursor:
            all_rows = await cursor.fetchall()

        if not all_rows:
            await ctx.send("❌ Ba9i ta 7sab ma mssjl f l'economy.")
            return

        guild_member_ids = set(m.id for m in ctx.guild.members if not m.bot) if ctx.guild else set()
        server_rows = [r for r in all_rows if r[0] in guild_member_ids][:50] if ctx.guild else []
        global_rows = all_rows[:50]

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        chunk_size = 10

        def _build_embeds(rows_list, is_server: bool):
            if not rows_list:
                return []
            chunks = [rows_list[i:i + chunk_size] for i in range(0, len(rows_list), chunk_size)]
            embeds = []
            total_pages = len(chunks)
            title = f"💰 Richest Wallets — {ctx.guild.name}" if (is_server and ctx.guild) else "💰 Richest Wallets — Global"
            scope_name = "members" if is_server else "global"

            for page_idx, chunk in enumerate(chunks):
                embed = discord.Embed(title=title, color=0x000000)
                if is_server and ctx.guild and ctx.guild.icon:
                    embed.set_thumbnail(url=ctx.guild.icon.url)

                lines = []
                for rank_offset, (u_id, bal) in enumerate(chunk):
                    overall_rank = page_idx * chunk_size + rank_offset + 1
                    rank_badge = medals.get(overall_rank, f"`#{overall_rank}`")
                    member = ctx.guild.get_member(u_id) if ctx.guild else None
                    if member:
                        member_str = member.mention
                    else:
                        u = self.bot.get_user(u_id)
                        member_str = f"**{u.name}**" if u else f"<@{u_id}>"
                    lines.append(f"{rank_badge} {member_str} • {format_tad(bal)}")

                embed.description = "\n".join(lines)
                embed.set_footer(text=f"Page {page_idx + 1}/{total_pages} • Top {len(rows_list)} {scope_name}")
                embeds.append(embed)
            return embeds

        server_embeds = _build_embeds(server_rows, is_server=True)
        global_embeds = _build_embeds(global_rows, is_server=False)

        initial_scope = "server" if server_embeds else "global"
        view = LeaderboardFilterPaginationView(
            ctx.author,
            server_pages=server_embeds,
            global_pages=global_embeds,
            initial_scope=initial_scope
        )
        msg = await ctx.send(embed=view.current_pages[0], view=view)
        view.message = msg

    @commands.command(name="daily", aliases=["day"], help="Ched chy baraka tlflous kola nhar.")
    @not_fraud()
    async def daily(self, ctx: commands.Context):
        now = int(time.time())
        now_casa = datetime.now(CASA_TZ)
        today_date = now_casa.date()
        user_id = ctx.author.id

        async with self.bot.db.execute(
            "SELECT last_daily, daily_streak FROM economy_cooldowns WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        last_daily = row[0] if row else 0
        streak = row[1] if row else 0

        # Cooldown check: resets for all users at 00:00 Casablanca timezone
        if last_daily:
            last_daily_date = datetime.fromtimestamp(last_daily, tz=CASA_TZ).date()
            if last_daily_date == today_date:
                next_midnight = (now_casa + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                next_midnight_ts = int(next_midnight.timestamp())
                await ctx.send(embed=discord.Embed(
                    description=f"⏳ Deja chditi daily lyouma.\nRje3 <t:{next_midnight_ts}:R>.",
                    color=0x000000
                ))
                return

            # Streak calculation: streak maintained if claimed yesterday
            yesterday_date = today_date - timedelta(days=1)
            if last_daily_date == yesterday_date:
                if streak >= 7:
                    streak = 1
                else:
                    streak += 1
            else:
                streak = 1
        else:
            streak = 1

        streak_bonus = (streak - 1) * 250
        reward = 1000 + streak_bonus
        new_bal = await self.add_balance(user_id, reward, context=f"Daily Reward (Streak {streak}x)")
        await self.add_xp(user_id, 50, channel=ctx.channel, message=ctx.message)

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
                f"🔥 **Streak:** `{streak}/7` (+{streak_bonus} TAD)\n"
                f"⚡ **Level XP:** `+50 XP`\n"
                f"💰 **New Balance:** {format_tad(new_bal)}"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="weekly", aliases=["week"], help="Ched chy baraka ta3 lflous kola simana.")
    @not_fraud()
    async def weekly(self, ctx: commands.Context):
        now = int(time.time())
        now_casa = datetime.now(CASA_TZ)
        user_id = ctx.author.id

        async with self.bot.db.execute(
            "SELECT last_weekly FROM economy_cooldowns WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        last_weekly = row[0] if row else 0

        # Cooldown check: resets for everyone at Sunday midnight (00:00 Monday) Casablanca timezone
        current_week_start_ts = get_current_week_start_ts()
        next_week_start_ts = get_next_week_start_ts()

        if last_weekly and last_weekly >= current_week_start_ts:
            await ctx.send(embed=discord.Embed(
                description=f"⏳ Deja chditi weekly ta3 had simana.\nRje3 <t:{next_week_start_ts}:R>.",
                color=0x000000
            ))
            return

        reward = 5000
        new_bal = await self.add_balance(user_id, reward, context="Weekly Reward")
        await self.add_xp(user_id, 250, channel=ctx.channel, message=ctx.message)

        await self.bot.db.execute(
            "INSERT INTO economy_cooldowns (user_id, last_weekly) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_weekly = ?",
            (user_id, now, now)
        )
        await self.bot.db.commit()

        embed = discord.Embed(
            title="🎁 Weekly Reward Claimed",
            description=(
                f"Chediti {format_tad(reward)}!\n\n"
                f"⚡ **Level XP:** `+250 XP`\n"
                f"💰 **New Balance:** {format_tad(new_bal)}"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="pay", aliases=["transfer", "versi"], help="Sift flous l chy wa7d (e.g. sat pay @User 50k wla sat pay 50k @User).")
    @not_fraud()
    async def pay(self, ctx: commands.Context, arg1: str, arg2: str):
        amt_conv = AmountConverter()
        fuzzy_conv = FuzzyMember()

        target = None
        amount = None

        # Attempt 1: arg1 is target, arg2 is amount (e.g. sat pay @User 50k)
        try:
            target = await fuzzy_conv.convert(ctx, arg1)
            amount = await amt_conv.convert(ctx, arg2)
        except Exception:
            pass

        # Attempt 2: arg1 is amount, arg2 is target (e.g. sat pay 50k @User)
        if target is None or amount is None:
            try:
                amount = await amt_conv.convert(ctx, arg1)
                target = await fuzzy_conv.convert(ctx, arg2)
            except Exception:
                pass

        if target is None or amount is None:
            await ctx.send("❌ Format ghalat. Kteb: `sat pay @User 50k` wla `sat pay 50k @User`.")
            return

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

        # 2% tax burn
        tax = round(amount * TAX_RATE)
        received = amount - tax

        await self.deduct_balance(ctx.author.id, amount, context=f"Sent to {target.display_name}")
        await self.add_balance(target.id, received, context=f"Received from {ctx.author.display_name}")
        if tax > 0:
            await self.deposit_vault("bank", tax, source="pay", context=f"Transfer: {ctx.author.name} -> {target.name}")

        embed = discord.Embed(
            title="💸 Payment Successful",
            description=(
                f"Sifti {format_tad(received)} l **{target.mention}**.\n\n"
                f"**2% Tax:** `{tax:,}` TAD"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    # ============ MODERATOR COMMANDS ============

    @commands.command(name="removetad", aliases=["tax"], help="N9ess flous mn wallet dial chy user .")
    @commands.is_owner()
    async def tax_user(self, ctx: commands.Context, target: FuzzyMember, amount: AmountConverter):
        if amount <= 0:
            await ctx.send("❌ Amount khas ykoun kber mn 0.")
            return

        w = await self.get_wallet(target.id)
        if w["balance"] <= 0:
            await ctx.send(f"⚠️ Wallet dial **{target.mention}** aslan fiha 0 TAD.")
            return

        deduct_amt = min(w["balance"], amount)
        await self.deduct_balance(target.id, deduct_amt, context=f"Taxed by Admin {ctx.author.display_name}", force=True)
        if deduct_amt > 0:
            await self.deposit_vault("bank", deduct_amt, source="admin_tax", context=f"Sanction on {target.name}")
        w_after = await self.get_wallet(target.id)

        embed = discord.Embed(
            title="🏛️ Economy Tax Applied",
            description=(
                f"N9ssna **{deduct_amt:,}** TAD mn wallet dial **{target.mention}** o tsiftat l **Central Bank**.\n"
                f"💰 **New Balance:** {format_tad(w_after['balance'])}"
            ),
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="setvault", help="Beddel balance dial bank wla casino (Owner only).")
    @commands.is_owner()
    async def set_vault_cmd(self, ctx: commands.Context, vault_name: str, amount: AmountConverter):
        v_name = vault_name.lower()
        if v_name not in ("bank", "casino"):
            await ctx.send("❌ Khtar `bank` wla `casino`.")
            return
        if amount < 0:
            await ctx.send("❌ Amount khas ykoun >= 0.")
            return
        now_ts = int(time.time())
        await self.bot.db.execute(
            "UPDATE economy_vaults SET balance = ?, updated_at = ? WHERE vault_name = ?",
            (amount, now_ts, v_name)
        )
        await self.bot.db.commit()
        await ctx.send(f"✅ Balance dial **{v_name.capitalize()}** tbeddel l: {format_tad(amount)}")

    @commands.command(name="addtad", aliases=["reward"], help="Zid flous l wallet dial chy user.")
    @commands.is_owner()
    async def reward_user(self, ctx: commands.Context, target: FuzzyMember, amount: AmountConverter):
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

    @commands.command(name="addxp", help="Zid XP l chy user.")
    @commands.is_owner()
    async def add_xp_cmd(self, ctx: commands.Context, target: FuzzyMember, amount: int):
        if amount <= 0:
            await ctx.send("❌ Amount khas ykoun kber mn 0.")
            return
        data = await self.add_xp(target.id, amount, channel=ctx.channel, message=ctx.message)
        await ctx.send(
            f"✅ Zdna **{amount:,} XP** l **{target.mention}**!\n"
            f"⭐ **New Level:** {data['level']} (`{data['current_xp']:,}/{data['xp_needed']:,} XP` • Total: `{data['total_xp']:,} XP`)"
        )

    @commands.command(name="removexp", help="N9ess XP l chy user.")
    @commands.is_owner()
    async def remove_xp_cmd(self, ctx: commands.Context, target: FuzzyMember, amount: int):
        if amount <= 0:
            await ctx.send("❌ Amount khas ykoun kber mn 0.")
            return
        data = await self.remove_xp(target.id, amount)
        await ctx.send(
            f"✅ N9ssna **{amount:,} XP** mn **{target.mention}**.\n"
            f"⭐ **New Level:** {data['level']} (`{data['current_xp']:,}/{data['xp_needed']:,} XP` • Total: `{data['total_xp']:,} XP`)"
        )

    # ============ USER LEVELING COMMANDS ============

    @commands.command(name="rank", aliases=["level", "lvl"], help="Chouf level ta3k wla ta3 chy user.")
    async def rank_cmd(self, ctx: commands.Context, target: Optional[FuzzyMember] = None):
        user = target or ctx.author
        user_data = await self.get_user_level(user.id)
        next_lvl, next_rew = get_next_milestone_info(user_data["level"])

        avatar_bytes = None
        if user.display_avatar:
            try:
                # 128px avatar maintains razor sharpness while keeping RAM strictly minimal
                avatar_bytes = await user.display_avatar.with_format("png").with_size(128).read()
            except Exception:
                avatar_bytes = None

        from cogs.leveling_render import render_level_card
        buf = await asyncio.to_thread(
            render_level_card,
            username=user.display_name,
            level=user_data["level"],
            current_xp=user_data["current_xp"],
            xp_needed=user_data["xp_needed"],
            total_xp=user_data["total_xp"],
            rank=user_data["rank"],
            avatar_bytes=avatar_bytes,
            next_milestone_level=next_lvl,
            next_milestone_reward=next_rew
        )

        file = discord.File(buf, filename="rank.png")
        embed = discord.Embed(
            title=f"Rank card ta3 {user.display_name}",
            color=0x000000
        )
        embed.set_image(url="attachment://rank.png")
        embed.set_footer(text=f"#{user_data['rank']} - Level {user_data['level']}")
        await ctx.send(embed=embed, file=file)

    @commands.command(name="levels", aliases=["ranks", "lvls"], help="Chouf tertib t levels ta3 bnadm.")
    async def levels_leaderboard(self, ctx: commands.Context):
        async with self.bot.db.execute(
            "SELECT user_id, level, total_xp FROM user_levels WHERE total_xp > 0 ORDER BY level DESC, total_xp DESC LIMIT 250"
        ) as cursor:
            all_rows = await cursor.fetchall()

        if not all_rows:
            await ctx.send(embed=discord.Embed(
                title="🏆 Sifdine Level Leaderboard",
                description="✨ Mazal 7ta wa7d mabda y leveli up! Bda thder f Chat o VC bach tkoun #1.",
                color=0x000000
            ))
            return

        guild_member_ids = set(m.id for m in ctx.guild.members if not m.bot) if ctx.guild else set()
        server_rows = [r for r in all_rows if r[0] in guild_member_ids][:50] if ctx.guild else []
        global_rows = all_rows[:50]

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        chunk_size = 10

        def _build_embeds(rows_list, is_server: bool):
            if not rows_list:
                return []
            chunks = [rows_list[i:i + chunk_size] for i in range(0, len(rows_list), chunk_size)]
            embeds = []
            total_pages = len(chunks)
            title = f"🏆 Levels Leaderboard — {ctx.guild.name}" if (is_server and ctx.guild) else "🏆 Global Levels Leaderboard"
            scope_name = "members" if is_server else "global"

            for page_idx, chunk in enumerate(chunks):
                embed = discord.Embed(title=title, color=0x000000)
                if is_server and ctx.guild and ctx.guild.icon:
                    embed.set_thumbnail(url=ctx.guild.icon.url)

                lines = []
                for rank_offset, (u_id, lvl, txp) in enumerate(chunk):
                    overall_rank = page_idx * chunk_size + rank_offset + 1
                    medal = medals.get(overall_rank, f"`#{overall_rank:02d}`")
                    member = ctx.guild.get_member(u_id) if ctx.guild else None
                    if member:
                        u_name = member.mention
                    else:
                        u = self.bot.get_user(u_id)
                        u_name = f"**{u.name}**" if u else f"<@{u_id}>"
                    lines.append(f"{medal} {u_name} • `Level {lvl}`")

                embed.description = "\n".join(lines)
                embed.set_footer(text=f"Page {page_idx + 1}/{total_pages} • Top {len(rows_list)} {scope_name}")
                embeds.append(embed)
            return embeds

        server_embeds = _build_embeds(server_rows, is_server=True)
        global_embeds = _build_embeds(global_rows, is_server=False)

        initial_scope = "server" if server_embeds else "global"
        view = LeaderboardFilterPaginationView(
            ctx.author,
            server_pages=server_embeds,
            global_pages=global_embeds,
            initial_scope=initial_scope
        )
        msg = await ctx.send(embed=view.current_pages[0], view=view)
        view.message = msg

    @commands.command(name="fraud", aliases=["nssab", "scammer", "cheater"], help="Blocki user mn l economy system (mention wla ID).")
    @commands.is_owner()
    async def fraud_user(self, ctx: commands.Context, target: FuzzyMember):
        w = await self.get_wallet(target.id)
        if w.get("is_fraud", 0) == 1:
            await ctx.send(f"⚠️ **{target.mention}** aslan mmarki **Fraud** mn 9bel!")
            return

        await self.bot.db.execute("UPDATE user_wallets SET is_fraud = 1 WHERE user_id = ?", (target.id,))
        await self.bot.db.commit()

        embed = discord.Embed(
            title="🚨 Economy Fraud Suspension",
            description=f"🔒 **{target.mention}** tmarka **Fraud**.\nWallet dialo tjmdat o may9edch ysta3mel l economy wla yl3b lgames.",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="legit", aliases=["n9i"], help="Unblocki user mn l economy system (mention wla ID).")
    @commands.is_owner()
    async def legit_user(self, ctx: commands.Context, target: FuzzyMember):
        w = await self.get_wallet(target.id)
        if w.get("is_fraud", 0) == 0:
            await ctx.send(f"⚠️ **{target.mention}** aslan **Legit** (machiy fraud).")
            return

        await self.bot.db.execute("UPDATE user_wallets SET is_fraud = 0 WHERE user_id = ?", (target.id,))
        await self.bot.db.commit()

        embed = discord.Embed(
            title="✅ Economy Standing Restored",
            description=f"🟢 **{target.mention}** rje3 **Legit**! Wallet dialo t7llat o progress dialo b9a kima kan.",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="fraudlist", aliases=["frauds", "scammers", "cheaters", "nssaba"], help="Chouf ga3 users li mmarkiyin fraud fl economy.")
    @commands.is_owner()
    async def fraud_list(self, ctx: commands.Context):
        async with self.bot.db.execute(
            "SELECT user_id, balance FROM user_wallets WHERE is_fraud = 1 ORDER BY balance DESC"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            embed = discord.Embed(
                title="🚨 Fraud List",
                description="✨ Walo! 7ta wa7d mammarki fraud f l'economy.",
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        entries = []
        for uid, bal in rows:
            u = self.bot.get_user(uid)
            user_str = f"**{u.name}**" if u else f"<@{uid}>"
            entries.append(f"• {user_str} (ID: `{uid}`) — Balance: **{bal:,} TAD**")

        paginator = self.bot.Paginator(
            ctx,
            pages=entries,
            per_page=10,
            title=f"🚨 Fraud List ({len(rows)})"
        )
        await paginator.send()


async def setup(bot):
    await bot.add_cog(Economy(bot))
