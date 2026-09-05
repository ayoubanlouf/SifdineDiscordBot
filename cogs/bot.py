import os
import io
import json
import psutil
import time
from datetime import datetime, timezone
import discord
from discord.ext import commands
from converters import FuzzyMember


class Bot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    def get_dir_size(self, path="."):
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
        return total_size

    async def get_discloud_app_id(self, force_refresh: bool = False):
        app_id_env = os.environ.get("DISCLOUD_APP_ID")
        if app_id_env and app_id_env.strip():
            return app_id_env.strip()

        if not force_refresh and hasattr(self, "_cached_discloud_app_id") and self._cached_discloud_app_id:
            return self._cached_discloud_app_id

        # Query /user endpoint to discover active app ID
        data = await self._discloud_request("GET", "/user")
        if data.get("status") == "ok" and "user" in data:
            user_apps = data["user"].get("apps", [])
            if user_apps and len(user_apps) > 0:
                self._cached_discloud_app_id = str(user_apps[0])
                return self._cached_discloud_app_id

        if os.path.exists("discloud.config"):
            try:
                with open("discloud.config", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("ID="):
                            val = line.split("=", 1)[1].strip()
                            if val:
                                return val
            except Exception:
                pass
        return "all"

    async def _discloud_request(self, method: str, endpoint: str, json_data: dict = None):
        token = os.environ.get("DISCLOUD_API_TOKEN")
        if not token:
            return {"status": "error", "message": "DISCLOUD_API_TOKEN missing from .env"}

        headers = {
            "api-token": token.strip(),
            "User-Agent": "SifdineDiscordBot/1.0"
        }
        url = f"https://api.discloud.app/v2{endpoint}"
        session = getattr(self.bot, "session", None)
        if not session or session.closed:
            return {"status": "error", "message": "Bot session not ready"}

        try:
            async with session.request(method, url, headers=headers, json=json_data, timeout=12) as resp:
                data = await resp.json()
                return data
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _make_progress_bar(self, used: float, total: float, length: int = 8) -> str:
        if total <= 0:
            return "[░░░░░░░░]"
        pct = min(1.0, max(0.0, used / total))
        filled = int(round(pct * length))
        return f"[`{'█' * filled}{'░' * (length - filled)}` {pct * 100:.0f}%]"

    async def _send_host_status(self, ctx):
        # Local metrics
        process = psutil.Process(os.getpid())
        ram_bytes = process.memory_info().rss
        local_ram_mb = ram_bytes / (1024 * 1024)
        local_cpu_pct = psutil.cpu_percent(interval=None)

        db_path = "bot_database.db"
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        dir_size_mb = self.get_dir_size(".") / (1024 * 1024)

        app_id = await self.get_discloud_app_id()
        data = await self._discloud_request("GET", f"/app/{app_id}/status")

        # Auto-retry if app ID changed
        if data.get("status") != "ok" and "not found" in str(data.get("message", "")).lower():
            app_id = await self.get_discloud_app_id(force_refresh=True)
            data = await self._discloud_request("GET", f"/app/{app_id}/status")

        embed = discord.Embed(
            title="☁️ Discloud Host Status",
            color=0x000000,
            timestamp=datetime.now(timezone.utc) if 'datetime' in globals() else ctx.message.created_at
        )

        if data.get("status") == "ok" and "apps" in data:
            apps_data = data["apps"]
            if isinstance(apps_data, list) and apps_data:
                app_info = apps_data[0]
            elif isinstance(apps_data, dict):
                app_info = apps_data
            else:
                app_info = {}

            container_status = app_info.get("container", "Online")
            status_emoji = "🟢" if container_status.lower() == "online" else "🔴"
            cpu_val = app_info.get("cpu", f"{local_cpu_pct:.1f}%")
            memory_str = app_info.get("memory", f"{local_ram_mb:.1f}/100MB")
            started_at = app_info.get("startedAt", "Unknown")
            restarts = app_info.get("restarts", 0)
            net_io = app_info.get("netIO", {})
            ssd_str = app_info.get("ssd", "N/A")

            # Parse memory numbers for progress bar if formatted as X/YMB
            ram_bar_str = ""
            try:
                if "/" in memory_str:
                    parts = memory_str.replace("MB", "").replace("GB", "").split("/")
                    used_val = float(parts[0])
                    total_val = float(parts[1])
                    ram_bar_str = f"\n{self._make_progress_bar(used_val, total_val)}"
            except Exception:
                pass

            embed.description = f"**Container State:** {status_emoji} `{container_status}`\n**App ID:** `{app_info.get('id', app_id)}`"
            embed.add_field(name="Host RAM", value=f"`{memory_str}`{ram_bar_str}", inline=True)
            embed.add_field(name="Host CPU", value=f"`{cpu_val}`", inline=True)
            embed.add_field(name="Restarts", value=f"`{restarts}`", inline=True)

            if net_io:
                down_str = net_io.get("down", "0 MB")
                up_str = net_io.get("up", "0 MB")
                embed.add_field(name="Network I/O", value=f"⬇️ `{down_str}` • ⬆️ `{up_str}`", inline=True)

            if ssd_str != "N/A":
                embed.add_field(name="SSD Storage", value=f"`{ssd_str}`", inline=True)

            embed.add_field(name="Container Uptime", value=f"`{started_at}`", inline=True)
        else:
            # Fallback if token not set or API error
            err_msg = data.get("message", "API unavailable")
            embed.description = f"⚠️ *Discloud API: {err_msg}*\nShowing local process metrics:"
            embed.add_field(name="Process RAM (RSS)", value=f"`{local_ram_mb:.2f} MB`", inline=True)
            embed.add_field(name="Process CPU", value=f"`{local_cpu_pct:.1f}%`", inline=True)

        embed.add_field(name="Database Size", value=f"`{db_size_mb:.2f} MB`", inline=True)
        embed.add_field(name="Project Folder", value=f"`{dir_size_mb:.2f} MB`", inline=True)
        embed.set_footer(text="Discloud Host Management", icon_url=self.bot.user.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.group(name="host", aliases=["discloud"], invoke_without_command=True, help="Discloud host & server metrics.")
    async def host(self, ctx):
        await self._send_host_status(ctx)

    @host.command(name="status", aliases=["stats", "info", "usage"], help="Tchouf live stats ta3 container f Discloud.")
    async def host_status(self, ctx):
        await self._send_host_status(ctx)

    @host.command(name="logs", aliases=["log", "terminal"], help="Tchouf live terminal console logs ta3 Discloud.")
    @commands.is_owner()
    async def host_logs(self, ctx):
        wait_msg = await ctx.send("⏳ Kanjbed logs mn Discloud terminal...")
        app_id = await self.get_discloud_app_id()
        data = await self._discloud_request("GET", f"/app/{app_id}/logs")

        if data.get("status") != "ok" and "not found" in str(data.get("message", "")).lower():
            app_id = await self.get_discloud_app_id(force_refresh=True)
            data = await self._discloud_request("GET", f"/app/{app_id}/logs")

        if data.get("status") != "ok" or "apps" not in data:
            err = data.get("message", "Mal9itch logs")
            await wait_msg.edit(content=f"❌ Mochkil f Discloud API: `{err}`")
            return

        apps_data = data["apps"]
        if isinstance(apps_data, list) and apps_data:
            app_obj = apps_data[0]
        elif isinstance(apps_data, dict):
            app_obj = apps_data
        else:
            app_obj = {}

        terminal_data = app_obj.get("terminal", {})
        raw_logs = ""
        if isinstance(terminal_data, dict):
            raw_logs = terminal_data.get("big") or terminal_data.get("small") or terminal_data.get("url") or ""
        elif isinstance(terminal_data, str):
            raw_logs = terminal_data

        if not raw_logs.strip():
            await wait_msg.edit(content="📄 Terminal logs khawyin f Discloud.")
            return

        # Chunk lines into pages for paginator (~25 lines or ~1200 chars per page)
        log_lines = raw_logs.strip().splitlines()
        pages = []
        current_chunk = []
        current_len = 0

        for line in log_lines:
            if current_len + len(line) > 1200 or len(current_chunk) >= 20:
                pages.append("```ini\n" + "\n".join(current_chunk) + "\n```")
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line)
        if current_chunk:
            pages.append("```ini\n" + "\n".join(current_chunk) + "\n```")

        await wait_msg.delete()
        view = self.bot.Paginator(ctx, pages=pages, title=f"🖥️ Discloud Terminal Logs ({len(log_lines)} lines)")
        view.message = await ctx.send(embed=view.get_page(), view=view if len(pages) > 1 else None)

    @host.command(name="restart", aliases=["reboot"], help="Rebooti container f Discloud.")
    @commands.is_owner()
    async def host_restart(self, ctx):
        confirm_msg = await ctx.send("🔄 Kansift reboot request l Discloud container...")
        app_id = await self.get_discloud_app_id()
        data = await self._discloud_request("PUT", f"/app/{app_id}/restart")

        if data.get("status") != "ok" and "not found" in str(data.get("message", "")).lower():
            app_id = await self.get_discloud_app_id(force_refresh=True)
            data = await self._discloud_request("PUT", f"/app/{app_id}/restart")

        if data.get("status") == "ok":
            await confirm_msg.edit(content="✅ **Reboot request dazt!** Container rah ghadi yredemarri daba.")
        else:
            err = data.get("message", "Error unknown")
            await confirm_msg.edit(content=f"❌ Tra mochkil f reboot: `{err}`")

    @host.command(name="backup", aliases=["snapshot", "cloudbackup"], help="Telechargi backup kamla mn Discloud.")
    @commands.is_owner()
    async def host_backup(self, ctx):
        wait_msg = await ctx.send("📦 Kantlb backup link mn Discloud...")
        app_id = await self.get_discloud_app_id()
        data = await self._discloud_request("GET", f"/app/{app_id}/backup")

        if data.get("status") != "ok" and "not found" in str(data.get("message", "")).lower():
            app_id = await self.get_discloud_app_id(force_refresh=True)
            data = await self._discloud_request("GET", f"/app/{app_id}/backup")

        if data.get("status") == "ok" and "backups" in data:
            backups_data = data["backups"]
            if isinstance(backups_data, list) and backups_data:
                backup_obj = backups_data[0]
            elif isinstance(backups_data, dict):
                backup_obj = backups_data
            else:
                backup_obj = {}

            backup_url = backup_obj.get("url")
            if not backup_url:
                await wait_msg.edit(content="❌ Mal9itch download URL f response ta3 Discloud.")
                return

            embed = discord.Embed(
                title="📦 Discloud Cloud Backup",
                description=f"✅ **Backup URL t9adat:**\n[🔗 Download Full Project Backup Zip]({backup_url})\n\n-# _Link kayt expira mora chwya dyal lwa9t._",
                color=0x000000,
                timestamp=ctx.message.created_at
            )
            embed.set_footer(text=f"App ID: {app_id}")
            try:
                await ctx.author.send(embed=embed)
                await wait_msg.edit(content="✅ Sifet lik direct Discloud backup download link f DMs!")
            except Exception:
                await ctx.send(embed=embed)
                await wait_msg.delete()
        else:
            err = data.get("message", "Error unknown")
            await wait_msg.edit(content=f"❌ Tra mochkil f Discloud backup: `{err}`")



    @commands.command(name="servers", aliases=['guilds'], help="Servers li dakhl lihom ana.")
    async def servers(self, ctx):
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("Ma3endekch l7e9 tsta3ml had lcmd :/")
            return

        members = 0
        owners = set()
        server_lines = []

        for guild in self.bot.guilds:
            line = f"**{guild.name}** (`{guild.id}`) | `{guild.owner}` | `{guild.member_count}`"
            server_lines.append(line)
            members += guild.member_count
            if guild.owner:
                owners.add(guild.owner.id)

        if not server_lines:
            await ctx.send("Ana makayn f ta server.")
            return

        title_text = f"Servers: ({len(self.bot.guilds)}) | Owners: ({len(owners)}) | Members: ({members})"

        view = self.bot.Paginator(ctx, pages=server_lines, per_page=10, title=title_text)
        view.message = await ctx.send(embed=view.get_page(), view=view)



    @commands.command(name="inviter", help="Chkoun dkhelni lserver.")
    async def inviter(self, ctx, guild_id: int):
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("Ma3nkdch l7e9 tkhdm had l cmd :/")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            await ctx.send("Makaynch ana fdak server.")
            return

        bot_inviter = "Unknown"
        try:
            integrations = await guild.integrations()
            for integration in integrations:
                if isinstance(integration, discord.BotIntegration):
                    if integration.application.user.id == self.bot.user.id:
                        bot_inviter = f"{integration.user.name} (`{integration.user.id}`)"
                        break
        except Exception:
            pass

        invite_url = "Ma3ndich perm bach n9ad invite."
        view = discord.ui.View()


        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                try:
                    invite = await channel.create_invite(max_age=300, max_uses=1)
                    invite_url = invite.url
                    view.add_item(discord.ui.Button(label="Join Server", url=invite_url, style=discord.ButtonStyle.link))
                    break
                except Exception:
                    continue

        embed = discord.Embed(title=guild.name, color=0x000000, timestamp=ctx.message.created_at)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="Owner", value=f"{guild.owner} (`{guild.owner_id if guild.owner else 'Unknown'}`)", inline=False)
        embed.add_field(name="Added By", value=bot_inviter, inline=False)
        embed.add_field(name="Members", value=f"`{guild.member_count}`", inline=True)
        embed.add_field(name="Created At", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name="Invite Link", value=invite_url, inline=False)

        await ctx.send(embed=embed, view=view if len(view.children) > 0 else None)


    @commands.command(name="block", aliases=["blocki", "tjahl", "nkhl"], help="Manb9ach njawb khouna.")
    @commands.is_owner()
    async def block(self, ctx, user: FuzzyMember):
        async with self.bot.db.execute("SELECT 1 FROM blacklists WHERE user_id = ?", (user.id,)) as cursor:
            if await cursor.fetchone():
                await ctx.send(f"`{user}` deja blockito hh")
                return

        async with self.bot.db.execute("INSERT INTO blacklists (user_id) VALUES (?)", (user.id,)):
            await self.bot.db.commit()
        await ctx.send(f"Safi blockit `{user}`.")


    @commands.command(name="unblock", aliases=["unblocki", "tsal7"], help="Nrje3 njawb khouna.")
    @commands.is_owner()
    async def unblock(self, ctx, user: FuzzyMember):
        async with self.bot.db.execute("SELECT 1 FROM blacklists WHERE user_id = ?", (user.id,)) as cursor:
            if not await cursor.fetchone():
                await ctx.send(f"`{user}` mamblokihch aslan.")
                return

        async with self.bot.db.execute("DELETE FROM blacklists WHERE user_id = ?", (user.id,)):
            await self.bot.db.commit()
        await ctx.send(f"Safi unblockit `{user}`.")


    @commands.command(name="blacklist", aliases=["blocks", "blocklist"], help="List ta3 nas li mblocki.")
    @commands.is_owner()
    async def blacklist(self, ctx):
        async with self.bot.db.execute("SELECT user_id FROM blacklists") as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await ctx.send("Mambloki ta wa7d.")
            return

        blacklist_lines = []
        for row in rows:
            user_id = row[0]
            # Try to look up username from cache, fallback to raw ID if unavailable
            user_obj = self.bot.get_user(user_id)
            if user_obj:
                blacklist_lines.append(f"• {user_obj.name} (`{user_id}`)")
            else:
                blacklist_lines.append(f"• Unknown User (`{user_id}`)")

        view = self.bot.Paginator(ctx, pages=blacklist_lines, per_page=10, title=f"Blacklist ({len(rows)})")
        view.message = await ctx.send(embed=view.get_page(), view=view)



    @commands.command(name="suggestion",aliases=["zid", "suggest"], help="Seft 9tira7 ladmin.")
    async def suggestion(self, ctx, *, content: str = None):
        if content is None and not ctx.message.attachments:
            await ctx.send("Khassk tktb chi suggestion wla lo7 chi tswira/file.")
            return

        channel_id = os.getenv("SUGGESTIONS_CHANNEL_ID")
        if not channel_id:
            print("SUGGESTIONS_CHANNEL_ID is not set inside .env file.")
            return
            
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            print(f"Couldn't find suggestions channel with ID: {channel_id}")
            return

        embed = discord.Embed(
            description=content or "No content provided.",
            color=0x000000,
            timestamp=ctx.message.created_at
        )
        embed.set_author(name=f"{ctx.author} ({ctx.author.id})", icon_url=ctx.author.display_avatar.url)
        
        files = [await a.to_file() for a in ctx.message.attachments]
        await channel.send(embed=embed, files=files)
        await ctx.send("Safi wslatni suggestion ta3k, an7awlo nzidouha f a9rab wa9t inshaallah!")

    @commands.command(name="bug", aliases=["report", "9ad"],  help="Reporti chi bug ladmin.")
    async def bug(self, ctx, *, content: str = None):
        if content is None and not ctx.message.attachments:
            await ctx.send("Khassk tktb chi bug wla lo7 chi tsowira/file.")
            return

        channel_id = os.getenv("BUGS_CHANNEL_ID")
        if not channel_id:
            print("BUGS_CHANNEL_ID is not set inside .env file.")
            return

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            print(f"Couldn't find bugs channel with ID: {channel_id}")
            return

        embed = discord.Embed(
            description=content or "No content provided.",
            color=0x000000,
            timestamp=ctx.message.created_at
        )
        embed.set_author(name=f"{ctx.author} ({ctx.author.id})", icon_url=ctx.author.display_avatar.url)
        
        files = [await a.to_file() for a in ctx.message.attachments]
        await channel.send(embed=embed, files=files)
        await ctx.send("Safi wselni lbug report ta3k, an9adoh f a9rab wa9t inshaallah!")

    @commands.command(name="botinfo", aliases=["info", "nta"])
    async def botinfo(self, ctx):


        total_guilds = len(self.bot.guilds)
        total_users = sum(g.member_count for g in self.bot.guilds if g.member_count)

        embed = discord.Embed(
            title=f"{self.bot.user.name}",
            description=f"Seftni lkhwadri AyouBot nkhdem blasto.\nIla khastk chy 7aja goul `{ctx.prefix}3te9`.",
            color=0x000000,
            timestamp=ctx.message.created_at
        )

        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)

        embed.add_field(name="Host", value=f"• **Owner:** `activif`\n• **Library:** `discord.py v{discord.__version__}`", inline=True)
        embed.add_field(name="Stats", value=f"• **Servers:** `{total_guilds}`\n• **Users:** `{total_users}`", inline=True)

        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)


        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Invite Bot",
            url="https://discord.com/oauth2/authorize?client_id=1522281059163701349&permissions=8&integration_type=0&scope=bot",
            style=discord.ButtonStyle.link
        ))
        view.add_item(discord.ui.Button(
            label="Tajda Server",
            url="https://discord.gg/QBkEfez3FJ",
            style=discord.ButtonStyle.link
        ))
        view.add_item(discord.ui.Button(
            label="GitHub Repo",
            url="https://github.com/ayoubanlouf/SifdineDiscordBot/",
            style=discord.ButtonStyle.link
        ))

        await ctx.send(embed=embed, view=view)


    @commands.command(name="backup", help="Sift cloud backup ta3 database l DMs ta3 lowner.")
    async def backup(self, ctx):
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("Ma3endekch l7e9 tsta3ml had lcmd :/")
            return

        wait_msg = await ctx.send("📦 Kanjbed snapshot mn database...")
        tables = ["guild_prefixes", "guild_logs", "blacklists", "afk", "minigame_leaderboard", "reminders"]
        backup_data = {
            "timestamp": int(time.time()),
            "datetime": datetime.now(timezone.utc).isoformat(),
            "guilds": len(self.bot.guilds),
            "tables": {}
        }
        try:
            total_records = 0
            for t in tables:
                async with self.bot.db.execute(f"SELECT * FROM {t}") as cursor:
                    rows = await cursor.fetchall()
                    col_names = cursor.col_names if hasattr(cursor, "col_names") else []
                    records = []
                    for r in rows:
                        if hasattr(r, "_values"):
                            records.append(list(r._values))
                        else:
                            records.append(list(r))
                    backup_data["tables"][t] = {
                        "columns": col_names,
                        "rows": records
                    }
                    total_records += len(records)

            json_bytes = json.dumps(backup_data, indent=2).encode("utf-8")
            file = discord.File(io.BytesIO(json_bytes), filename=f"sifdine_cloud_backup_{int(time.time())}.json")
            embed = discord.Embed(
                title="☁️ Database Snapshot Backup",
                description=(
                    f"• **Timestamp:** <t:{int(time.time())}:F>\n"
                    f"• **Tables Backed Up:** `{len(tables)}`\n"
                    f"• **Total Records:** `{total_records:,}`\n"
                    f"• **Size:** `{len(json_bytes) / 1024:.2f} KB`\n"
                    f"• **Guilds:** `{len(self.bot.guilds)}`"
                ),
                color=0x000000
            )
            await ctx.author.send(embed=embed, file=file)
            await wait_msg.edit(content="✅ Sifet lik database backup snapshot f DMs!")
        except Exception as e:
            await wait_msg.edit(content=f"❌ Tra mochkil f backup: `{e}`")

    @commands.command(name="disable", help="Desactivi command f had server.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def disable_command(self, ctx: commands.Context, *, command_name: str):
        clean_name = command_name.strip().lower()
        if clean_name.startswith(ctx.prefix.lower()):
            clean_name = clean_name[len(ctx.prefix):].strip()

        target_cmd = self.bot.get_command(clean_name)
        if not target_cmd:
            await ctx.send(f"❌ Mal9itch chi command smitha `{command_name}`.")
            return

        canonical_name = target_cmd.qualified_name.lower()
        if canonical_name in ("enable", "disable", "disabled", "help"):
            await ctx.send(f"❌ Mat9dch tdesactivi command `{canonical_name}` 7it essential!")
            return

        async with self.bot.db.execute(
            "SELECT 1 FROM disabled_commands WHERE guild_id = ? AND command_name = ?",
            (ctx.guild.id, canonical_name)
        ) as cursor:
            exists = await cursor.fetchone()

        if exists:
            await ctx.send(f"⚠️ Command `{canonical_name}` deja mdesaktivia f had server.")
            return

        await self.bot.db.execute(
            "INSERT INTO disabled_commands (guild_id, command_name) VALUES (?, ?)",
            (ctx.guild.id, canonical_name)
        )
        await self.bot.db.commit()

        embed = discord.Embed(
            title="🚫 Command Disabled",
            description=f"✅ Desaktiviti command `{canonical_name}` f had server.\n7ta wa7d ma ghay9der ysta3melha daba.",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="enable", help="Activi chy command mdesactivia f had server.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def enable_command(self, ctx: commands.Context, *, command_name: str):
        clean_name = command_name.strip().lower()
        if clean_name.startswith(ctx.prefix.lower()):
            clean_name = clean_name[len(ctx.prefix):].strip()

        target_cmd = self.bot.get_command(clean_name)
        canonical_name = target_cmd.qualified_name.lower() if target_cmd else clean_name

        async with self.bot.db.execute(
            "SELECT 1 FROM disabled_commands WHERE guild_id = ? AND command_name = ?",
            (ctx.guild.id, canonical_name)
        ) as cursor:
            exists = await cursor.fetchone()

        if not exists:
            await ctx.send(f"⚠️ Command `{canonical_name}` mamdisabliach f had server.")
            return

        await self.bot.db.execute(
            "DELETE FROM disabled_commands WHERE guild_id = ? AND command_name = ?",
            (ctx.guild.id, canonical_name)
        )
        await self.bot.db.commit()

        embed = discord.Embed(
            title="✅ Command Enabled",
            description=f"🟢 Re-enablit command `{canonical_name}` f had server!",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="disabled", aliases=["disabledlist", "disabledcmds"], help="Chouf ga3 commands li mdesactivyin f had server.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def list_disabled(self, ctx: commands.Context):
        async with self.bot.db.execute(
            "SELECT command_name FROM disabled_commands WHERE guild_id = ? ORDER BY command_name ASC",
            (ctx.guild.id,)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            embed = discord.Embed(
                title="📋 Disabled Commands",
                description="✨ Walo! Ga3 commands khdamin f had server.",
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        cmds_list = [f"• `{r[0]}`" for r in rows]
        paginator = self.bot.Paginator(ctx, cmds_list, per_page=15, title=f"🚫 Disabled Commands ({len(rows)})")
        embed = discord.Embed(
            title=f"🚫 Disabled Commands ({len(rows)})",
            description="\n".join(paginator.chunks[0]) if paginator.chunks else "Walo",
            color=0x000000
        )
        if paginator.total_pages > 1:
            embed.set_footer(text=f"Page 1/{paginator.total_pages} • Server: {ctx.guild.name}")
            msg = await ctx.send(embed=embed, view=paginator)
            paginator.message = msg
        else:
            embed.set_footer(text=f"Server: {ctx.guild.name}")
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Bot(bot))