from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


def load_repo_to_repl(config: dict[str, Any]) -> dict[str, str]:
    """Load repository files into an in-memory mapping for REPL analysis."""
    repo_root = (
        Path(config["paths"]["repo_dir"]) / config["repo"]["owner"] / config["repo"]["name"]
    )
    if not repo_root.exists():
        return {}

    repo: dict[str, str] = {}
    for root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            file_path = Path(root) / filename
            try:
                if _is_binary_file(file_path):
                    continue
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError, OSError):
                continue
            rel_path = file_path.relative_to(repo_root).as_posix()
            repo[rel_path] = content

    return repo


def build_repo_tree(repo: dict[str, str]) -> str:
    """Build a simple tree-style view from flat repo paths."""
    tree: dict[str, dict[str, Any]] = {}

    for path in sorted(repo):
        cursor = tree
        parts = path.split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = {}

    lines: list[str] = []

    def walk(node: dict[str, dict[str, Any]], depth: int = 0) -> None:
        indent = "  " * depth
        for name in sorted(node):
            child = node[name]
            is_dir = bool(child)
            lines.append(f"{indent}{name}/" if is_dir else f"{indent}{name}")
            if is_dir:
                walk(child, depth + 1)

    walk(tree)
    return "\n".join(lines)


def load_prs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load all PRs from JSONL into memory."""
    import json
    prs_path = Path(config["paths"]["data_dir"]) / "prs" / "all_prs.jsonl"
    if not prs_path.exists():
        return []
    prs = []
    for line in prs_path.read_text().splitlines():
        if line.strip():
            prs.append(json.loads(line))
    return prs


def load_issues(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load all issues from JSONL into memory."""
    import json
    issues_path = Path(config["paths"]["data_dir"]) / "issues" / "all_issues.jsonl"
    if not issues_path.exists():
        return []
    issues = []
    for line in issues_path.read_text().splitlines():
        if line.strip():
            issues.append(json.loads(line))
    return issues


def build_pr_table(prs: list[dict[str, Any]]) -> str:
    """Build a compact text table of PR metadata for the prompt."""
    lines = ["# Open PRs Summary", f"Total: {len([p for p in prs if p.get('state') == 'open'])} open / {len(prs)} total", ""]
    lines.append(f"{'PR':>6} | {'State':<8} | {'Files':>5} | {'+':>6} | {'-':>6} | {'Author':<20} | Title")
    lines.append("-" * 100)
    for pr in sorted(prs, key=lambda p: p.get("number", 0), reverse=True):
        lines.append(
            f"{pr.get('number', '?'):>6} | {pr.get('state', '?'):<8} | "
            f"{pr.get('changedFiles', 0):>5} | {pr.get('additions', 0):>6} | {pr.get('deletions', 0):>6} | "
            f"{(pr.get('author', {}) or {}).get('login', '?'):<20} | {pr.get('title', '?')[:80]}"
        )
    return "\n".join(lines)


def build_issue_table(issues: list[dict[str, Any]]) -> str:
    """Build a compact text table of issue metadata for the prompt."""
    open_issues = [i for i in issues if i.get("state") == "open"]
    lines = ["# Open Issues Summary", f"Total: {len(open_issues)} open / {len(issues)} total", ""]
    lines.append(f"{'#':>6} | {'State':<8} | {'Comments':>8} | {'Author':<20} | Title")
    lines.append("-" * 100)
    for issue in sorted(open_issues, key=lambda i: i.get("number", 0), reverse=True):
        lines.append(
            f"{issue.get('number', '?'):>6} | {issue.get('state', '?'):<8} | "
            f"{issue.get('comments', 0):>8} | "
            f"{(issue.get('author', {}) or {}).get('login', '?'):<20} | {issue.get('title', '?')[:80]}"
        )
    return "\n".join(lines)


def _is_binary_file(path: Path, sniff_size: int = 8192) -> bool:
    with path.open("rb") as f:
        chunk = f.read(sniff_size)
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
