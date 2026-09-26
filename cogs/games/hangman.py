from __future__ import annotations
import random
import asyncio
from typing import Optional, Union

import discord
from discord.ui import Button, View, Modal, TextInput

from cogs.economy import format_tad, TAD_EMOJI, calculate_pvp_payout
from cogs.games.helpers import (
    get_hangman_secret,
    record_minigame_win, record_minigame_loss,
    is_user_in_game, set_user_in_game, clear_user_game
)

# ============ HANGMAN HELPERS & UI CLASSES ============

HANGMAN_STAGES = [
    """
  +---+
  |   |
      |
      |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
      |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
  |   |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|   |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|\\  |
      |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|\\  |
 /    |
      |
=========""",
    """
  +---+
  |   |
  O   |
 /|\\  |
 / \\  |
      |
========="""
]


class HangmanSoloModal(Modal, title="Hangman — Guess"):
    guess_input = TextInput(
        label="Letter or Full Word",
        placeholder="e.g. E, A, or PLANET...",
        min_length=1,
        max_length=20,
        required=True
    )

    def __init__(self, view: "HangmanSoloView"):
        super().__init__()
        self.game_view = view

    async def on_submit(self, interaction: discord.Interaction):
        guess_str = self.guess_input.value.strip().lower()
        if not guess_str.isalpha():
            await interaction.response.send_message("❌ Dkhel 7arf wla kelma s7i7a.", ephemeral=True)
            return

        await self.game_view.process_guess(interaction, guess_str)


