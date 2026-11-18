from __future__ import annotations
import os
import glob
import time
import asyncio
import aiohttp
import discord
from discord.ext import commands
from typing import Optional

from cogs.agent_core.gemini_pipeline import run_agent_pipeline
from cogs.agent_core.github_sync import commit_file_to_github, delete_file_from_github


AGENT_GEN_DIR = os.path.join("cogs", "agent_generated")


class AgentCog(commands.Cog, name="Agent"):
    """
    Autonomous AI Command Generator using Google Gemini and GitHub Sync.
    Owner-only pipeline that designs, writes, audits, and hot-loads Discord commands safely.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        os.makedirs(AGENT_GEN_DIR, exist_ok=True)

    @commands.group(name="agent", invoke_without_command=True)
    @commands.is_owner()
    async def agent_group(self, ctx: commands.Context, *, prompt: Optional[str] = None):
        """
        AI Agentic Command Pipeline.
        Usage: sat agent <prompt describing the command to create>
        """
        if not prompt or not prompt.strip():
            prefix = ctx.clean_prefix
            embed = discord.Embed(
                title="🤖 Sifdine Autonomous Agent Pipeline",
                description=(
                    f"**Commands:**\n"
                    f"• `{prefix}agent <prompt>` — Create and hot-load a new command.\n"
                    f"• `{prefix}agent list` — List all agent-generated commands.\n"
                    f"• `{prefix}agent undo [name]` — Rollback the latest (or named) command.\n"
                    f"• `{prefix}agent reload <name>` — Hot-reload a generated cog.\n"
                    f"• `{prefix}agent delete <name>` — Permanently delete a generated command.\n\n"
                    f"_All generated commands are AST-audited for safety and committed to GitHub._"
                ),
                color=0x000000
            )
            await ctx.send(embed=embed)
            return

        # Check API keys
        if not os.getenv("GEMINI_API_KEY"):
            await ctx.send("❌ `GEMINI_API_KEY` is missing from `.env`.")
            return

        status_embed = discord.Embed(
            title="🤖 Autonomous Command Pipeline",
            description="⚙️ Initializing multi-agent pipeline...",
            color=0x000000
        )
        status_embed.set_footer(text="Sifdine Agentic Pipeline • Owner Authorized")
        status_msg = await ctx.send(embed=status_embed)

        async def update_status(step: int, total: int, text: str):
            status_embed.description = f"**[{step}/{total}]** {text}"
            try:
                await status_msg.edit(embed=status_embed)
            except Exception:
                pass

        session = getattr(self.bot, "session", None)
        created_session = False
        if not session or session.closed:
            session = aiohttp.ClientSession()
            created_session = True

        try:
            # 1. Run multi-agent pipeline (Planner -> Coder -> AST Review -> Sandbox)
            cmd_name, code, plan = await run_agent_pipeline(
                session=session,
                user_prompt=prompt,
                status_callback=update_status
            )

            # 2. Stage 5: Save locally & Hot-Load
            await update_status(5, 5, f"📦 Saving `cogs/agent_generated/{cmd_name}.py` and hot-loading into Discord...")
            file_path = os.path.join(AGENT_GEN_DIR, f"{cmd_name}.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)

            ext_name = f"cogs.agent_generated.{cmd_name}"
            # Load or reload extension
            if ext_name in self.bot.extensions:
                await self.bot.reload_extension(ext_name)
            else:
                await self.bot.load_extension(ext_name)

            # 3. GitHub Persistence Sync
            github_synced = False
            github_note = "Skipped (no token)"
            if os.getenv("GITHUB_TOKEN"):
                repo_rel_path = f"cogs/agent_generated/{cmd_name}.py"
                commit_msg = f"🤖 Agent: Added command '{cmd_name}'"
                ok, gh_msg = await commit_file_to_github(session, repo_rel_path, code, commit_msg)
                github_synced = ok
                github_note = "🟢 Synced to GitHub origin/main" if ok else f"⚠️ GitHub sync failed: {gh_msg}"

            # 4. Success Completion Embed
            prefix = ctx.clean_prefix
            done_embed = discord.Embed(
                title=f"✅ Command `{prefix}{cmd_name}` Created & Loaded!",
                description=plan.get("description", "Agent-generated command."),
                color=0x00FF00
            )
            done_embed.add_field(name="🎯 Mechanics", value=plan.get("mechanics", "N/A")[:1024], inline=False)
            if plan.get("economy_rewards"):
                done_embed.add_field(name="💰 Economy Rewards", value=str(plan.get("economy_rewards"))[:1024], inline=False)
            done_embed.add_field(name="📦 GitHub Persistence", value=github_note, inline=False)
            done_embed.add_field(
                name="🛡️ Safe Management",
                value=f"• Try it: `{prefix}{cmd_name}`\n• Undo: `{prefix}agent undo {cmd_name}`\n• File: `{file_path}`",
                inline=False
            )
            await status_msg.edit(embed=done_embed)

        except Exception as e:
            # Self-healing failure / error boundary
            error_embed = discord.Embed(
                title="❌ Agent Pipeline Failed",
                description=f"An error occurred during generation or verification:\n```py\n{str(e)[:1800]}\n```",
                color=0xFF0000
            )
            await status_msg.edit(embed=error_embed)

        finally:
            if created_session:
                await session.close()

    @agent_group.command(name="list", aliases=["all"])
    @commands.is_owner()
    async def agent_list(self, ctx: commands.Context):
        """List all agent-generated commands."""
        files = glob.glob(os.path.join(AGENT_GEN_DIR, "*.py"))
        active_files = [f for f in files if not os.path.basename(f).startswith("__")]

        if not active_files:
            await ctx.send("ℹ️ Makayn 7eta command generated b l'agent 7etta l daba.")
            return

        lines = []
        for f in sorted(active_files):
            name = os.path.splitext(os.path.basename(f))[0]
            ext = f"cogs.agent_generated.{name}"
            is_loaded = ext in self.bot.extensions
            status_emoji = "🟢 Loaded" if is_loaded else "⚪ Unloaded"
            size_kb = round(os.path.getsize(f) / 1024, 1)
            lines.append(f"• `{name}` — {status_emoji} (`{size_kb} KB`)")

        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="🤖 Agent-Generated Commands",
            description="\n".join(lines),
            color=0x000000
        )
        embed.set_footer(text=f"Use '{prefix}agent undo <name>' to remove any command.")
        await ctx.send(embed=embed)

    @agent_group.command(name="undo", aliases=["revert"])
    @commands.is_owner()
    async def agent_undo(self, ctx: commands.Context, command_name: Optional[str] = None):
        """Rollback and delete an agent-generated command."""
        files = glob.glob(os.path.join(AGENT_GEN_DIR, "*.py"))
        active_files = [f for f in files if not os.path.basename(f).startswith("__")]

        if not active_files:
            await ctx.send("❌ Makayn 7etta command agent bach ndiro liha undo.")
            return

        target_file: Optional[str] = None
        target_name: Optional[str] = None

        if command_name:
            clean = command_name.strip().lower()
            if not clean.endswith(".py"):
                clean = f"{clean}.py"
            expected_path = os.path.join(AGENT_GEN_DIR, clean)
            if os.path.exists(expected_path):
                target_file = expected_path
                target_name = os.path.splitext(clean)[0]
            else:
                await ctx.send(f"❌ Mal9itch command b smia `{command_name}`.")
                return
        else:
            # Pick most recently modified file
            active_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            target_file = active_files[0]
            target_name = os.path.splitext(os.path.basename(target_file))[0]

        # 1. Unload extension from bot
        ext_name = f"cogs.agent_generated.{target_name}"
        if ext_name in self.bot.extensions:
            try:
                await self.bot.unload_extension(ext_name)
            except Exception as e:
                print(f"[Agent Undo] Failed to unload {ext_name}: {e}")

        # 2. Delete file locally
        try:
            if os.path.exists(target_file):
                os.remove(target_file)
        except Exception as e:
            await ctx.send(f"⚠️ Mochkil f msa7 local file: `{e}`")

        # 3. Delete from GitHub
        session = getattr(self.bot, "session", None)
        created_session = False
        if not session or session.closed:
            session = aiohttp.ClientSession()
            created_session = True

        gh_note = ""
        try:
            if os.getenv("GITHUB_TOKEN"):
                repo_rel_path = f"cogs/agent_generated/{target_name}.py"
                commit_msg = f"🤖 Agent: Reverted command '{target_name}'"
                ok, gh_msg = await delete_file_from_github(session, repo_rel_path, commit_msg)
                gh_note = "\n• **GitHub:** Deleted from repo." if ok else f"\n• **GitHub Warning:** {gh_msg}"
        finally:
            if created_session:
                await session.close()

        await ctx.send(f"🗑️ **Undo Successful:** Command `{target_name}` was unloaded, deleted locally, and removed from GitHub.{gh_note}")

    @agent_group.command(name="reload")
    @commands.is_owner()
    async def agent_reload(self, ctx: commands.Context, command_name: str):
        """Hot-reload an existing agent-generated cog."""
        clean = command_name.strip().lower()
        ext_name = f"cogs.agent_generated.{clean}"
        file_path = os.path.join(AGENT_GEN_DIR, f"{clean}.py")

        if not os.path.exists(file_path):
            await ctx.send(f"❌ Mal9itch file dial `{clean}` f `cogs/agent_generated/`.")
            return

        try:
            if ext_name in self.bot.extensions:
                await self.bot.reload_extension(ext_name)
            else:
                await self.bot.load_extension(ext_name)
            await ctx.send(f"🔄 **Reloaded `{clean}` successfully!**")
        except Exception as e:
            await ctx.send(f"❌ Failed to reload `{clean}`:\n```py\n{e}\n```")

    @agent_group.command(name="delete")
    @commands.is_owner()
    async def agent_delete(self, ctx: commands.Context, command_name: str):
        """Permanently delete an agent-generated command."""
        await self.agent_undo(ctx, command_name=command_name)


async def setup(bot: commands.Bot):
    await bot.add_cog(AgentCog(bot))
