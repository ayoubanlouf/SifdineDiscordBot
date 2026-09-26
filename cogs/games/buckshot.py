from __future__ import annotations
import asyncio
import random
import time
from typing import Optional, List, Dict, Any, Union
import discord
from discord.ui import View, Button, Select

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import is_user_in_game, set_user_in_game, clear_user_game

ITEMS_INFO = {
    "glass": {
        "name": "Magnifying Glass",
        "emoji": "🔍",
        "desc": "Peek at the current chamber secretly"
    },
    "saw": {
        "name": "Handsaw",
        "emoji": "🪚",
        "desc": "Double the damage of your next shot (2 DMG)"
    },
    "cigs": {
        "name": "Cigarettes",
        "emoji": "🚬",
        "desc": "Restore +1 Health charge (max 4)"
    },
    "beer": {
        "name": "Beer",
        "emoji": "🍺",
        "desc": "Rack the slide and eject current shell unspent"
    },
    "cuffs": {
        "name": "Handcuffs",
        "emoji": "⛓️",
        "desc": "Opponent skips their next turn"
    }
}

ALL_ITEM_KEYS = ["glass", "saw", "cigs", "beer", "cuffs"]


def render_health_bar(hp: int, max_hp: int = 4) -> str:
    clamped = max(0, min(max_hp, hp))
    filled = "❚" * clamped
    empty = " " * (max_hp - clamped)
    return f"⚡ `[{filled}{empty}]` {clamped}/{max_hp}"


def render_item_tray(items: List[str]) -> str:
    if not items:
        return "*Empty Tray*"
    rendered = []
    # Count occurrences
    counts: Dict[str, int] = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    for key, count in counts.items():
        info = ITEMS_INFO.get(key, {"emoji": "📦", "name": key.title()})
        mult = f" x{count}" if count > 1 else ""
        rendered.append(f"[{info['emoji']} {info['name']}{mult}]")
    return "  ".join(rendered)


class BuckshotItemSelect(Select):
    def __init__(self, player_items: List[str]):
        options = []
        counts: Dict[str, int] = {}
        for it in player_items:
            counts[it] = counts.get(it, 0) + 1

        for it, count in counts.items():
            info = ITEMS_INFO.get(it, {"emoji": "📦", "name": it.title(), "desc": ""})
            qty_label = f" (x{count})" if count > 1 else ""
            options.append(
                discord.SelectOption(
                    label=f"{info['name']}{qty_label}",
                    value=it,
                    emoji=info["emoji"],
                    description=info["desc"][:100]
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No items available",
                    value="none",
                    description="Your tray is empty"
                )
            )

        super().__init__(
            placeholder="🎒 Use an Item from your Tray...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            disabled=(len(player_items) == 0)
        )

    async def callback(self, interaction: discord.Interaction):
        view: BuckshotGameView = self.view
        if interaction.user != view.current_turn:
            await interaction.response.send_message("❌ Machi noubtk!", ephemeral=True)
            return

        item_key = self.values[0]
        if item_key == "none":
            await interaction.response.send_message("Tray dyalk khawi!", ephemeral=True)
            return

        await view.handle_item_use(interaction, item_key)


