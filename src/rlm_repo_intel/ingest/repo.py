"""Clone or update a GitHub repository."""

import subprocess
from pathlib import Path

from rich.console import Console

console = Console()


def clone_or_pull(owner: str, name: str, branch: str, repo_dir: Path):
    """Clone the repo if not present, otherwise pull latest."""
    repo_url = f"https://github.com/{owner}/{name}.git"

    if (repo_dir / ".git").exists():
        console.print(f"  Pulling latest from {branch}...")
        subprocess.run(
            ["git", "pull", "origin", branch],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
    else:
        console.print(f"  Cloning {repo_url}...")
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
        )

    # Fetch all PR refs for later diff access
    console.print("  Fetching PR refs...")
    subprocess.run(
        ["git", "fetch", "origin", "+refs/pull/*/head:refs/remotes/origin/pr/*"],
        cwd=repo_dir,
        capture_output=True,
    )