class HangmanSoloView(View):
    def __init__(self, player: discord.Member, secret: str, cog: "Minigames", difficulty: str = "easy"):
        super().__init__(timeout=300)
        self.player = player
        self.secret = secret.lower()
        self.cog = cog
        self.difficulty = difficulty
        self.guessed_letters: set[str] = set()
        self.wrong_guesses: list[str] = []
        self.game_over = False
        self.won = False
        self.message: Optional[discord.Message] = None

    def get_content(self) -> str:
        mistakes = len(self.wrong_guesses)
        stage_ascii = HANGMAN_STAGES[min(mistakes, 6)]
        lives = max(0, 6 - mistakes)
        diff_mult = DIFFICULTY_STAKES.get(self.difficulty, 1.0)

        masked = " ".join(ch.upper() if ch in self.guessed_letters else "\\_" for ch in self.secret)
        wrong_str = ", ".join(w.upper() for w in self.wrong_guesses) if self.wrong_guesses else "None"

        lines = [
            f"🪢 **Hangman (Solo)** — L9a lkelma 9bel ma tchn9! (🎯 **{self.difficulty.upper()}** • **{diff_mult}x**)",
            f"Lives: **{lives}/6** ❤️ | Length: **{len(self.secret)} letters**",
            f"```{stage_ascii}```",
            f"Word: `{masked}`",
            f"Wrong guesses: **{wrong_str}**"
        ]

        if self.game_over:
            if self.won:
                lines.append(f"\n🎉🏆 **Rbe7ti!** L9iti lkelma 9bel ma tchn9!\nLkelma kant: **{self.secret.upper()}**")
            else:
                lines.append(f"\n💀 **Game Over!** Tchn9ti!\nLkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player:
            await interaction.response.send_message("Machy nta li m9ssr had lgame.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="🔤")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.game_over:
            await interaction.response.send_message("Had lgame deja salat.", ephemeral=True)
            return
        await interaction.response.send_modal(HangmanSoloModal(self))

    @discord.ui.button(label="Exit Game", style=discord.ButtonStyle.danger, emoji="🚪")
    async def exit_button(self, interaction: discord.Interaction, button: Button):
        self.game_over = True
        self.stop()
        for item in self.children:
            item.disabled = True
        content = self.get_content() + f"\n\n🚪 {self.player.mention} khrej mn lgame."
        await interaction.response.edit_message(content=content, view=self)

    async def process_guess(self, interaction: discord.Interaction, guess: str):
        if len(guess) == 1:
            if guess in self.guessed_letters or guess in self.wrong_guesses:
                await interaction.response.send_message("⚠️ Deja guessiti had l7arf.", ephemeral=True)
                return
            if guess in self.secret:
                self.guessed_letters.add(guess)
                if all(ch in self.guessed_letters for ch in self.secret):
                    self.game_over = True
                    self.won = True
            else:
                self.wrong_guesses.append(guess)
                if len(self.wrong_guesses) >= 6:
                    self.game_over = True
        else:
            if guess == self.secret:
                for ch in self.secret:
                    self.guessed_letters.add(ch)
                self.game_over = True
                self.won = True
            else:
                if guess not in self.wrong_guesses:
                    self.wrong_guesses.append(guess)
                if len(self.wrong_guesses) >= 6:
                    self.game_over = True

        if self.game_over:
            self.stop()
            for item in self.children:
                item.disabled = True

            economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
            eco_msg = ""
            diff_mult = DIFFICULTY_STAKES.get(self.difficulty, 1.0)
            if economy_cog:
                if self.won:
                    lives_left = max(0, 6 - len(self.wrong_guesses))
                    base = 200 if lives_left >= 5 else (100 if lives_left >= 3 else 50)
                    gross = int(round(base * diff_mult))
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, gross, context=f"Hangman Solo ({self.difficulty.capitalize()} • {lives_left}/6 HP)")
                    eco_msg = f"\n\n💰 Rbe7ti **+{net}** {TAD_EMOJI} TAD (Gross: {gross} TAD • `{tax}` TAD tax)!"
                    if interaction.guild:
                        await self.cog.record_minigame_win(interaction.guild.id, self.player.id, "hangman", earnings=net)
                else:
                    gross = int(round(20 * diff_mult))
                    net, tax = await economy_cog.apply_tax_and_add_balance(self.player.id, gross, context=f"Hangman Participation ({self.difficulty.capitalize()})")
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
        return "🚪 Kherjti mn Hangman!"

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

class HangmanMultiplayerModal(Modal, title="Hangman 1v1 — Guess"):
    guess_input = TextInput(
        label="Letter or Full Word",
        placeholder="e.g. E, A, or PLANET...",
        min_length=1,
        max_length=20,
        required=True
    )

    def __init__(self, match: "HangmanMultiplayerMatch", player: discord.Member):
        super().__init__()
        self.match = match
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        guess_str = self.guess_input.value.strip().lower()
        if not guess_str.isalpha():
            await interaction.response.send_message("❌ Dkhel 7arf wla kelma s7i7a.", ephemeral=True)
            return

        await self.match.process_player_guess(interaction, self.player, guess_str)


class HangmanDMView(View):
    def __init__(self, match: "HangmanMultiplayerMatch", player: discord.Member):
        super().__init__(timeout=300)
        self.match = match
        self.player = player

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="🔤")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        if self.match.game_over or self.match.finished.get(self.player.id, False):
            await interaction.response.send_message("Saliti attempts dialk wla lmatch deja sala.", ephemeral=True)
            return
        await interaction.response.send_modal(HangmanMultiplayerModal(self.match, self.player))

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


