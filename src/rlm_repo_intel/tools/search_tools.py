from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


def web_search(query: str, count: int = 5) -> list[dict[str, Any]]:
    """Search the web using Brave Search API.

    Returns list entries with title/url/snippet fields.
    """
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        # Return empty results gracefully when API key is not available.
        return []

    limit = max(1, min(int(count), 20))
    try:
        response = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": limit},
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    payload = response.json()
    results = payload.get("web", {}).get("results", [])

    out: list[dict[str, Any]] = []
    for item in results:
        out.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
        )
    return out


def git_log(file_path: str, repo_dir: str, n: int = 10) -> list[dict[str, Any]]:
    """Return recent git history for a file path within a repository."""
    repo_root = Path(repo_dir)
    if not repo_root.exists():
        return [{"error": f"Repository path does not exist: {repo_root}"}]

    limit = max(1, min(int(n), 200))
    cmd = [
        "git",
        "log",
        '--pretty=format:%H|%an|%ae|%ad|%s',
        "-n",
        str(limit),
        "--",
        file_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        return [{"error": "git executable not found"}]
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip() or "git log failed"
        return [{"error": stderr}]

    output = result.stdout.strip()
    if not output:
        return []

    entries: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        commit_hash, author, email, date_raw, message = parts
        entries.append(
            {
                "hash": commit_hash,
                "author": author,
                "email": email,
                "date": date_raw,
                "message": message,
            }
        )
    return entries


def git_blame(file_path: str, repo_dir: str) -> list[dict[str, Any]]:
    """Return git blame metadata for a file, capped to first 100 lines."""
    repo_root = Path(repo_dir)
    if not repo_root.exists():
        return [{"error": f"Repository path does not exist: {repo_root}"}]

    cmd = ["git", "blame", "--porcelain", file_path]
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        return [{"error": "git executable not found"}]
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip() or "git blame failed"
        return [{"error": stderr}]

    lines = result.stdout.splitlines()
    if not lines:
        return []

    entries: list[dict[str, Any]] = []
    idx = 0
    current_hash = ""
    current_author = ""
    current_time = ""

    while idx < len(lines) and len(entries) < 100:
        line = lines[idx]

        # Header line format: <hash> <orig_lineno> <final_lineno> <num_lines>
        if line and not line.startswith(("\t", "author ", "author-time ")):
            parts = line.split()
            if len(parts) >= 3 and len(parts[0]) >= 8:
                current_hash = parts[0]

        if line.startswith("author "):
            current_author = line.removeprefix("author ")
        elif line.startswith("author-time "):
            ts = line.removeprefix("author-time ").strip()
            try:
                current_time = datetime.fromtimestamp(int(ts)).isoformat()
            except (TypeError, ValueError, OSError):
                current_time = ts
        elif line.startswith("\t"):
            content = line[1:]
            entries.append(
                {
                    "line_number": len(entries) + 1,
                    "hash": current_hash,
                    "author": current_author,
                    "date": current_time,
                    "content": content,
                }
            )
        idx += 1

    return entries