class BuckshotGameView(View):
    def __init__(
        self,
        player_1: Union[discord.Member, discord.User],
        player_2: Union[discord.Member, discord.User],
        is_bot_game: bool = False,
        bet: int = 0,
        cog = None
    ):
        super().__init__(timeout=120)
        self.p1 = player_1
        self.p2 = player_2
        self.is_bot_game = is_bot_game
        self.bet = bet
        self.cog = cog

        self.max_hp = 4
        self.p1_hp = 4
        self.p2_hp = 4

        self.p1_items: List[str] = []
        self.p2_items: List[str] = []

        self.shells: List[str] = []
        self.round_num = 1
        self.current_turn = self.p1

        # Modifiers
        self.saw_active = False
        self.cuffed_user_id: Optional[int] = None
        self.ai_known_shell: Optional[str] = None
        self.last_action_log = "The game begins. Shotgun is loaded."

        self.game_over = False
        self.message: Optional[discord.Message] = None
        self._turn_task: Optional[asyncio.Task] = None
        self.turn_timeout_seconds = 45
        self.last_turn_timestamp = time.time()

        # Initial shell load and item distribution
        self._setup_new_loadout(first_round=True)

    def _setup_new_loadout(self, first_round: bool = False):
        """Generates random shells and deals items."""
        presets = [
            (1, 2), (2, 1), (2, 2),
            (3, 2), (2, 3), (3, 3),
            (4, 2), (3, 4), (4, 4)
        ]
        live_c, blank_c = random.choice(presets)
        self.shells = ["live"] * live_c + ["blank"] * blank_c
        random.shuffle(self.shells)

        # Distribute items (2 items each, max 8)
        items_to_deal = 2
        for _ in range(items_to_deal):
            if len(self.p1_items) < 8:
                self.p1_items.append(random.choice(ALL_ITEM_KEYS))
            if len(self.p2_items) < 8:
                self.p2_items.append(random.choice(ALL_ITEM_KEYS))

        self.saw_active = False
        self.ai_known_shell = None

        load_msg = (
            f"🔄 **RELOAD:** **{live_c} Live 🔴** - **{blank_c} Blank ⚪**"
        )
        if not first_round:
            self.last_action_log = load_msg
        else:
            self.last_action_log = (
                f"🎲 **ROUND 1:** **{live_c} Live 🔴** - **{blank_c} Blank ⚪**"
            )

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎲 BUCKSHOT ROULETTE — ROUND {self.round_num}",
            color=0x000000
        )

        p2_name = "DEALER [BOT]" if self.is_bot_game else self.p2.display_name
        p2_hp_bar = render_health_bar(self.p2_hp, self.max_hp)
        p2_tray = render_item_tray(self.p2_items)

        p1_name = self.p1.display_name
        p1_hp_bar = render_health_bar(self.p1_hp, self.max_hp)
        p1_tray = render_item_tray(self.p1_items)

        desc = (
            f"🤖 **{p2_name}**\n"
            f"Health:  {p2_hp_bar}\n"
            f"Items:   {p2_tray}\n\n"
            f"──────────── **VS** ────────────\n\n"
            f"👤 **{p1_name}**\n"
            f"Health:  {p1_hp_bar}\n"
            f"Items:   {p1_tray}\n\n"
            f"────────────────────────────────"
        )
        embed.description = desc

        status_notes = []
        if self.saw_active:
            status_notes.append("🪚 **Sawed-Off Barrel:** Next shot deals **2x Damage**!")
        if self.cuffed_user_id:
            cuffed_name = self.p1.display_name if self.cuffed_user_id == self.p1.id else p2_name
            status_notes.append(f"⛓️ **Handcuffed:** {cuffed_name} will skip their next turn.")

        if status_notes:
            embed.add_field(name="⚠️ Status Modifiers", value="\n".join(status_notes), inline=False)

        if self.last_action_log:
            embed.add_field(name="📜 Action Report", value=self.last_action_log, inline=False)

        embed.set_footer(text="Count spent shells wisely • Turn timer: 45s")
        return embed

    def get_turn_content(self) -> str:
        if self.game_over:
            return ""
        if self.is_bot_game and self.current_turn == self.p2:
            return "🤖 **Dealer is thinking and calculating odds...**"
        return f"👉 {self.current_turn.mention}, it's your turn!"

    def refresh_components(self):
        self.clear_items()
        if self.game_over:
            return

        # Add item select for current human turn
        current_items = self.p1_items if self.current_turn == self.p1 else self.p2_items
        is_human_turn = not (self.is_bot_game and self.current_turn == self.p2)

        if is_human_turn:
            self.add_item(BuckshotItemSelect(current_items))

            btn_opp = Button(
                label=f"Shoot {self.get_opponent().display_name}",
                style=discord.ButtonStyle.danger,
                emoji="💥",
                custom_id="shoot_opponent",
                row=1
            )
            btn_opp.callback = self.shoot_opponent_callback
            self.add_item(btn_opp)

            btn_self = Button(
                label="Shoot Yourself",
                style=discord.ButtonStyle.secondary,
                emoji="🎯",
                custom_id="shoot_self",
                row=1
            )
            btn_self.callback = self.shoot_self_callback
            self.add_item(btn_self)

            btn_quit = Button(
                label="Quit",
                style=discord.ButtonStyle.secondary,
                emoji="🚪",
                custom_id="quit_game",
                row=1
            )
            btn_quit.callback = self.quit_game_callback
            self.add_item(btn_quit)

    def get_opponent(self) -> Union[discord.Member, discord.User]:
        return self.p2 if self.current_turn == self.p1 else self.p1

    def reset_turn_timer(self):
        self.last_turn_timestamp = time.time()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._turn_task = asyncio.create_task(self._watchdog_timeout())

    async def _watchdog_timeout(self):
        try:
            await asyncio.sleep(self.turn_timeout_seconds)
            if self.game_over:
                return

            timed_out_user = self.current_turn
            if self.is_bot_game and timed_out_user == self.p2:
                return  # Bot doesn't time out

            # Human timed out -> forfeit
            winner = self.get_opponent()
            self.game_over = True
            self.clear_items()
            end_embed = self.build_embed()
            end_embed.title = "⏰ BUCKSHOT ROULETTE — FORFEIT"
            end_embed.color = 0x000000

            payout_msg = await self._handle_game_payout(winner)
            end_embed.description += (
                f"\n\n⏰ **{timed_out_user.mention} ran out of time!**\n"
                f"🏆 **{winner.mention}** wins by forfeit!{payout_msg}"
            )
            if self.message:
                try:
                    await self.message.edit(content="", embed=end_embed, view=None)
                except Exception:
                    pass
            self._cleanup_game_locks()
        except asyncio.CancelledError:
            pass

    async def handle_item_use(self, interaction: discord.Interaction, item_key: str):
        current_items = self.p1_items if self.current_turn == self.p1 else self.p2_items
        if item_key not in current_items:
            await interaction.response.send_message("❌ Ma 3ndekch had l'item!", ephemeral=True)
            return

        actor_name = self.current_turn.display_name

        if item_key == "glass":
            current_shell = self.shells[0]
            shell_desc = "**🔴 LIVE**" if current_shell == "live" else "**⚪ BLANK**"
            current_items.remove("glass")
            self.last_action_log = f"🔍 **{actor_name}** inspected the chamber with a Magnifying Glass."
            self.refresh_components()
            await interaction.response.send_message(
                f"🔍 **Chamber Intel:** The loaded shell is {shell_desc}!",
                ephemeral=True
            )
            await self.update_game_message()
            return

        elif item_key == "saw":
            if self.saw_active:
                await interaction.response.send_message("❌ Shotgun is already sawed-off!", ephemeral=True)
                return
            current_items.remove("saw")
            self.saw_active = True
            self.last_action_log = f"🪚 **{actor_name}** sawed off the barrel! The next shot deals **2x Damage**."
            self.refresh_components()
            await interaction.response.edit_message(content=self.get_turn_content(), embed=self.build_embed(), view=self)
            return

        elif item_key == "cigs":
            hp_attr = "p1_hp" if self.current_turn == self.p1 else "p2_hp"
            curr_hp = getattr(self, hp_attr)
            if curr_hp >= self.max_hp:
                await interaction.response.send_message("❌ Health charges already full (4/4)!", ephemeral=True)
                return
            setattr(self, hp_attr, curr_hp + 1)
            current_items.remove("cigs")
            self.last_action_log = f"🚬 **{actor_name}** smoked a Cigarette (+1 ⚡ Health)."
            self.refresh_components()
            await interaction.response.edit_message(content=self.get_turn_content(), embed=self.build_embed(), view=self)
            return

        elif item_key == "beer":
            current_items.remove("beer")
            ejected = self.shells.pop(0)
            e_str = "🔴 LIVE" if ejected == "live" else "⚪ BLANK"
            self.ai_known_shell = None
            self.last_action_log = f"🍺 **{actor_name}** racked the slide! Ejected a unspent **{e_str}** shell onto the floor."

            if len(self.shells) == 0:
                self.round_num += 1
                self._setup_new_loadout(first_round=False)

            self.refresh_components()
            await interaction.response.edit_message(content=self.get_turn_content(), embed=self.build_embed(), view=self)
            return

        elif item_key == "cuffs":
            opp = self.get_opponent()
            if self.cuffed_user_id == opp.id:
                await interaction.response.send_message("❌ Opponent is already handcuffed!", ephemeral=True)
                return
            current_items.remove("cuffs")
            self.cuffed_user_id = opp.id
            self.last_action_log = f"⛓️ **{actor_name}** locked **{opp.display_name}** in Handcuffs! (Skips their next turn)"
            self.refresh_components()
            await interaction.response.edit_message(content=self.get_turn_content(), embed=self.build_embed(), view=self)
            return

    async def shoot_opponent_callback(self, interaction: discord.Interaction):
        if interaction.user != self.current_turn:
            await interaction.response.send_message("❌ Machi noubtk!", ephemeral=True)
            return

        await interaction.response.defer()
        await self._process_shot(target_self=False)

    async def shoot_self_callback(self, interaction: discord.Interaction):
        if interaction.user != self.current_turn:
            await interaction.response.send_message("❌ Machi noubtk!", ephemeral=True)
            return

        await interaction.response.defer()
        await self._process_shot(target_self=True)

    async def quit_game_callback(self, interaction: discord.Interaction):
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("❌ Machi nta li la3b had lgame!", ephemeral=True)
            return

        await interaction.response.defer()
        await self.handle_user_quit(interaction.user)

    async def handle_user_quit(self, quitter: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""

        winner = self.p2 if quitter == self.p1 else self.p1

        self.game_over = True
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

        self.clear_items()
        payout_msg = await self._handle_game_payout(winner)

        end_embed = self.build_embed()
        end_embed.title = "🚪 BUCKSHOT ROULETTE — FORFEIT"
        end_embed.color = 0x000000

        p2_display = "DEALER [BOT]" if self.is_bot_game else self.p2.display_name
        winner_display = p2_display if winner == self.p2 else winner.display_name

        end_embed.description += (
            f"\n\n🚪 **{quitter.mention} has left the match!**\n"
            f"🏆 **{winner_display}** wins by forfeit!{payout_msg}"
        )

        if self.message:
            try:
                await self.message.edit(content="", embed=end_embed, view=None)
            except Exception:
                pass
        self._cleanup_game_locks()
        return f"🚪 Kherjti mn match dial **Buckshot Roulette** o t-3tbat forfeit!"

    async def _process_shot(self, target_self: bool):
        if self.game_over:
            return

        shooter = self.current_turn
        opponent = self.get_opponent()
        shell = self.shells.pop(0)
        self.ai_known_shell = None

        damage = 2 if self.saw_active else 1
        was_sawed = self.saw_active
        self.saw_active = False

        if target_self:
            if shell == "live":
                # Took live damage
                if shooter == self.p1:
                    self.p1_hp = max(0, self.p1_hp - damage)
                else:
                    self.p2_hp = max(0, self.p2_hp - damage)

                saw_text = " (2x Sawed-Off Damage)" if was_sawed else ""
                self.last_action_log = f"💥 **{shooter.display_name}** shot THEMSELVES with a **🔴 LIVE** shell! -{damage} ⚡{saw_text}"

                if self._check_game_over():
                    await self._finish_game(winner=opponent)
                    return

                if len(self.shells) == 0:
                    self.round_num += 1
                    self._setup_new_loadout(first_round=False)

                # Shooting self with live loses turn
                self._advance_turn(switched=True)
            else:
                # Shot self with blank -> Extra turn!
                self.last_action_log = f"💨 *Click!* **{shooter.display_name}** shot themselves with a **⚪ BLANK** shell! **(Extra Turn!)**"
                if len(self.shells) == 0:
                    self.round_num += 1
                    self._setup_new_loadout(first_round=False)
                # Keep turn!
                self._advance_turn(switched=False)
        else:
            # Shot opponent
            if shell == "live":
                if opponent == self.p1:
                    self.p1_hp = max(0, self.p1_hp - damage)
                else:
                    self.p2_hp = max(0, self.p2_hp - damage)

                saw_text = " (2x Sawed-Off Damage)" if was_sawed else ""
                self.last_action_log = f"💥 **{shooter.display_name}** shot **{opponent.display_name}** with a **🔴 LIVE** shell! -{damage} ⚡{saw_text}"

                if self._check_game_over():
                    await self._finish_game(winner=shooter)
                    return
            else:
                self.last_action_log = f"💨 *Click!* **{shooter.display_name}** aimed at **{opponent.display_name}**, but it was a **⚪ BLANK** shell!"

            if len(self.shells) == 0:
                self.round_num += 1
                self._setup_new_loadout(first_round=False)

            self._advance_turn(switched=True)

        await self.update_game_message()

        # If it's the bot's turn in solo mode, trigger the AI
        if not self.game_over and self.is_bot_game and self.current_turn == self.p2:
            asyncio.create_task(self._run_bot_ai_turn())

    def _advance_turn(self, switched: bool):
        """Calculates next turn, respecting handcuffs."""
        if not switched:
            self.reset_turn_timer()
            return

        opp = self.get_opponent()
        if self.cuffed_user_id == opp.id:
            # Opponent is cuffed -> skips turn
            self.cuffed_user_id = None
            self.last_action_log += f"\n⛓️ **{opp.display_name}** is handcuffed and skips their turn!"
            # Current player goes again!
        else:
            self.current_turn = opp

        self.reset_turn_timer()

    def _check_game_over(self) -> bool:
        return self.p1_hp <= 0 or self.p2_hp <= 0

    async def _finish_game(self, winner: Union[discord.Member, discord.User]):
        self.game_over = True
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

        self.clear_items()
        payout_msg = await self._handle_game_payout(winner)

        end_embed = self.build_embed()
        end_embed.title = "🏆 BUCKSHOT ROULETTE — MATCH OVER"
        end_embed.color = 0x000000

        p2_display = "DEALER [BOT]" if self.is_bot_game else self.p2.display_name
        winner_display = p2_display if winner == self.p2 else winner.display_name

        end_embed.description += f"\n\n💀 **{winner_display}** has survived the match!{payout_msg}"

        if self.message:
            try:
                await self.message.edit(content="", embed=end_embed, view=None)
            except Exception:
                pass
        self._cleanup_game_locks()

    async def _handle_game_payout(self, winner: Union[discord.Member, discord.User]) -> str:
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if not economy_cog or self.bet <= 0:
            return ""

        guild_id = self.message.guild.id if self.message and self.message.guild else None

        if self.is_bot_game:
            if winner == self.p1:
                # Player beat Dealer
                gross = int(round(self.bet * 1.95))
                net, tax = await economy_cog.apply_tax_and_add_balance(
                    self.p1.id, gross, context="Buckshot Roulette Win", vault="casino"
                )
                if guild_id and self.cog:
                    await self.cog.record_minigame_win(guild_id, self.p1.id, "buckshot", earnings=net)
                return f"\n\n💰 **{self.p1.mention}** beat the Dealer and won **+{net:,}** {TAD_EMOJI} TAD! (`{tax:,}` TAD tax)"
            else:
                # Dealer won, player lost bet
                tax = await economy_cog.apply_lost_gamble_tax(self.bet, context="Buckshot Roulette Loss")
                if guild_id and self.cog:
                    await self.cog.record_minigame_loss(guild_id, self.p1.id, "buckshot", loss_amount=self.bet)
                return f"\n\n💸 **{self.p1.mention}** lost **{format_tad(self.bet)}** to the Casino Vault!"
        else:
            # 1v1 PvP
            w_payout, burned, _ = calculate_pvp_payout(self.bet)
            loser = self.p2 if winner == self.p1 else self.p1

            if burned > 0:
                await economy_cog.deposit_vault("bank", burned, source="pvp_wager", context="Buckshot PvP Wager")
            await economy_cog.add_balance(winner.id, w_payout, context="Buckshot PvP Win")

            if guild_id and self.cog:
                await self.cog.record_minigame_win(guild_id, winner.id, "buckshot", earnings=w_payout)
                await self.cog.record_minigame_loss(guild_id, loser.id, "buckshot", loss_amount=self.bet)

            return f"\n\n💰 **{winner.mention}** won {format_tad(w_payout)}! (`{burned:,}` {TAD_EMOJI} tax)"

    def _cleanup_game_locks(self):
        if self.cog and hasattr(self.cog, "bot"):
            clear_user_game(self.cog.bot, self.p1.id)
            if not self.is_bot_game:
                clear_user_game(self.cog.bot, self.p2.id)

    async def update_game_message(self):
        if not self.message:
            return
        self.refresh_components()
        try:
            await self.message.edit(
                content=self.get_turn_content(),
                embed=self.build_embed(),
                view=self if not self.game_over else None
            )
        except Exception:
            pass

    # ============ MINIMAX / PROBABILISTIC DEALER AI ============

    async def _run_bot_ai_turn(self):
        """Executes the Dealer's turn with mathematical precision and pacing."""
        while self.current_turn == self.p2 and not self.game_over:
            await asyncio.sleep(1.8)

            # Step 1: Health recovery if hurt
            if self.p2_hp < self.max_hp and "cigs" in self.p2_items:
                self.p2_items.remove("cigs")
                self.p2_hp += 1
                self.last_action_log = "🤖 **Dealer** used 🚬 Cigarettes (+1 ⚡ Health)."
                await self.update_game_message()
                await asyncio.sleep(1.4)
                continue

            # Step 2: Probability assessment
            live_rem = self.shells.count("live")
            blank_rem = self.shells.count("blank")
            total_rem = len(self.shells)

            if total_rem == 0:
                self.round_num += 1
                self._setup_new_loadout(first_round=False)
                await self.update_game_message()
                continue

            # Deterministic count check
            if live_rem == 0:
                known = "blank"
            elif blank_rem == 0:
                known = "live"
            else:
                known = self.ai_known_shell

            # Step 3: Information gathering with Magnifying Glass
            if known is None and "glass" in self.p2_items:
                self.p2_items.remove("glass")
                known = self.shells[0]
                self.ai_known_shell = known
                self.last_action_log = "🤖 **Dealer** peered into the chamber with a 🔍 Magnifying Glass."
                await self.update_game_message()
                await asyncio.sleep(1.4)
                continue

            # Step 4: Known shell execution
            if known == "blank":
                # Guaranteed 0 damage + guaranteed extra turn
                self.last_action_log = "🤖 **Dealer** calculated a 0% danger rate and aimed at themselves."
                await self._process_shot(target_self=True)
                break
            elif known == "live":
                # Optimize lethal damage
                if "saw" in self.p2_items and not self.saw_active and self.p1_hp > 1:
                    self.p2_items.remove("saw")
                    self.saw_active = True
                    self.last_action_log = "🤖 **Dealer** sawed off the shotgun with a 🪚 Handsaw!"
                    await self.update_game_message()
                    await asyncio.sleep(1.4)
                    continue

                if "cuffs" in self.p2_items and self.cuffed_user_id != self.p1.id and self.p1_hp > (2 if self.saw_active else 1):
                    self.p2_items.remove("cuffs")
                    self.cuffed_user_id = self.p1.id
                    self.last_action_log = f"🤖 **Dealer** snapped ⛓️ Handcuffs on {self.p1.display_name}!"
                    await self.update_game_message()
                    await asyncio.sleep(1.4)
                    continue

                # Shoot player with live
                await self._process_shot(target_self=False)
                break

            # Step 5: Probabilistic reasoning (unknown shell)
            p_live = live_rem / total_rem
            p_blank = blank_rem / total_rem

            # Beer: If live probability is low (<35%) and blanks > 1, cycle unspent blank
            if "beer" in self.p2_items and p_live < 0.35 and blank_rem > 1:
                self.p2_items.remove("beer")
                ejected = self.shells.pop(0)
                e_str = "🔴 LIVE" if ejected == "live" else "⚪ BLANK"
                self.ai_known_shell = None
                self.last_action_log = f"🤖 **Dealer** chugged a 🍺 Beer! Ejected a unspent **{e_str}** shell."
                if len(self.shells) == 0:
                    self.round_num += 1
                    self._setup_new_loadout(first_round=False)
                await self.update_game_message()
                await asyncio.sleep(1.4)
                continue

            # Self-shot vs Opponent-shot expected value
            if p_blank > 0.55 and self.p2_hp > 1:
                # Blank probability favors self-shot for tempo
                await self._process_shot(target_self=True)
                break
            else:
                # Shoot opponent. If live odds are strong (>= 66%), use saw
                if p_live >= 0.66 and "saw" in self.p2_items and not self.saw_active and self.p1_hp > 1:
                    self.p2_items.remove("saw")
                    self.saw_active = True
                    self.last_action_log = "🤖 **Dealer** evaluated high live probability and used a 🪚 Handsaw!"
                    await self.update_game_message()
                    await asyncio.sleep(1.4)
                    continue
                await self._process_shot(target_self=False)
                break


class BuckshotChallengeView(View):
    """Handles the 1v1 PvP challenge acceptance."""
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog, bet: int = 0):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.bet = bet
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Duel", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        if is_user_in_game(self.cog.bot, self.challenger.id):
            await interaction.response.send_message(f"❌ {self.challenger.mention} 3ndo deja game khddama!", ephemeral=True)
            return
        if is_user_in_game(self.cog.bot, self.challenged.id):
            await interaction.response.send_message("❌ 3ndek deja game khddama!", ephemeral=True)
            return

        if self.bet > 0:
            economy_cog = self.cog.bot.get_cog("Economy")
            if economy_cog:
                w1 = await economy_cog.get_wallet(self.challenger.id)
                w2 = await economy_cog.get_wallet(self.challenged.id)
                if w1["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ {self.challenger.mention} ma b9ach 3ndo kafi dial flous!", ephemeral=True)
                    return
                if w2["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ Flousk makafyinch ({format_tad(w2['balance'])} / {format_tad(self.bet)})!", ephemeral=True)
                    return
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"Buckshot Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"Buckshot Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        game_view = BuckshotGameView(
            player_1=self.challenger,
            player_2=self.challenged,
            is_bot_game=False,
            bet=self.bet,
            cog=self.cog
        )
        set_user_in_game(self.cog.bot, self.challenger.id, "Buckshot Roulette", game_view)
        set_user_in_game(self.cog.bot, self.challenged.id, "Buckshot Roulette", game_view)
        game_view.refresh_components()
        game_view.reset_turn_timer()

        content = game_view.get_turn_content()
        embed = game_view.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=game_view)
        game_view.message = interaction.message

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=f"❌ {self.challenged.mention} mabghach il3eb Buckshot Roulette.",
            view=self
        )

    async def on_timeout(self):
        if not self.accepted:
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ Challenge ma t-acceptach f lwe9t.", view=self)
                except discord.NotFound:
                    pass
