from __future__ import annotations
import random
import asyncio
from typing import Optional, Union

import discord
from discord.ui import Button, View, Modal, TextInput

from assets.wordle_words import WORDLE_TARGETS
from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import (
    is_english_word, get_wordle_secret,
    record_minigame_win, record_minigame_loss,
    is_user_in_game, set_user_in_game, clear_user_game
)

# ============ WORDLE HELPERS & UI CLASSES ============

def evaluate_wordle_guess(guess: str, secret: str) -> list[tuple[str, str]]:
    guess = guess.lower()
    secret = secret.lower()
    res = ["⬛"] * 5
    secret_counts = {}
    for i in range(5):
        if guess[i] == secret[i]:
            res[i] = "🟩"
        else:
            secret_counts[secret[i]] = secret_counts.get(secret[i], 0) + 1

    for i in range(5):
        if res[i] == "🟩":
            continue
        g_char = guess[i]
        if secret_counts.get(g_char, 0) > 0:
            res[i] = "🟨"
            secret_counts[g_char] -= 1
        else:
            res[i] = "⬛"

    return [(guess[i].upper(), res[i]) for i in range(5)]


class WordleSoloModal(Modal, title="Wordle — Guess"):
    guess_input = TextInput(
        label="5-Letter Word",
        placeholder="e.g. CRANE, PLANES...",
        min_length=5,
        max_length=5,
        required=True
    )

    def __init__(self, view: "WordleSoloView"):
        super().__init__()
        self.game_view = view

    async def on_submit(self, interaction: discord.Interaction):
        word = self.guess_input.value.strip().lower()
        if len(word) != 5 or not word.isalpha():
            await interaction.response.send_message("❌ Khes lkelma tkoun fiha 5 d l7orof alphabetic.", ephemeral=True)
            return

        if not self.game_view.cog.is_english_word(word):
            await interaction.response.send_message("❌ Had lkelma ma kaynach f dictionary.", ephemeral=True)
            return

        await self.game_view.process_guess(interaction, word)


class WordleSoloView(View):
    def __init__(self, player: discord.Member, secret: str, cog: "Minigames", difficulty: str = "easy"):
        super().__init__(timeout=300)
        self.player = player
        self.secret = secret.lower()
        self.cog = cog
        self.difficulty = difficulty
        self.guesses: list[str] = []
        self.game_over = False
        self.message: Optional[discord.Message] = None

    def get_content(self) -> str:
        diff_mult = DIFFICULTY_STAKES.get(self.difficulty, 1.0)
        lines = [
            f"🟩 **Wordle (Solo)** — L9a lkelma dial 5 d l7orof! (🎯 **{self.difficulty.upper()}** • **{diff_mult}x**)",
            f"Attempts: **{len(self.guesses)}/6**\n"
        ]

        for g in self.guesses:
            eval_res = evaluate_wordle_guess(g, self.secret)
            pattern = " ".join(e[1] for e in eval_res)
            letters = " ".join(f"**{e[0]}**" for e in eval_res)
            lines.append(f"{pattern}  |  {letters}")

        for _ in range(6 - len(self.guesses)):
            lines.append("⬛ ⬛ ⬛ ⬛ ⬛  |  - - - - -")

        if self.game_over:
            if self.guesses and self.guesses[-1] == self.secret:
                lines.append(f"\n🎉🏆 **Rbe7ti!** L9iti lkelma f **{len(self.guesses)}/6** attempts!\nLkelma kant: **{self.secret.upper()}**")
            else:
                lines.append(f"\n💥 **Game Over!** Salat attempts dialk.\nLkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Type Word", style=discord.ButtonStyle.primary, emoji="⌨️")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return
        await interaction.response.send_modal(WordleSoloModal(self))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True
        content = self.get_content() + f"\n\n🚪 {self.player.mention} khrej mn lgame."
        await interaction.response.edit_message(content=content, view=self)

    async def process_guess(self, interaction: discord.Interaction, word: str):
        self.guesses.append(word)
        if word == self.secret or len(self.guesses) >= 6:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            eco_msg = ""
            diff_mult = DIFFICULTY_STAKES.get(self.difficulty, 1.0)
            if economy_cog:
                if word == self.secret:
                    attempts = len(self.guesses)
                    base = 200 if attempts <= 2 else (100 if attempts <= 4 else 50)
                    gross = int(round(base * diff_mult))
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, gross, context=f"Wordle Solo ({self.difficulty.capitalize()} • {attempts}/6)")
                    eco_msg = f"\n\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • `{tax}` TAD tax)!"
                    if interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, self.player.id, "wordle", earnings=net)
                else:
                    gross = int(round(20 * diff_mult))
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, gross, context=f"Wordle Participation ({self.difficulty.capitalize()})")
                    eco_msg = f"\n\n💰 Reb7a ta3 lmoucharaka: **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • `{tax}` TAD tax)."

            content = self.get_content() + eco_msg
            await interaction.response.edit_message(content=content, view=self)
            return

        await interaction.response.edit_message(content=self.get_content(), view=self)

    def stop(self):
        if self.cog and hasattr(self.cog, "bot") and self.player:
            clear_user_game(self.cog.bot, self.player.id)
        super().stop()

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over:
            return ""
        self.game_over = True
        for item in self.children:
            item.disabled = True
        content = self.get_content() + f"\n\n🚪 {user.mention} khrej mn lgame."
        if self.message:
            try:
                await self.message.edit(content=content, view=self)
            except Exception:
                pass
        self.stop()
        return "🚪 Kherjti mn Wordle!"

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            self.stop()
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content=self.get_content() + "\n\n⏰ **Sala lwe9t!** Match sala bsbab inactivity.", view=self)
                except Exception:
                    pass


