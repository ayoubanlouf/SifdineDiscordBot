import io
import asyncio
import urllib.parse
from typing import Optional
import discord
from discord.ext import commands
from PIL import Image, ImageEnhance, ImageOps
from converters import FuzzyMember


def memegen_escape(text: str) -> str:
    if not text or not text.strip():
        return "_"
    clean = text.strip()
    return (clean
            .replace("_", "__")
            .replace("-", "--")
            .replace(" ", "_")
            .replace("?", "~q")
            .replace("&", "~a")
            .replace("%", "~p")
            .replace("#", "~h")
            .replace("/", "~s")
            .replace('"', "''"))


class Manipulation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def resolve_image(self, ctx, target: Optional[str] = None) -> str:
        if not target:
            if ctx.message.attachments:
                return ctx.message.attachments[0].url
            return ctx.author.display_avatar.url
        
        target_lower = target.lower()
        if target_lower.startswith("http://") or target_lower.startswith("https://"):
            return target
            
        try:
            member = await FuzzyMember().convert(ctx, target)
            return member.display_avatar.url
        except commands.CommandError:
            raise commands.BadArgument("Malkitch had l member, 3awd chouf smiya wla dir link direct.")

    async def _fetch_nekobot(self, ctx, endpoint_type: str, **params) -> None:
        """Helper to fetch image from Nekobot API and reply with image URL."""
        qs = urllib.parse.urlencode({"type": endpoint_type, **params})
        url = f"https://nekobot.xyz/api/imagegen?{qs}"
        session = getattr(self.bot, "session", None)
        if not session or session.closed:
            await ctx.reply("❌ Bot session not ready.", mention_author=False)
            return

        try:
            async with session.get(url, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    msg_url = data.get("message")
                    if msg_url and msg_url.startswith("http"):
                        await ctx.reply(msg_url, mention_author=False)
                        return
                await ctx.reply("❌ Tra mochkil f generation dial tswira, 3awed jereb.", mention_author=False)
        except Exception as e:
            await ctx.reply(f"❌ Error: `{e}`", mention_author=False)

    async def _download_image_bytes(self, url: str) -> Optional[bytes]:
        session = getattr(self.bot, "session", None)
        if not session or session.closed:
            return None
        try:
            async with session.get(url, timeout=12) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            pass
        return None

    # ==================== TEXT & MEME GENERATORS ====================

    @commands.command(name="worthless", help="Ma hdra ma walo.")
    async def worthless(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/worthless?text={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="drake", help="Meme ta3 drake.")
    async def drake(self, ctx, *, text: str):
        if "," in text:
            top, bottom = text.split(",", 1)
            top = top.strip()
            bottom = bottom.strip()
        else:
            top = text.strip()
            bottom = " "

        quoted1 = urllib.parse.quote(top, safe='')
        quoted2 = urllib.parse.quote(bottom, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/drake?text1={quoted1}&text2={quoted2}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="presidentialalert", aliases=["presidential", "alert", "notification", "noti"], help="Red lmessage ta3k notification.")
    async def presidentialalert(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/presidentialalert?text={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="spongebobburnpaper", help="Spongebob kay7re9 lmessage ta3k.")
    async def spongebobburnpaper(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/spongebobburnpaper?text={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="lisastage", aliases=["stage", "lisa"], help="Lisa katwri lmessage ta3k f stage.")
    async def lisastage(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/lisastage?text={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="changemymind", aliases=["changemind"], help="Change my mind okda.")
    async def changemymind(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/changemymind?text={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="awkwardmonkey", aliases=["sideeye"], help="Awkward monkey kaychouf fl message ta3k.")
    async def awkwardmonkey(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/awkwardmonkey?text={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="clyde", help="Sift message b style dial Clyde Discord bot.")
    async def clyde(self, ctx, *, text: str):
        await self._fetch_nekobot(ctx, "clyde", text=text)

    @commands.command(name="trump", aliases=["trumptweet"], help="Sift fake tweet dial Donald Trump.")
    async def trump(self, ctx, *, text: str):
        await self._fetch_nekobot(ctx, "trumptweet", text=text)

    @commands.command(name="nobitches", aliases=["nowhat"], help="Sift l meme ta3 nobitches? b text libghiti.")
    async def nobitches(self, ctx, *, text: str):
        quoted = urllib.parse.quote(text, safe='')
        url = f"https://some-random-api.com/canvas/misc/nobitches?no={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="tweet", help="Sift message b style ta3 tweet.")
    async def tweet(self, ctx, *, text: str):
        target_member = ctx.author
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            try:
                found_member = await FuzzyMember().convert(ctx, parts[0])
                target_member = found_member
                text = parts[1]
            except Exception:
                pass

        avatar = urllib.parse.quote(target_member.display_avatar.url, safe='')
        d_name = urllib.parse.quote(target_member.display_name, safe='')
        u_name = urllib.parse.quote(target_member.name, safe='')
        comment = urllib.parse.quote(text, safe='')
        url = f"https://some-random-api.com/canvas/misc/tweet?avatar={avatar}&displayname={d_name}&username={u_name}&comment={comment}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="youtubecomment", aliases=["ytcomment", "comment"], help="Sift message b style ta3 youtube comment.")
    async def youtubecomment(self, ctx, *, text: str):
        target_member = ctx.author
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            try:
                found_member = await FuzzyMember().convert(ctx, parts[0])
                target_member = found_member
                text = parts[1]
            except Exception:
                pass

        avatar = urllib.parse.quote(target_member.display_avatar.url, safe='')
        u_name = urllib.parse.quote(target_member.display_name, safe='')
        comment = urllib.parse.quote(text, safe='')
        url = f"https://some-random-api.com/canvas/misc/youtube-comment?avatar={avatar}&username={u_name}&comment={comment}"
        await ctx.reply(url, mention_author=False)

    # ==================== IMAGE FILTERS & MANIPULATION ====================

    @commands.command(name="blur", aliases=["blurri"], help="Katred tswira mdbeba.")
    async def blur(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/filter/blur?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="circle", aliases=["round", "dwer", "dower"], help="Katred tswira mdwra.")
    async def circle(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/circle?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="invert", help="Katinverti l alwan ta3 tswira.")
    async def invert(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/invert?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="wide", aliases=["stretch", "3erred"], help="Katred tswira 3rida.")
    async def wide(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/wide?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="uglyupclose", aliases=["ugly", "khayb"], help="Spongebob o basit kaygoulo lik ugly hh.")
    async def uglyupclose(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/uglyupclose?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="clown", aliases=["bahlawan"], help="Robin kaygoul lik nta bahlawan.")
    async def clown(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/clown?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="restinpeace", aliases=["rip", "layr7mo", "tr7mo3lih"], help="Tr7mo 3lih.")
    async def restinpeace(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/rip?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="affectbaby", aliases=["baby"], help="Lwalida drbat walakin ma9ysatch.")
    async def affectbaby(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/affectbaby?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="trash", aliases=["zbel", "throw", "lo7", "zbl", "rmi"], help="Katlo7 tswira fzbel.")
    async def trash(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://frenchnoodles.xyz/api/endpoints/trash?image={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="threats", aliases=["threat"], help="3 akbar threats 3la lmojtama3 meme.")
    async def threats(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        await self._fetch_nekobot(ctx, "threats", url=img_url)

    @commands.command(name="pixellate", aliases=["pixelate", "pixel", "8bit"], help="Katred tswira 8-bit pixelated.")
    async def pixellate(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/filter/pixelate?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="simpcard", aliases=["simp", "license"], help="Official Simp Identification Card.")
    async def simpcard(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/misc/simpcard?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="hornycard", aliases=["horny", "hornylicense"], help="Official Horny License Card.")
    async def hornycard(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/misc/horny?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="heart", aliases=["heartframe"], help="Dir tswira wst 9elb.")
    async def heart(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/misc/heart?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="sepia", help="Red tswira b style sepia.")
    async def sepia(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/filter/sepia?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="greyscale", aliases=["grayscale", "bnw", "blackandwhite", "bw", "grey", "gray"], help="Red tswira b style black and white.")
    async def greyscale(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/filter/greyscale?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="brightness", aliases=["brighten", "light"], help="Zid l brightness dial tswira.")
    async def brightness(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/filter/brightness?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="treshold", aliases=["threshold", "monochrome"], help="Red tswira b style High-contrast monochrome threshold.")
    async def treshold(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/filter/threshold?avatar={quoted}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="ship", aliases=["love", "crush"], help="Shippi jouj members.")
    async def ship(self, ctx, user1: Optional[str] = None, user2: Optional[str] = None):
        if not user1:
            await ctx.reply("❌ Khassek t7ded au moins 1 user bach dir m3ah ship! Example: `sat ship @user`", mention_author=False)
            return

        if user2:
            try:
                man = await FuzzyMember().convert(ctx, user1)
            except Exception:
                await ctx.reply(f"❌ Malkitch lmember `{user1}`.", mention_author=False)
                return
            try:
                woman = await FuzzyMember().convert(ctx, user2)
            except Exception:
                await ctx.reply(f"❌ Malkitch lmember `{user2}`.", mention_author=False)
                return
        else:
            man = ctx.author
            try:
                woman = await FuzzyMember().convert(ctx, user1)
            except Exception:
                await ctx.reply(f"❌ Malkitch lmember `{user1}`.", mention_author=False)
                return

        await self._fetch_nekobot(ctx, "ship", user1=woman.display_avatar.url, user2=man.display_avatar.url)

    @commands.command(name="mirror", aliases=["flip"], help="9leb tswira.")
    async def mirror(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        raw_bytes = await self._download_image_bytes(img_url)
        if not raw_bytes:
            await ctx.reply("❌ Ma9ditch ntelechargi tswira.", mention_author=False)
            return

        def _process():
            with Image.open(io.BytesIO(raw_bytes)).convert("RGBA") as img:
                mirrored = ImageOps.mirror(img)
                out = io.BytesIO()
                mirrored.save(out, format="PNG")
                out.seek(0)
                return out

        loop = asyncio.get_running_loop()
        out_buf = await loop.run_in_executor(None, _process)
        await ctx.reply(file=discord.File(out_buf, filename="mirrored.png"), mention_author=False)

    @commands.command(name="deepfry", aliases=["fry", "deepfried"], help="Red tswira b style deep fried.")
    async def deepfry(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        raw_bytes = await self._download_image_bytes(img_url)
        if not raw_bytes:
            await ctx.reply("❌ Ma9ditch ntelechargi tswira.", mention_author=False)
            return

        def _process():
            with Image.open(io.BytesIO(raw_bytes)).convert("RGB") as img:
                img = ImageEnhance.Color(img).enhance(3.0)
                img = ImageEnhance.Contrast(img).enhance(2.5)
                img = ImageEnhance.Sharpness(img).enhance(4.0)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=45)
                out.seek(0)
                return out

        loop = asyncio.get_running_loop()
        out_buf = await loop.run_in_executor(None, _process)
        await ctx.reply(file=discord.File(out_buf, filename="deepfried.jpg"), mention_author=False)

    @commands.command(name="buzzeverywhere", aliases=["buzz", "everywhere"], help="Meme dial Buzz Lightyear (X Everywhere).")
    async def buzzeverywhere(self, ctx, *, text: str):
        if "," in text:
            top, bottom = text.split(",", 1)
            top = top.strip()
            bottom = bottom.strip()
        else:
            top = text.strip()
            bottom = f"{top} Everywhere"
        t_enc = memegen_escape(top)
        b_enc = memegen_escape(bottom)
        url = f"https://api.memegen.link/images/buzz/{t_enc}/{b_enc}.png"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="customcaption", aliases=["caption", "meme"], help="Zid text fo9 avatar ta3 chy wa7d.")
    async def customcaption(self, ctx, *, text: str):
        target_url = ctx.author.display_avatar.url
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            first_word = parts[0]
            if first_word.startswith("http://") or first_word.startswith("https://"):
                target_url = first_word
                text = parts[1]
            else:
                try:
                    m = await FuzzyMember().convert(ctx, first_word)
                    target_url = m.display_avatar.url
                    text = parts[1]
                except Exception:
                    pass

        if ctx.message.attachments:
            target_url = ctx.message.attachments[0].url

        if "," in text:
            top, bottom = text.split(",", 1)
            top = top.strip()
            bottom = bottom.strip()
        else:
            words = text.split()
            if len(words) <= 1:
                top = text.strip()
                bottom = ""
            else:
                mid = len(words) // 2
                top = " ".join(words[:mid])
                bottom = " ".join(words[mid:])

        top_enc = memegen_escape(top)
        bottom_enc = memegen_escape(bottom)
        bg_enc = urllib.parse.quote(target_url, safe='')
        url = f"https://api.memegen.link/images/custom/{top_enc}/{bottom_enc}.png?background={bg_enc}"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="dailystruggle", aliases=["struggle", "twobuttons", "2buttons"], help="Dir khyar bin jouj buttons.")
    async def dailystruggle(self, ctx, *, text: str):
        if "," in text:
            b1, b2 = text.split(",", 1)
            b1 = b1.strip()
            b2 = b2.strip()
        else:
            words = text.split()
            mid = max(1, len(words) // 2)
            b1 = " ".join(words[:mid])
            b2 = " ".join(words[mid:]) if len(words) > 1 else "..."
        b1_enc = memegen_escape(b1)
        b2_enc = memegen_escape(b2)
        url = f"https://api.memegen.link/images/ds/{b1_enc}/{b2_enc}.png"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="gruplan", aliases=["gru"], help="9ad tswira ta3 4 step plan ta3 gru.")
    async def gruplan(self, ctx, *, text: str):
        if "," in text:
            chunks = [c.strip() for c in text.split(",")]
            s1 = chunks[0] if len(chunks) > 0 else "_"
            s2 = chunks[1] if len(chunks) > 1 else "_"
            s3 = chunks[2] if len(chunks) > 2 else "_"
        else:
            words = text.split()
            if len(words) >= 3:
                n = len(words) // 3
                s1 = " ".join(words[:n])
                s2 = " ".join(words[n:2*n])
                s3 = " ".join(words[2*n:])
            else:
                s1, s2, s3 = text, text, text
        s1_enc = memegen_escape(s1)
        s2_enc = memegen_escape(s2)
        s3_enc = memegen_escape(s3)
        url = f"https://api.memegen.link/images/gru/{s1_enc}/{s2_enc}/{s3_enc}/{s3_enc}.png"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="isthis", aliases=["isthisa"], help="Meme dial 'Is this a ...?'.")
    async def isthis(self, ctx, *, text: str):
        clean = text.strip()
        if clean.lower().startswith("is this "):
            clean = clean[8:].strip()
        elif clean.lower().startswith("is this a "):
            clean = clean[10:].strip()
        t_enc = memegen_escape(clean)
        url = f"https://api.memegen.link/images/pigeon/is_this/{t_enc}.png"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="tuxedopooh", aliases=["pooh", "fancy"], help="Regular Pooh vs Tuxedo Pooh meme.")
    async def tuxedopooh(self, ctx, *, text: str):
        if "," in text:
            reg, fancy = text.split(",", 1)
            reg = reg.strip()
            fancy = fancy.strip()
        else:
            words = text.split()
            mid = max(1, len(words) // 2)
            reg = " ".join(words[:mid])
            fancy = " ".join(words[mid:]) if len(words) > 1 else text
        r_enc = memegen_escape(reg)
        f_enc = memegen_escape(fancy)
        url = f"https://api.memegen.link/images/pooh/{r_enc}/{f_enc}.png"
        await ctx.reply(url, mention_author=False)

    @commands.command(name="nekofact", aliases=["neko", "uwu"], help="Sift tswira ta3 neko fiha text li bghiti.")
    async def nekofact(self, ctx, *, text: str):
        await self._fetch_nekobot(ctx, "fact", text=text)

    @commands.command(name="magik", aliases=["liquid"], help="Red tswira m3wwja.")
    async def magik(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        await self._fetch_nekobot(ctx, "magik", image=img_url)

    @commands.command(name="trapcard", aliases=["yugioh", "yugi"], help="9ad card ta3 Yu-Gi-Oh b tswira o smiya ta3k.")
    async def trapcard(self, ctx, *, target: Optional[str] = None):
        target_name = ctx.author.display_name
        img_url = ctx.author.display_avatar.url
        if target:
            try:
                m = await FuzzyMember().convert(ctx, target)
                target_name = m.display_name
                img_url = m.display_avatar.url
            except Exception:
                img_url = await self.resolve_image(ctx, target)
        elif ctx.message.attachments:
            img_url = ctx.message.attachments[0].url

        await self._fetch_nekobot(ctx, "trap", name=target_name, author="Trap Card", image=img_url)

    @commands.command(name="transgender", aliases=["trans", "tranny"], help="Dir avatar ta3 chy wa7d wst mn transgender flag.")
    async def transgender(self, ctx, *, target: Optional[str] = None):
        img_url = await self.resolve_image(ctx, target)
        quoted = urllib.parse.quote(img_url, safe='')
        url = f"https://some-random-api.com/canvas/misc/transgender?avatar={quoted}"
        await ctx.reply(url, mention_author=False)


async def setup(bot):
    await bot.add_cog(Manipulation(bot))
