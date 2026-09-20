import os
import io
import re
import sys
import json
import psutil
import time
import asyncio
from datetime import datetime, timezone
import discord
from discord.ext import commands
from converters import FuzzyMember


RESTART_STATE_FILE = ".pending_restart.json"


class Bot(commands.Cog, name="Bot"):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = getattr(self.bot, "start_time", None) or time.time()
        self._cached_discloud_app_id = None
        self._cached_bothosting_deployment_id = None
        self._reboot_checked = False

    async def _save_pending_restart(self, channel_id: int, message_id: int, action: str, provider: str):
        timestamp = time.time()
        # 1. Local JSON state file
        try:
            with open(RESTART_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "action": action,
                    "provider": provider,
                    "timestamp": timestamp
                }, f)
        except Exception as e:
            print(f"[RESTART] Failed to write state file: {e}")

        # 2. Database state fallback
        try:
            await self.bot.db.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_restart (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
                """
            )
            await self.bot.db.execute(
                "INSERT INTO pending_restart (channel_id, message_id, action, provider, timestamp) VALUES (?, ?, ?, ?, ?)",
                (channel_id, message_id, action, provider, timestamp)
            )
        except Exception as e:
            print(f"[RESTART] Failed to save to DB: {e}")

    async def _clear_pending_restart(self):
        try:
            if os.path.exists(RESTART_STATE_FILE):
                os.remove(RESTART_STATE_FILE)
        except Exception:
            pass

        try:
            await self.bot.db.execute("DROP TABLE IF EXISTS pending_restart")
        except Exception:
            pass

    async def _check_pending_restart(self):
        state = None
        # 1. Check file state
        if os.path.exists(RESTART_STATE_FILE):
            try:
                with open(RESTART_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = None

        # 2. Check DB state
        if not state:
            try:
                async with self.bot.db.execute(
                    "SELECT channel_id, message_id, action, provider, timestamp FROM pending_restart ORDER BY id DESC LIMIT 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        state = {
                            "channel_id": row["channel_id"],
                            "message_id": row["message_id"],
                            "action": row["action"],
                            "provider": row["provider"],
                            "timestamp": row["timestamp"]
                        }
            except Exception:
                pass

        await self._clear_pending_restart()

        if not state:
            return

        channel_id = state.get("channel_id")
        message_id = state.get("message_id")
        action = state.get("action", "restart")
        provider = state.get("provider") or self.detect_hosting_provider()
        start_ts = state.get("timestamp", time.time())
        duration = max(0.5, time.time() - start_ts)

        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(channel_id)
            if not channel:
                return
            msg = await channel.fetch_message(message_id)
            if not msg:
                return

            if action == "pull":
                title = "🚀 3odna!"
                desc = (
                    "✅ Safi pullit akher commit mn repo.\n"
                    f"⏱️ **Duration:** `{duration:.1f}s`"
                )
            else:
                title = "🟢 3odna!"
                desc = (
                    "✅ Redemarrit am3lm.\n"
                    f"⏱️ **Duration:** `{duration:.1f}s`"
                )

            embed = discord.Embed(
                title=title,
                description=desc,
                color=0x000000,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"Sifdine Host Management • {provider.capitalize()}")
            await msg.edit(content=None, embed=embed)
        except Exception as e:
            print(f"[RESTART] Failed to edit confirmation message: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._reboot_checked:
            return
        self._reboot_checked = True
        await self._check_pending_restart()


    def _get_dir_size_sync(self, path="."):
        total_size = 0
        ignored_dirs = {".git", ".venv", "__pycache__", ".idea"}
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in ignored_dirs]
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    try:
                        total_size += os.path.getsize(fp)
                    except OSError:
                        pass
        return total_size

    async def get_dir_size(self, path="."):
        return await asyncio.to_thread(self._get_dir_size_sync, path)

    def detect_hosting_provider(self) -> str:
        # 1. Explicit environment variable check
        p = os.environ.get("HOSTING_PROVIDER", "").strip().lower()
        if p in ("bothosting", "bot-hosting", "bot-hosting.net"):
            return "bothosting"
        if p == "discloud":
            return "discloud"
        if p == "local":
            return "local"

        # 2. Check Bot-Hosting indicators
        if os.environ.get("P_SERVER_UUID") or os.environ.get("BOT_HOSTING") or os.environ.get("BOT_HOSTING_API_KEY"):
            if os.name != "nt" or os.environ.get("P_SERVER_UUID"):
                return "bothosting"

        # 3. Check Discloud indicators
        if os.environ.get("DISCLOUD_APP_ID") and os.environ.get("ENVIRONMENT") == "prod":
            return "discloud"
        if os.path.exists("/home/container/discloud.config"):
            return "discloud"

        # 4. Fallback to local
        return "local"

    # ==================== BOT-HOSTING.NET HELPERS ====================
    async def _bothosting_request(self, method: str, endpoint: str, json_data: dict = None, params: dict = None):
        token = os.environ.get("BOT_HOSTING_API_KEY")
        if not token:
            return {"status": "error", "message": "BOT_HOSTING_API_KEY missing from .env"}

        headers = {
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "SifdineDiscordBot/1.0"
        }
        url = f"https://bot-hosting.net/api/v1{endpoint}"
        session = getattr(self.bot, "session", None)
        if not session or session.closed:
            return {"status": "error", "message": "Bot session not ready"}

        try:
            async with session.request(method, url, headers=headers, json=json_data, params=params, timeout=25) as resp:
                try:
                    data = await resp.json()
                    if (resp.status >= 400 or data.get("ok") is False) and isinstance(data, dict):
                        data.setdefault("status", "error")
                        # Unpack error message from Bot-Hosting's error schema
                        if "message" not in data or not data["message"]:
                            err_obj = data.get("error")
                            if isinstance(err_obj, dict):
                                data["message"] = err_obj.get("message") or err_obj.get("code") or "Error unknown"
                            elif isinstance(err_obj, str):
                                data["message"] = err_obj
                            elif data.get("detail"):
                                data["message"] = str(data["detail"])
                    return data
                except Exception:
                    text = await resp.text()
                    return {"status": "error" if resp.status >= 400 else "ok", "raw": text, "message": text.strip() or f"HTTP {resp.status}", "http_status": resp.status}
        except asyncio.TimeoutError:
            return {"status": "error", "message": "Request timed out (>25s)"}
        except Exception as e:
            return {"status": "error", "message": str(e) or "Network connection failed"}

    async def get_bothosting_deployment_id(self, force_refresh: bool = False) -> str:
        dep_id = os.environ.get("BOT_HOSTING_DEPLOYMENT_ID")
        if dep_id and dep_id.strip():
            return dep_id.strip()

        if not force_refresh and self._cached_bothosting_deployment_id:
            return self._cached_bothosting_deployment_id

        data = await self._bothosting_request("GET", "/deployments")
        deployments = []
        if isinstance(data, list):
            deployments = data
        elif isinstance(data, dict):
            deployments = data.get("deployments", [])

        if deployments:
            for d in deployments:
                name = str(d.get("name", "")).lower()
                if "sifdine" in name or "bot" in name:
                    self._cached_bothosting_deployment_id = str(d.get("id"))
                    return self._cached_bothosting_deployment_id
            self._cached_bothosting_deployment_id = str(deployments[0].get("id"))
            return self._cached_bothosting_deployment_id

        return None

    # ==================== DISCLOUD HELPERS ====================
    async def get_discloud_app_id(self, force_refresh: bool = False):
        app_id_env = os.environ.get("DISCLOUD_APP_ID")
        if app_id_env and app_id_env.strip():
            return app_id_env.strip()

        if not force_refresh and hasattr(self, "_cached_discloud_app_id") and self._cached_discloud_app_id:
            return self._cached_discloud_app_id

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

    # ==================== HOST OVERVIEW & STATUS ====================
    async def _send_host_overview(self, ctx):
        provider = self.detect_hosting_provider()
        now_dt = datetime.now(timezone.utc)

        process = psutil.Process(os.getpid())
        ram_bytes = process.memory_info().rss
        local_ram_mb = ram_bytes / (1024 * 1024)
        local_cpu_pct = psutil.cpu_percent(interval=None)

        uptime_seconds = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        if provider == "bothosting":
            title = "Bot-Hosting.net"
            badge = "🟢 **Online on Bot-Hosting.net Container**"
            dep_id = await self.get_bothosting_deployment_id()
            desc = f"{badge}\n**Deployment ID:** `{dep_id or 'Auto-discovering...'}`"

            subcommands = (
                "• `sat host status` — Live container resource metrics (RAM, CPU, Disk, Network)\n"
                "• `sat host ping` — Latency metrics (Discord Gateway, REST & Database)\n"
                "• `sat host env` — Audit environment variables (ENVIRONMENT value & keys status)\n"
                "• `sat host git` — Current Git commit, branch & linked repository\n"
                "• `sat host pull` (or `sync`) — Direct GitHub pull & auto-restart\n"
                "• `sat host autopull [on|off]` — View or toggle auto-pull on container restart\n"
                "• `sat host dir [path]` — Browse container files and folders\n"
                "• `sat host logs [lines]` — Paginated console terminal logs\n"
                "• `sat host restart` — Send reboot power signal to container\n"
                "• `sat host backup` — Create instant cloud snapshot\n"
                "• `sat host diagnose` — Run deployment diagnostic health check"
            )
            color = 0x000000

        elif provider == "discloud":
            title = "Discloud Host"
            badge = "☁️ **Online on Discloud Container**"
            app_id = await self.get_discloud_app_id()
            desc = f"{badge}\n**App ID:** `{app_id}`"

            subcommands = (
                "• `sat host status` — Live container resource metrics (RAM, CPU, Restarts, SSD)\n"
                "• `sat host ping` — Latency metrics (Discord Gateway, REST & Database)\n"
                "• `sat host env` — Audit environment variables (ENVIRONMENT value & keys status)\n"
                "• `sat host git` — Current Git commit, branch & deployment info\n"
                "• `sat host dir [path]` — Browse container files and folders\n"
                "• `sat host logs` — Paginated terminal console logs\n"
                "• `sat host restart` — Reboot Discloud container\n"
                "• `sat host backup` — Generate & DM full project backup zip"
            )
            color = 0x000000

        else:
            title = "Local Development Host"
            badge = "💻 **Running Locally** (Dev Environment)"
            py_ver = sys.version.split()[0]
            desc = f"{badge}\n**Platform:** `{os.name.upper()}` • **Python:** `{py_ver}`"

            subcommands = (
                "• `sat host status` — Local process memory, CPU, DB & project size\n"
                "• `sat host ping` — Latency metrics (Discord Gateway, REST & Database)\n"
                "• `sat host env` — Audit environment variables (ENVIRONMENT value & keys status)\n"
                "• `sat host git` — Current Git commit, branch & author\n"
                "• `sat host dir [path]` — Browse project files and folders\n"
                "• `sat host restart` — Restart local bot process\n"
                "• `sat host backup` — Generate local project zip backup\n"
                "• `sat host logs` — View recent process logs"
            )
            color = 0x000000

        embed = discord.Embed(
            title=title,
            description=desc,
            color=color,
            timestamp=now_dt
        )
        embed.add_field(name="Process RAM", value=f"`{local_ram_mb:.2f} MB`", inline=True)
        embed.add_field(name="Process CPU", value=f"`{local_cpu_pct:.1f}%`", inline=True)
        embed.add_field(name="Process Uptime", value=f"`{uptime_str}`", inline=True)
        embed.add_field(name="🛠️ Available Host Commands", value=subcommands, inline=False)
        embed.set_footer(text="Owner Only", icon_url=self.bot.user.display_avatar.url if self.bot.user else None)
        await ctx.send(embed=embed)

    async def _send_host_status(self, ctx):
        provider = self.detect_hosting_provider()

        process = psutil.Process(os.getpid())
        ram_bytes = process.memory_info().rss
        local_ram_mb = ram_bytes / (1024 * 1024)
        local_cpu_pct = psutil.cpu_percent(interval=None)

        db_path = "bot_database.db"
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        dir_size_mb = (await self.get_dir_size(".")) / (1024 * 1024)

        if provider == "bothosting":
            dep_id = await self.get_bothosting_deployment_id()
            if not dep_id:
                await ctx.send("❌ Mal9itch chi deployment f Bot-Hosting.net account dyalk.")
                return

            wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
            dep_info = await self._bothosting_request("GET", f"/deployments/{dep_id}")
            res_info = await self._bothosting_request("GET", f"/deployments/{dep_id}/resources")

            if dep_info.get("status") == "error" or res_info.get("status") == "error":
                dep_id = await self.get_bothosting_deployment_id(force_refresh=True)
                if dep_id:
                    dep_info = await self._bothosting_request("GET", f"/deployments/{dep_id}")
                    res_info = await self._bothosting_request("GET", f"/deployments/{dep_id}/resources")

            embed = discord.Embed(
                title="🟢 Bot-Hosting.net Host Status",
                color=0x000000,
                timestamp=datetime.now(timezone.utc)
            )

            state = dep_info.get("state") or res_info.get("state") or "running"
            status_emoji = "🟢" if state.lower() == "running" else ("🟡" if state.lower() in ("starting", "stopping") else "🔴")
            embed.description = f"**Container State:** {status_emoji} `{state.capitalize()}`\n**Deployment ID:** `{dep_id}`\n**Name:** `{dep_info.get('name', 'Sifdine')}`"

            mem_data = res_info.get("memory", {})
            used_mem_bytes = mem_data.get("usedBytes", 0)
            limit_mem_bytes = mem_data.get("limitBytes", 268435456)
            used_mb = used_mem_bytes / (1024 * 1024)
            limit_mb = (limit_mem_bytes / (1024 * 1024)) if limit_mem_bytes else 256.0
            ram_bar = self._make_progress_bar(used_mb, limit_mb)
            embed.add_field(name="Host RAM (256MB Pool)", value=f"`{used_mb:.1f} / {limit_mb:.0f} MB`\n{ram_bar}", inline=True)

            cpu_data = res_info.get("cpu", {})
            cpu_pct = cpu_data.get("usedPercent", 0.0)
            embed.add_field(name="Host CPU", value=f"`{cpu_pct:.1f}%`", inline=True)

            uptime_raw = res_info.get("uptime")
            if uptime_raw is not None:
                u_sec = int(uptime_raw)
                h, rem = divmod(u_sec, 3600)
                m, s = divmod(rem, 60)
                embed.add_field(name="Container Uptime", value=f"`{h}h {m}m {s}s`", inline=True)

            disk_data = res_info.get("disk", {})
            if disk_data:
                used_disk_mb = disk_data.get("usedBytes", 0) / (1024 * 1024)
                embed.add_field(name="Disk Storage", value=f"`{used_disk_mb:.1f} MB`", inline=True)

            net_data = res_info.get("network", {})
            if net_data:
                rx_mb = net_data.get("rxBytes", 0) / (1024 * 1024)
                tx_mb = net_data.get("txBytes", 0) / (1024 * 1024)
                embed.add_field(name="Network I/O", value=f"⬇️ `{rx_mb:.1f} MB` • ⬆️ `{tx_mb:.1f} MB`", inline=True)

            git_data = dep_info.get("git", {})
            if git_data and isinstance(git_data, dict):
                repo_str = git_data.get("repo")
                branch_str = git_data.get("branch", "main")
                autopull_str = "Enabled" if git_data.get("autoPull") else "Disabled"
                if repo_str:
                    embed.add_field(name="Linked GitHub", value=f"`{repo_str}:{branch_str}` (Auto-pull: `{autopull_str}`)", inline=False)

            embed.add_field(name="Database Size", value=f"`{db_size_mb:.2f} MB`", inline=True)
            embed.add_field(name="Project Folder", value=f"`{dir_size_mb:.2f} MB`", inline=True)
            embed.set_footer(text="Owner Only", icon_url=self.bot.user.display_avatar.url if self.bot.user else None)

            await wait_msg.delete()
            await ctx.send(embed=embed)
            return

        elif provider == "discloud":
            app_id = await self.get_discloud_app_id()
            data = await self._discloud_request("GET", f"/app/{app_id}/status")

            if data.get("status") != "ok" and "not found" in str(data.get("message", "")).lower():
                app_id = await self.get_discloud_app_id(force_refresh=True)
                data = await self._discloud_request("GET", f"/app/{app_id}/status")

            embed = discord.Embed(
                title="☁️ Discloud Host Status",
                color=0x000000,
                timestamp=datetime.now(timezone.utc)
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
                err_msg = data.get("message", "API unavailable")
                embed.description = f"⚠️ *Discloud API: {err_msg}*\nShowing local process metrics:"
                embed.add_field(name="Process RAM (RSS)", value=f"`{local_ram_mb:.2f} MB`", inline=True)
                embed.add_field(name="Process CPU", value=f"`{local_cpu_pct:.1f}%`", inline=True)

            embed.add_field(name="Database Size", value=f"`{db_size_mb:.2f} MB`", inline=True)
            embed.add_field(name="Project Folder", value=f"`{dir_size_mb:.2f} MB`", inline=True)
            embed.set_footer(text="Owner Only", icon_url=self.bot.user.display_avatar.url if self.bot.user else None)
            await ctx.send(embed=embed)
            return

        else:
            embed = discord.Embed(
                title="💻 Local Development Host Status",
                color=0x000000,
                timestamp=datetime.now(timezone.utc)
            )
            py_ver = sys.version.split()[0]
            embed.description = f"**Environment:** 💻 Local Development\n**Platform:** `{os.name.upper()}` • **Python:** `{py_ver}`"

            # System RAM
            sys_ram = psutil.virtual_memory()
            sys_ram_used_mb = sys_ram.used / (1024 * 1024)
            sys_ram_total_mb = sys_ram.total / (1024 * 1024)
            ram_bar = self._make_progress_bar(sys_ram_used_mb, sys_ram_total_mb)

            embed.add_field(name="Process RAM (RSS)", value=f"`{local_ram_mb:.2f} MB`", inline=True)
            embed.add_field(name="Process CPU", value=f"`{local_cpu_pct:.1f}%`", inline=True)
            embed.add_field(name="System RAM", value=f"`{sys_ram_used_mb / 1024:.1f} / {sys_ram_total_mb / 1024:.1f} GB`\n{ram_bar}", inline=True)

            disk = psutil.disk_usage(".")
            disk_used_gb = disk.used / (1024 ** 3)
            disk_total_gb = disk.total / (1024 ** 3)
            disk_bar = self._make_progress_bar(disk_used_gb, disk_total_gb)
            embed.add_field(name="Disk Usage", value=f"`{disk_used_gb:.1f} / {disk_total_gb:.1f} GB`\n{disk_bar}", inline=True)

            embed.add_field(name="Database Size", value=f"`{db_size_mb:.2f} MB`", inline=True)
            embed.add_field(name="Project Folder", value=f"`{dir_size_mb:.2f} MB`", inline=True)
            embed.set_footer(text="Owner Only", icon_url=self.bot.user.display_avatar.url if self.bot.user else None)
            await ctx.send(embed=embed)
            return

    # ==================== HOST COMMAND GROUP (OWNER ONLY) ====================
    @commands.group(name="host", aliases=["discloud"], invoke_without_command=True, help="Host & server management.")
    @commands.is_owner()
    async def host(self, ctx):
        await self._send_host_overview(ctx)

    @host.command(name="status", aliases=["stats", "info", "usage"], help="Tchouf live stats dyal host/container.")
    @commands.is_owner()
    async def host_status(self, ctx):
        await self._send_host_status(ctx)

    @host.command(name="logs", aliases=["log", "terminal"], help="Tchouf live terminal console logs.")
    @commands.is_owner()
    async def host_logs(self, ctx, lines: int = 100):
        provider = self.detect_hosting_provider()

        if provider == "bothosting":
            dep_id = await self.get_bothosting_deployment_id()
            if not dep_id:
                await ctx.send("❌ Mal9itch chi deployment ID.")
                return

            wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
            data = await self._bothosting_request("GET", f"/deployments/{dep_id}/logs", params={"size": lines})

            if data.get("status") == "error":
                await wait_msg.edit(content=f"❌ Mochkil f Bot-Hosting logs: `{data.get('message')}`")
                return

            log_lines = data.get("lines", [])
            if isinstance(log_lines, str):
                log_lines = log_lines.splitlines()
            elif not isinstance(log_lines, list):
                log_lines = []

            if not log_lines:
                await wait_msg.edit(content="📄 Console logs khawyin f Bot-Hosting.net.")
                return

            clean_lines = [re.sub(r'\x1b\[[0-9;]*[mGKH]', '', l) for l in log_lines]
            pages = []
            current_chunk = []
            current_len = 0

            for line in clean_lines:
                if current_len + len(line) > 1200 or len(current_chunk) >= 20:
                    pages.append("```ini\n" + "\n".join(current_chunk) + "\n```")
                    current_chunk = [line]
                    current_len = len(line)
                else:
                    current_chunk.append(line)
                    current_len += len(line)
            if current_chunk:
                pages.append("```ini\n" + "\n".join(current_chunk) + "\n```")

            pages.reverse()

            await wait_msg.delete()
            view = self.bot.Paginator(ctx, pages=pages, title=f"🖥️ Bot-Hosting.net Logs — Latest first ({len(clean_lines)} lines)")
            view.message = await ctx.send(embed=view.get_page(), view=view if len(pages) > 1 else None)
            return

        elif provider == "discloud":
            wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
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

            pages.reverse()

            await wait_msg.delete()
            view = self.bot.Paginator(ctx, pages=pages, title=f"🖥️ Discloud Terminal Logs — Latest first ({len(log_lines)} lines)")
            view.message = await ctx.send(embed=view.get_page(), view=view if len(pages) > 1 else None)
            return

        else:
            await ctx.send("ℹ️ Running locally — stdout/stderr is printing to your local terminal console.")

    @host.command(name="restart", aliases=["reboot"], help="Rebooti container / process.")
    @commands.is_owner()
    async def host_restart(self, ctx):
        provider = self.detect_hosting_provider()

        
        confirm_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))

        await self._save_pending_restart(
            channel_id=ctx.channel.id,
            message_id=confirm_msg.id,
            action="restart",
            provider=provider
        )

        if provider == "bothosting":
            dep_id = await self.get_bothosting_deployment_id()
            if not dep_id:
                await self._clear_pending_restart()
                await confirm_msg.edit(embed=discord.Embed(description="❌ Mal9itch chi deployment ID.", color=0x000000))
                return

            data = await self._bothosting_request("POST", f"/deployments/{dep_id}/power", json_data={"signal": "restart"})

            if data.get("status") == "error":
                err = data.get("message", "Error unknown")
                await self._clear_pending_restart()
                await confirm_msg.edit(embed=discord.Embed(description=f"❌ Mochkil f reboot: `{err}`", color=0x000000))
            return

        elif provider == "discloud":
            app_id = await self.get_discloud_app_id()
            data = await self._discloud_request("PUT", f"/app/{app_id}/restart")

            if data.get("status") != "ok" and "not found" in str(data.get("message", "")).lower():
                app_id = await self.get_discloud_app_id(force_refresh=True)
                data = await self._discloud_request("PUT", f"/app/{app_id}/restart")

            if data.get("status") != "ok":
                err = data.get("message", "Error unknown")
                await self._clear_pending_restart()
                await confirm_msg.edit(embed=discord.Embed(description=f"❌ Tra mochkil f reboot: `{err}`", color=0x000000))
            return

        else:
            await asyncio.sleep(0.5)
            os.execv(sys.executable, ['python'] + sys.argv)

    @host.command(name="backup", aliases=["snapshot", "cloudbackup"], help="Backup project.")
    @commands.is_owner()
    async def host_backup(self, ctx):
        provider = self.detect_hosting_provider()

        if provider == "bothosting":
            dep_id = await self.get_bothosting_deployment_id()
            if not dep_id:
                await ctx.send("❌ Mal9itch chi deployment ID.")
                return

            wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
            data = await self._bothosting_request("POST", f"/deployments/{dep_id}/backups")

            if data.get("status") == "error":
                err = data.get("message", "Error unknown")
                await wait_msg.edit(content=f"❌ Mochkil f backup: `{err}`")
            else:
                await wait_msg.edit(content="✅ **Cloud Backup snapshot started!** Bot-Hosting.net rah kay9ad backup kamla daba.")
            return

        elif provider == "discloud":
            wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
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
            return

        else:
            # Local backup
            wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
            try:
                from discloud.bothosting_zip import create_bundle as make_local_zip
                await asyncio.to_thread(make_local_zip)
                await wait_msg.edit(content="✅ **Local Backup t9adat!** File rah f `discloud/bothosting_bundle.zip`.")
            except Exception as e:
                await wait_msg.edit(content=f"❌ Mochkil f local backup: `{e}`")

    @host.command(name="pull", aliases=["sync"], help="Pull latest code from GitHub & restart (Bot-Hosting.net).")
    @commands.is_owner()
    async def host_pull(self, ctx):
        provider = self.detect_hosting_provider()
        if provider != "bothosting":
            await ctx.send("ℹ️ Had lcmd khasa b **Bot-Hosting.net** 7it 3ndha direct GitHub sync.")
            return

        dep_id = await self.get_bothosting_deployment_id()
        if not dep_id:
            await ctx.send("❌ Mal9itch chi deployment ID.")
            return

        
        wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))

        await self._save_pending_restart(
            channel_id=ctx.channel.id,
            message_id=wait_msg.id,
            action="pull",
            provider=provider
        )

        data = await self._bothosting_request("POST", f"/deployments/{dep_id}/sync", json_data={})

        if data.get("status") == "error" or data.get("ok") is False:
            err = data.get("message") or (data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None) or "Error unknown"
            await self._clear_pending_restart()
            await wait_msg.edit(embed=discord.Embed(description=f"❌ Mochkil f GitHub sync: `{err}`", color=0x000000))

    @host.command(name="autopull", help="Tchouf wla tbdel auto-pull on restart (Bot-Hosting.net).")
    @commands.is_owner()
    async def host_autopull(self, ctx, toggle: str = None):
        provider = self.detect_hosting_provider()
        if provider != "bothosting":
            await ctx.send("ℹ️ Had lcmd khasa b **Bot-Hosting.net**.")
            return

        dep_id = await self.get_bothosting_deployment_id()
        if not dep_id:
            await ctx.send("❌ Mal9itch chi deployment ID.")
            return

        if toggle is None:
            data = await self._bothosting_request("GET", f"/deployments/{dep_id}/git")
            if data.get("status") == "error":
                await ctx.send(f"❌ Mochkil: `{data.get('message')}`")
                return
            is_auto = data.get("autoPull", False)
            repo = data.get("repo", "Unknown")
            branch = data.get("branch", "main")
            status_txt = "🟢 **Enabled**" if is_auto else "🔴 **Disabled**"
            await ctx.send(f"📦 **Bot-Hosting.net GitHub Auto-Pull Status:**\n• **State:** {status_txt}\n• **Repo:** `{repo}`\n• **Branch:** `{branch}`\n\n_Bghiti tbdelha? Dir `sat host autopull on` wla `sat host autopull off`._")
        else:
            choice = toggle.lower().strip()
            if choice in ("on", "enable", "true", "1", "khedem"):
                new_state = True
            elif choice in ("off", "disable", "false", "0", "7bes"):
                new_state = False
            else:
                await ctx.send("❌ Kteb `on` wla `off`: `sat host autopull on/off`")
                return

            data = await self._bothosting_request("PATCH", f"/deployments/{dep_id}/git", json_data={"autoPull": new_state})
            if data.get("status") == "error":
                await ctx.send(f"❌ Mochkil f update dyal auto-pull: `{data.get('message')}`")
            else:
                action = "🟢 **khedmat** (Enabled)" if new_state else "🔴 **t7bsat** (Disabled)"
                await ctx.send(f"✅ Auto-pull f kola restart {action}!")

    @host.command(name="diagnose", aliases=["diag", "check"], help="Diagnostic check ta3 deployment.")
    @commands.is_owner()
    async def host_diagnose(self, ctx):
        provider = self.detect_hosting_provider()
        if provider != "bothosting":
            await self._send_host_overview(ctx)
            return

        dep_id = await self.get_bothosting_deployment_id()
        if not dep_id:
            await ctx.send("❌ Mal9itch chi deployment ID.")
            return

        wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        data = await self._bothosting_request("GET", f"/deployments/{dep_id}/diagnose")

        if data.get("status") == "error":
            await wait_msg.edit(content=f"❌ Mochkil f diagnose: `{data.get('message')}`")
            return

        embed = discord.Embed(
            title="🔍 Bot-Hosting.net Diagnostic Report",
            color=0x000000,
            timestamp=datetime.now(timezone.utc)
        )
        state = data.get("state", "unknown")
        embed.add_field(name="Container State", value=f"`{state}`", inline=True)
        if "cpu" in data and isinstance(data["cpu"], dict):
            embed.add_field(name="CPU Usage", value=f"`{data['cpu'].get('usedPercent', 0)}%`", inline=True)
        if "memory" in data and isinstance(data["memory"], dict):
            used_mb = data["memory"].get("usedBytes", 0) / (1024 * 1024)
            embed.add_field(name="Memory Usage", value=f"`{used_mb:.1f} MB`", inline=True)

        logs = data.get("logs", [])
        if isinstance(logs, list) and logs:
            clean_logs = [re.sub(r'\x1b\[[0-9;]*[mGKH]', '', l) for l in logs[-15:]]
            tail_logs = "\n".join(clean_logs)
            if len(tail_logs) > 1000:
                tail_logs = tail_logs[-1000:]
            embed.add_field(name="Recent Console Tail", value=f"```ini\n{tail_logs}\n```", inline=False)

        await wait_msg.delete()
        await ctx.send(embed=embed)

    @host.command(name="dir", aliases=["files", "ls", "tree"], help="Tchouf files o folders f container volume.")
    @commands.is_owner()
    async def host_dir(self, ctx, *, path: str = "."):
        root_abs = os.path.abspath(".")
        clean_target = path.strip().strip("\"'")
        target_abs = os.path.abspath(os.path.join(root_abs, clean_target))

        # Security: Prevent traversing outside project root
        if not target_abs.startswith(root_abs):
            await ctx.send("❌ Mat9edch tchouf files barra mn project directory.")
            return

        if not os.path.exists(target_abs):
            await ctx.send(f"❌ Mal9itch had l'path: `{path}`")
            return

        if os.path.isfile(target_abs):
            size_kb = os.path.getsize(target_abs) / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(target_abs), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            embed = discord.Embed(
                title=f"📄 {os.path.basename(target_abs)}",
                description=f"• **Path:** `{os.path.relpath(target_abs, root_abs)}`\n• **Size:** `{size_kb:.2f} KB`\n• **Modified:** `{mtime} UTC`",
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        entries = []
        try:
            with os.scandir(target_abs) as it:
                for entry in it:
                    if entry.name in (".git", ".venv", "__pycache__", ".idea"):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        size = entry.stat().st_size if not is_dir else 0
                        entries.append((entry.name, is_dir, size))
                    except OSError:
                        pass
        except Exception as e:
            await ctx.send(f"❌ Ma9ditch n9ra lfolder: `{e}`")
            return

        entries.sort(key=lambda x: (not x[1], x[0].lower()))

        lines = []
        for name, is_dir, size in entries:
            if is_dir:
                lines.append(f"📁 **{name}/**")
            else:
                if size >= 1024 * 1024:
                    s_str = f"{size / (1024 * 1024):.1f} MB"
                elif size >= 1024:
                    s_str = f"{size / 1024:.1f} KB"
                else:
                    s_str = f"{size} B"
                lines.append(f"📄 `{name}` ({s_str})")

        rel_path = os.path.relpath(target_abs, root_abs)
        display_path = "/" if rel_path == "." else f"/{rel_path}"

        if not lines:
            lines = ["*Folder khawi.*"]

        title = f"📁 Files Explorer — `{display_path}` ({len(entries)} items)"
        view = self.bot.Paginator(ctx, pages=lines, per_page=15, title=title)
        view.message = await ctx.send(embed=view.get_page(), view=view if view.total_pages > 1 else None)

    @host.command(name="env", aliases=["vars", "environment"], help="Tchouf environment variables status.")
    @commands.is_owner()
    async def host_env(self, ctx):
        embed = discord.Embed(
            title="⚙️ Host Environment Configuration",
            color=0x000000,
            timestamp=datetime.now(timezone.utc)
        )

        # Environment Mode & Provider (Values Visible)
        env_mode = os.environ.get("ENVIRONMENT", "prod").strip().strip("\"'").lower()
        provider = self.detect_hosting_provider()
        embed.description = f"• **ENVIRONMENT:** `{env_mode.upper()}`\n• **HOSTING_PROVIDER:** `{provider}`"

        # Core required credentials
        core_vars = [
            ("DISCORD_TOKEN", "Discord Bot Token"),
            ("TURSO_DATABASE_URL", "Turso DB URL"),
            ("TURSO_AUTH_TOKEN", "Turso Auth Token"),
        ]
        core_lines = []
        for var_name, label in core_vars:
            val = os.environ.get(var_name)
            is_set = bool(val and val.strip().strip("\"'"))
            status = "🟢 Configured" if is_set else "🔴 Missing"
            core_lines.append(f"• `{var_name}`: {status}")
        embed.add_field(name="🔑 Core Required Keys", value="\n".join(core_lines), inline=False)

        # Hosting API credentials
        hosting_vars = [
            ("BOT_HOSTING_API_KEY", "Bot-Hosting.net API Key"),
            ("BOT_HOSTING_DEPLOYMENT_ID", "Bot-Hosting Deployment ID"),
            ("DISCLOUD_API_TOKEN", "Discloud API Token"),
            ("DISCLOUD_APP_ID", "Discloud App ID"),
        ]
        host_lines = []
        for var_name, label in hosting_vars:
            val = os.environ.get(var_name)
            is_set = bool(val and val.strip().strip("\"'"))
            status = "🟢 Configured" if is_set else "⚪ Not Set"
            host_lines.append(f"• `{var_name}`: {status}")
        embed.add_field(name="☁️ Hosting Integration Keys", value="\n".join(host_lines), inline=False)

        # Optional feature APIs
        optional_vars = [
            "IMAGE_KEY", "WEATHER_KEY", "SERPAPI_KEY", "OCR_KEY",
            "RAPID_API_KEY_1", "RAPID_API_KEY_2", "STEAM_API_KEY",
            "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"
        ]
        opt_lines = []
        for var_name in optional_vars:
            val = os.environ.get(var_name)
            is_set = bool(val and val.strip().strip("\"'"))
            status = "🟢 Configured" if is_set else "⚪ Not Set"
            opt_lines.append(f"• `{var_name}`: {status}")
        embed.add_field(name="📦 Optional Feature APIs", value="\n".join(opt_lines), inline=False)

        # Channel IDs
        channel_vars = ["SUGGESTIONS_CHANNEL_ID", "BUGS_CHANNEL_ID", "BACKUP_CHANNEL_ID"]
        chan_lines = []
        for var_name in channel_vars:
            val = os.environ.get(var_name)
            is_set = bool(val and val.strip().strip("\"'") and val.strip() != "0")
            status = "🟢 Configured" if is_set else "⚪ Not Set"
            chan_lines.append(f"• `{var_name}`: {status}")
        embed.add_field(name="📢 Special Channels", value="\n".join(chan_lines), inline=False)

        embed.set_footer(text="Sifdine Host Configuration • Sensitive values are masked")
        await ctx.send(embed=embed)

    @host.command(name="git", aliases=["commit", "version"], help="Tchouf current Git commit o branch.")
    @commands.is_owner()
    async def host_git(self, ctx):
        provider = self.detect_hosting_provider()
        embed = discord.Embed(
            title="🐙 Git Deployment Info",
            color=0x000000,
            timestamp=datetime.now(timezone.utc)
        )

        commit_hash = "Unknown"
        commit_msg = "Unknown"
        commit_author = "Unknown"
        commit_date = "Unknown"
        branch = "Unknown"

        # 1. Try running git CLI command
        try:
            import subprocess
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "log", "-1", "--format=%h|%s|%an|%ar"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parts = proc.stdout.strip().split("|")
                if len(parts) >= 4:
                    commit_hash, commit_msg, commit_author, commit_date = parts[0], parts[1], parts[2], parts[3]

            branch_proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if branch_proc.returncode == 0 and branch_proc.stdout.strip():
                branch = branch_proc.stdout.strip()
        except Exception:
            pass

        # 2. Fallback to reading .git/HEAD directly if git CLI unavailable
        if commit_hash == "Unknown" and os.path.exists(".git/HEAD"):
            try:
                with open(".git/HEAD", "r", encoding="utf-8") as f:
                    ref = f.read().strip()
                if ref.startswith("ref: refs/heads/"):
                    branch = ref.split("refs/heads/", 1)[1]
                    ref_file = os.path.join(".git", "refs", "heads", branch)
                    if os.path.exists(ref_file):
                        with open(ref_file, "r", encoding="utf-8") as rf:
                            commit_hash = rf.read().strip()[:7]
            except Exception:
                pass

        # 3. If on Bot-Hosting.net, query linked repo details from API
        if provider == "bothosting":
            dep_id = await self.get_bothosting_deployment_id()
            if dep_id:
                git_data = await self._bothosting_request("GET", f"/deployments/{dep_id}/git")
                if isinstance(git_data, dict) and git_data.get("linked"):
                    repo_name = git_data.get("repo", "Unknown")
                    gh_branch = git_data.get("branch", "main")
                    is_autopull = git_data.get("autoPull", False)
                    auto_str = "🟢 Enabled" if is_autopull else "🔴 Disabled"
                    embed.add_field(name="Linked GitHub Repo", value=f"[{repo_name}](https://github.com/{repo_name})", inline=True)
                    embed.add_field(name="GitHub Branch", value=f"`{gh_branch}`", inline=True)
                    embed.add_field(name="Auto-Pull on Restart", value=auto_str, inline=True)

        embed.add_field(name="Current Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit Hash", value=f"`{commit_hash}`", inline=True)
        embed.add_field(name="Commit Date", value=f"`{commit_date}`", inline=True)
        embed.add_field(name="Commit Message", value=f"*{commit_msg}*", inline=False)
        if commit_author != "Unknown":
            embed.add_field(name="Author", value=f"`{commit_author}`", inline=True)

        embed.set_footer(text=f"Owner Only")
        await ctx.send(embed=embed)

    @host.command(name="ping", aliases=["latency", "pong"], help="Chouf latency dyal Discord WebSocket, REST API o Database.")
    @commands.is_owner()
    async def host_ping(self, ctx: commands.Context):
        ws_latency_ms = round(self.bot.latency * 1000)

        # Measure DB latency
        t_db0 = time.perf_counter()
        async with self.bot.db.execute("SELECT 1") as cursor:
            await cursor.fetchone()
        db_latency_ms = round((time.perf_counter() - t_db0) * 1000, 2)

        # Measure REST API roundtrip
        t_msg0 = time.perf_counter()
        wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
        rest_latency_ms = round((time.perf_counter() - t_msg0) * 1000)

        # Connection health indicator
        if ws_latency_ms < 100:
            indicator = "🟢 Fast"
        elif ws_latency_ms < 250:
            indicator = "🟡 Okay"
        else:
            indicator = "🔴 Slow"

        provider = self.detect_hosting_provider()
        embed = discord.Embed(
            title=f"Latency Metrics ({provider.capitalize()})",
            color=0x000000,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="WebSocket (Gateway)", value=f"`{ws_latency_ms} ms`", inline=True)
        embed.add_field(name="REST API (Roundtrip)", value=f"`{rest_latency_ms} ms`", inline=True)
        embed.add_field(name="Database (Turso/SQLite)", value=f"`{db_latency_ms} ms`", inline=True)
        embed.add_field(name="Connection Health", value=indicator, inline=False)
        embed.set_footer(text="Owner Only")

        await wait_msg.edit(content=None, embed=embed)



    @commands.command(name="servers", aliases=['guilds'], help="Servers li dakhl lihom ana.")
    @commands.is_owner()
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
    @commands.is_owner()
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


    @commands.command(name="block", aliases=["blocki", "tjahl", "nkhl", "ignore"], help="Manb9ach njawb khouna.")
    @commands.is_owner()
    async def block(self, ctx, user: FuzzyMember):
        async with self.bot.db.execute("SELECT 1 FROM blacklists WHERE user_id = ?", (user.id,)) as cursor:
            if await cursor.fetchone():
                await ctx.send(f"`{user}` deja blockito hh")
                return

        async with self.bot.db.execute("INSERT INTO blacklists (user_id) VALUES (?)", (user.id,)):
            await self.bot.db.commit()
        if hasattr(self.bot, "blacklist_cache"):
            self.bot.blacklist_cache.add(user.id)
        await ctx.send(f"Safi blockit `{user}`.")


    @commands.command(name="unblock", aliases=["unblocki", "tsal7", "unignore"], help="Nrje3 njawb khouna.")
    @commands.is_owner()
    async def unblock(self, ctx, user: FuzzyMember):
        async with self.bot.db.execute("SELECT 1 FROM blacklists WHERE user_id = ?", (user.id,)) as cursor:
            if not await cursor.fetchone():
                await ctx.send(f"`{user}` mamblokihch aslan.")
                return

        async with self.bot.db.execute("DELETE FROM blacklists WHERE user_id = ?", (user.id,)):
            await self.bot.db.commit()
        if hasattr(self.bot, "blacklist_cache"):
            self.bot.blacklist_cache.discard(user.id)
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
            url="https://discord.com/oauth2/authorize?client_id=1522281059163701349&permissions=1100346747847&integration_type=0&scope=bot",
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
    @commands.is_owner()
    async def backup(self, ctx):
        if not await self.bot.is_owner(ctx.author):
            await ctx.send("Ma3endekch l7e9 tsta3ml had lcmd :/")
            return

        wait_msg = await ctx.send(embed=discord.Embed(description="Sber 3lia...", color=0x000000))
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
        if canonical_name in ("enable", "disable", "disabled", "globalenable", "globaldisable", "globaldisabled", "genable", "gdisable", "gdisabled", "help"):
            await ctx.send(f"❌ Mat9dch t disabli command `{canonical_name}` 7it daroria!")
            return

        async with self.bot.db.execute(
            "SELECT 1 FROM disabled_commands WHERE guild_id = ? AND command_name = ?",
            (ctx.guild.id, canonical_name)
        ) as cursor:
            exists = await cursor.fetchone()

        if exists:
            await ctx.send(f"⚠️ Command `{canonical_name}` deja mdisablia f had server.")
            return

        await self.bot.db.execute(
            "INSERT INTO disabled_commands (guild_id, command_name) VALUES (?, ?)",
            (ctx.guild.id, canonical_name)
        )
        await self.bot.db.commit()
        if hasattr(self.bot, "disabled_commands_cache"):
            self.bot.disabled_commands_cache.add((ctx.guild.id, canonical_name))

        embed = discord.Embed(
            description=f"🚫 Disablit command `{canonical_name}` f had server.\n7ta wa7d ma ghay9der ysta3melha daba.",
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
        if hasattr(self.bot, "disabled_commands_cache"):
            self.bot.disabled_commands_cache.discard((ctx.guild.id, canonical_name))

        embed = discord.Embed(
            description=f"🟢 Enablit command `{canonical_name}` f had server!",
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
        embed = paginator.get_page()
        if paginator.total_pages > 1:
            embed.set_footer(text=f"Page 1/{paginator.total_pages} • Server: {ctx.guild.name}")
            msg = await ctx.send(embed=embed, view=paginator)
            paginator.message = msg
        else:
            embed.set_footer(text=f"Server: {ctx.guild.name}")
            await ctx.send(embed=embed)

    @commands.command(name="globaldisable", aliases=["gdisable"], help="Desactivi command globally f ga3 servers.")
    @commands.is_owner()
    async def global_disable(self, ctx: commands.Context, *, command_name: str):
        clean_name = command_name.strip().lower()
        if clean_name.startswith(ctx.prefix.lower()):
            clean_name = clean_name[len(ctx.prefix):].strip()

        target_cmd = self.bot.get_command(clean_name)
        if not target_cmd:
            await ctx.send(f"❌ Mal9itch chi command smitha `{command_name}`.")
            return

        canonical_name = target_cmd.qualified_name.lower()
        if canonical_name in ("enable", "disable", "disabled", "globalenable", "globaldisable", "globaldisabled", "genable", "gdisable", "gdisabled", "help"):
            await ctx.send(f"❌ Mat9dch t disabli command `{canonical_name}` 7it daroria!")
            return

        async with self.bot.db.execute(
            "SELECT 1 FROM global_disabled_commands WHERE command_name = ?",
            (canonical_name,)
        ) as cursor:
            exists = await cursor.fetchone()

        if exists:
            await ctx.send(f"⚠️ Command `{canonical_name}` deja mdisablia globally.")
            return

        await self.bot.db.execute(
            "INSERT INTO global_disabled_commands (command_name) VALUES (?)",
            (canonical_name,)
        )
        await self.bot.db.commit()
        if hasattr(self.bot, "global_disabled_commands_cache"):
            self.bot.global_disabled_commands_cache.add(canonical_name)

        embed = discord.Embed(
            description=f"🌐 🚫 Disablit command `{canonical_name}` **globally** f ga3 servers!",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="globalenable", aliases=["genable"], help="Activi command li kant mdesactivia globally.")
    @commands.is_owner()
    async def global_enable(self, ctx: commands.Context, *, command_name: str):
        clean_name = command_name.strip().lower()
        if clean_name.startswith(ctx.prefix.lower()):
            clean_name = clean_name[len(ctx.prefix):].strip()

        target_cmd = self.bot.get_command(clean_name)
        canonical_name = target_cmd.qualified_name.lower() if target_cmd else clean_name

        async with self.bot.db.execute(
            "SELECT 1 FROM global_disabled_commands WHERE command_name = ?",
            (canonical_name,)
        ) as cursor:
            exists = await cursor.fetchone()

        if not exists:
            await ctx.send(f"⚠️ Command `{canonical_name}` mamdisabliach globally.")
            return

        await self.bot.db.execute(
            "DELETE FROM global_disabled_commands WHERE command_name = ?",
            (canonical_name,)
        )
        await self.bot.db.commit()
        if hasattr(self.bot, "global_disabled_commands_cache"):
            self.bot.global_disabled_commands_cache.discard(canonical_name)

        embed = discord.Embed(
            description=f"🌐 🟢 Enablit command `{canonical_name}` **globally**!",
            color=0x000000
        )
        await ctx.send(embed=embed)

    @commands.command(name="globaldisabled", aliases=["gdisabled"], help="Chouf ga3 commands li mdesactivyin globally.")
    @commands.is_owner()
    async def list_global_disabled(self, ctx: commands.Context):
        async with self.bot.db.execute(
            "SELECT command_name FROM global_disabled_commands ORDER BY command_name ASC"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            embed = discord.Embed(
                title="🌐 📋 Globally Disabled Commands",
                description="✨ Walo! Ga3 commands khdamin globally.",
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        cmds_list = [f"• `{r[0]}`" for r in rows]
        paginator = self.bot.Paginator(ctx, cmds_list, per_page=15, title=f"🌐 🚫 Globally Disabled Commands ({len(rows)})")
        embed = paginator.get_page()
        if paginator.total_pages > 1:
            embed.set_footer(text=f"Page 1/{paginator.total_pages} • Global")
            msg = await ctx.send(embed=embed, view=paginator)
            paginator.message = msg
        else:
            embed.set_footer(text="Global")
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Bot(bot))