# Multiplayer 1v1 Classes

class WordleMultiplayerModal(Modal, title="Wordle 1v1 — Guess"):
    guess_input = TextInput(
        label="5-Letter Word",
        placeholder="Enter your 5-letter guess...",
        min_length=5,
        max_length=5,
        required=True
    )

    def __init__(self, match: "WordleMultiplayerMatch", player: discord.Member):
        super().__init__()
        self.match = match
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        word = self.guess_input.value.strip().lower()
        if len(word) != 5 or not word.isalpha():
            await interaction.response.send_message("❌ Khes lkelma tkoun fiha 5 d l7orof alphabetic.", ephemeral=True)
            return

        if not self.match.cog.is_english_word(word):
            await interaction.response.send_message("❌ Had lkelma ma kaynach f dictionary.", ephemeral=True)
            return

        await self.match.process_player_guess(interaction, self.player, word)


class WordleDMView(View):
    def __init__(self, match: "WordleMultiplayerMatch", player: discord.Member):
        super().__init__(timeout=300)
        self.match = match
        self.player = player

    @discord.ui.button(label="Type Word", style=discord.ButtonStyle.primary, emoji="⌨️")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.match.game_over or self.match.finished.get(self.player.id, False):
            await interaction.response.send_message("Saliti attempts dialk wla lmatch deja sala.", ephemeral=True)
            return
        await interaction.response.send_modal(WordleMultiplayerModal(self.match, self.player))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        await self.match.player_quit(interaction, self.player)

    async def on_timeout(self):
        if not self.match.game_over:
            self.match.quit[self.player.id] = True
            self.match.finished[self.player.id] = True
            opponent = self.match.p2 if self.player == self.match.p1 else self.match.p1
            if self.match.finished[opponent.id] or self.match.quit[opponent.id]:
                self.match.game_over = True
                if self.match.cog and hasattr(self.match.cog, "bot"):
                    clear_user_game(self.match.cog.bot, self.match.p1.id)
                    clear_user_game(self.match.cog.bot, self.match.p2.id)