class HangmanMultiplayerMatch:
    def __init__(self, p1: discord.Member, p2: discord.Member, channel_msg: discord.Message, secret: str, cog: "Minigames", bet: int = 0):
        self.p1 = p1
        self.p2 = p2
        self.channel_msg = channel_msg
        self.secret = secret.lower()
        self.cog = cog
        self.bet = bet
        self.payout_handled = False

        self.guessed_letters = {p1.id: set(), p2.id: set()}
        self.wrong_guesses = {p1.id: [], p2.id: []}
        self.finished = {p1.id: False, p2.id: False}
        self.won = {p1.id: False, p2.id: False}
        self.quit = {p1.id: False, p2.id: False}
        self.dm_messages: dict[int, discord.Message] = {}
        self.dm_views: dict[int, HangmanDMView] = {}
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
            await economy_cog.add_balance(self.p1.id, self.bet, context="Hangman Draw Refund")
            await economy_cog.add_balance(self.p2.id, self.bet, context="Hangman Draw Refund")
            return f"\n\n🤝 **Draw Refund:** {format_tad(self.bet)} returned to each player."
        else:
            loser = self.p2 if winner.id == self.p1.id else self.p1
            if tax_burned > 0:
                await economy_cog.deposit_vault("bank", tax_burned, source="pvp_wager", context=f"Hangman PvP vs {loser.name}")
            await economy_cog.add_balance(winner.id, winner_payout, context=f"Hangman Wager Win vs {loser.name}")
            net_profit = winner_payout - self.bet
            if self.channel_msg and self.channel_msg.guild:
                await self.cog.record_minigame_win(self.channel_msg.guild.id, winner.id, "hangman", earnings=net_profit)
                await self.cog.record_minigame_loss(self.channel_msg.guild.id, loser.id, "hangman", loss_amount=self.bet)
            return f"\n\n💰 **Wager Payout:** {winner.mention} rbe7 **+{format_tad(winner_payout)}** (Gross: {self.bet*2:,} TAD • `{tax_burned:,}` TAD tax)!"

    def get_player_dm_content(self, player: discord.Member) -> str:
        opponent = self.p2 if player == self.p1 else self.p1
        p_guessed = self.guessed_letters[player.id]
        p_wrong = self.wrong_guesses[player.id]
        mistakes = len(p_wrong)
        stage_ascii = HANGMAN_STAGES[min(mistakes, 6)]
        lives = max(0, 6 - mistakes)

        masked = " ".join(ch.upper() if ch in p_guessed else "\\_" for ch in self.secret)
        wrong_str = ", ".join(w.upper() for w in p_wrong) if p_wrong else "None"

        lines = [
            f"🪢 **Hangman 1v1 Match** vs **{opponent.display_name}**" + (f" (💰 Pot: {format_tad(self.bet*2)})" if self.bet > 0 else ""),
            f"Lives: **{lives}/6** ❤️ | Length: **{len(self.secret)} letters**",
            f"```{stage_ascii}```",
            f"Word: `{masked}`",
            f"Wrong guesses: **{wrong_str}**"
        ]

        if self.quit[player.id]:
            lines.append("\n🚪 **Khrejti mn lgame.**")
        elif self.finished[player.id]:
            if self.won[player.id]:
                lines.append(f"\n🎉 **L9iti lkelma!** ({mistakes} wrong guesses). Tsna l opponent isali.")
            else:
                lines.append("\n💀 **Tchn9ti!** (6/6 mistakes). Tsna l opponent isali.")

        if self.quit[opponent.id] and not self.game_over:
            lines.append(f"\nℹ️ **{opponent.display_name} khrej mn lmatch**, t9der tkml attempts dialk!")

        if self.game_over:
            lines.append(f"\n🏁 **Match sala!** Lkelma kant: **{self.secret.upper()}**")

        return "\n".join(lines)

    def get_spectator_content(self, eco_msg: str = "") -> str:
        p1_wrong = self.wrong_guesses[self.p1.id]
        p2_wrong = self.wrong_guesses[self.p2.id]
        p1_guessed = self.guessed_letters[self.p1.id]
        p2_guessed = self.guessed_letters[self.p2.id]

        p1_solved_count = sum(1 for ch in self.secret if ch in p1_guessed)
        p2_solved_count = sum(1 for ch in self.secret if ch in p2_guessed)

        if not self.game_over:
            # Spoiler Protected View
            wager_str = f" | 💰 Pot: {format_tad(self.bet*2)}" if self.bet > 0 else ""
            lines = [
                f"🪢 **Hangman 1v1 Match (Live Spectator)**{wager_str}",
                f"⚔️ **{self.p1.display_name}** vs **{self.p2.display_name}**\n"
            ]

            p1_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p1.id] else ""
            p1_lives = max(0, 6 - len(p1_wrong))
            lines.append(f"🔴 **{self.p1.display_name}**{p1_status}:")
            lines.append(f"❤️ Lives: **{p1_lives}/6** | Letters found: **{p1_solved_count}/{len(self.secret)}** | Mistakes: **{len(p1_wrong)}/6**")
            lines.append(f"```{HANGMAN_STAGES[min(len(p1_wrong), 6)]}```")

            p2_status = " — 🚪 *Khrej mn lmatch*" if self.quit[self.p2.id] else ""
            p2_lives = max(0, 6 - len(p2_wrong))
            lines.append(f"\n🔵 **{self.p2.display_name}**{p2_status}:")
            lines.append(f"❤️ Lives: **{p2_lives}/6** | Letters found: **{p2_solved_count}/{len(self.secret)}** | Mistakes: **{len(p2_wrong)}/6**")
            lines.append(f"```{HANGMAN_STAGES[min(len(p2_wrong), 6)]}```")

            return "\n".join(lines)
        else:
            # Full Reveal
            lines = ["🏁 **Hangman 1v1 Match — Final Results**"]

            p1_won = self.won[self.p1.id]
            p2_won = self.won[self.p2.id]
            p1_quit = self.quit[self.p1.id]
            p2_quit = self.quit[self.p2.id]
            p1_mistakes = len(p1_wrong)
            p2_mistakes = len(p2_wrong)

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
                if p1_mistakes < p2_mistakes:
                    winner_text = f"🏆 **{self.p1.mention} rbe7** (b {p1_mistakes} mistakes vs {p2_mistakes})!"
                elif p2_mistakes < p1_mistakes:
                    winner_text = f"🏆 **{self.p2.mention} rbe7** (b {p2_mistakes} mistakes vs {p1_mistakes})!"
                else:
                    winner_text = f"🤝 **Ta3adol!** Bjojkom l9itoha b **{p1_mistakes} mistakes**!"
            else:
                winner_text = "💀 **Ta3adol!** Bjoj tchn9to."

            lines.append(f"{winner_text}\nLkelma kant: **{self.secret.upper()}**{eco_msg}\n")

            # Reveal P1
            p1_masked = " ".join(ch.upper() if ch in p1_guessed else "\\_" for ch in self.secret)
            lines.append(f"🔴 **{self.p1.display_name}**" + (" (🚪 Khrej)" if p1_quit else "") + f": `{p1_masked}` (Mistakes: **{p1_mistakes}/6**)")
            lines.append(f"```{HANGMAN_STAGES[min(p1_mistakes, 6)]}```")

            # Reveal P2
            p2_masked = " ".join(ch.upper() if ch in p2_guessed else "\\_" for ch in self.secret)
            lines.append(f"\n🔵 **{self.p2.display_name}**" + (" (🚪 Khrej)" if p2_quit else "") + f": `{p2_masked}` (Mistakes: **{p2_mistakes}/6**)")
            lines.append(f"```{HANGMAN_STAGES[min(p2_mistakes, 6)]}```")

            return "\n".join(lines)

    async def process_player_guess(self, interaction: discord.Interaction, player: discord.Member, guess: str):
        if self.game_over or self.finished[player.id]:
            await interaction.response.send_message("Lmatch deja sala wla saliti attempts dialk.", ephemeral=True)
            return

        p_guessed = self.guessed_letters[player.id]
        p_wrong = self.wrong_guesses[player.id]

        if len(guess) == 1:
            if guess in p_guessed or guess in p_wrong:
                await interaction.response.send_message("⚠️ Deja guessiti had l7arf.", ephemeral=True)
                return
            if guess in self.secret:
                p_guessed.add(guess)
                if all(ch in p_guessed for ch in self.secret):
                    self.won[player.id] = True
                    self.finished[player.id] = True
            else:
                p_wrong.append(guess)
                if len(p_wrong) >= 6:
                    self.finished[player.id] = True
        else:
            if guess == self.secret:
                for ch in self.secret:
                    p_guessed.add(ch)
                self.won[player.id] = True
                self.finished[player.id] = True
            else:
                if guess not in p_wrong:
                    p_wrong.append(guess)
                if len(p_wrong) >= 6:
                    self.finished[player.id] = True

        p1_won = self.won[self.p1.id]
        p2_won = self.won[self.p2.id]
        p1_quit = self.quit[self.p1.id]
        p2_quit = self.quit[self.p2.id]

        if (self.finished[self.p1.id] or p1_quit) and (self.finished[self.p2.id] or p2_quit):
            self.game_over = True

        view = self.dm_views.get(player.id)
        if self.finished[player.id] and view:
            for item in view.children:
                if isinstance(item, Button) and item.label == "Guess":
                    item.disabled = True
        await interaction.response.edit_message(content=self.get_player_dm_content(player), view=view)

        eco_msg = ""
        if self.game_over:
            winner = None
            is_draw = False
            p1_mistakes = len(self.wrong_guesses[self.p1.id])
            p2_mistakes = len(self.wrong_guesses[self.p2.id])
            if p1_quit and not p2_quit:
                winner = self.p2
            elif p2_quit and not p1_quit:
                winner = self.p1
            elif p1_won and not p2_won:
                winner = self.p1
            elif p2_won and not p1_won:
                winner = self.p2
            elif p1_won and p2_won:
                if p1_mistakes < p2_mistakes:
                    winner = self.p1
                elif p2_mistakes < p1_mistakes:
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
            print(f"[hangman spectator update error]: {e}")

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
            print(f"[hangman spectator update on quit error]: {e}")

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

        return f"🚪 Kherjti mn match dial **Hangman** o t-3tbat forfeit!"


