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
            if _is_binary_file(file_path):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
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
