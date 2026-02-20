"""Fetch PRs and issues from GitHub using the gh CLI."""

import json
import subprocess
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

console = Console()

# Fields to fetch for PRs
PR_FIELDS = [
    "number", "title", "body", "state", "author", "labels",
    "createdAt", "updatedAt", "mergedAt", "closedAt",
    "additions", "deletions", "changedFiles",
    "headRefName", "baseRefName",
    "reviewDecision", "url",
]

# Fields to fetch for issues
ISSUE_FIELDS = [
    "number", "title", "body", "state", "author", "labels",
    "createdAt", "updatedAt", "closedAt",
    "comments", "url",
]


def fetch_prs(owner: str, name: str, output_dir: Path, batch_size: int = 100) -> int:
    """Fetch all PRs using gh CLI. Returns count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    repo = f"{owner}/{name}"

    # Get total count first
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}", "--jq", ".open_issues_count"],
        capture_output=True, text=True,
    )

    all_prs = []
    cursor = ""

    with Progress() as progress:
        task = progress.add_task("Fetching PRs...", total=None)

        while True:
            cmd = [
                "gh", "pr", "list",
                "--repo", repo,
                "--state", "all",
                "--limit", str(batch_size),
                "--json", ",".join(PR_FIELDS),
            ]
            if cursor:
                cmd.extend(["--cursor", cursor])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                console.print(f"[red]Error fetching PRs: {result.stderr}[/]")
                break

            batch = json.loads(result.stdout)
            if not batch:
                break

            all_prs.extend(batch)
            progress.update(task, completed=len(all_prs))

            if len(batch) < batch_size:
                break

    # Save as JSONL for streaming access
    jsonl_path = output_dir / "all_prs.jsonl"
    with open(jsonl_path, "w") as f:
        for pr in all_prs:
            f.write(json.dumps(pr) + "\n")

    # Also save index for quick lookup
    index = {pr["number"]: i for i, pr in enumerate(all_prs)}
    with open(output_dir / "index.json", "w") as f:
        json.dump(index, f)

    return len(all_prs)


def fetch_issues(owner: str, name: str, output_dir: Path, batch_size: int = 100) -> int:
    """Fetch all issues using gh CLI. Returns count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    repo = f"{owner}/{name}"

    all_issues = []

    with Progress() as progress:
        task = progress.add_task("Fetching issues...", total=None)

        while True:
            cmd = [
                "gh", "issue", "list",
                "--repo", repo,
                "--state", "all",
                "--limit", str(batch_size),
                "--json", ",".join(ISSUE_FIELDS),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                console.print(f"[red]Error fetching issues: {result.stderr}[/]")
                break

            batch = json.loads(result.stdout)
            if not batch:
                break

            all_issues.extend(batch)
            progress.update(task, completed=len(all_issues))

            if len(batch) < batch_size:
                break

    # Save as JSONL
    jsonl_path = output_dir / "all_issues.jsonl"
    with open(jsonl_path, "w") as f:
        for issue in all_issues:
            f.write(json.dumps(issue) + "\n")

    # Index
    index = {issue["number"]: i for i, issue in enumerate(all_issues)}
    with open(output_dir / "index.json", "w") as f:
        json.dump(index, f)

    return len(all_issues)


def fetch_pr_diff(owner: str, name: str, pr_number: int) -> str:
    """Fetch the diff for a specific PR."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{name}"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""
