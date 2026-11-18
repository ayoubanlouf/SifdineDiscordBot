from __future__ import annotations
import os
import base64
import aiohttp
from typing import Tuple, Optional


def get_github_config() -> Tuple[Optional[str], Optional[str]]:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO", "ayoubanlouf/SifdineDiscordBot").strip()
    return token, repo


async def commit_file_to_github(
    session: aiohttp.ClientSession,
    repo_file_path: str,
    content: str,
    commit_message: str
) -> Tuple[bool, str]:
    """
    Commits a file directly to the GitHub repository on the main branch via REST API.
    """
    token, repo = get_github_config()
    if not token or not repo:
        return False, "GITHUB_TOKEN or GITHUB_REPO not configured in environment."

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Sifdine-Agent-Bot",
    }
    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_file_path}"

    sha: Optional[str] = None
    try:
        # 1. Check if file already exists to get its SHA
        async with session.get(api_url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                sha = data.get("sha")
            elif resp.status not in (404,):
                err = await resp.text()
                return False, f"Failed to check file status on GitHub: HTTP {resp.status} - {err[:150]}"

        # 2. Put / Commit the file
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha

        async with session.put(api_url, headers=headers, json=payload) as resp:
            if resp.status in (200, 201):
                return True, "Committed to GitHub successfully."
            else:
                err = await resp.text()
                return False, f"GitHub commit failed: HTTP {resp.status} - {err[:200]}"

    except Exception as e:
        return False, f"GitHub request exception: {str(e)}"


async def delete_file_from_github(
    session: aiohttp.ClientSession,
    repo_file_path: str,
    commit_message: str
) -> Tuple[bool, str]:
    """
    Deletes a file from the GitHub repository via REST API.
    """
    token, repo = get_github_config()
    if not token or not repo:
        return False, "GITHUB_TOKEN or GITHUB_REPO not configured in environment."

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Sifdine-Agent-Bot",
    }
    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_file_path}"

    try:
        # 1. Get file SHA
        async with session.get(api_url, headers=headers) as resp:
            if resp.status == 404:
                return True, "File does not exist on GitHub."
            elif resp.status != 200:
                err = await resp.text()
                return False, f"Failed to fetch file from GitHub: HTTP {resp.status} - {err[:150]}"
            data = await resp.json()
            sha = data.get("sha")

        # 2. Delete file
        payload = {
            "message": commit_message,
            "sha": sha,
            "branch": "main",
        }
        async with session.delete(api_url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                return True, "Deleted from GitHub successfully."
            else:
                err = await resp.text()
                return False, f"GitHub delete failed: HTTP {resp.status} - {err[:200]}"

    except Exception as e:
        return False, f"GitHub delete exception: {str(e)}"
