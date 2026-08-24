import discord
from discord.ext import commands, tasks
from collections import deque
from datetime import datetime, timezone
import urllib
import difflib
import traceback

_mymemory_supported_languages = None


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if not hasattr(bot, "snipe_cache"):
            bot.snipe_cache = {}
        if not hasattr(bot, "edit_cache"):
            bot.edit_cache = {}
        if not hasattr(bot, "reaction_cache"):
            bot.reaction_cache = {}

        self.cleanup_snipe_cache.start()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Logged in as {self.bot.user.name}.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            if ctx.command.parent:
                parent_command = ctx.command.full_parent_name
                command_signature = ctx.command.signature
                correct_usage = f"sat {parent_command} {ctx.command.name} {command_signature}"
            else:
                correct_usage = f"sat {ctx.command.name} {ctx.command.signature}"

            aliases_str = '|'.join(ctx.command.aliases) if ctx.command.aliases else "_"
            e = discord.Embed(
                title="Khassk argument :/",
                description=f"**Dir b7al hka:** `{correct_usage}`\n**Aliases:** `[{aliases_str}]`",
                color=0x000000
            )
            await ctx.reply(embed=e)

        elif isinstance(error, commands.MaxConcurrencyReached):
            await ctx.reply("Rani khdam f command okhra, sber ta tsali.")

        elif isinstance(error, commands.NoPrivateMessage):
            try:
                await ctx.author.send(f'{ctx.command} makhdamach f DMs.')
            except discord.HTTPException:
                pass

        elif isinstance(error, commands.BadArgument):
            if ctx.command.parent:
                parent_command = ctx.command.full_parent_name
                command_signature = ctx.command.signature
                correct_usage = f"sat {parent_command} {ctx.command.name} {command_signature}"
            else:
                correct_usage = f"sat {ctx.command.name} {ctx.command.signature}"

            aliases_str = '|'.join(ctx.command.aliases) if ctx.command.aliases else "_"
            e = discord.Embed(
                title="Argument ghalt :/",
                description=f"**Dir b7al hka:** `{correct_usage}`\n**Aliases:** `[{aliases_str}]`",
                color=0x000000
            )
            await ctx.reply(embed=e)

        elif isinstance(error, commands.DisabledCommand):
            await ctx.reply(f'**{ctx.command}** makhdamach db.')

        elif isinstance(error, commands.MissingPermissions):
            await ctx.reply("Ma3ndkch l permissions bach tdir had lcommand.")

        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.reply("Ma3endich l permissions bach ndir had lcommand.")
        
        elif isinstance(error, commands.CommandNotFound):
            invoked_cmd = ctx.invoked_with.lower() if ctx.invoked_with else ""
            if not invoked_cmd:
                return

            all_cmd_names = []
            for cmd in self.bot.commands:
                if not cmd.hidden:
                    all_cmd_names.append(cmd.name.lower())
                    all_cmd_names.extend([a.lower() for a in cmd.aliases])

            matches = difflib.get_close_matches(invoked_cmd, all_cmd_names, n=1, cutoff=0.5)
            if matches:
                suggested = matches[0]
                await ctx.send(f"Wa9ila biti tgoul `{ctx.prefix}{suggested}`?")
            return
        else:
            traceback.print_exception(type(error), error, error.__traceback__)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        async with self.bot.db.execute("SELECT 1 FROM afk WHERE user_id = ?", (message.author.id,)) as cursor:
            if await cursor.fetchone():
                await self.bot.db.execute("DELETE FROM afk WHERE user_id = ?", (message.author.id,))
                await self.bot.db.commit()
                await message.reply(f"3la slamto.", mention_author=False)

        if message.mentions:
            for mentioned in message.mentions:
                if mentioned.id == message.author.id:
                    continue

                async with self.bot.db.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?",
                                               (mentioned.id,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        reason, ts = row[0], row[1]
                        time_tag = f"<t:{ts}:R>"
                        await message.channel.send(
                            f"**{mentioned.name}** mamsalich, galik \"{reason}\" ({time_tag})",
                            allowed_mentions=discord.AllowedMentions.none()
                        )

        ctx = await self.bot.get_context(message)

        if ctx.prefix and not ctx.command:
            cleaned_content = message.content.strip().lower()
            cleaned_prefix = ctx.prefix.strip().lower()

            if cleaned_content == cleaned_prefix:
                await ctx.send("we")
                return

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.author.bot or not message.guild:
            return

        channel_id = message.channel.id
        if channel_id not in self.bot.snipe_cache:
            self.bot.snipe_cache[channel_id] = deque(maxlen=5)

        self.bot.snipe_cache[channel_id].append({
            "content": message.content,
            "author_name": message.author.name,
            "author_avatar": message.author.display_avatar.url,
            "time": message.created_at,
            "attachment": message.attachments[0].url if message.attachments else None
        })

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.author.bot or not before.guild or before.content == after.content:
            return

        if before.content != after.content:
            await self.bot.process_commands(after)

        channel_id = before.channel.id
        if channel_id not in self.bot.edit_cache:
            self.bot.edit_cache[channel_id] = deque(maxlen=5)

        self.bot.edit_cache[channel_id].append({
            "old_content": before.content,
            "new_content": after.content,
            "author_name": before.author.name,
            "author_avatar": before.author.display_avatar.url,
            "time": after.edited_at or datetime.utcnow()
        })

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if not payload.guild_id:
            return

        channel_id = payload.channel_id
        if channel_id not in self.bot.reaction_cache:
            self.bot.reaction_cache[channel_id] = deque(maxlen=5)

        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if member and member.bot:
            return

        username = member.name if member else f"User ID {payload.user_id}"
        avatar = member.display_avatar.url if member else None

        self.bot.reaction_cache[channel_id].append({
            "emoji": str(payload.emoji),
            "message_id": payload.message_id,
            "guild_id": payload.guild_id,
            "author_name": username,
            "author_avatar": avatar,
            "time": datetime.utcnow()
        })

    def cog_unload(self):
        self.cleanup_snipe_cache.cancel()

    @tasks.loop(hours=1)
    async def cleanup_snipe_cache(self):
        now = datetime.now(timezone.utc)

        # CHANGE THE 2 TO NUMBER OF INACTIVITY HOURS
        max_idle_seconds = 2 * 3600

        for channel_id in list(self.bot.snipe_cache.keys()):
            deque_entry = self.bot.snipe_cache[channel_id]
            if deque_entry:
                last_activity = deque_entry[-1]["time"]
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=timezone.utc)

                if (now - last_activity).total_seconds() > max_idle_seconds:
                    del self.bot.snipe_cache[channel_id]
            else:
                del self.bot.snipe_cache[channel_id]

        for channel_id in list(self.bot.edit_cache.keys()):
            deque_entry = self.bot.edit_cache[channel_id]
            if deque_entry:
                last_activity = deque_entry[-1]["time"]
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=timezone.utc)

                if (now - last_activity).total_seconds() > max_idle_seconds:
                    del self.bot.edit_cache[channel_id]
            else:
                del self.bot.edit_cache[channel_id]

        for channel_id in list(self.bot.reaction_cache.keys()):
            deque_entry = self.bot.reaction_cache[channel_id]
            if deque_entry:
                last_activity = deque_entry[-1]["time"]
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=timezone.utc)

                if (now - last_activity).total_seconds() > max_idle_seconds:
                    del self.bot.reaction_cache[channel_id]
            else:
                del self.bot.reaction_cache[channel_id]

    @cleanup_snipe_cache.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        global _mymemory_supported_languages
        if not reaction.message.guild:
            return
        if user.bot:
            return

        message = reaction.message
        channel = message.channel
        emoji = reaction.emoji

        if emoji == "👀":
            if not message.content:
                return
            text = urllib.parse.quote(message.content, safe='')
            url = f"https://frenchnoodles.xyz/api/endpoints/awkwardmonkey?text={text}"
            await message.reply(url, mention_author=False)
        elif emoji == "🧠":
            if not message.content:
                return
            text = urllib.parse.quote(message.content, safe='')
            url = f"https://frenchnoodles.xyz/api/endpoints/changemymind?text={text}"
            await message.reply(url, mention_author=False)
        elif emoji == "🧽":
            if not message.content:
                return
            text = urllib.parse.quote(message.content, safe='')
            url = f"https://frenchnoodles.xyz/api/endpoints/spongebobburnpaper?text={text}"
            await message.reply(url, mention_author=False)
        elif emoji == "👎":
            if not message.content:
                return
            text = urllib.parse.quote(message.content, safe='')
            url = f"https://frenchnoodles.xyz/api/endpoints/worthless?text={text}"
            await message.reply(url, mention_author=False)
        elif emoji == "🚨":
            if not message.content:
                return
            text = urllib.parse.quote(message.content, safe='')
            url = f"https://frenchnoodles.xyz/api/endpoints/presidentialalert?text={text}"
            await message.reply(url, mention_author=False)
        elif emoji == "🎤":
            if not message.content:
                return
            text = urllib.parse.quote(message.content, safe='')
            url = f"https://frenchnoodles.xyz/api/endpoints/lisastage?text={text}"
            await message.reply(url, mention_author=False)
        elif emoji == "🇺🇸":
            try:
                if not message.content:
                    return
                from langdetect import detect
                from deep_translator import MyMemoryTranslator
                loop = self.bot.loop
                detected_lang = await loop.run_in_executor(None, detect, message.content)
                
                if _mymemory_supported_languages is None:
                    temp = MyMemoryTranslator(source='english', target='french')
                    _mymemory_supported_languages = temp.get_supported_languages(as_dict=True)
                supported = _mymemory_supported_languages
                
                src_code = next((code for code in supported.values() if code.lower() == detected_lang.lower() or code.lower().startswith(detected_lang.lower() + '-')), 'en-GB')
                
                detected_name = next((k for k, v in supported.items() if v == src_code), detected_lang)
                
                translator = MyMemoryTranslator(source=src_code, target='en-GB')
                translated_text = await loop.run_in_executor(None, translator.translate, message.content)
                
                e = discord.Embed(
                    title=f"Terjama men ({detected_name}) l (english)",
                    color=0x000000,
                    description=f"```{translated_text}```"
                )
                await message.reply(embed=e, mention_author=False)
            except Exception:
                pass
        elif emoji == "🇲🇦":
            try:
                if not message.content:
                    return
                from langdetect import detect
                from deep_translator import MyMemoryTranslator
                loop = self.bot.loop
                detected_lang = await loop.run_in_executor(None, detect, message.content)
                
                if _mymemory_supported_languages is None:
                    temp = MyMemoryTranslator(source='english', target='french')
                    _mymemory_supported_languages = temp.get_supported_languages(as_dict=True)
                supported = _mymemory_supported_languages
                
                src_code = next((code for code in supported.values() if code.lower() == detected_lang.lower() or code.lower().startswith(detected_lang.lower() + '-')), 'en-GB')
                
                detected_name = next((k for k, v in supported.items() if v == src_code), detected_lang)
                
                translator = MyMemoryTranslator(source=src_code, target='ar-SA')
                translated_text = await loop.run_in_executor(None, translator.translate, message.content)
                
                e = discord.Embed(
                    title=f"Terjama men ({detected_name}) l (arabic)",
                    color=0x000000,
                    description=f"```{translated_text}```"
                )
                await message.reply(embed=e, mention_author=False)
            except Exception:
                pass
        else:
            return


async def setup(bot):
    await bot.add_cog(Events(bot))
