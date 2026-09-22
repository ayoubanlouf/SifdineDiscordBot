import time
import re
import json
import math
import os
import io
import asyncio
import aiohttp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Union
from PIL import Image, ImageOps
import discord
from discord.ext import commands
from converters import FuzzyMember, AmountConverter
from cogs.shop_catalog import (
    ShopItem,
    CATALOG,
    get_item,
    get_active_shop_items,
    get_items_by_category,
    register_item
)
from cogs.leveling_render import (
    render_level_card,
    render_wallet_card,
    parse_color_input,
    rgb_to_hex
)


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


def optimize_card_background(raw_bytes: bytes, target_size: Tuple[int, int]) -> bytes:
    """
    Pre-crops and compresses background wallpapers to exact card dimensions (e.g. 1640x640).
    Saves as 88-quality JPEG (~150KB) or PNG (if transparent).
    Prevents 8MB upload limit issues and accelerates future card renders by ~70%.
    """
    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = img.convert("RGBA")
        fitted = ImageOps.fit(img, target_size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        has_transparency = any(p[3] < 255 for p in fitted.getdata()) if fitted.mode in ("RGBA", "LA") else False
        if has_transparency:
            fitted.save(out, format="PNG", optimize=False)
        else:
            fitted_rgb = fitted.convert("RGB")
            fitted_rgb.save(out, format="JPEG", quality=88, optimize=True)
            fitted_rgb.close()
        fitted.close()
        out.seek(0)
        return out.getvalue()


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
    def __init__(self, target_user: Union[discord.Member, discord.User], author: Union[discord.Member, discord.User], cog, cached_card_bytes: Optional[bytes] = None):
        super().__init__(timeout=90)
        self.target_user = target_user
        self.author = author
        self.cog = cog
        self.cached_card_bytes = cached_card_bytes
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
            await interaction.response.send_message("❌ Had l button machi ta3k!", ephemeral=True)
            return False
        return True

    async def show_wallet_callback(self, interaction: discord.Interaction):
        self.current_page = "wallet"
        self._update_buttons()
        async with self.cog.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = 'custom_wallet' AND quantity > 0",
            (self.target_user.id,)
        ) as cur:
            has_cw = bool(await cur.fetchone())
        embed = await self.cog.get_wallet_embed(self.target_user, has_custom_wallet=has_cw)
        if has_cw and self.cached_card_bytes:
            file = discord.File(io.BytesIO(self.cached_card_bytes), filename="wallet.png")
            embed.set_image(url="attachment://wallet.png")
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
        else:
            await interaction.response.edit_message(embed=embed, view=self, attachments=[])

    async def show_transactions_callback(self, interaction: discord.Interaction):
        self.current_page = "transactions"
        self.filter_mode = "all"
        self._update_buttons()
        embed = await self.cog.get_transactions_embed(self.target_user, self.filter_mode)
        await interaction.response.edit_message(embed=embed, view=self, attachments=[])

    async def show_summary_callback(self, interaction: discord.Interaction):
        self.current_page = "summary"
        self._update_buttons()
        embed = await self.cog.get_wallet_summary_embed(self.target_user)
        await interaction.response.edit_message(embed=embed, view=self, attachments=[])

    async def toggle_filter_callback(self, interaction: discord.Interaction):
        modes = ["all", "plus", "minus"]
        curr_idx = modes.index(self.filter_mode)
        self.filter_mode = modes[(curr_idx + 1) % len(modes)]
        self._update_buttons()
        embed = await self.cog.get_transactions_embed(self.target_user, self.filter_mode)
        await interaction.response.edit_message(embed=embed, view=self, attachments=[])

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
            await interaction.response.send_message("❌ Had l button machi ta3k!", ephemeral=True)
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


