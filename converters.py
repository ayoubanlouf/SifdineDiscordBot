import difflib
import re
from typing import Union
import discord
from discord.ext import commands


import unicodedata
from typing import Union, List


def normalize_name(text: str) -> str:
    """Strips unicode decorators, symbols, brackets (clan tags), and trims whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', text)
    # Remove clan/team tags like [TAG] or (TAG)
    text = re.sub(r'^[\[\(].*?[\]\)]\s*', '', text)
    text = re.sub(r'\s*[\[\(].*?[\]\)]$', '', text)
    # Remove emojis and non-alphanumeric/non-space/non-common punctuation
    text = re.sub(r'[^\w\s\.\-_]', '', text)
    return text.strip().lower()


class FuzzyMember(commands.Converter[Union[discord.Member, discord.User]]):
    async def convert(self, ctx: commands.Context, argument: str) -> Union[discord.Member, discord.User]:
        arg_clean_str = argument.strip()

        # 1. Standard MemberConverter (exact mentions, user#0000, exact ID, or exact name in guild)
        try:
            return await commands.MemberConverter().convert(ctx, arg_clean_str)
        except commands.MemberNotFound:
            pass

        # 2. If argument looks like an ID or mention, resolve globally as discord.User
        match = re.match(r'^<@!?([0-9]{15,20})>$|^([0-9]{15,20})$', arg_clean_str)
        if match or arg_clean_str.isdigit():
            user_id_str = match.group(1) or match.group(2) if match else arg_clean_str
            try:
                user_id = int(user_id_str)
                user = ctx.bot.get_user(user_id)
                if not user:
                    user = await ctx.bot.fetch_user(user_id)
                if user:
                    return user
            except (discord.NotFound, discord.HTTPException, ValueError):
                pass

        if not ctx.guild:
            try:
                return await commands.UserConverter().convert(ctx, arg_clean_str)
            except commands.UserNotFound:
                raise commands.MemberNotFound(argument)

        # 3. Build candidate pool: query gateway first, fallback to cached guild members
        members: List[discord.Member] = []
        try:
            members = await ctx.guild.query_members(query=arg_clean_str, limit=50)
        except Exception:
            pass
        if not members:
            members = list(ctx.guild.members)

        arg_lower = arg_clean_str.lower()
        arg_norm = normalize_name(arg_clean_str)

        exact_matches = []
        prefix_matches = []
        word_boundary_matches = []
        substring_matches = []
        fuzzy_matches = []

        for member in members:
            raw_names = [member.name, member.display_name]
            if member.global_name:
                raw_names.append(member.global_name)
            if member.nick:
                raw_names.append(member.nick)

            lower_names = [n.lower() for n in raw_names]
            norm_names = [normalize_name(n) for n in raw_names if normalize_name(n)]

            # Tier 2: Exact case-insensitive match
            if any(n == arg_lower for n in lower_names) or (arg_norm and any(n == arg_norm for n in norm_names)):
                exact_matches.append(member)
                continue

            # Tier 3: Prefix match
            if any(n.startswith(arg_lower) for n in lower_names) or (arg_norm and any(n.startswith(arg_norm) for n in norm_names)):
                prefix_matches.append(member)
                continue

            # Tier 4: Word-boundary match (e.g. searching "anlouf" for "Ayoub Anlouf")
            has_word_match = False
            for name in lower_names:
                words = re.split(r'[\s\.\-_]+', name)
                if any(w.startswith(arg_lower) for w in words if w):
                    word_boundary_matches.append(member)
                    has_word_match = True
                    break
            if has_word_match:
                continue

            # Tier 5: Substring match
            if any(arg_lower in n for n in lower_names) or (arg_norm and any(arg_norm in n for n in norm_names)):
                substring_matches.append(member)
                continue

            # Tier 6: Normalized Fuzzy Similarity
            best_score = 0.0
            for n in norm_names:
                score = difflib.SequenceMatcher(None, arg_norm, n).ratio()
                if score > best_score:
                    best_score = score

            if best_score >= 0.65:
                fuzzy_matches.append((best_score, member))

        if exact_matches:
            return exact_matches[0]
        if prefix_matches:
            prefix_matches.sort(key=lambda m: len(m.display_name))
            return prefix_matches[0]
        if word_boundary_matches:
            word_boundary_matches.sort(key=lambda m: len(m.display_name))
            return word_boundary_matches[0]
        if substring_matches:
            substring_matches.sort(key=lambda m: len(m.display_name))
            return substring_matches[0]
        if fuzzy_matches:
            fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
            return fuzzy_matches[0][1]

        # 4. Final fallback: search global bot users cache
        for user in ctx.bot.users:
            u_names = [user.name.lower()]
            if user.global_name:
                u_names.append(user.global_name.lower())
            if any(n == arg_lower or n.startswith(arg_lower) for n in u_names):
                return user

        raise commands.MemberNotFound(argument)



class AmountConverter(commands.Converter[int]):
    async def convert(self, ctx: commands.Context, argument: str) -> int:
        arg_clean = argument.strip().lower().replace(",", "")
        
        # Check natural keywords if economy cog available
        if arg_clean in ("all", "max", "kolchi"):
            economy_cog = ctx.bot.get_cog("Economy")
            if economy_cog:
                w = await economy_cog.get_wallet(ctx.author.id)
                val = w.get("balance", 0)
                if val > 0:
                    return val
            raise commands.BadArgument("Ma3ndkch flous f wallet dialk.")
        elif arg_clean in ("half", "ness", "50%"):
            economy_cog = ctx.bot.get_cog("Economy")
            if economy_cog:
                w = await economy_cog.get_wallet(ctx.author.id)
                val = w.get("balance", 0) // 2
                if val > 0:
                    return val
            raise commands.BadArgument("Ma3ndkch flous kafyin f wallet dialk.")

        pattern = re.compile(r"^(?:amount:|amt:)?([0-9]+(?:\.[0-9]+)?)(k|m|mil|kilo|b|bil|tad|t|drhm|drhem)?$", re.IGNORECASE)
        m = pattern.match(arg_clean)
        if not m:
            raise commands.BadArgument(f"'{argument}' machi valid amount. Kteb b7al `500`, `50k`, `1.5m`.")

        num_str, suffix = m.group(1), m.group(2)
        try:
            num_val = float(num_str)
            if suffix:
                s = suffix.lower()
                if s in ("k", "kilo"):
                    num_val *= 1000
                elif s in ("m", "mil"):
                    num_val *= 1000000
                elif s in ("b", "bil"):
                    num_val *= 1000000000
            val = int(round(num_val))
            if val <= 0:
                raise commands.BadArgument("Amount khas ykoun kber mn 0.")
            return val
        except ValueError:
            raise commands.BadArgument(f"'{argument}' machi amount valid.")

