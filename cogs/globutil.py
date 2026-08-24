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
import yt_dlp


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
        query_encoded = urllib.parse.quote(q)
        url = f"https://tenor.com/search/{query_encoded}-gifs"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                media_urls = re.findall(r'https://media\.tenor\.com/[^\s\"\'\>]+', r.text)
                gifs = list(set([u for u in media_urls if u.endswith('.gif')]))
                if gifs:
                    gif = random.choice(gifs)
                    await ctx.send(gif)
                    return
            await ctx.send(f"Malkitch chy gif l `{q}`.")
        except Exception as e:
            await ctx.send(f"Mochkil: {e}")

    @commands.command(aliases=['cf', 'drhm', 'drhem'], help="Nlou7 derhem o nchouf wach jat ras wla njema.")
    async def coinflip(self, ctx):
        choices = [':coin: njma (tails)', ':coin: ras (heads)']
        await ctx.send(random.choice(choices))

    @commands.command(aliases=["ye", "kanye", "quote"], help="Quotes ta3 Kanye West.")
    async def kanyequote(self, ctx):
        quote = json.loads(requests.get("https://api.kanye.rest/").content)["quote"]
        await ctx.send(f'"{quote}" -Kanye West')

    @commands.command(aliases=["nrd"], help="Lo7 dice o chouf ch7al jak.")
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

    @commands.command(name="github", aliases=["gh"], help="Njbed lik details ta3 user f github.")
    async def github(self, ctx, username: str):
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        url = f"https://api.github.com/users/{username}"
        headers = {"User-Agent": "SifdineBot"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 404:
                        await wait.edit(embed=discord.Embed(description=f"Mal9itch had l-user f GitHub: `{username}`", color=0x000000))
                        return
                    if resp.status != 200:
                        raise Exception(f"HTTP Code {resp.status}")
                    data = await resp.json()

            name = data.get("name") or data.get("login")
            bio = data.get("bio") or "Nsit ndir bio hh."
            followers = data.get("followers", 0)
            following = data.get("following", 0)
            public_repos = data.get("public_repos", 0)
            avatar_url = data.get("avatar_url")
            html_url = data.get("html_url")
            location = data.get("location") or "Makhfi"
            company = data.get("company") or "No company"
            created_at_str = data.get("created_at")

            created_date = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")

            embed = discord.Embed(
                title=f"GitHub Profile ta3 {name}",
                url=html_url,
                description=bio,
                color=0x000000
            )
            embed.set_thumbnail(url=avatar_url)
            embed.add_field(name="Followers", value=str(followers), inline=True)
            embed.add_field(name="Following", value=str(following), inline=True)
            embed.add_field(name="Public Repos", value=str(public_repos), inline=True)
            embed.add_field(name="Location", value=location, inline=True)
            embed.add_field(name="Company", value=company, inline=True)
            embed.add_field(name="Mchyed f", value=created_date, inline=True)

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

    @commands.command(name="reddituser", aliases=["u"], help="Njbed lik details ta3 user f reddit.")
    async def reddituser(self, ctx, username: str):
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))

        # Try direct Reddit JSON API
        url = f"https://www.reddit.com/user/{username}/about.json"
        headers = {"User-Agent": f"SifdineBot/1.0 (by /u/{username})"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=8) as resp:
                    if resp.status == 200:
                        res_json = await resp.json()
                        data = res_json.get("data", {})

                        name = data.get("name")
                        comment_karma = data.get("comment_karma", 0)
                        link_karma = data.get("link_karma", 0)
                        total_karma = comment_karma + link_karma
                        created_utc = data.get("created_utc")
                        created_date = datetime.fromtimestamp(created_utc, pytz.utc).strftime("%Y-%m-%d")
                        is_gold = "Ahya" if data.get("is_gold") else "La"
                        icon_img = data.get("icon_img", "").split("?")[0]

                        embed = discord.Embed(
                            title=f"Reddit Profile ta3 u/{name}",
                            url=f"https://www.reddit.com/user/{name}",
                            color=0x000000
                        )
                        if icon_img and icon_img.startswith("http"):
                            embed.set_thumbnail(url=icon_img)
                        embed.add_field(name="Total Karma", value=str(total_karma), inline=True)
                        embed.add_field(name="Post Karma", value=str(link_karma), inline=True)
                        embed.add_field(name="Comment Karma", value=str(comment_karma), inline=True)
                        embed.add_field(name="Gold User", value=is_gold, inline=True)
                        embed.add_field(name="Mchyed f", value=created_date, inline=True)

                        await wait.edit(embed=embed)
                        return
                    elif resp.status == 404:
                        await wait.edit(embed=discord.Embed(description=f"Mal9itch had l-user f Reddit: `{username}`", color=0x000000))
                        return
        except Exception:
            pass

        # Direct API blocked — provide direct profile link
        embed = discord.Embed(
            title=f"Reddit Profile: u/{username}",
            url=f"https://www.reddit.com/user/{username}",
            description="Ma9drtch njbed l-details mn Reddit API. Kliki 3la l-link bach tchouf l-profile.",
            color=0x000000
        )
        await wait.edit(embed=embed)

    @commands.command(name="tiktok", aliases=["tt"], help="Nqelleb lik 3la user f TikTok.")
    async def tiktok(self, ctx, username: str):
        username = username.lstrip("@")
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        try:
            params = {
                "engine": "google",
                "q": f"site:tiktok.com/@{username}",
                "api_key": os.getenv('SERPAPI_KEY')
            }
            search = serpapi.GoogleSearch(params)
            results = await asyncio.to_thread(search.get_dict)

            organic = results.get("organic_results", [])
            profile_res = None
            if organic:
                for res in organic:
                    link_lower = res.get("link", "").lower()
                    # STRICT: only match if the actual profile URL is in the link
                    if f"tiktok.com/@{username.lower()}" in link_lower:
                        profile_res = res
                        break

            if not profile_res:
                embed = discord.Embed(
                    title=f"TikTok Profile: @{username}",
                    url=f"https://www.tiktok.com/@{username}",
                    description="Kliki 3la l-link bach tchouf l-profile.",
                    color=0x000000
                )
                await wait.edit(embed=embed)
                return

            title = profile_res.get("title", f"@{username} on TikTok")
            snippet = profile_res.get("snippet", "")
            link = profile_res.get("link", f"https://www.tiktok.com/@{username}")

            followers = "unknown"
            likes = "unknown"

            followers_match = re.search(r"([\d\.]+[KMB]?) Followers", snippet, re.IGNORECASE)
            if followers_match:
                followers = followers_match.group(1)
            likes_match = re.search(r"([\d\.]+[KMB]?) Likes", snippet, re.IGNORECASE)
            if likes_match:
                likes = likes_match.group(1)

            embed = discord.Embed(
                title=title,
                url=link,
                description=snippet,
                color=0x000000
            )
            embed.add_field(name="Followers", value=followers, inline=True)
            embed.add_field(name="Likes", value=likes, inline=True)

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

    @commands.command(name="instagram", aliases=["ig"], help="Nqelleb lik 3la user f Instagram.")
    async def instagram(self, ctx, username: str):
        username = username.lstrip("@")
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        try:
            params = {
                "engine": "google",
                "q": f"site:instagram.com/{username}",
                "api_key": os.getenv('SERPAPI_KEY')
            }
            search = serpapi.GoogleSearch(params)
            results = await asyncio.to_thread(search.get_dict)

            organic = results.get("organic_results", [])
            profile_res = None
            if organic:
                for res in organic:
                    link_lower = res.get("link", "").lower()
                    # STRICT: only match if the profile URL path is exact
                    # Match instagram.com/username but NOT instagram.com/p/ or /reel/
                    if f"instagram.com/{username.lower()}" in link_lower and "/p/" not in link_lower and "/reel/" not in link_lower:
                        profile_res = res
                        break

            if not profile_res:
                embed = discord.Embed(
                    title=f"Instagram Profile: @{username}",
                    url=f"https://www.instagram.com/{username}",
                    description="Kliki 3la l-link bach tchouf l-profile.",
                    color=0x000000
                )
                await wait.edit(embed=embed)
                return

            title = profile_res.get("title", f"@{username} on Instagram")
            snippet = profile_res.get("snippet", "")
            link = profile_res.get("link", f"https://www.instagram.com/{username}")

            followers = "unknown"
            following = "unknown"
            posts = "unknown"

            f_match = re.search(r"([\d\.]+[KMB]?) Followers", snippet, re.IGNORECASE)
            if f_match:
                followers = f_match.group(1)
            f_match2 = re.search(r"([\d\.]+[KMB]?) Following", snippet, re.IGNORECASE)
            if f_match2:
                following = f_match2.group(1)
            p_match = re.search(r"([\d\.]+[KMB]?) Posts", snippet, re.IGNORECASE)
            if p_match:
                posts = p_match.group(1)

            embed = discord.Embed(
                title=title,
                url=link,
                description=snippet,
                color=0x000000
            )
            embed.add_field(name="Followers", value=followers, inline=True)
            embed.add_field(name="Following", value=following, inline=True)
            embed.add_field(name="Posts", value=posts, inline=True)

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

    @commands.command(name="twitter", aliases=["x"], help="Nqelleb lik 3la user f Twitter (X).")
    async def twitter(self, ctx, username: str):
        username = username.lstrip("@")
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        try:
            params = {
                "engine": "google",
                "q": f"site:x.com/{username}",
                "api_key": os.getenv('SERPAPI_KEY')
            }
            search = serpapi.GoogleSearch(params)
            results = await asyncio.to_thread(search.get_dict)

            organic = results.get("organic_results", [])
            profile_res = None
            if organic:
                for res in organic:
                    link_lower = res.get("link", "").lower()
                    # STRICT: only match if the profile URL itself is the link
                    # Match x.com/username but NOT x.com/username/status/
                    if (f"x.com/{username.lower()}" in link_lower or f"twitter.com/{username.lower()}" in link_lower) and "/status/" not in link_lower:
                        profile_res = res
                        break

            if not profile_res:
                embed = discord.Embed(
                    title=f"X Profile: @{username}",
                    url=f"https://x.com/{username}",
                    description="Kliki 3la l-link bach tchouf l-profile.",
                    color=0x000000
                )
                await wait.edit(embed=embed)
                return

            title = profile_res.get("title", f"@{username} on X")
            snippet = profile_res.get("snippet", "")
            link = profile_res.get("link", f"https://x.com/{username}")

            followers = "unknown"
            f_match = re.search(r"([\d\.]+[KMB]?) Followers", snippet, re.IGNORECASE)
            if f_match:
                followers = f_match.group(1)

            embed = discord.Embed(
                title=title,
                url=link,
                description=snippet,
                color=0x000000
            )
            embed.add_field(name="Followers", value=followers, inline=True)

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

    @commands.command(name="download", aliases=["dl", "save"], help="Ndownloadi lik video/audio mn TikTok, Instagram, wla YouTube Shorts.")
    async def download(self, ctx, url: str):
        wait = await ctx.send(embed=discord.Embed(description="Kandownloadi l-media, sber chwia...", color=0x000000))
        
        filename = f"download_{ctx.message.id}"
        
        def run_ytdl():
            ydl_opts = {
                'outtmpl': f'{filename}.%(ext)s',
                'format': 'best',
                'max_filesize': 25 * 1024 * 1024,
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        try:
            downloaded_file = await asyncio.to_thread(run_ytdl)
            if downloaded_file and os.path.exists(downloaded_file):
                file_size = os.path.getsize(downloaded_file)
                if file_size <= 25 * 1024 * 1024:
                    await ctx.send(file=discord.File(downloaded_file))
                    await wait.delete()
                else:
                    await wait.edit(embed=discord.Embed(description="L-file kber mn 25MB. M9drtch nsifto f Discord.", color=0x000000))
                try:
                    os.remove(downloaded_file)
                except:
                    pass
            else:
                await wait.edit(embed=discord.Embed(description="Mochkil: Mal9itch l-file dyal download.", color=0x000000))
        except Exception as e:
            for f in os.listdir("."):
                if f.startswith(filename):
                    try:
                        os.remove(f)
                    except:
                        pass
            await wait.edit(embed=discord.Embed(description=f"Mochkil: `{e}`", color=0x000000))

    @commands.command(name="usernamecheck", aliases=["usersearch"], help="Nchouf lik username wach khdam f social media kamlin.")
    async def usernamecheck(self, ctx, username: str):
        wait = await ctx.send(embed=discord.Embed(description=f"Kanchekki username `{username}` f 20 platforms...", color=0x000000))

        platforms = {
            "GitHub": {"url": "https://api.github.com/users/{username}", "type": "status"},
            "Reddit": {"url": "https://www.reddit.com/user/{username}/about.json", "type": "status"},
            "Chess.com": {"url": "https://api.chess.com/pub/player/{username}", "type": "status"},
            "Scratch": {"url": "https://api.scratch.mit.edu/users/{username}", "type": "status"},
            "Docker Hub": {"url": "https://hub.docker.com/v2/users/{username}/", "type": "status"},
            "Spotify": {"url": "https://open.spotify.com/user/{username}", "type": "status"},
            "Pinterest": {"url": "https://www.pinterest.com/{username}/", "type": "status"},
            "DeviantArt": {"url": "https://www.deviantart.com/{username}", "type": "status"},
            "SoundCloud": {"url": "https://soundcloud.com/{username}", "type": "status"},
            "Twitch": {"url": "https://www.twitch.tv/{username}", "type": "status"},
            "Steam": {"url": "https://steamcommunity.com/id/{username}", "type": "steam"},
            "Linktree": {"url": "https://linktree.com/{username}", "type": "status"},
            "Letterboxd": {"url": "https://letterboxd.com/{username}/", "type": "status"},
            "DailyMotion": {"url": "https://www.dailymotion.com/{username}", "type": "status"},
            "Behance": {"url": "https://www.behance.net/{username}", "type": "status"},
            "Medium": {"url": "https://medium.com/@{username}", "type": "status"},
            "Duolingo": {"url": "https://www.duolingo.com/2017-06-30/users?username={username}", "type": "duolingo"},
            "Vimeo": {"url": "https://vimeo.com/{username}", "type": "status"},
            "NPM": {"url": "https://www.npmjs.com/~{username}", "type": "status"},
            "Patreon": {"url": "https://www.patreon.com/{username}", "type": "status"}
        }

        async def check_platform(session, name, config, u):
            url = config["url"].format(username=u)
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
            try:
                if config["type"] == "status":
                    async with session.get(url, headers=headers, timeout=8) as resp:
                        if resp.status == 404:
                            return name, "Available"
                        elif resp.status in [200, 301, 302]:
                            return name, "Taken"
                        else:
                            return name, f"Error ({resp.status})"
                elif config["type"] == "steam":
                    async with session.get(url, headers=headers, timeout=8) as resp:
                        if resp.status == 404:
                            return name, "Available"
                        html = await resp.text()
                        if "The specified profile could not be found" in html or "Error" in html:
                            return name, "Available"
                        return name, "Taken"
                elif config["type"] == "duolingo":
                    async with session.get(url, headers=headers, timeout=8) as resp:
                        if resp.status == 404:
                            return name, "Available"
                        data = await resp.json()
                        if data.get("users"):
                            return name, "Taken"
                        return name, "Available"
            except Exception:
                return name, "Error"

        async with aiohttp.ClientSession() as session:
            tasks = [check_platform(session, name, config, username) for name, config in platforms.items()]
            results = await asyncio.gather(*tasks)

        lines = []
        for name, status in results:
            icon = "✅" if status == "Available" else "❌" if status == "Taken" else "⚠️"
            lines.append(f"{icon} **{name}**: {status}")

        half = len(lines) // 2
        col1 = "\n".join(lines[:half])
        col2 = "\n".join(lines[half:])

        embed = discord.Embed(
            title=f"Username Check: `{username}`",
            color=0x000000
        )
        embed.add_field(name="Platforms (1-10)", value=col1, inline=True)
        embed.add_field(name="Platforms (11-20)", value=col2, inline=True)

        await wait.edit(embed=embed)

    @commands.command(name="minecraft", aliases=["mc", "namemc"], help="Njbed lik details w skin dyal Minecraft user.")
    async def minecraft(self, ctx, username: str):
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        url = f"https://playerdb.co/api/player/minecraft/{username}"
        headers = {"User-Agent": "SifdineBot"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 404:
                        await wait.edit(embed=discord.Embed(description=f"Mal9itch had l-user f Minecraft: `{username}`", color=0x000000))
                        return
                    if resp.status != 200:
                        raise Exception(f"HTTP Code {resp.status}")
                    data = await resp.json()

            if not data.get("success"):
                await wait.edit(embed=discord.Embed(description=f"Mal9itch had l-user f Minecraft: `{username}`", color=0x000000))
                return

            player_data = data["data"]["player"]
            uuid = player_data["id"]
            current_name = player_data["username"]
            meta = player_data.get("meta", {})
            name_history_raw = meta.get("name_history", [])

            history_list = []
            for idx, hist in enumerate(name_history_raw):
                history_list.append(f"{idx+1}. {hist.get('name')}")

            history_str = "\n".join(history_list) if history_list else "Walo history."
            if len(history_str) > 1024:
                history_str = history_str[:1020] + "..."

            embed = discord.Embed(
                title=f"Minecraft Profile: {current_name}",
                url=f"https://namemc.com/profile/{uuid}",
                description=f"**Name History:**\n{history_str}",
                color=0x000000
            )
            embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{uuid}/128")
            embed.set_image(url=f"https://mc-heads.net/body/{uuid}/256")
            embed.add_field(name="UUID", value=f"`{uuid}`", inline=False)
            embed.add_field(name="Skins & Customizations", value=f"[Khtar l-Skin dyalo (Skin URL)](https://mc-heads.net/skin/{uuid})", inline=False)
            embed.set_footer(text="Mojang name history restrictions may apply.")

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

    @commands.command(name="roblox", aliases=["rbx"], help="Njbed lik details ta3 user f Roblox.")
    async def roblox(self, ctx, username: str):
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        try:
            resolve_url = "https://users.roblox.com/v1/usernames/users"
            payload = {"usernames": [username], "excludeBannedUsers": False}

            async with aiohttp.ClientSession() as session:
                async with session.post(resolve_url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        raise Exception(f"Roblox username resolution failed with code {resp.status}")
                    search_data = await resp.json()

            user_list = search_data.get("data", [])
            if not user_list:
                await wait.edit(embed=discord.Embed(description=f"Mal9itch had l-user f Roblox: `{username}`", color=0x000000))
                return

            user_id = user_list[0]["id"]

            detail_url = f"https://users.roblox.com/v1/users/{user_id}"
            avatar_url = f"https://thumbnails.roblox.com/v1/users/avatar?userIds={user_id}&size=352x352&format=Png&isCircular=false"

            async with aiohttp.ClientSession() as session:
                async with session.get(detail_url, headers=headers) as resp_detail, \
                           session.get(avatar_url, headers=headers) as resp_avatar:

                    if resp_detail.status != 200 or resp_avatar.status != 200:
                        raise Exception("Roblox APIs were not responsive.")

                    details = await resp_detail.json()
                    avatar_data = await resp_avatar.json()

            display_name = details.get("displayName")
            real_name = details.get("name")
            bio = details.get("description") or "Walo bio."
            created_at_str = details.get("created")

            created_date = created_at_str.split("T")[0] if created_at_str else "Makhfi"

            thumb_url = None
            avatar_list = avatar_data.get("data", [])
            if avatar_list:
                thumb_url = avatar_list[0].get("imageUrl")

            embed = discord.Embed(
                title=f"Roblox Profile: {display_name} (@{real_name})",
                url=f"https://www.roblox.com/users/{user_id}/profile",
                description=bio,
                color=0x000000
            )
            if thumb_url:
                embed.set_thumbnail(url=thumb_url)

            embed.add_field(name="User ID", value=f"`{user_id}`", inline=True)
            embed.add_field(name="Mchyed f", value=created_date, inline=True)

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

    @commands.command(name="chessuser", aliases=["chessprofile"], help="Njbed lik details o stats ta3 user f Chess.com.")
    async def chessuser(self, ctx, username: str):
        wait = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        headers = {"User-Agent": "SifdineBot"}
        profile_url = f"https://api.chess.com/pub/player/{username}"
        stats_url = f"https://api.chess.com/pub/player/{username}/stats"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(profile_url, headers=headers) as resp_profile, \
                           session.get(stats_url, headers=headers) as resp_stats:

                    if resp_profile.status == 404:
                        await wait.edit(embed=discord.Embed(description=f"Mal9itch had l-user f Chess.com: `{username}`", color=0x000000))
                        return
                    if resp_profile.status != 200 or resp_stats.status != 200:
                        raise Exception("Chess.com APIs were not responsive.")

                    profile = await resp_profile.json()
                    stats = await resp_stats.json()

            name = profile.get("name") or profile.get("username")
            title = profile.get("title")
            avatar = profile.get("avatar")
            followers = profile.get("followers", 0)
            joined_timestamp = profile.get("joined")

            joined_date = datetime.fromtimestamp(joined_timestamp, pytz.utc).strftime("%Y-%m-%d") if joined_timestamp else "Makhfi"

            blitz_rating = stats.get("chess_blitz", {}).get("last", {}).get("rating", "N/A")
            rapid_rating = stats.get("chess_rapid", {}).get("last", {}).get("rating", "N/A")
            bullet_rating = stats.get("chess_bullet", {}).get("last", {}).get("rating", "N/A")

            embed = discord.Embed(
                title=f"Chess.com Profile: {name}" + (f" [{title}]" if title else ""),
                url=profile.get("url"),
                color=0x000000
            )
            if avatar:
                embed.set_thumbnail(url=avatar)

            embed.add_field(name="Blitz Rating ⚡", value=f"**{blitz_rating}**", inline=True)
            embed.add_field(name="Rapid Rating ⏱️", value=f"**{rapid_rating}**", inline=True)
            embed.add_field(name="Bullet Rating ☄️", value=f"**{bullet_rating}**", inline=True)
            embed.add_field(name="Followers", value=str(followers), inline=True)
            embed.add_field(name="Mchyed f", value=joined_date, inline=True)

            await wait.edit(embed=embed)
        except Exception as e:
            await wait.edit(embed=discord.Embed(description=f"Tra chy mochkil: `{e}`", color=0x000000))

async def setup(bot):
    await bot.add_cog(GlobUtil(bot))