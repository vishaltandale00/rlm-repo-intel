"""Ingest module — clone repos, fetch PRs and issues from GitHub."""

from .repo import clone_or_pull
from .github_fetch import fetch_prs, fetch_issues
from .graph_builder import build_structural_graph

from pathlib import Path
from rich.console import Console

console = Console()


def run_ingest(
    owner: str,
    name: str,
    branch: str,
    include_prs: bool,
    include_issues: bool,
    config: dict,
):
    """Full ingest pipeline: clone repo, fetch PRs/issues, build structural graph."""
    repo_dir = Path(config["paths"]["repo_dir"])
    data_dir = Path(config["paths"]["data_dir"])

    # Step 1: Clone or pull the repository
    console.print(f"\n[bold blue]Step 1:[/] Cloning/pulling {owner}/{name}...")
    clone_or_pull(owner, name, branch, repo_dir)

    # Step 2: Fetch PRs
    if include_prs:
        console.print(f"\n[bold blue]Step 2:[/] Fetching PRs...")
        pr_count = fetch_prs(owner, name, data_dir / "prs")
        console.print(f"  → {pr_count} PRs fetched")

    # Step 3: Fetch Issues
    if include_issues:
        console.print(f"\n[bold blue]Step 3:[/] Fetching issues...")
        issue_count = fetch_issues(owner, name, data_dir / "issues")
        console.print(f"  → {issue_count} issues fetched")

    # Step 4: Build structural graph (deterministic, no LLM)
    console.print(f"\n[bold blue]Step 4:[/] Building structural graph...")
    graph_stats = build_structural_graph(repo_dir, config)
    console.print(f"  → {graph_stats['files']} files, {graph_stats['modules']} modules")

    console.print(f"\n[bold green]✓ Ingest complete.[/]")
