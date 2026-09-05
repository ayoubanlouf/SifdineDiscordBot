import difflib
import re
from typing import Union
import discord
from discord.ext import commands


class FuzzyMember(commands.Converter[Union[discord.Member, discord.User]]):
    async def convert(self, ctx: commands.Context, argument: str) -> Union[discord.Member, discord.User]:
        arg_clean_str = argument.strip()

        # 1. Exact resolution within current guild or shared guilds (IDs, @mentions, exact usernames/nicknames)
        try:
            return await commands.MemberConverter().convert(ctx, arg_clean_str)
        except commands.MemberNotFound:
            pass

        # 2. If argument is an ID or mention, resolve globally as discord.User (even across other servers or outside guilds)
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
            # If in DMs and not an ID, try UserConverter for cached users
            try:
                return await commands.UserConverter().convert(ctx, arg_clean_str)
            except commands.UserNotFound:
                raise commands.MemberNotFound(argument)

        arg_clean = arg_clean_str.lower()
        matches = []

        # 3. Compare input against server members (dynamically query gateway or fallback to cache)
        try:
            members = await ctx.guild.query_members(query=argument, limit=50)
        except Exception:
            members = ctx.guild.members

        for member in members:
            names = {
                member.name.lower(),
                member.display_name.lower(),
            }
            if member.global_name:
                names.add(member.global_name.lower())
            if member.nick:
                names.add(member.nick.lower())

            highest_score_for_member = max(
                difflib.SequenceMatcher(None, arg_clean, name).ratio()
                for name in names
            )

            if highest_score_for_member >= 0.50:
                matches.append((highest_score_for_member, member))

        # 4. Select maximum similarity score
        if matches:
            best_match = max(matches, key=lambda x: x[0])[1]
            return best_match

        raise commands.MemberNotFound(argument)