class WordleMultiplayerMatch:
    def __init__(self, p1: discord.Member, p2: discord.Member, channel_msg: discord.Message, secret: str, cog: "Minigames", bet: int = 0):
        self.p1 = p1
        self.p2 = p2
        self.channel_msg = channel_msg
        self.secret = secret.lower()
        self.cog = cog
        self.bet = bet
        self.payout_handled = False
        
        self.guesses = {p1.id: [], p2.id: []}
        self.finished = {p1.id: False, p2.id: False}
        self.won = {p1.id: False, p2.id: False}
        self.quit = {p1.id: False, p2.id: False}
        self.dm_messages: dict[int, discord.Message] = {}
        self.dm_views: dict[int, WordleDMView] = {}
        self.game_over = False

    async def handle_economy_payout(self, winner: Optional[Union[discord.Member, discord.User]] = None, is_draw: bool = False) -> str:
        if self.payout_handled or self.bet <= 0:
            return ""
        self.payout_handled = True
        economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
        if not economy_cog:
            return ""

        winner_payout, tax_burned, _ = calculate_pvp_payout(self.bet)
        if is_draw or winner is None:
            await economy_cog.add_balance(self.p1.id, self.bet, context="Wordle Draw Refund")
            await economy_cog.add_balance(self.p2.id, self.bet, context="Wordle Draw Refund")
            return f"\n\n🤝 **Draw Refund:** {format_tad(self.bet)} returned to each player."
        else:
            loser = self.p2 if winner.id == self.p1.id else self.p1
            if tax_burned > 0:
                await economy_cog.deposit_vault("bank", tax_burned, source="pvp_wager", context=f"Wordle PvP vs {loser.name}")
            await economy_cog.add_balance(winner.id, winner_payout, context=f"Wordle Wager Win vs {loser.name}")
            net_profit = winner_payout - self.bet
            if self.channel_msg and self.channel_msg.guild:
                await self.cog.record_minigame_win(self.channel_msg.guild.id, winner.id, "wordle", earnings=net_profit)
                await self.cog.record_minigame_loss(self.channel_msg.guild.id, loser.id, "wordle", loss_amount=self.bet)
            return f"\n\n💰 **Wager Payout:** {winner.mention} rbe7 **+{format_tad(winner_payout)}** (Gross: {self.bet*2:,} TAD • `{tax_burned:,}` TAD tax)!"

    def get_player_dm_content(self, player: discord.Member) -> str:
        opponent = self.p2 if player == self.p1 else self.p1
        p_guesses = self.guesses[player.id]
        lines = [
            f"🟩 **Wordle 1v1 Match** vs **{opponent.display_name}**" + (f" (💰 Pot: {format_tad(self.bet*2)})" if self.bet > 0 else ""),
            f"Attempts: **{len(p_guesses)}/6**\n"
        ]

        for g in p_guesses:
            eval_res = evaluate_wordle_guess(g, self.secret)
            pattern = " ".join(e[1] for e in eval_res)
            letters = " ".join(f"**{e[0]}**" for e in eval_res)
            lines.append(f"{pattern}  |  {letters}")

        for _ in range(6 - len(p_guesses)):
            lines.append("⬛ ⬛ ⬛ ⬛ ⬛  |  - - - - -")

        if self.quit[player.id]:
            lines.append("\n🚪 **Khrejti mn lgame.**")
        elif self.finished[player.id]:
            if self.won[player.id]:
                lines.append(f"\n🎉 L9iti lkelma f **{len(p_guesses)}/6**! Kattsna opponent isali.")
            else:
                lines.append("\n💥 Saliti attempts (6/6). Kattsna opponent isali.")

        if self.quit[opponent.id] and not self.game_over:
            lines.append(f"\nℹ️ **{opponent.display_name} khrej mn lmatch**, t9der attempts dialk!")

        if self.game_over:
            lines.append(f"\n🏁 **Match sala!** Lkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    def get_spectator_content(self, eco_msg: str = "") -> str:
        p1_guesses = self.guesses[self.p1.id]
        p2_guesses = self.guesses[self.p2.id]

        if not self.game_over:
            # Spoiler Protected View (Only squares, no letters!)
            wager_str = f" | 💰 Pot: {format_tad(self.bet*2)}" if self.bet > 0 else ""
            lines = [
                f"🟩 **Wordle 1v1 Match (Live Spectator)**{wager_str}",
                f"⚔️ **{self.p1.display_name}** vs **{self.p2.display_name}**\n"
            ]

            p1_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p1.id] else ""
            lines.append(f"🔴 **{self.p1.display_name}** ({len(p1_guesses)}/6){p1_status}:")
            for g in p1_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                lines.append("".join(e[1] for e in eval_res))
            for _ in range(6 - len(p1_guesses)):
                lines.append("⬛⬛⬛⬛⬛")

            p2_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p2.id] else ""
            lines.append(f"\n🔵 **{self.p2.display_name}** ({len(p2_guesses)}/6){p2_status}:")
            for g in p2_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                lines.append("".join(e[1] for e in eval_res))
            for _ in range(6 - len(p2_guesses)):
                lines.append("⬛⬛⬛⬛⬛")

            return "\n".join(lines)
        else:
            # Full Reveal with letters and tiles
            lines = ["🏁 **Wordle 1v1 Match — Final Results**"]
            
            p1_won = self.won[self.p1.id]
            p2_won = self.won[self.p2.id]
            p1_quit = self.quit[self.p1.id]
            p2_quit = self.quit[self.p2.id]
            p1_count = len(p1_guesses)
            p2_count = len(p2_guesses)

            if p1_quit and p2_quit:
                winner_text = "🚪 **Ta wa7d ma rbe7 (bjoj khrejo mn lmatch).**"
            elif p1_quit:
                winner_text = f"🏆 **{self.p2.mention} rbe7!** ({self.p1.display_name} khrej mn lmatch)"
            elif p2_quit:
                winner_text = f"🏆 **{self.p1.mention} rbe7!** ({self.p2.display_name} khrej mn lmatch)"
            elif p1_won and not p2_won:
                winner_text = f"🏆 **{self.p1.mention} rbe7!**"
            elif p2_won and not p1_won:
                winner_text = f"🏆 **{self.p2.mention} rbe7!**"
            elif p1_won and p2_won:
                if p1_count < p2_count:
                    winner_text = f"🏆 **{self.p1.mention} rbe7** (f {p1_count} attempts vs {p2_count})!"
                elif p2_count < p1_count:
                    winner_text = f"🏆 **{self.p2.mention} rbe7** (f {p2_count} attempts vs {p1_count})!"
                else:
                    winner_text = f"🤝 **Ta3adol!** Bjojkom l9itoha f **{p1_count} attempts**!"
            else:
                winner_text = "🤝 **Ta3adol!** Ta wa7d ma l9a lkelma."

            lines.append(f"{winner_text}\nLkelma kant: **{self.secret.upper()}**{eco_msg}\n")

            # Reveal P1
            lines.append(f"🔴 **{self.p1.display_name}** ({p1_count}/6)" + (" (🚪 Khrej)" if p1_quit else "") + ":")
            for g in p1_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                pattern = " ".join(e[1] for e in eval_res)
                letters = " ".join(f"**{e[0]}**" for e in eval_res)
                lines.append(f"{pattern}  |  {letters}")

            # Reveal P2
            lines.append(f"\n🔵 **{self.p2.display_name}** ({p2_count}/6)" + (" (🚪 Khrej)" if p2_quit else "") + ":")
            for g in p2_guesses:
                eval_res = evaluate_wordle_guess(g, self.secret)
                pattern = " ".join(e[1] for e in eval_res)
                letters = " ".join(f"**{e[0]}**" for e in eval_res)
                lines.append(f"{pattern}  |  {letters}")

            return "\n".join(lines)

    async def process_player_guess(self, interaction: discord.Interaction, player: discord.Member, word: str):
        if self.game_over or self.finished[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla saliti attempts dialk.", ephemeral=True)
            return

        self.guesses[player.id].append(word)
        if word == self.secret:
            self.won[player.id] = True
            self.finished[player.id] = True
        elif len(self.guesses[player.id]) >= 6:
            self.finished[player.id] = True

        p1_guesses_len = len(self.guesses[self.p1.id])
        p2_guesses_len = len(self.guesses[self.p2.id])
        p1_won = self.won[self.p1.id]
        p2_won = self.won[self.p2.id]
        p1_quit = self.quit[self.p1.id]
        p2_quit = self.quit[self.p2.id]

        if (self.finished[self.p1.id] or p1_quit) and (self.finished[self.p2.id] or p2_quit):
            self.game_over = True
        elif p1_won and (p2_guesses_len > p1_guesses_len or self.finished[self.p2.id] or p2_quit):
            self.game_over = True
        elif p2_won and (p1_guesses_len > p2_guesses_len or self.finished[self.p1.id] or p1_quit):
            self.game_over = True

        view = self.dm_views.get(player.id)
        if self.finished[player.id] and view:
            for item in view.children:
                if isinstance(item, Button) and item.label == "Type Word":
                    item.disabled = True
        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        eco_msg = ""
        if self.game_over:
            winner = None
            is_draw = False
            if p1_quit and not p2_quit:
                winner = self.p2
            elif p2_quit and not p1_quit:
                winner = self.p1
            elif p1_won and not p2_won:
                winner = self.p1
            elif p2_won and not p1_won:
                winner = self.p2
            elif p1_won and p2_won:
                if p1_guesses_len < p2_guesses_len:
                    winner = self.p1
                elif p2_guesses_len < p1_guesses_len:
                    winner = self.p2
                else:
                    is_draw = True
            else:
                is_draw = True
            eco_msg = await self.handle_economy_payout(winner=winner, is_draw=is_draw)
            if self.cog and hasattr(self.cog, "bot"):
                clear_user_game(self.cog.bot, self.p1.id)
                clear_user_game(self.cog.bot, self.p2.id)

            for p in (self.p1, self.p2):
                dm_msg = self.dm_messages.get(p.id)
                dm_v = self.dm_views.get(p.id)
                if dm_msg and dm_v:
                    for item in dm_v.children:
                        item.disabled = True
                    try:
                        await dm_msg.edit(content=self.get_player_dm_content(p), view=dm_v)
                    except Exception:
                        pass

        try:
            await self.channel_msg.edit(content=self.get_spectator_content(eco_msg=eco_msg))
        except Exception as e:
            print(f"[spectator update error]: {e}")

    async def player_quit(self, interaction: discord.Interaction, player: discord.Member):
        if self.game_over or self.quit[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla khrejti deja.", ephemeral=True)
            return

        self.quit[player.id] = True
        self.finished[player.id] = True

        opponent = self.p2 if player == self.p1 else self.p1

        if self.finished[opponent.id] or self.quit[opponent.id]:
            self.game_over = True

        view = self.dm_views.get(player.id)
        if view:
            for item in view.children:
                item.disabled = True

        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        eco_msg = ""
        if self.game_over:
            if self.cog and hasattr(self.cog, "bot"):
                clear_user_game(self.cog.bot, self.p1.id)
                clear_user_game(self.cog.bot, self.p2.id)
            winner = None
            is_draw = False
            if self.quit[self.p1.id] and self.quit[self.p2.id]:
                is_draw = True
            elif self.quit[self.p1.id]:
                winner = self.p2
            elif self.quit[self.p2.id]:
                winner = self.p1
            eco_msg = await self.handle_economy_payout(winner=winner, is_draw=is_draw)

        try:
            await self.channel_msg.edit(content=self.get_spectator_content(eco_msg=eco_msg))
        except Exception as e:
            print(f"[spectator update on quit error]: {e}")

        opp_msg = self.dm_messages.get(opponent.id)
        opp_v = self.dm_views.get(opponent.id)
        if opp_msg and opp_v:
            if self.game_over:
                for item in opp_v.children:
                    item.disabled = True
            try:
                await opp_msg.edit(content=self.get_player_dm_content(opponent), view=opp_v)
            except Exception:
                pass

    async def handle_user_quit(self, user: Union[discord.Member, discord.User]) -> str:
        if self.game_over or self.quit.get(user.id, False):
            return ""
        self.quit[user.id] = True
        self.finished[user.id] = True
        self.game_over = True
        opponent = self.p2 if user.id == self.p1.id else self.p1
        eco_msg = await self.handle_economy_payout(winner=opponent, is_draw=False)
        if self.cog and hasattr(self.cog, "bot"):
            clear_user_game(self.cog.bot, self.p1.id)
            clear_user_game(self.cog.bot, self.p2.id)

        for p in (self.p1, self.p2):
            dm_msg = self.dm_messages.get(p.id)
            dm_v = self.dm_views.get(p.id)
            if dm_msg and dm_v:
                for item in dm_v.children:
                    item.disabled = True
                try:
                    await dm_msg.edit(content=self.get_player_dm_content(p), view=dm_v)
                except Exception:
                    pass

        try:
            await self.channel_msg.edit(content=self.get_spectator_content(eco_msg=eco_msg))
        except Exception:
            pass

        return f"🚪 Kherjti mn match dial **Wordle** o t-3tbat forfeit!"


class WordleChallengeView(View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, cog: "Minigames", bet: int = 0, difficulty: str = "easy"):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.cog = cog
        self.bet = bet
        self.difficulty = difficulty
        self.message: Optional[discord.Message] = None
        self.accepted = False

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success, emoji="✅")
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
            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            if economy_cog:
                w1 = await economy_cog.get_wallet(self.challenger.id)
                w2 = await economy_cog.get_wallet(self.challenged.id)
                if w1["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ {self.challenger.mention} ma b9ach 3ndo kafi dial flous!", ephemeral=True)
                    return
                if w2["balance"] < self.bet:
                    await interaction.response.send_message(f"❌ Flousk makafyinch ({format_tad(w2['balance'])} / {format_tad(self.bet)})!", ephemeral=True)
                    return
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"Wordle Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"Wordle Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        secret = self.cog.get_wordle_secret(self.difficulty)
        match = WordleMultiplayerMatch(self.challenger, self.challenged, interaction.message, secret, self.cog, bet=self.bet)

        set_user_in_game(self.cog.bot, self.challenger.id, "Wordle", match)
        set_user_in_game(self.cog.bot, self.challenged.id, "Wordle", match)

        try:
            p1_view = WordleDMView(match, self.challenger)
            p1_msg = await self.challenger.send(content=match.get_player_dm_content(self.challenger), view=p1_view)
            match.dm_messages[self.challenger.id] = p1_msg
            match.dm_views[self.challenger.id] = p1_view
        except discord.Forbidden:
            clear_user_game(self.cog.bot, self.challenger.id)
            clear_user_game(self.cog.bot, self.challenged.id)
            if self.bet > 0:
                economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                if economy_cog:
                    await economy_cog.add_balance(self.challenger.id, self.bet, context="Wordle Refund (DM Closed)")
                    await economy_cog.add_balance(self.challenged.id, self.bet, context="Wordle Refund (DM Closed)")
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenger.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        try:
            p2_view = WordleDMView(match, self.challenged)
            p2_msg = await self.challenged.send(content=match.get_player_dm_content(self.challenged), view=p2_view)
            match.dm_messages[self.challenged.id] = p2_msg
            match.dm_views[self.challenged.id] = p2_view
        except discord.Forbidden:
            clear_user_game(self.cog.bot, self.challenger.id)
            clear_user_game(self.cog.bot, self.challenged.id)
            if self.bet > 0:
                economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                if economy_cog:
                    await economy_cog.add_balance(self.challenger.id, self.bet, context="Wordle Refund (DM Closed)")
                    await economy_cog.add_balance(self.challenged.id, self.bet, context="Wordle Refund (DM Closed)")
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenged.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        await interaction.response.edit_message(content=match.get_spectator_content(), view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Ta wa7d ma challengak nta.", ephemeral=True)
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=f"❌ {self.challenged.mention} mabghach il3eb.",
            view=self
        )

    async def on_timeout(self):
        if not self.accepted:
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(content="⏰ Challenge ma t acceptach.", view=self)
                except discord.NotFound:
                    pass

