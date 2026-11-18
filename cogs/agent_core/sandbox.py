from __future__ import annotations
import os
import sys
import tempfile
import subprocess
from typing import Tuple, Optional

# Keys to strip from sandbox process environment
SENSITIVE_ENV_KEYS = {
    "DISCORD_TOKEN",
    "TURSO_AUTH_TOKEN",
    "TURSO_DATABASE_URL",
    "GEMINI_API_KEY",
    "GITHUB_TOKEN",
    "BOT_HOSTING_API_KEY",
    "DISCLOUD_API_TOKEN",
    "REDDIT_CLIENT_SECRET",
    "STEAM_API_KEY",
    "RAPID_API_KEY_1",
    "RAPID_API_KEY_2",
}


def run_isolated_dry_run(code: str, module_name: str) -> Tuple[bool, Optional[str]]:
    """
    Validates code in an isolated subprocess with stripped environment.
    Ensures the code compiles cleanly, has valid syntax, and does not crash on import.
    """
    # 1. In-process compilation check
    try:
        compile(code, f"<agent_generated_{module_name}>", "exec")
    except SyntaxError as e:
        return False, f"SyntaxError on line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Compilation error: {e}"

    # 2. Subprocess dry-run in a clean temporary environment
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_file = os.path.join(tmp_dir, f"{module_name}.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(code)

        # Prepare clean environment
        clean_env = {
            k: v for k, v in os.environ.items()
            if k not in SENSITIVE_ENV_KEYS
        }
        # Add current working directory to PYTHONPATH so cogs.economy / helpers can resolve
        cwd = os.getcwd()
        existing_pythonpath = clean_env.get("PYTHONPATH", "")
        clean_env["PYTHONPATH"] = f"{cwd};{existing_pythonpath}" if existing_pythonpath else cwd

        # Test script to verify module imports and setup() function exists
        test_script = f"""
import sys, importlib.util
spec = importlib.util.spec_from_file_location('{module_name}', r'{test_file}')
if not spec or not spec.loader:
    print('Failed to create spec')
    sys.exit(1)
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f'ExecutionError: {{type(e).__name__}}: {{e}}')
    sys.exit(2)

if not hasattr(mod, 'setup'):
    print('Missing setup function')
    sys.exit(3)

print('DRY_RUN_SUCCESS')
sys.exit(0)
"""

        try:
            result = subprocess.run(
                [sys.executable, "-c", test_script],
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=8,
                cwd=cwd
            )

            if result.returncode == 0 and "DRY_RUN_SUCCESS" in result.stdout:
                return True, None
            else:
                err_output = (result.stderr.strip() or result.stdout.strip() or f"Exited with code {result.returncode}")
                return False, f"Dry-run verification failed: {err_output}"

        except subprocess.TimeoutExpired:
            return False, "Dry-run timed out (>8s). Potential infinite loop or blocking call at module level."
        except Exception as e:
            return False, f"Dry-run execution error: {e}"
