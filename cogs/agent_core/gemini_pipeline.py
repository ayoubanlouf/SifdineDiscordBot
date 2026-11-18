from __future__ import annotations
import os
import re
import json
import asyncio
import aiohttp
from typing import Callable, Optional, Dict, Any, Tuple

from cogs.agent_core.security import validate_python_ast
from cogs.agent_core.sandbox import run_isolated_dry_run

FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]


async def query_gemini_api(
    session: aiohttp.ClientSession,
    prompt: str,
    system_instruction: Optional[str] = None,
    temperature: float = 0.2
) -> str:
    """
    Calls Google Gemini API asynchronously using aiohttp with automatic model fallback.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY (or GEMINI_KEY) is missing from environment.")

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    last_error: Optional[Exception] = None

    for model_name in FALLBACK_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
            }
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text_chunks = [p["text"] for p in parts if "text" in p and not p.get("thought", False)]
                        if not text_chunks:
                            text_chunks = [p["text"] for p in parts if "text" in p]
                        if text_chunks:
                            return "".join(text_chunks)
                    raise ValueError(f"Empty candidate response from {model_name}")
                elif resp.status in (404, 429, 503):
                    # Model unavailable, busy, or rate-limited; try next model
                    err_text = await resp.text()
                    last_error = RuntimeError(f"Model {model_name} returned HTTP {resp.status}: {err_text[:120]}")
                    continue
                else:
                    err_text = await resp.text()
                    raise RuntimeError(f"Gemini API HTTP {resp.status} on {model_name}: {err_text[:200]}")
        except asyncio.TimeoutError:
            last_error = TimeoutError(f"Model {model_name} timed out after 45s")
            continue
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def extract_code_block(text: str) -> str:
    """
    Extracts python code from markdown code fences if present.
    """
    pattern = r"```(?:python)?\s*([\s\S]*?)```"
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return text.strip()


async def run_agent_pipeline(
    session: aiohttp.ClientSession,
    user_prompt: str,
    status_callback: Optional[Callable[[int, int, str], Any]] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Runs the 4-stage agent pipeline:
    1. Planner Agent -> JSON Architectural Spec
    2. Code Generation Agent -> Python Cog Source
    3. Review & Security Agent -> AST Analysis & Self-Healing
    4. Test & Sandbox Agent -> Isolated Import Verification
    
    Returns: (command_name, python_code, plan_dict)
    """
    total_steps = 5

    # ==================== STAGE 1: PLANNER AGENT ====================
    if status_callback:
        await status_callback(1, total_steps, "🧠 Planning architecture, mechanics & Moroccan Darija tone...")

    planner_system = (
        "You are the Lead Software Architect for 'Sifdine', a Moroccan Arabic (Darija) Discord bot built on discord.py 2.x.\n"
        "Your task is to analyze the user request and generate a complete architectural specification in pure JSON format.\n"
        "Guidelines:\n"
        "- The command must be a standalone Cog.\n"
        "- The user experience must be interactive (e.g. discord.ui.View with Buttons or Modals).\n"
        "- Payouts must use 'TAD' currency through the bot's Economy cog if rewards apply.\n"
        "- Responses, error messages, and win/loss notifications should be in Moroccan Darija (Arabizi/Latin script).\n"
        "- All imports must strictly be safe Discord/Python utilities (no os, sys, subprocess, eval, exec, network scraping).\n"
        "Return ONLY a valid JSON object with keys:\n"
        "{\n"
        '  "command_name": "clean_lowercase_name",\n'
        '  "class_name": "PascalCaseCogName",\n'
        '  "description": "Short explanation",\n'
        '  "mechanics": "Detailed game rules and progression",\n'
        '  "ui_components": ["View with buttons", "Modal for text guess", etc],\n'
        '  "economy_rewards": "TAD payout rules (e.g. 100 TAD for win, 20 TAD participation)",\n'
        '  "darija_tone": "Examples of Darija phrases for start, guess, win, lose"\n'
        "}"
    )

    plan_raw = await query_gemini_api(session, user_prompt, system_instruction=planner_system, temperature=0.1)
    cleaned_plan = extract_code_block(plan_raw)
    try:
        plan = json.loads(cleaned_plan)
    except Exception:
        # Fallback parsing in case model adds prose around json
        json_match = re.search(r"\{[\s\S]*\}", cleaned_plan)
        if json_match:
            plan = json.loads(json_match.group(0))
        else:
            raise ValueError("Planner agent failed to return a valid JSON specification.")

    cmd_name = plan.get("command_name", "custom_cmd").strip().lower().replace(" ", "_")
    class_name = plan.get("class_name", "CustomCommandCog")

    # ==================== STAGE 2: CODE GENERATION AGENT ====================
    if status_callback:
        await status_callback(2, total_steps, f"✍️ Generating Python Cog code for '{cmd_name}'...")

    coder_system = (
        "You are an expert Discord Bot Engineer writing code for 'Sifdine' (discord.py 2.x).\n"
        "Write a complete, standalone, production-ready Python Cog module based on the architectural plan.\n"
        "MANDATORY REQUIREMENTS:\n"
        "1. Start with imports. ONLY use safe modules:\n"
        "   import random, asyncio, discord\n"
        "   from discord.ext import commands\n"
        "   from discord.ui import View, Button, Modal, TextInput\n"
        "   from typing import Optional, List\n"
        "   (Optionally: cogs.economy for TAD_EMOJI, or cogs.games.helpers for game helpers)\n"
        "2. Implement interactive games using discord.ui.View and/or discord.ui.Modal.\n"
        "3. In Views, implement 'async def interaction_check(self, interaction: discord.Interaction) -> bool:' to ensure only the original command invoker can click buttons.\n"
        "4. In View 'on_timeout', disable all buttons or edit the message so interactions don't dangle.\n"
        "5. For economy payouts, hook dynamically into the Economy cog:\n"
        "   economy_cog = self.bot.get_cog('Economy')\n"
        "   if economy_cog:\n"
        "       net, tax = await economy_cog.apply_tax_and_add_balance(user.id, gross_amount, context='...')\n"
        "6. In the Cog class, define the command with @commands.command(name='...').\n"
        "7. Include 'async def setup(bot): await bot.add_cog(...)'.\n"
        "8. Use Moroccan Darija for user-facing texts (win, lose, attempts, errors).\n"
        "9. NEVER import or use os, sys, subprocess, eval, exec, compile, open, socket, requests, urllib, or aiohttp.\n"
        "Return ONLY Python code enclosed in ```python ... ```."
    )

    coder_prompt = (
        f"Generate the full Python Cog source file for this architectural specification:\n"
        f"{json.dumps(plan, indent=2)}\n\n"
        f"Original User Request: {user_prompt}"
    )

    generated_code_raw = await query_gemini_api(session, coder_prompt, system_instruction=coder_system, temperature=0.2)
    code = extract_code_block(generated_code_raw)

    # ==================== STAGE 3: AST SECURITY & SELF-HEALING ====================
    if status_callback:
        await status_callback(3, total_steps, "🛡️ Running AST security review & policy checks...")

    max_healing_attempts = 2
    for attempt in range(max_healing_attempts + 1):
        is_safe, issues = validate_python_ast(code)
        if is_safe:
            break

        if attempt == max_healing_attempts:
            raise ValueError(f"Security validation failed after {max_healing_attempts} attempts: " + "; ".join(issues))

        if status_callback:
            await status_callback(3, total_steps, f"🛡️ Resolving security flags (attempt {attempt + 1})...")

        fix_prompt = (
            f"Your generated code triggered AST security policy violations:\n"
            + "\n".join(f"- {issue}" for issue in issues) +
            f"\n\nHere is the code:\n```python\n{code}\n```\n\n"
            "Please fix ALL security policy violations. Return ONLY the corrected Python code in ```python ... ```."
        )
        code_fixed_raw = await query_gemini_api(session, fix_prompt, system_instruction=coder_system, temperature=0.1)
        code = extract_code_block(code_fixed_raw)

    # ==================== STAGE 4: SANDBOX DRY-RUN & SELF-HEALING ====================
    if status_callback:
        await status_callback(4, total_steps, "🧪 Executing isolated dry-run and syntax verification...")

    for attempt in range(max_healing_attempts + 1):
        is_ok, err_msg = run_isolated_dry_run(code, cmd_name)
        if is_ok:
            break

        if attempt == max_healing_attempts:
            raise ValueError(f"Sandbox dry-run failed after {max_healing_attempts} attempts: {err_msg}")

        if status_callback:
            await status_callback(4, total_steps, f"🧪 Repairing syntax/runtime error in sandbox (attempt {attempt + 1})...")

        fix_prompt = (
            f"The isolated Python dry-run failed with the following error:\n{err_msg}\n\n"
            f"Here is the code:\n```python\n{code}\n```\n\n"
            "Fix the syntax or import/definition issue so it compiles and loads cleanly. Return ONLY the corrected code in ```python ... ```."
        )
        code_fixed_raw = await query_gemini_api(session, fix_prompt, system_instruction=coder_system, temperature=0.1)
        code = extract_code_block(code_fixed_raw)

    return cmd_name, code, plan