class ShopCheckoutView(discord.ui.View):
    def __init__(self, author: Union[discord.Member, discord.User], cog, item: ShopItem):
        super().__init__(timeout=90)
        self.author = author
        self.cog = cog
        self.item = item
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had checkout machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirm Purchase", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, msg = await self.cog.purchase_shop_item(self.author.id, self.item.id)
        embed = discord.Embed(
            title="✅ Receipt" if success else "❌ Purchase Failed",
            description=msg,
            color=0x000000
        )
        for child in self.children:
            if child.label == "✅ Confirm Purchase":
                child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀️ Back to Shop", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.cog.build_shop_embed(self.author.id)
        view = ShopView(self.author, self.cog)
        view.message = self.message
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class ShopView(discord.ui.View):
    def __init__(self, author: Union[discord.Member, discord.User], cog):
        super().__init__(timeout=90)
        self.author = author
        self.cog = cog
        self.message: Optional[discord.Message] = None
        self._build_ui()

    def _build_ui(self):
        self.clear_items()
        active_items = get_active_shop_items()
        if not active_items:
            return

        item_options = []
        for it in active_items[:25]:
            item_options.append(discord.SelectOption(
                label=it.name,
                value=it.id,
                description=f"{it.price:,} TAD • Lvl {it.min_level}+",
                emoji=it.emoji if len(it.emoji) <= 2 else None
            ))
        item_select = discord.ui.Select(placeholder="🛍️ Khtar item bach tchrih...", options=item_options, row=0)
        item_select.callback = self.item_select_callback
        self.add_item(item_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l menu machi ta3k!", ephemeral=True)
            return False
        return True

    async def item_select_callback(self, interaction: discord.Interaction):
        selected_id = interaction.data["values"][0]
        item = get_item(selected_id)
        if not item:
            await interaction.response.send_message("❌ Had l item ma kayench!", ephemeral=True)
            return

        wallet = await self.cog.get_wallet(self.author.id)
        user_lvl = await self.cog.get_user_level(self.author.id)

        # Check existing inventory count
        async with self.cog.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = ?",
            (self.author.id, item.id)
        ) as cursor:
            row = await cursor.fetchone()
        owned_qty = row[0] if row else 0

        can_afford = wallet["balance"] >= item.price
        has_level = user_lvl["level"] >= item.min_level
        not_maxed = owned_qty < item.max_stack

        embed = discord.Embed(
            title=f"🛒 Checkout — {item.emoji} {item.name}",
            color=0x000000
        )
        embed.description = f"*{item.description}*\n"
        embed.add_field(name="💵 Prix", value=format_tad(item.price), inline=True)
        embed.add_field(name="💳 Floussek (Balance)", value=format_tad(wallet["balance"]), inline=True)
        embed.add_field(name="📦 Deja 3ndek", value=f"`{owned_qty}/{item.max_stack}`", inline=True)

        status_notes = []
        if not can_afford:
            status_notes.append(f"❌ **Flousk makafyinch!** Khassak {format_tad(item.price - wallet['balance'])}.")
        if not has_level:
            status_notes.append(f"❌ **Level Na9ess!** Khassek tkoun Level {item.min_level}.")
        if not not_maxed:
            status_notes.append("❌ **Max Stock!** 3ndek deja lmaximum mn had l item.")

        if status_notes:
            embed.add_field(name="⚠️ Remarques", value="\n".join(status_notes), inline=False)

        checkout_view = ShopCheckoutView(self.author, self.cog, item)
        checkout_view.message = self.message

        for child in checkout_view.children:
            if child.label == "✅ Confirm Purchase" and (not can_afford or not has_level or not not_maxed):
                child.disabled = True

        await interaction.response.edit_message(embed=embed, view=checkout_view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class InventoryView(discord.ui.View):
    def __init__(self, target_user: Union[discord.Member, discord.User], author: Union[discord.Member, discord.User], cog, items: list, cosmetics: Optional[dict] = None):
        super().__init__(timeout=90)
        self.target_user = target_user
        self.author = author
        self.cog = cog
        self.items = items
        self.cosmetics = cosmetics or {}
        self.current_page = 0
        self.page_size = 6
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        total_pages = max(1, math.ceil(len(self.items) / self.page_size))
        if total_pages > 1:
            btn_prev = discord.ui.Button(label="◀️", style=discord.ButtonStyle.primary, disabled=(self.current_page == 0), row=0)
            btn_prev.callback = self.prev_page
            self.add_item(btn_prev)

            btn_next = discord.ui.Button(label="▶️", style=discord.ButtonStyle.primary, disabled=(self.current_page >= total_pages - 1), row=0)
            btn_next.callback = self.next_page
            self.add_item(btn_next)

        # Usable items dropdown menu (only available if viewing own inventory)
        if self.author.id == self.target_user.id:
            now_ts = int(time.time())
            usable_options = []
            seen_ids = set()

            for it in self.items:
                i_id = it["item_id"]
                if i_id in seen_ids:
                    continue
                seen_ids.add(i_id)

                cat_item = get_item(i_id)
                if not cat_item or not cat_item.usable:
                    continue

                # Filter out items that are currently on cooldown (1h cooldown)
                if i_id == "custom_wallet":
                    last_up = self.cosmetics.get("wallet_last_updated", 0) or 0
                    if now_ts - last_up < 1 * 3600:
                        continue
                elif i_id == "custom_rank":
                    last_up = self.cosmetics.get("rank_last_updated", 0) or 0
                    if now_ts - last_up < 1 * 3600:
                        continue

                label = cat_item.name[:25]
                desc = f"Sta3mel {cat_item.name}"[:50]
                usable_options.append(discord.SelectOption(
                    label=label,
                    value=i_id,
                    emoji=cat_item.emoji or "📦",
                    description=desc
                ))

            if usable_options:
                select = discord.ui.Select(
                    placeholder="⚡ Sta3mel item mn chkara...",
                    options=usable_options[:25],
                    row=1
                )
                select.callback = self.on_use_select
                self.add_item(select)

    async def on_use_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l menu machi ta3k!", ephemeral=True)
            return

        selected_id = interaction.data["values"][0]

        # Disable view items to prevent duplicate actions
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        ctx = await self.cog.bot.get_context(interaction.message)
        ctx.author = self.author
        await self.cog.use_cmd(ctx, item_id=selected_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l menu machi ta3k!", ephemeral=True)
            return False
        return True

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            embed = self.cog.build_inventory_embed(self.target_user, self.items, self.current_page, self.page_size, cosmetics=self.cosmetics)
            await interaction.response.edit_message(embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        total_pages = max(1, math.ceil(len(self.items) / self.page_size))
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            embed = self.cog.build_inventory_embed(self.target_user, self.items, self.current_page, self.page_size, cosmetics=self.cosmetics)
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class CustomizationColorsModal(discord.ui.Modal):
    def __init__(self, target_name: str, curr_main: str, curr_accent: str, parent_view):
        super().__init__(title=f"🎨 Colors — {target_name[:20]}")
        self.parent_view = parent_view
        self.curr_main = curr_main
        self.curr_accent = curr_accent

        self.main_input = discord.ui.TextInput(
            label="Main Color (Glass Tint)",
            placeholder=f"Hex wla smya (default: {curr_main})",
            default=curr_main,
            required=False,
            max_length=30
        )
        self.accent_input = discord.ui.TextInput(
            label="Accent Color (Glow & Borders)",
            placeholder=f"Hex wla smya (default: {curr_accent})",
            default=curr_accent,
            required=False,
            max_length=30
        )
        self.hex_input = discord.ui.TextInput(
            label="Hexcodes",
            default="https://share.google/BfHGfcbhTi1r6Yt89",
            placeholder="Hexcodes: https://share.google/BfHGfcbhTi1r6Yt89",
            required=False,
            max_length=100
        )
        self.add_item(self.main_input)
        self.add_item(self.accent_input)
        self.add_item(self.hex_input)

    async def on_submit(self, interaction: discord.Interaction):
        m_val = self.main_input.value.strip()
        a_val = self.accent_input.value.strip()

        # 1. Main color validation
        if not m_val or m_val.lower() == "skip":
            resolved_main = self.curr_main
        elif m_val.lower() in ("reset", "default"):
            resolved_main = "#000000"
        else:
            parsed = parse_color_input(m_val)
            if parsed:
                resolved_main = rgb_to_hex(parsed)
            else:
                err_embed = discord.Embed(
                    title=f"🎨 Customizing {self.parent_view.target_name} - 2/2",
                    description=(
                        f"🎨 **Step 2/2: Colors (Main & Accent)**\n\n"
                        f"❌ **Erreur:** Main Color `{m_val}` mal9itach.\n"
                        f"• Kteb colorname (e.g. `black`, `navy`, `purple`, `cyan`, `crimson`) wla Hex code (`#111827`).\n\n"
                        f"Hexcodes: https://share.google/BfHGfcbhTi1r6Yt89"
                    ),
                    color=0x000000
                )
                err_embed.set_footer(text="Kteb 'skip' f ay field bach tkhlli loun l9dim.")
                await interaction.response.edit_message(embed=err_embed, view=self.parent_view)
                return

        # 2. Accent color validation
        if not a_val or a_val.lower() == "skip":
            resolved_accent = self.curr_accent
        elif a_val.lower() in ("reset", "default"):
            resolved_accent = "#ffffff"
        else:
            parsed = parse_color_input(a_val)
            if parsed:
                resolved_accent = rgb_to_hex(parsed)
            else:
                err_embed = discord.Embed(
                    title=f"🎨 Customizing {self.parent_view.target_name} - 2/2",
                    description=(
                        f"🎨 **Step 2/2: Colors (Main & Accent)**\n\n"
                        f"❌ **Erreur:** Accent Color `{a_val}` mal9itach.\n"
                        f"• Kteb colorname (e.g. `black`, `navy`, `purple`, `cyan`, `crimson`) wla Hex code (`#111827`).\n\n"
                        f"Hexcodes: https://share.google/BfHGfcbhTi1r6Yt89"
                    ),
                    color=0x000000
                )
                err_embed.set_footer(text="Kteb 'skip' f ay field bach tkhlli loun l9dim.")
                await interaction.response.edit_message(embed=err_embed, view=self.parent_view)
                return

        # Both colors valid!
        self.parent_view.chosen_main = resolved_main
        self.parent_view.chosen_accent = resolved_accent
        self.parent_view.finished.set()
        await interaction.response.defer()


class CustomizationBackgroundView(discord.ui.View):
    def __init__(self, author: Union[discord.Member, discord.User]):
        super().__init__(timeout=90)
        self.author = author
        self.skipped = False
        self.finished = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l menu machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.skipped = True
        self.finished.set()
        await interaction.response.defer()


class CustomizationColorsView(discord.ui.View):
    def __init__(self, author: Union[discord.Member, discord.User], target_name: str, curr_main: str, curr_accent: str):
        super().__init__(timeout=120)
        self.author = author
        self.target_name = target_name
        self.curr_main = curr_main
        self.curr_accent = curr_accent
        self.chosen_main: Optional[str] = None
        self.chosen_accent: Optional[str] = None
        self.went_back: bool = False
        self.finished = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had menu machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏮️ Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.went_back = True
        self.finished.set()
        await interaction.response.defer()

    @discord.ui.button(label="🎨 Choose Colors", style=discord.ButtonStyle.primary)
    async def open_modal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CustomizationColorsModal(self.target_name, self.curr_main, self.curr_accent, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.chosen_main = self.curr_main
        self.chosen_accent = self.curr_accent
        self.finished.set()
        await interaction.response.defer()


class CustomizationConfirmView(discord.ui.View):
    def __init__(self, ctx: commands.Context, cog, item_type: str, new_bg_url: Optional[str], new_main_color: str, new_accent_color: str):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.author = ctx.author
        self.cog = cog
        self.item_type = item_type  # "wallet" or "rank"
        self.new_bg_url = new_bg_url
        self.new_main_color = new_main_color
        self.new_accent_color = new_accent_color
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Had l menu machi ta3k!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Save", style=discord.ButtonStyle.success)
    async def confirm_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        now_ts = int(time.time())
        if self.item_type == "wallet":
            await self.cog.update_user_cosmetics(
                self.author.id,
                wallet_bg_url=self.new_bg_url,
                wallet_main_color=self.new_main_color,
                wallet_accent_color=self.new_accent_color,
                wallet_last_updated=now_ts
            )
            title = "✨ Wallet Theme Saved!"
            desc = "✅ Safi rah 9adit theme dialk. dir `sat wallet` bach tchoufo."
        else:
            await self.cog.update_user_cosmetics(
                self.author.id,
                rank_bg_url=self.new_bg_url,
                rank_main_color=self.new_main_color,
                rank_accent_color=self.new_accent_color,
                rank_last_updated=now_ts
            )
            title = "✨ Rank Card Theme Saved!"
            desc = "✅ Safi rah 9adit theme dialk. dir `sat rank` bach tchoufo."

        for child in self.children:
            child.disabled = True
        self.stop()
        embed = discord.Embed(title=title, description=desc, color=0x000000)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔄 Retry", style=discord.ButtonStyle.secondary)
    async def retry_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        self.stop()
        try:
            await interaction.message.delete()
        except Exception:
            pass
        await self.cog.run_cosmetic_wizard(self.ctx, item_type=self.item_type)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        self.stop()
        embed = discord.Embed(
            title="🚫 Customization Discarded",
            description="Tcancela lprocess. Ma tbeddel walo f lcard dialek.",
            color=0x000000
        )
        try:
            await interaction.response.edit_message(embed=embed, view=self, attachments=[])
        except Exception:
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            timeout_embed = discord.Embed(
                title="⏳ Fatt Lweqt (Timeout)",
                description="T3etelti bzaf f lpreview. Tcancela lprocess o ma tbeddel walo f lcard dialk.",
                color=0x000000
            )
            timeout_embed.set_footer(text="Ila bghiti t3awed tcustomizi, dir sat use mra khra.")
            try:
                await self.message.edit(embed=timeout_embed, view=self, attachments=[])
            except Exception:
                try:
                    await self.message.edit(embed=timeout_embed, view=self)
                except Exception:
                    pass


class Economy(commands.Cog, name="Economy"):
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

        claimed = set(user_data["claimed_milestones"])
        rewards_awarded = []

        if new_lvl > old_level:
            for lvl in range(old_level + 1, new_lvl + 1):
                if lvl not in claimed:
                    claimed.add(lvl)
                    if lvl % 10 == 0:
                        reward = min(lvl * 1000, 100000)
                        context = f"Level {lvl} Milestone Reward"
                    else:
                        reward = 1000
                        context = f"Level {lvl} Reward"
                    await self.add_balance(user_id, reward, context=context)
                    rewards_awarded.append((lvl, reward))

        claimed_list = sorted(list(claimed))
        claimed_json = json.dumps(claimed_list)

        await self.bot.db.execute(
            "INSERT INTO user_levels (user_id, level, current_xp, total_xp, claimed_milestones) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET level = ?, current_xp = ?, total_xp = ?, claimed_milestones = ?",
            (user_id, new_lvl, new_curr, new_total_xp, claimed_json,
             new_lvl, new_curr, new_total_xp, claimed_json)
        )
        await self.bot.db.commit()

        # Announce level-ups with rewards
        if rewards_awarded and (channel or message):
            try:
                user_name = None
                if message and hasattr(message, "author") and message.author.id == user_id:
                    user_name = message.author.display_name
                else:
                    u = self.bot.get_user(user_id)
                    user_name = u.display_name if u else "Player"

                if len(rewards_awarded) == 1:
                    a_lvl, a_rew = rewards_awarded[0]
                    desc = f"Wselti level {a_lvl}! Chediti `{a_rew:,}` TAD {TAD_EMOJI}"
                else:
                    total_rew = sum(r for _, r in rewards_awarded)
                    desc = f"Wselti level {new_lvl}! Chediti `{total_rew:,}` TAD {TAD_EMOJI}"

                embed = discord.Embed(
                    title=f"Mbrok a {user_name}",
                    description=desc,
                    color=0x000000
                )

                sent = False
                if message:
                    try:
                        await message.reply(embed=embed, mention_author=True)
                        sent = True
                    except Exception:
                        sent = False

                if not sent:
                    target_channel = channel or (message.channel if message else None)
                    if target_channel:
                        await target_channel.send(embed=embed)
            except Exception:
                pass

        user_data["level"] = new_lvl
        user_data["current_xp"] = new_curr
        user_data["xp_needed"] = new_needed
        user_data["total_xp"] = new_total_xp
        user_data["claimed_milestones"] = claimed_list
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

    # ============ INVENTORY & SHOP DATABASE METHODS ============

    async def get_user_inventory(self, user_id: int) -> list:
        async with self.bot.db.execute(
            "SELECT id, item_id, quantity, serial_number, metadata, acquired_at FROM user_inventory WHERE user_id = ? AND quantity > 0 ORDER BY acquired_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()

        items = []
        for r in rows:
            inv_id, item_id, qty, serial_num, meta_raw, acq_at = r
            try:
                meta = json.loads(meta_raw) if meta_raw else {}
            except Exception:
                meta = {}
            catalog_item = get_item(item_id)
            items.append({
                "id": inv_id,
                "item_id": item_id,
                "quantity": qty,
                "serial_number": serial_num,
                "metadata": meta,
                "acquired_at": acq_at,
                "catalog_item": catalog_item,
                "name": catalog_item.name if catalog_item else item_id.replace("_", " ").title(),
                "emoji": catalog_item.emoji if catalog_item else "📦",
                "description": catalog_item.description if catalog_item else "Special item",
                "tradeable": catalog_item.tradeable if catalog_item else False,
                "usable": catalog_item.usable if catalog_item else False,
            })
        return items

    async def add_inventory_item(self, user_id: int, item_id: str, quantity: int = 1, serial_number: int = 0, metadata: Optional[dict] = None) -> bool:
        if quantity <= 0:
            return False
        meta_json = json.dumps(metadata or {})
        now_ts = int(time.time())
        clean_id = item_id.lower().strip()
        await self.bot.db.execute(
            "INSERT INTO user_inventory (user_id, item_id, quantity, serial_number, metadata, acquired_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, item_id, serial_number) DO UPDATE SET quantity = quantity + ?",
            (user_id, clean_id, quantity, serial_number, meta_json, now_ts, quantity)
        )
        await self.bot.db.commit()
        return True

    async def remove_inventory_item(self, user_id: int, item_id: str, quantity: int = 1, serial_number: int = 0) -> bool:
        if quantity <= 0:
            return False
        clean_id = item_id.lower().strip()
        async with self.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = ? AND serial_number = ?",
            (user_id, clean_id, serial_number)
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < quantity:
            return False

        current_qty = row[0]
        if current_qty - quantity <= 0:
            await self.bot.db.execute(
                "DELETE FROM user_inventory WHERE user_id = ? AND item_id = ? AND serial_number = ?",
                (user_id, clean_id, serial_number)
            )
        else:
            await self.bot.db.execute(
                "UPDATE user_inventory SET quantity = quantity - ? WHERE user_id = ? AND item_id = ? AND serial_number = ?",
                (quantity, user_id, clean_id, serial_number)
            )
        await self.bot.db.commit()
        return True

    async def purchase_shop_item(self, user_id: int, item_id: str, quantity: int = 1, ctx: Optional[commands.Context] = None) -> tuple:
        if quantity <= 0:
            return False, "❌ Quantity khas tkoun kber mn 0."

        item = get_item(item_id)
        if not item or not item.is_active_in_shop:
            return False, f"❌ Had l item `{item_id}` makayench f l7anout 7aliyan."

        # Level requirement check
        user_lvl_data = await self.get_user_level(user_id)
        if user_lvl_data["level"] < item.min_level:
            return False, f"❌ Khassek tkoun **Level {item.min_level}** bach tchri **{item.name}**! (Level ta3k: {user_lvl_data['level']})"

        # Stack limit check
        clean_id = item.id.lower()
        async with self.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = ? AND serial_number = 0",
            (user_id, clean_id)
        ) as cursor:
            row = await cursor.fetchone()
        current_owned = row[0] if row else 0

        if current_owned + quantity > item.max_stack:
            return False, f"❌ Mat9dch tksb kter mn **{item.max_stack}** mn had l item (3ndek déjà `{current_owned}`)."

        total_cost = item.price * quantity
        wallet = await self.get_wallet(user_id)
        if wallet["balance"] < total_cost:
            return False, f"❌ Flousk makafyinch! Khassek {format_tad(total_cost)} (Balance ta3k: {format_tad(wallet['balance'])})."

        deducted = await self.deduct_balance(user_id, total_cost, context=f"Shop: {item.name} x{quantity}")
        if not deducted:
            return False, "❌ Mochkil f lflous wla wallet dialek mjemda."

        await self.deposit_vault("bank", total_cost, source="shop_purchase", context=f"Purchase of {item.name} x{quantity} by user {user_id}")
        await self.add_inventory_item(user_id, clean_id, quantity=quantity)

        # Trigger hook if exists
        if item.on_buy:
            try:
                hook_res, hook_msg = await item.on_buy(self.bot, user_id, ctx)
                if not hook_res:
                    return True, f"✅ Chriti **{quantity}x {item.emoji} {item.name}** b {format_tad(total_cost)}! (Note: {hook_msg})"
            except Exception:
                pass

        return True, f"✅ Chriti **{quantity}x {item.emoji} {item.name}** b {format_tad(total_cost)}!"

    async def use_inventory_item(self, user_id: int, item_id: str, ctx: commands.Context) -> tuple:
        clean_id = item_id.lower().strip()
        item = get_item(clean_id)

        # Check ownership
        async with self.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = ? AND quantity > 0",
            (user_id, clean_id)
        ) as cursor:
            row = await cursor.fetchone()

        if not row or row[0] <= 0:
            return False, f"❌ Ma3ndekch had l item `{item_id}` f chkara ta3k."

        if not item or not item.usable:
            return False, f"❌ Had l item **{item.name if item else item_id}** ma ymkench yst3mel directly."

        # Execute hook
        if item.on_use:
            try:
                success, msg = await item.on_use(self.bot, user_id, ctx)
                if not success:
                    return False, f"❌ Ma 9ditch tsta3mel l item: {msg}"
            except Exception as e:
                return False, f"❌ Mochkil f l usage ta3 l item: {e}"

        if getattr(item, "consumable", True):
            await self.remove_inventory_item(user_id, clean_id, quantity=1)
        return True, f"✨ Sta3melti **{item.emoji} {item.name}** b-naja7!"

    async def get_user_cosmetics(self, user_id: int) -> dict:
        async with self.bot.db.execute(
            "SELECT wallet_private, wallet_bg_url, wallet_main_color, wallet_accent_color, wallet_last_updated, "
            "rank_bg_url, rank_main_color, rank_accent_color, rank_last_updated FROM user_cosmetics WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return {
                "wallet_private": 0,
                "wallet_bg_url": None,
                "wallet_main_color": "#000000",
                "wallet_accent_color": "#ffffff",
                "wallet_last_updated": 0,
                "rank_bg_url": None,
                "rank_main_color": "#000000",
                "rank_accent_color": "#ffffff",
                "rank_last_updated": 0,
            }
        return {
            "wallet_private": row[0] or 0,
            "wallet_bg_url": row[1],
            "wallet_main_color": row[2] or "#000000",
            "wallet_accent_color": row[3] or "#ffffff",
            "wallet_last_updated": row[4] or 0,
            "rank_bg_url": row[5],
            "rank_main_color": row[6] or "#000000",
            "rank_accent_color": row[7] or "#ffffff",
            "rank_last_updated": row[8] or 0,
        }

    async def update_user_cosmetics(self, user_id: int, **kwargs) -> None:
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO user_cosmetics (user_id) VALUES (?)",
            (user_id,)
        )
        valid_cols = {
            "wallet_private", "wallet_bg_url", "wallet_main_color", "wallet_accent_color", "wallet_last_updated",
            "rank_bg_url", "rank_main_color", "rank_accent_color", "rank_last_updated"
        }
        filtered = {k: v for k, v in kwargs.items() if k in valid_cols}
        if filtered:
            set_clauses = [f"{k} = ?" for k in filtered.keys()]
            values = list(filtered.values()) + [user_id]
            sql = f"UPDATE user_cosmetics SET {', '.join(set_clauses)} WHERE user_id = ?"
            await self.bot.db.execute(sql, tuple(values))
        await self.bot.db.commit()

    async def fetch_image_bytes(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) <= 10 * 1024 * 1024:
                            return data
        except Exception:
            pass
        return None

    async def rehost_asset(self, img_bytes: bytes, filename: str, user: Union[discord.Member, discord.User]) -> Optional[str]:
        assets_channel_id_str = os.getenv("ASSETS_CHANNEL_ID")
        if not assets_channel_id_str:
            return None
        try:
            channel_id = int(assets_channel_id_str)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)
            if channel:
                clean_ext = "png"
                if filename and "." in filename:
                    ext = filename.split(".")[-1].lower()
                    if ext in ["jpg", "jpeg", "webp", "png"]:
                        clean_ext = ext
                file = discord.File(io.BytesIO(img_bytes), filename=f"asset_{user.id}_{int(time.time())}.{clean_ext}")
                msg = await channel.send(f"Asset upload by {user.name} (`{user.id}`):", file=file)
                if msg.attachments:
                    return msg.attachments[0].url
        except Exception:
            pass
        return None

    async def run_cosmetic_wizard(self, ctx: commands.Context, item_type: str):
        user_id = ctx.author.id
        cosmetics = await self.get_user_cosmetics(user_id)

        target_name = "Wallet Card" if item_type == "wallet" else "Rank Level Card"
        curr_bg = cosmetics.get(f"{item_type}_bg_url")
        curr_main = cosmetics.get(f"{item_type}_main_color") or "#000000"
        curr_accent = cosmetics.get(f"{item_type}_accent_color") or "#ffffff"

        def check(m: discord.Message):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        current_step = 1
        new_bg_url = curr_bg
        new_main_color = curr_main
        new_accent_color = curr_accent
        step_msg = None

        while True:
            if current_step == 1:
                start_embed = discord.Embed(
                    title=f"🎨 Customizing {target_name} - 1/2",
                    description=(
                        f"🖼️ **Step 1/2: Background Image**\n\n"
                        f"• Sift background jdida f had channel (upload attachment wla direct link).\n"
                        f"• Wla wrek 3la **Skip** bach tkhlli background l9dima.\n"
                    ),
                    color=0x000000
                )
                start_embed.set_footer(text="Wrek 3la Skip wla kteb 'reset' bach trje3 default.")
                bg_view = CustomizationBackgroundView(ctx.author)
                if step_msg is None:
                    step_msg = await ctx.send(embed=start_embed, view=bg_view)
                else:
                    await step_msg.edit(embed=start_embed, view=bg_view)

                # Step 1: Background Loop
                error_msgs = []
                step1_done = False

                while not step1_done:
                    msg_task = asyncio.create_task(self.bot.wait_for("message", check=check))
                    btn_task = asyncio.create_task(bg_view.finished.wait())

                    done, pending = await asyncio.wait(
                        [msg_task, btn_task],
                        timeout=90.0,
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()

                    if not done:
                        bg_view.stop()
                        for em in error_msgs:
                            try:
                                await em.delete()
                            except Exception:
                                pass
                        return await ctx.send("⏳ T3etelti 3lia, 3awd mn lwl.")

                    if btn_task in done:
                        # User clicked the Skip button!
                        new_bg_url = curr_bg
                        bg_view.stop()
                        current_step = 2
                        step1_done = True
                        break

                    user_msg = msg_task.result()
                    content = user_msg.content.strip()
                    is_valid = False
                    error_reason = None

                    if user_msg.attachments:
                        att = user_msg.attachments[0]
                        if att.size > 15 * 1024 * 1024:
                            error_reason = "⚠️ Tsouira kbeera bazaf (fayta 15MB). Sift tsouira sgher mn 15MB."
                        elif att.content_type and not att.content_type.startswith("image/"):
                            error_reason = "⚠️ Had lfile machi tsouira. Sift tsouira (PNG, JPG, WEBP)."
                        else:
                            try:
                                att_bytes = await att.read()
                                Image.open(io.BytesIO(att_bytes)).verify()
                                target_size = (1640, 640) if item_type == "wallet" else (1640, 540)
                                processed_bytes = await asyncio.to_thread(optimize_card_background, att_bytes, target_size)
                                rehosted_url = await self.rehost_asset(processed_bytes, "wallpaper.jpg", ctx.author)
                                new_bg_url = rehosted_url or att.url
                                is_valid = True
                            except Exception:
                                error_reason = "⚠️ Had lfile machi tsouira valid. 3awd jereb b tsouira khra."

                    elif content.lower() in ["reset", "none", "remove", "default"]:
                        new_bg_url = None
                        is_valid = True

                    elif content.lower() == "skip":
                        new_bg_url = curr_bg
                        is_valid = True

                    elif content.startswith("http://") or content.startswith("https://"):
                        url_bytes = await self.fetch_image_bytes(content)
                        if url_bytes:
                            try:
                                Image.open(io.BytesIO(url_bytes)).verify()
                                target_size = (1640, 640) if item_type == "wallet" else (1640, 540)
                                processed_bytes = await asyncio.to_thread(optimize_card_background, url_bytes, target_size)
                                rehosted_url = await self.rehost_asset(processed_bytes, "wallpaper.jpg", ctx.author)
                                new_bg_url = rehosted_url or content
                                is_valid = True
                            except Exception:
                                error_reason = "⚠️ Had link machi tsouira valid. 3tini direct link wla upload-iha."
                        else:
                            error_reason = "⚠️ Ma9ditch ntelechargi had link d tsouira. 3tini direct link wla upload-iha."
                    else:
                        error_reason = "⚠️ Sift tsouira (upload attachment) wla direct link, wla kteb `skip` / `reset`."

                    if is_valid:
                        bg_view.stop()
                        try:
                            await user_msg.add_reaction("✅")
                        except Exception:
                            pass
                        await asyncio.sleep(1.5)
                        try:
                            await user_msg.delete()
                        except Exception:
                            pass
                        for em in error_msgs:
                            try:
                                await em.delete()
                            except Exception:
                                pass
                        current_step = 2
                        step1_done = True
                        break
                    else:
                        try:
                            await user_msg.add_reaction("❌")
                        except Exception:
                            pass
                        err_reply = await user_msg.reply(error_reason, mention_author=True)
                        error_msgs.append(err_reply)

            elif current_step == 2:
                # Step 2: Main Color & Accent Color via Modal
                step2_embed = discord.Embed(
                    title=f"🎨 Customizing {target_name} - 2/2",
                    description=(
                        f"🎨 **Step 2/2: Colors (Main & Accent)**\n\n"
                        f"• Wrek 3la **Choose Colors** bach tkhtar **Main Color** o **Accent Color**.\n"
                        f"• Wrek 3la **Skip** bach tkhlli l alwan l9dam.\n"
                        f"• Wla wrek 3la **Back** bach trje3 l Step 1 (Background).\n"
                        f"• Dekhel colorname (e.g. `black`, `navy`, `purple`, `cyan`, `pink`, `orange`, `gold`) wla Hexcode (`#111827`, `#ff2a85`).\n\n"
                        f"Hexcodes: https://share.google/BfHGfcbhTi1r6Yt89"
                    ),
                    color=0x000000
                )
                step2_embed.set_footer(text="Kteb 'skip' f ay field bach tkhlli loun l9dim.")
                colors_view = CustomizationColorsView(ctx.author, target_name, curr_main, curr_accent)
                await step_msg.edit(embed=step2_embed, view=colors_view)

                try:
                    await asyncio.wait_for(colors_view.finished.wait(), timeout=120.0)
                except asyncio.TimeoutError:
                    colors_view.stop()
                    try:
                        await step_msg.edit(view=None)
                    except Exception:
                        pass
                    return await ctx.send("⏳ T3etelti 3lia, 3awd mn lwl.")

                if colors_view.went_back:
                    colors_view.stop()
                    current_step = 1
                    continue

                new_main_color = colors_view.chosen_main or curr_main
                new_accent_color = colors_view.chosen_accent or curr_accent
                break

        try:
            await step_msg.delete()
        except Exception:
            pass

        # If user didn't change anything, cancel without rendering preview or cooldown
        bg_unchanged = (new_bg_url == curr_bg)
        main_unchanged = (new_main_color.lower() == curr_main.lower())
        accent_unchanged = (new_accent_color.lower() == curr_accent.lower())
        if bg_unchanged and main_unchanged and accent_unchanged:
            return await ctx.send("ℹ️ Mabeddelti walou f lcard dialk. Customization cancelled.")

        # Step 4: Live Card Preview & Confirmation
        loading_msg = await ctx.send("⏳ Sber n9ad lik l preview...")

        avatar_bytes = None
        if ctx.author.display_avatar:
            try:
                avatar_bytes = await ctx.author.display_avatar.with_format("png").with_size(128).read()
            except Exception:
                avatar_bytes = None

        bg_bytes = None
        if new_bg_url:
            bg_bytes = await self.fetch_image_bytes(new_bg_url)

        if item_type == "wallet":
            w = await self.get_wallet(user_id)
            async with self.bot.db.execute(
                "SELECT last_daily, daily_streak, last_weekly FROM economy_cooldowns WHERE user_id = ?",
                (user_id,)
            ) as cur:
                cdr = await cur.fetchone()
            last_daily = cdr[0] if cdr and cdr[0] else 0
            d_streak = cdr[1] if cdr and cdr[1] else 0
            last_weekly = cdr[2] if cdr and cdr[2] else 0

            now_ts = int(time.time())
            now_casa = datetime.now(CASA_TZ)
            today_date = now_casa.date()
            daily_claimed = (datetime.fromtimestamp(last_daily, tz=CASA_TZ).date() == today_date) if last_daily else False
            next_midnight = (now_casa + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            diff_secs = max(0, int(next_midnight.timestamp() - now_ts))
            hours = diff_secs // 3600
            mins = (diff_secs % 3600) // 60
            if hours > 0:
                daily_resets_in_str = f"Resets in {hours}h {mins}m"
            else:
                daily_resets_in_str = f"Resets in {mins}m"

            current_week_start_ts = get_current_week_start_ts()
            next_week_start_ts = get_next_week_start_ts()
            weekly_claimed = bool(last_weekly and last_weekly >= current_week_start_ts)
            diff_week = max(0, int(next_week_start_ts - now_ts))
            w_days = diff_week // 86400
            w_hours = (diff_week % 86400) // 3600
            if w_days > 0:
                weekly_resets_in_str = f"Resets in {w_days}d {w_hours}h"
            else:
                weekly_resets_in_str = f"Resets in {w_hours}h"

            buf = await asyncio.to_thread(
                render_wallet_card,
                username=ctx.author.display_name,
                balance=w["balance"],
                is_active=(w["is_fraud"] == 0),
                is_private=bool(cosmetics.get("wallet_private", 0)),
                daily_streak=d_streak,
                daily_claimed=daily_claimed,
                daily_resets_in_str=daily_resets_in_str,
                weekly_claimed=weekly_claimed,
                weekly_resets_in_str=weekly_resets_in_str,
                avatar_bytes=avatar_bytes,
                bg_bytes=bg_bytes,
                main_color_str=new_main_color,
                accent_color_str=new_accent_color
            )
            preview_filename = "wallet_preview.png"
        else:
            user_data = await self.get_user_level(user_id)
            next_lvl, next_rew = get_next_milestone_info(user_data["level"])
            buf = await asyncio.to_thread(
                render_level_card,
                username=ctx.author.display_name,
                level=user_data["level"],
                current_xp=user_data["current_xp"],
                xp_needed=user_data["xp_needed"],
                total_xp=user_data["total_xp"],
                rank=user_data["rank"],
                avatar_bytes=avatar_bytes,
                next_milestone_level=next_lvl,
                next_milestone_reward=next_rew,
                bg_bytes=bg_bytes,
                main_color_str=new_main_color,
                accent_color_str=new_accent_color
            )
            preview_filename = "rank_preview.png"

        file = discord.File(buf, filename=preview_filename)
        preview_embed = discord.Embed(
            title=f"👁️ Preview — {target_name}",
            description=(
                f"Haka ghadi tban lcard ta3k!\n"
                f"• **Main Color:** `{new_main_color}`\n"
                f"• **Accent Color:** `{new_accent_color}`\n"
                f"• **Background:** {'`Custom Image`' if new_bg_url else '`Default Solid`'}"
            ),
            color=0x000000
        )
        preview_embed.set_image(url=f"attachment://{preview_filename}")

        confirm_view = CustomizationConfirmView(ctx, self, item_type, new_bg_url, new_main_color, new_accent_color)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        confirm_view.message = await ctx.send(embed=preview_embed, file=file, view=confirm_view)

    async def build_shop_embed(self, user_id: int) -> discord.Embed:
        active_items = get_active_shop_items()

        embed = discord.Embed(
            title="🏪 L7anout ta3 Sifdine",
            color=0x000000
        )

        if not active_items:
            embed.description = "*L7anout khawi 7aliyan...*"
            return embed

        desc_parts = []
        for item in active_items:
            desc_parts.append(
                f"{item.emoji} **{item.name}** — {format_tad(item.price)}\n"
                f"-# {item.description}"
            )

        embed.description = "\n\n".join(desc_parts)
        embed.set_footer(text="💡 Khtar item mn dropdown menu bach tchrih.")
        return embed

    def build_inventory_embed(self, target_user: Union[discord.Member, discord.User], items: list, page: int = 0, page_size: int = 6, cosmetics: Optional[dict] = None) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎒 Chkara ta3 {target_user.display_name}",
            color=0x000000
        )
        if target_user.display_avatar:
            embed.set_thumbnail(url=target_user.display_avatar.url)

        if not items:
            embed.description = "*Chkara khawya... Ma3ndek 7ta item daba.*"
            embed.set_footer(text="0 Items")
            return embed

        total_pages = max(1, math.ceil(len(items) / page_size))
        start_idx = page * page_size
        page_items = items[start_idx:start_idx + page_size]
        desc_lines = []
        now_ts = int(time.time())

        for it in page_items:
            serial_str = f" `#{it['serial_number']}`" if it["serial_number"] > 0 else ""
            item_text = f"{it['emoji']} **{it['name']}** ×{it['quantity']:,}{serial_str}\n-# {it['description']} (ID: `{it['item_id']}`)"

            # Check if this item is on cooldown
            if cosmetics:
                if it['item_id'] == 'custom_wallet':
                    w_last = cosmetics.get('wallet_last_updated', 0) or 0
                    if now_ts - w_last < 1 * 3600:
                        ready_ts = w_last + 1 * 3600
                        item_text += f"\n-# ⏳ Cooldown: <t:{ready_ts}:R>"
                elif it['item_id'] == 'custom_rank':
                    r_last = cosmetics.get('rank_last_updated', 0) or 0
                    if now_ts - r_last < 1 * 3600:
                        ready_ts = r_last + 1 * 3600
                        item_text += f"\n-# ⏳ Cooldown: <t:{ready_ts}:R>"

            desc_lines.append(item_text)

        embed.description = "\n\n".join(desc_lines)
        embed.set_footer(text=f"Inventory Overview • Page {page + 1}/{total_pages}")
        return embed

    async def get_wallet_embed(self, user: Union[discord.Member, discord.User], has_custom_wallet: bool = False) -> discord.Embed:
        w = await self.get_wallet(user.id)
        status_str = "🔒 **Frozen (Fraud)**" if w["is_fraud"] == 1 else "🟢 **Active**"

        embed = discord.Embed(
            title=f"💼 Bstam ta3 {user.display_name}",
            color=0x000000
        )
        embed.set_footer(text=f"Wallet Overview • Page 1/3 • {user.display_name}")

        if has_custom_wallet:
            embed.set_image(url="attachment://wallet.png")
            return embed

        embed.set_thumbnail(url=user.display_avatar.url)

        # Check daily and weekly cooldown status
        async with self.bot.db.execute(
            "SELECT last_daily, daily_streak, last_weekly FROM economy_cooldowns WHERE user_id = ?",
            (user.id,)
        ) as cursor:
            cd_row = await cursor.fetchone()

        last_daily = cd_row[0] if cd_row and cd_row[0] else 0
        daily_streak = cd_row[1] if cd_row and cd_row[1] else 0
        last_weekly = cd_row[2] if cd_row and cd_row[2] else 0

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

        embed.add_field(name="Balance", value=format_tad(w['balance']), inline=True)
        embed.add_field(name="Status", value=status_str, inline=True)
        embed.add_field(name="Daily Reward", value=daily_val, inline=False)
        embed.add_field(name="Weekly Reward", value=weekly_val, inline=False)
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
            color = 0x000000
        else:
            title = "🎰 Royal Casino — Recent Table Tax Receipts"
            color = 0x000000

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
        cosmetics = await self.get_user_cosmetics(target.id)

        # Privacy Check
        if target.id != ctx.author.id:
            if cosmetics.get("wallet_private", 0) == 1:
                await ctx.send("🔒 Mat9edch tchouf had lbstam.")
                return

        # Check custom_wallet ownership
        async with self.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = 'custom_wallet' AND quantity > 0",
            (target.id,)
        ) as cur:
            has_custom_wallet = bool(await cur.fetchone())

        embed = await self.get_wallet_embed(target, has_custom_wallet=has_custom_wallet)

        if has_custom_wallet:
            # Render custom wallet card
            avatar_bytes = None
            if target.display_avatar:
                try:
                    avatar_bytes = await target.display_avatar.with_format("png").with_size(128).read()
                except Exception:
                    avatar_bytes = None

            bg_bytes = None
            if cosmetics.get("wallet_bg_url"):
                bg_bytes = await self.fetch_image_bytes(cosmetics["wallet_bg_url"])

            w = await self.get_wallet(target.id)

            # Check daily/weekly cooldown status
            async with self.bot.db.execute(
                "SELECT last_daily, daily_streak, last_weekly FROM economy_cooldowns WHERE user_id = ?",
                (target.id,)
            ) as cursor:
                cd_row = await cursor.fetchone()

            last_daily = cd_row[0] if cd_row and cd_row[0] else 0
            daily_streak = cd_row[1] if cd_row and cd_row[1] else 0
            last_weekly = cd_row[2] if cd_row and cd_row[2] else 0

            now_ts = int(time.time())
            now_casa = datetime.now(CASA_TZ)
            today_date = now_casa.date()
            daily_claimed = (datetime.fromtimestamp(last_daily, tz=CASA_TZ).date() == today_date) if last_daily else False
            next_midnight = (now_casa + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            diff_secs = max(0, int(next_midnight.timestamp() - now_ts))
            hours = diff_secs // 3600
            mins = (diff_secs % 3600) // 60
            if hours > 0:
                daily_resets_in_str = f"Resets in {hours}h {mins}m"
            else:
                daily_resets_in_str = f"Resets in {mins}m"

            current_week_start_ts = get_current_week_start_ts()
            next_week_start_ts = get_next_week_start_ts()
            weekly_claimed = bool(last_weekly and last_weekly >= current_week_start_ts)
            diff_week = max(0, int(next_week_start_ts - now_ts))
            w_days = diff_week // 86400
            w_hours = (diff_week % 86400) // 3600
            if w_days > 0:
                weekly_resets_in_str = f"Resets in {w_days}d {w_hours}h"
            else:
                weekly_resets_in_str = f"Resets in {w_hours}h"

            buf = await asyncio.to_thread(
                render_wallet_card,
                username=target.display_name,
                balance=w["balance"],
                is_active=(w["is_fraud"] == 0),
                is_private=bool(cosmetics.get("wallet_private", 0)),
                daily_streak=daily_streak,
                daily_claimed=daily_claimed,
                daily_resets_in_str=daily_resets_in_str,
                weekly_claimed=weekly_claimed,
                weekly_resets_in_str=weekly_resets_in_str,
                avatar_bytes=avatar_bytes,
                bg_bytes=bg_bytes,
                main_color_str=cosmetics.get("wallet_main_color"),
                accent_color_str=cosmetics.get("wallet_accent_color")
            )

            card_bytes = buf.getvalue()
            view = WalletView(target, ctx.author, self, cached_card_bytes=card_bytes)
            file = discord.File(io.BytesIO(card_bytes), filename="wallet.png")
            msg = await ctx.send(embed=embed, file=file, view=view)
        else:
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
            await ctx.send("❌ Mazal makayna ta wallet.")
            return

        # Query private wallet user IDs
        async with self.bot.db.execute("SELECT user_id FROM user_cosmetics WHERE wallet_private = 1") as cur:
            private_rows = await cur.fetchall()
        private_users = set(r[0] for r in private_rows)

        bot_id = self.bot.user.id if self.bot.user else 0
        guild_member_ids = set(m.id for m in ctx.guild.members if not m.bot) if ctx.guild else set()
        server_rows = [r for r in all_rows if r[0] in guild_member_ids and r[0] != bot_id][:50] if ctx.guild else []
        global_rows = [r for r in all_rows if r[0] != bot_id and not self.is_bot_user(r[0])][:50]

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

                    bal_display = "Hidden" if u_id in private_users else format_tad(bal)
                    lines.append(f"{rank_badge} {member_str} • {bal_display}")

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

    @commands.command(name="setvault", help="Beddel balance dial bank wla casino.")
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

        cosmetics = await self.get_user_cosmetics(user.id)

        avatar_bytes = None
        if user.display_avatar:
            try:
                # 128px avatar maintains razor sharpness while keeping RAM strictly minimal
                avatar_bytes = await user.display_avatar.with_format("png").with_size(128).read()
            except Exception:
                avatar_bytes = None

        bg_bytes = None
        if cosmetics.get("rank_bg_url"):
            bg_bytes = await self.fetch_image_bytes(cosmetics["rank_bg_url"])

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
            next_milestone_reward=next_rew,
            bg_bytes=bg_bytes,
            main_color_str=cosmetics.get("rank_main_color"),
            accent_color_str=cosmetics.get("rank_accent_color")
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

    # ============ SHOP & INVENTORY COMMANDS ============

    @commands.command(name="shop", aliases=["store", "lhanout", "l7anout", "lhanot", "l7anot"], help="Chouf l7anout ta3 Sifdine.")
    @not_fraud()
    async def shop_cmd(self, ctx: commands.Context):
        embed = await self.build_shop_embed(ctx.author.id)
        view = ShopView(ctx.author, self)
        view.message = await ctx.send(embed=embed, view=view)

    @commands.command(name="inventory", aliases=["inv", "bag", "chkara", "shkara", "xkara"], help="Chouf l items li f chkara dialek wla dial chy user.")
    @not_fraud()
    async def inventory_cmd(self, ctx: commands.Context, target: Optional[FuzzyMember] = None):
        user = target or ctx.author
        items = await self.get_user_inventory(user.id)
        cosmetics = await self.get_user_cosmetics(user.id)
        embed = self.build_inventory_embed(user, items, cosmetics=cosmetics)
        view = InventoryView(user, ctx.author, self, items, cosmetics=cosmetics)
        view.message = await ctx.send(embed=embed, view=view)

    @commands.command(name="use", help="Sta3mel item mn chkara dialek. Mital: sat use private_wallet")
    @not_fraud()
    async def use_cmd(self, ctx: commands.Context, *, item_id: Optional[str] = None):
        if not item_id:
            await ctx.send("Ina item bghiti tsta3mel? dir `sat inv` bach tchouf items ta3k.")
            return

        raw_id = item_id.lower().strip()
        clean_id = raw_id.replace(" ", "_").replace("-", "_")
        item = get_item(raw_id) or get_item(clean_id)
        if item:
            clean_id = item.id

        # Check ownership
        async with self.bot.db.execute(
            "SELECT quantity FROM user_inventory WHERE user_id = ? AND item_id = ? AND quantity > 0",
            (ctx.author.id, clean_id)
        ) as cursor:
            row = await cursor.fetchone()

        if not row or row[0] <= 0:
            item_name = item.name if item else clean_id
            await ctx.send(f"❌ Ma3ndekch had l item `{item_name}` f chkara ta3k.")
            return

        if not item or not item.usable:
            name_str = item.name if item else clean_id
            await ctx.send(f"❌ Had l item **{name_str}** ma ymkench yst3mel directly.")
            return

        # Special cosmetic / perk item handlers
        if clean_id == "private_wallet":
            cosmetics = await self.get_user_cosmetics(ctx.author.id)
            current_priv = cosmetics.get("wallet_private", 0)
            new_priv = 0 if current_priv == 1 else 1
            await self.update_user_cosmetics(ctx.author.id, wallet_private=new_priv)
            if new_priv == 1:
                await ctx.send("🔒 Safi ta wa7d may9ed ichouf bstamk db.")
            else:
                await ctx.send("🔓 Koulchy i9ed ichouf bstamk db.")
            return

        elif clean_id == "custom_wallet":
            cosmetics = await self.get_user_cosmetics(ctx.author.id)
            last_up = cosmetics.get("wallet_last_updated", 0) or 0
            now_ts = int(time.time())
            cooldown_dur = 1 * 3600
            if now_ts - last_up < cooldown_dur:
                ready_ts = last_up + cooldown_dur
                await ctx.send(f"⏳ Mat9edch tkhedem **Custom Wallet** db. Rje3 <t:{ready_ts}:R>.")
                return
            await self.run_cosmetic_wizard(ctx, item_type="wallet")
            return

        elif clean_id == "custom_rank":
            cosmetics = await self.get_user_cosmetics(ctx.author.id)
            last_up = cosmetics.get("rank_last_updated", 0) or 0
            now_ts = int(time.time())
            cooldown_dur = 1 * 3600
            if now_ts - last_up < cooldown_dur:
                ready_ts = last_up + cooldown_dur
                await ctx.send(f"⏳ Mat9edch tkhedem **Custom Rank Card** db. Rje3 <t:{ready_ts}:R>.")
                return
            await self.run_cosmetic_wizard(ctx, item_type="rank")
            return

        # Generic usable item fallback
        success, msg = await self.use_inventory_item(ctx.author.id, clean_id, ctx)
        await ctx.send(msg)

    @commands.command(name="additem", aliases=["giveitem"], help="Zid item l chy user.")
    @commands.is_owner()
    async def add_item_cmd(self, ctx: commands.Context, arg1: str, arg2: str, quantity: int = 1):
        if quantity <= 0:
            await ctx.send("❌ Quantity khas tkoun kber mn 0.")
            return

        fuzzy_conv = FuzzyMember()
        target = None
        item_id = None
        try:
            target = await fuzzy_conv.convert(ctx, arg1)
            item_id = arg2.lower().strip()
        except Exception:
            try:
                target = await fuzzy_conv.convert(ctx, arg2)
                item_id = arg1.lower().strip()
            except Exception:
                await ctx.send("❌ Kteb: `sat additem @User <item_id> [quantity]`")
                return

        clean_id = item_id.lower().strip()
        await self.add_inventory_item(target.id, clean_id, quantity=quantity)
        cat_item = get_item(clean_id)
        name_str = f"**{quantity}x {cat_item.emoji} {cat_item.name}**" if cat_item else f"**{quantity}x `{clean_id}`**"
        await ctx.send(f"✅ Zdna {name_str} f chkara dial **{target.mention}**!")

    @commands.command(name="removeitem", aliases=["takeitem", "delitem"], help="N9ess item mn chkara dial chy user.")
    @commands.is_owner()
    async def remove_item_cmd(self, ctx: commands.Context, arg1: str, arg2: str, quantity: int = 1):
        if quantity <= 0:
            await ctx.send("❌ Quantity khas tkoun kber mn 0.")
            return

        fuzzy_conv = FuzzyMember()
        target = None
        item_id = None
        try:
            target = await fuzzy_conv.convert(ctx, arg1)
            item_id = arg2.lower().strip()
        except Exception:
            try:
                target = await fuzzy_conv.convert(ctx, arg2)
                item_id = arg1.lower().strip()
            except Exception:
                await ctx.send("❌ Kteb: `sat removeitem @User <item_id> [quantity]`")
                return

        clean_id = item_id.lower().strip()
        success = await self.remove_inventory_item(target.id, clean_id, quantity=quantity)
        if success:
            cat_item = get_item(clean_id)
            name_str = f"**{quantity}x {cat_item.emoji} {cat_item.name}**" if cat_item else f"**{quantity}x `{clean_id}`**"
            await ctx.send(f"✅ N9ssna {name_str} mn chkara dial **{target.mention}**.")
        else:
            await ctx.send(f"❌ **{target.mention}** ma3ndoch had quantity dial `{clean_id}` f chkara dialo.")


async def setup(bot):
    await bot.add_cog(Economy(bot))
