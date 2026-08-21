import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from moviepy.video.io.VideoFileClip import VideoFileClip
import difflib
import requests
import json
from datetime import datetime
import pytz
from googletrans import Translator
import urllib
import re
import serpapi
import random


class GlobUtil(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="makegif", aliases=["togif"], help="3tini video nreddo GIF.")
    async def makegif(self, ctx):
        if not ctx.message.attachments:
            await ctx.send("3tini chy video nredo GIF.")
            return

        attachment = ctx.message.attachments[0]

        if not attachment.content_type or not attachment.content_type.startswith('video/'):
            await ctx.send("3tini video mgad..")
            return

        input_video = f"video_{ctx.message.id}.mp4"
        output_gif = f"converted_{ctx.message.id}.gif"

        wait_embed = discord.Embed(
            description="Sber 3lia...",
            color=0x000000
        )
        wait_msg = await ctx.send(embed=wait_embed)

        try:
            video_data = await attachment.read()
            with open(input_video, "wb") as f:
                f.write(video_data)

            def convert():
                with VideoFileClip(input_video) as clip:
                    # Native v2.x method to resize dimensions down and save space
                    if clip.w > 480:
                        clip = clip.resized(width=480)

                    # Strictly standard arguments to avoid keyword conflicts
                    clip.write_gif(
                        output_gif,
                        fps=10,
                        logger=None
                    )

            await asyncio.to_thread(convert)

            await ctx.send(file=discord.File(output_gif))
            await wait_msg.delete()


        except Exception as e:
            err_embed = discord.Embed(
                description=f"Tra chy mochkil :/ `{e}`",
                color=0x000000
            )
            await wait_msg.edit(embed=err_embed)

        finally:
            for filepath in (input_video, output_gif):
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass

    @commands.command(name="image", aliases=['img', "tsawr", "tsawer"], help="Njbed lik tsawr mn google.")
    async def image(self, ctx, *, query: str):
        wait_embed = discord.Embed(
            description=f"Sbr 3lia...",
            color=0x000000
        )
        status_msg = await ctx.send(embed=wait_embed)

        params = {
            'api_key': os.getenv('IMAGE_KEY'),
            'q': query,
            'search_type': 'images',
            'location': 'United States'
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.scaleserp.com/search', params=params) as resp:
                    if resp.status != 200:
                        raise Exception(f"API 3tani code {resp.status}")
                    data = await resp.json()

            image_results = data.get('image_results', [])
            if not image_results:
                await status_msg.edit(
                    embed=discord.Embed(description="Mal9it ta tswira ._.", color=0x000000))
                return

            embed_pages = []
            for item in image_results:
                img_url = item.get('image')
                title = item.get('title', 'Image Search Result')
                source_link = item.get('link', '')

                if img_url:
                    embed = discord.Embed(
                        title=title[:256],
                        url=source_link if source_link.startswith("http") else None,
                        color=0x000000
                    )
                    embed.set_image(url=img_url)
                    embed_pages.append(embed)

            if not embed_pages:
                await status_msg.edit(embed=discord.Embed(description="Tswira fiha chy mochkil.", color=0x000000))
                return


            view = self.bot.Paginator(ctx, pages=embed_pages)
            initial_embed = view.get_page()

            await status_msg.edit(embed=initial_embed, view=view)
            view.message = status_msg

        except Exception as e:
            err_embed = discord.Embed(
                description=f"Chy 7aja mahiyach smo7at. :(\n`{e}`",
                color=0x000000
            )
            await status_msg.edit(embed=err_embed)

    @commands.command(name="dictionary", aliases=["definition", "define", "chre7", "chr7", "dict"], help="Njbed lik ay definition mn Urban Dictionary")
    async def dictionary(self, ctx, *, search: str):

        search_query = search.replace(" ", "+")
        url = f"http://api.urbandictionary.com/v0/define?term={search_query}"

        wait_embed = discord.Embed(
            description=f"Sbr 3lia...",
            color=0x000000
        )
        status_msg = await ctx.send(embed=wait_embed)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise Exception(f"API 3tani code {resp.status}")
                    data = await resp.json()

            results = data.get("list", [])
            if not results:
                await status_msg.edit(
                    embed=discord.Embed(description=f"Mal9itch definition ta3 **{search}**.", color=0x000000))
                return

            embed_pages = []
            for item in results:
                definition = item.get("definition", "No definition provided.")
                example = item.get("example", "No example provided.")
                thumbs_up = item.get("thumbs_up", 0)
                thumbs_down = item.get("thumbs_down", 0)


                clean_def = definition.replace("[", "").replace("]", "")
                clean_ex = example.replace("[", "").replace("]", "")


                if len(clean_def) > 1024:
                    clean_def = clean_def[:1021] + "..."
                if len(clean_ex) > 1024:
                    clean_ex = clean_ex[:1021] + "..."

                description = f"\n**Definition:**\n{clean_def}\n\n**Example:**\n*{clean_ex}*\n"

                em = discord.Embed(
                    title=f'"{search}" fl Urban Dictionary',
                    color=0x000000,
                    description=description,
                    timestamp=ctx.message.created_at
                )
                em.set_footer(text=f"👍 {thumbs_up} | 👎 {thumbs_down}")

                embed_pages.append(em)


            view = self.bot.Paginator(ctx, pages=embed_pages)
            initial_embed = view.get_page()

            await status_msg.edit(embed=initial_embed, view=view)
            view.message = status_msg

        except Exception as e:
            err_embed = discord.Embed(
                description=f"Chy 7aja mahiyach smo7at. :(\n`{e}`",
                color=0x000000
            )
            await status_msg.edit(embed=err_embed)

    @commands.command(aliases=["3alam", "raya", "country"], help="Flag ta3 ay dawla.")
    async def flag(self, ctx, *, country: str):

        code_clean = country.strip().lower()

        if code_clean == "tajda":
            url = "https://cdn.discordapp.com/attachments/1522282041738002465/1522382935188177037/20260703_002525.jpg?ex=6a484518&is=6a46f398&hm=4fcaea72fb7bb378f13bfa036be9abf907fa5978a050bbe4ca5a5ac86ba24bdb&"
            em = discord.Embed(color=0x000000)
            em.set_image(url=url)
            await ctx.send(embed=em)
            return

        elif code_clean == "israel":
            code_clean = "palestine"


        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://flagcdn.com/en/codes.json") as resp:
                    if resp.status == 200:
                        countries_map = await resp.json()
                    else:
                        raise Exception()
        except Exception:
            countries_map = {"ma": "Morocco", "us": "United States", "fr": "France"}

        name_to_code = {name.lower(): code for code, name in countries_map.items()}

        if code_clean in countries_map:
            final_code = code_clean
        else:
            possible_names = list(name_to_code.keys())
            matches = difflib.get_close_matches(code_clean, possible_names, n=1, cutoff=0.4)

            if matches:
                matched_name = matches[0]
                final_code = name_to_code[matched_name]
            else:
                await ctx.send("Mal9itch had lblad.")
                return

        url = f"https://flagcdn.com/w640/{final_code}.png"

        em = discord.Embed(color=0x000000)
        em.set_image(url=url)

        matched_formal_name = countries_map[final_code]
        em.set_footer(text=f"Matched: {matched_formal_name}")

        await ctx.send(embed=em)

    @commands.command(aliases=["jow", "temp", "temperature"], help="7alat ta9s f ay mdina.")
    async def weather(self, ctx, city: str = None):
        if city == None:
            await ctx.send("Ina mdina?")
        else:
            key = os.getenv("WEATHER_KEY")
            r = json.loads(
                requests.get(f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={key}").content)
            weather = r["weather"][0]["main"]
            description = r["weather"][0]["description"]
            icon = r["weather"][0]["icon"]
            thumbnail = f"http://openweathermap.org/img/wn/{icon}@2x.png"
            name = r["name"]
            country = r["sys"]["country"]
            temp = r["main"]["temp"]
            temperature = int(temp) - 273.15
            tempee = "{:.1f}".format(temperature)
            humidity = r["main"]["humidity"]
            url = f"https://flagsapi.com/{country}/flat/64.png"
            e = discord.Embed(title=f"Weather in {name}",
                              description=f"**{str(tempee)}°C**\n"
                                          f"**{weather}** _{description}_\n"
                                          f"{humidity}% Humidity",
                              timestamp=ctx.message.created_at,
                              color=0x000000)
            e.set_footer(text=f"{name} | {country}", icon_url=url)
            e.set_thumbnail(url=thumbnail)
            await ctx.send(embed=e)

    @commands.command(name="time", aliases=["timezone", "sa3a"], help="Sa3a f ay mdina.")
    async def time(self, ctx, *, city: str = None):

        if city is None:
            local_tz = pytz.timezone("Africa/Casablanca")
            now = datetime.now(local_tz)
            time_str = now.strftime("%H:%M:%S")
            date_str = now.strftime("%Y-%m-%d")

            em = discord.Embed(
                title="Sa3a flmghrib.",
                description=f"**Time:** {time_str}\n**Date:** {date_str}",
                color=0x000000
            )
            await ctx.send(embed=em)

        else:
            city_clean = city.strip().lower().replace(" ", "_")

            matched_zone = None
            for zone in pytz.all_timezones:
                if city_clean in zone.lower():
                    matched_zone = zone
                    break

            if not matched_zone:
                await ctx.send(f"Mal9itch chy mdina smitha `{city}`.")
                return

            target_tz = pytz.timezone(matched_zone)
            now = datetime.now(target_tz)

            time_str = now.strftime("%H:%M:%S")
            date_str = now.strftime("%Y-%m-%d")
            display_zone = matched_zone.replace("_", " ")

            em = discord.Embed(
                title=f"Sa3a f {display_zone}",
                description=f"**Time:** {time_str}\n**Date:** {date_str}",
                color=0x000000
            )
            em.set_footer(text=f"UTC Offset: {now.strftime('%z')}")

            await ctx.send(embed=em)

    @commands.command(aliases=["trans", "trjm", "trjem", "terjem"], help="Nterjem lik ay 7aja.")
    async def translate(self, ctx, fromlang, tolang, *, text):
        translation = await Translator().translate(text, src=fromlang, dest=tolang)
        e = discord.Embed(title=f"Terjama men ({fromlang}) l ({tolang})",
                           color=0x000000,
                           timestamp=ctx.message.created_at,
                           description=f"```{translation.text}```")
        await ctx.send(embed=e)

    @commands.command(aliases=['yt'], help="N9elleb lik f youtube.")
    async def youtube(self, ctx, *, search):
        string = urllib.parse.urlencode({'search_query': search})
        content = urllib.request.urlopen('https://www.youtube.com/results?' + string)
        results = re.findall(r'/watch\?v=(.{11})', content.read().decode())
        video = "https://www.youtube.com/watch?v=" + results[0]
        await ctx.send(video)

    @commands.command(aliases=["calc", "7sb", "7seb"], help="N7seb lik lmath sahel.")
    async def calculate(self, ctx, *, operation):
        await ctx.send(eval(operation.replace(" ", "")))

    @commands.command(name="rhyme", aliases=["rhymes", "9afiya"], help="Njbed lik lkelmat li 3endhom nafs l 9afiya.")
    async def rhyme(self, ctx, word: str):

        url = f"https://api.datamuse.com/words?rel_rhy={word}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await ctx.send("Tra chy mochkil m3a l API.")
                        return
                    data = await resp.json()
        except Exception:
            await ctx.send("Tra chy mochkil fl API.")
            return

        words = [item["word"] for item in data if "word" in item]

        if not words:
            await ctx.send("Mal9it walo hh.")
            return

        view = self.bot.Paginator(
            ctx,
            pages=words,
            per_page=15,
            title=f'Lkelmat li 3ndhom 9afiya m3a "{word}"'
        )

        initial_embed = view.get_page()
        status_msg = await ctx.send(embed=initial_embed, view=view)
        view.message = status_msg

    @commands.command(aliases=["ip", "doxx", "location"], help="Nl9a lik location ta3 ay IP address.")
    async def locate(self, ctx, ip: str):
        r = json.loads(requests.get(f"http://ip-api.com/json/{ip}").content)
        status = r["status"]
        if status == 'success':
            country = r["country"]
            code = r["countryCode"]
            region = r["region"]
            rname = r["regionName"]
            city = r["city"]
            zip = r["zip"]
            timezone = r["timezone"]
            isp = r["isp"]
            AS = r["as"]
            e = discord.Embed(title=f"Location ta3: {r['query']}",
                               description=f"**Country** {country} / {code}\n"
                                           f"**Region** {rname} / {region}\n"
                                           f"**City** {city}\n"
                                           f"**ZIP** {zip}\n"
                                           f"**Timezone** {timezone}\n"
                                           f"**ISP** {isp}\n"
                                           f"**AS** {AS}",
                               color=0x000000,
                               timestamp=ctx.message.created_at)
            await ctx.send(embed=e)
        else:
            await ctx.send("dak l IP mal9itouch :/")

    @commands.command(name="reverse", aliases=["reverseimage"], help="3tini tswira n9elleb lik 3liha fl web.")
    async def reverse(self, ctx, user_or_image: str = None):

        if user_or_image:
            if "http" in user_or_image:
                image = user_or_image
            else:
                try:
                    member = await commands.UserConverter().convert(ctx, user_or_image)
                    image = member.display_avatar.url
                except Exception:
                    await ctx.send("3tini ya tswira, ya lien ta3 tswira wla pingi chy wa7d.")
                    return
        else:
            if len(ctx.message.attachments) > 0:
                image = ctx.message.attachments[0].url
            else:
                await ctx.send("3tini ya tswira, ya lien ta3 tswira wla pingi chy wa7d.")
                return

        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))

        try:
            params = {
                "engine": "google_lens",
                "url": image,
                "api_key": os.getenv('SERPAPI_KEY')
            }

            search = serpapi.GoogleSearch(params)
            results = search.get_dict()

            matches = results.get('visual_matches', [])
            links = [item['link'] for item in matches if 'link' in item]

            if not links:
                await wait.edit(
                    embed=discord.Embed(description="Mal9it walo f tal9ib dyal had tswira.", color=0x000000))
                return

            view = self.bot.Paginator(
                ctx,
                pages=links,
                per_page=10,
                title=f"Had tswira l9itha f {len(links)} blasa"
            )

            initial_embed = view.get_page()
            initial_embed.set_thumbnail(url=image)

            await wait.edit(embed=initial_embed, view=view)
            view.message = wait

        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: {e}", color=0x000000))

    @commands.command(aliases=['8ball', '8b', "wach", "wash", "wax"], help="Sewelni njawbk.")
    async def eightball(self, ctx):
        responses = ["Ah", "Wayeh", "Darori", "Rah bayna", "Btabi3t l7al", "M3rofa hadi", "Ayeh kayna", "Bss7",
                     "La", "Maymknch", "Mosta7il", "Gher katkhrbe9", "Ma so2al ma walo", "Mandmench lik", "S3eb",
                     "Mo7al", "Momkin", "Ghaliban", "Ma3rftch sara7a", "3la 7sab", "Allah A3lam"]
        await ctx.reply(random.choice(responses), mention_author=False)

    @commands.command(help="N9elleb lik 3la gif.")
    async def gif(self, ctx, *, q="None"):
        api_key = os.getenv("TENOR_KEY")
        ckey = os.getenv("TENOR_C")
        lmt = 20
        r = requests.get(
            "https://tenor.googleapis.com/v2/search?q=%s&key=%s&client_key=%s&limit=%s" % (q, api_key, ckey, lmt))
        gifs = []
        for i in range(0, 20):
            g = json.loads(r.content)["results"][0]["media_formats"]["gif"]["url"]
            gifs.append(g)
        gif = random.choice(gifs)
        await ctx.send(gif)

    @commands.command(aliases=['cf', 'drhm', 'drhem'], help="Nlou7 derhem o nchouf wach jat ras wla njema.")
    async def coinflip(self, ctx):
        choices = [':coin: njma (tails)', ':coin: ras (heads)']
        await ctx.send(random.choice(choices))

    @commands.command(aliases=["ye", "kanye", "quote"], help="Quotes ta3 Kanye West.")
    async def kanyequote(self, ctx):
        quote = json.loads(requests.get("https://api.kanye.rest/").content)["quote"]
        await ctx.send(f'"{quote}" -Kanye West')

    @commands.command(aliases=["nrd", "lo7"], help="Lo7 dice o chouf ch7al jak.")
    async def dice(self, ctx):
        dice = [1, 2, 3, 4, 5 , 6]
        await ctx.send(f"🎲 {random.choice(dice)}")

    @commands.command(aliases=['anime'], help="N3tik informations 3la ay anime.")
    async def animeinfo(self, ctx, *, anime: str):
        anime = anime.lower().replace(" ", "%20")
        r = json.loads(requests.get(f"https://kitsu.io/api/edge/anime?filter[text]={anime}").content)['data'][0]
        type = r['type']
        description = r['attributes']['description']
        titleen = r['attributes']['titles']['en']
        titleja = r['attributes']['titles']['en_jp']
        status = r['attributes']['status']
        start = r['attributes']['startDate']
        end = r['attributes']['endDate']
        poster = r['attributes']['posterImage']['large']
        episodes = r['attributes']['episodeCount']
        e = discord.Embed(title=titleen, color=0x000000, timestamp=ctx.message.created_at)
        e.add_field(name="Japanese Title", value=titleja)
        e.add_field(name="Type", value=type)
        e.add_field(name="Status", value=status)
        e.add_field(name="Start Date", value=start)
        e.add_field(name="End Date", value=end)
        e.add_field(value=episodes, name="Episodes")
        e.add_field(name="Description", value=description)
        e.set_image(url=poster)
        await ctx.send(embed=e)

async def setup(bot):
    await bot.add_cog(GlobUtil(bot))