class HangmanChallengeView(View):
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
                await economy_cog.deduct_balance(self.challenger.id, self.bet, context=f"Hangman Wager Stake ({self.bet} TAD)")
                await economy_cog.deduct_balance(self.challenged.id, self.bet, context=f"Hangman Wager Stake ({self.bet} TAD)")

        self.accepted = True
        self.stop()

        secret = self.cog.get_hangman_secret(self.difficulty)
        match = HangmanMultiplayerMatch(self.challenger, self.challenged, interaction.message, secret, self.cog, bet=self.bet)

        set_user_in_game(self.cog.bot, self.challenger.id, "Hangman", match)
        set_user_in_game(self.cog.bot, self.challenged.id, "Hangman", match)

        try:
            p1_view = HangmanDMView(match, self.challenger)
            p1_msg = await self.challenger.send(content=match.get_player_dm_content(self.challenger), view=p1_view)
            match.dm_messages[self.challenger.id] = p1_msg
            match.dm_views[self.challenger.id] = p1_view
        except discord.Forbidden:
            clear_user_game(self.cog.bot, self.challenger.id)
            clear_user_game(self.cog.bot, self.challenged.id)
            if self.bet > 0:
                economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                if economy_cog:
                    await economy_cog.add_balance(self.challenger.id, self.bet, context="Hangman Refund (DM Closed)")
                    await economy_cog.add_balance(self.challenged.id, self.bet, context="Hangman Refund (DM Closed)")
            await interaction.response.edit_message(
                content=f"❌ Man9edch nsift DM l **{self.challenger.display_name}**. Khasso i7el DMs.",
                view=None
            )
            return

        try:
            p2_view = HangmanDMView(match, self.challenged)
            p2_msg = await self.challenged.send(content=match.get_player_dm_content(self.challenged), view=p2_view)
            match.dm_messages[self.challenged.id] = p2_msg
            match.dm_views[self.challenged.id] = p2_view
        except discord.Forbidden:
            clear_user_game(self.cog.bot, self.challenger.id)
            clear_user_game(self.cog.bot, self.challenged.id)
            if self.bet > 0:
                economy_cog = self.cog.bot.get_cog("Economy") if self.cog else None
                if economy_cog:
                    await economy_cog.add_balance(self.challenger.id, self.bet, context="Hangman Refund (DM Closed)")
                    await economy_cog.add_balance(self.challenged.id, self.bet, context="Hangman Refund (DM Closed)")
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


