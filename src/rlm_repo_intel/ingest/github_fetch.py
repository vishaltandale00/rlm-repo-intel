"""Fetch PRs and issues from GitHub using the gh CLI."""

import json
import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

console = Console()
ENRICH_SLEEP_SECONDS = 0.1


def fetch_prs(
    owner: str,
    name: str,
    output_dir: Path,
    batch_size: int = 100,
    max_items: int | None = None,
) -> int:
    """Fetch PRs using gh api pagination. Returns count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    repo = f"{owner}/{name}"

    all_prs = []
    page = 1
    per_page = max(1, min(batch_size, 100))

    with Progress() as progress:
        task = progress.add_task("Fetching PRs...", total=None)

        while True:
            endpoint = f"repos/{repo}/pulls?state=all&per_page={per_page}&page={page}"
            result = subprocess.run(
                ["gh", "api", endpoint],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]Error fetching PRs: {result.stderr}[/]")
                break

            batch = json.loads(result.stdout)
            if not batch:
                break

            all_prs.extend(_normalize_pr(pr) for pr in batch)
            if max_items is not None and len(all_prs) >= max_items:
                all_prs = all_prs[:max_items]
                progress.update(task, completed=len(all_prs))
                break

            progress.update(task, completed=len(all_prs))

            if len(batch) < per_page:
                break
            page += 1

    open_pr_indices = [idx for idx, pr in enumerate(all_prs) if pr.get("state") == "open"]
    if open_pr_indices:
        with Progress() as progress:
            task = progress.add_task("Enriching open PRs...", total=len(open_pr_indices))
            for idx in open_pr_indices:
                all_prs[idx] = _enrich_pr(owner, name, all_prs[idx])
                progress.update(task, advance=1)
                if ENRICH_SLEEP_SECONDS > 0:
                    time.sleep(ENRICH_SLEEP_SECONDS)

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
    """Fetch issues using gh api pagination. Returns count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    repo = f"{owner}/{name}"

    all_issues = []
    page = 1
    per_page = max(1, min(batch_size, 100))

    with Progress() as progress:
        task = progress.add_task("Fetching issues...", total=None)

        while True:
            endpoint = f"repos/{repo}/issues?state=all&per_page={per_page}&page={page}"
            result = subprocess.run(
                ["gh", "api", endpoint],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]Error fetching issues: {result.stderr}[/]")
                break

            batch = json.loads(result.stdout)
            if not batch:
                break

            for issue in batch:
                if "pull_request" in issue:
                    continue
                all_issues.append(_normalize_issue(issue))
            progress.update(task, completed=len(all_issues))

            if len(batch) < per_page:
                break
            page += 1

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


def _enrich_pr(owner: str, name: str, pr: dict) -> dict:
    """Fetch detail metadata and diff for a single PR."""
    number = pr.get("number")
    if number is None:
        return pr

    repo = f"{owner}/{name}"

    detail_result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{number}"],
        capture_output=True,
        text=True,
    )
    if detail_result.returncode == 0:
        try:
            detail = json.loads(detail_result.stdout)
        except json.JSONDecodeError:
            detail = {}
        pr["additions"] = detail.get("additions", pr.get("additions", 0))
        pr["deletions"] = detail.get("deletions", pr.get("deletions", 0))
        pr["changedFiles"] = detail.get(
            "changed_files",
            detail.get("changedFiles", pr.get("changedFiles", 0)),
        )

    diff_result = subprocess.run(
        ["gh", "pr", "diff", str(number), "--repo", repo],
        capture_output=True,
        text=True,
    )
    pr["diff"] = diff_result.stdout if diff_result.returncode == 0 else ""

    return pr


def _normalize_pr(pr: dict) -> dict:
    """Map GitHub REST PR payload to expected ingest schema."""
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "body": pr.get("body"),
        "state": pr.get("state"),
        "author": pr.get("user"),
        "labels": pr.get("labels", []),
        "createdAt": pr.get("created_at"),
        "updatedAt": pr.get("updated_at"),
        "mergedAt": pr.get("merged_at"),
        "closedAt": pr.get("closed_at"),
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changedFiles": pr.get("changed_files", 0),
        "headRefName": (pr.get("head") or {}).get("ref"),
        "baseRefName": (pr.get("base") or {}).get("ref"),
        "reviewDecision": None,
        "url": pr.get("html_url"),
        "diff": "",
    }


def _normalize_issue(issue: dict) -> dict:
    """Map GitHub REST issue payload to expected ingest schema."""
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "body": issue.get("body"),
        "state": issue.get("state"),
        "author": issue.get("user"),
        "labels": issue.get("labels", []),
        "createdAt": issue.get("created_at"),
        "updatedAt": issue.get("updated_at"),
        "closedAt": issue.get("closed_at"),
        "comments": issue.get("comments", 0),
        "url": issue.get("html_url"),
    }
