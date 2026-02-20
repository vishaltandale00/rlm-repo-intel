from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RepoQueryTools:
    def __init__(self, config: dict):
        self.config = config
        self.data_dir = Path(config["paths"]["data_dir"])

    def list_prs(
        self, state: str = "all", limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        prs_path = self.data_dir / "prs" / "all_prs.jsonl"
        out: list[dict[str, Any]] = []
        if not prs_path.exists():
            return out
        with prs_path.open() as f:
            for line in f:
                pr = json.loads(line)
                if state != "all" and pr.get("state") != state:
                    continue
                out.append({
                    "number": pr.get("number"),
                    "title": pr.get("title"),
                    "state": pr.get("state"),
                    "changedFiles": pr.get("changedFiles", 0),
                    "additions": pr.get("additions", 0),
                    "deletions": pr.get("deletions", 0),
                    "url": pr.get("url"),
                })
        return out[offset : offset + max(1, min(limit, 2000))]

    def read_pr_diff(self, pr_number: int) -> dict[str, Any]:
        # Preferred source: pre-fetched diff field in JSONL.
        prs_path = self.data_dir / "prs" / "all_prs.jsonl"
        if not prs_path.exists():
            return {"pr_number": pr_number, "diff": "", "changed_files": []}

        for line in prs_path.read_text().splitlines():
            pr = json.loads(line)
            if int(pr.get("number", 0)) == int(pr_number):
                diff = pr.get("diff", "") or ""
                changed = []
                for dline in diff.splitlines():
                    if dline.startswith("diff --git "):
                        parts = dline.split()
                        if len(parts) >= 4:
                            b = parts[3]
                            if b.startswith("b/"):
                                b = b[2:]
                            changed.append(b)
                return {
                    "pr_number": pr_number,
                    "title": pr.get("title"),
                    "diff": diff,
                    "changed_files": changed,
                }

        return {"pr_number": pr_number, "diff": "", "changed_files": []}

    def list_issues(
        self, state: str = "all", limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        issues_path = self.data_dir / "issues" / "all_issues.jsonl"
        out: list[dict[str, Any]] = []
        if not issues_path.exists():
            return out
        with issues_path.open() as f:
            for line in f:
                issue = json.loads(line)
                if state != "all" and issue.get("state") != state:
                    continue
                out.append({
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "comments": issue.get("comments", 0),
                    "url": issue.get("url"),
                })
        return out[offset : offset + max(1, min(limit, 2000))]


def build_custom_tools(config: dict) -> dict[str, Any]:
    repo = RepoQueryTools(config)
    return {
        "list_prs": repo.list_prs,
        "read_pr_diff": repo.read_pr_diff,
        "list_issues": repo.list_issues,
    }
