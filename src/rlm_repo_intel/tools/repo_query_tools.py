from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rlm_repo_intel.graph.store import GraphStore


class RepoQueryTools:
    def __init__(self, config: dict):
        self.config = config
        self.data_dir = Path(config["paths"]["data_dir"])
        self.repo_dir = Path(config["paths"]["repo_dir"]) / config["repo"]["owner"] / config["repo"]["name"]
        self.graph = GraphStore(config["paths"]["graph_dir"])
        self.graph.load()

    def list_files(self, prefix: str = "", limit: int = 500) -> list[str]:
        files = []
        for p in self.repo_dir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(self.repo_dir))
                if rel.startswith(prefix):
                    files.append(rel)
        return sorted(files)[: max(1, min(limit, 5000))]

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        target = (self.repo_dir / path).resolve()
        if not str(target).startswith(str(self.repo_dir.resolve())):
            raise ValueError("path escapes repository root")
        text = target.read_text(errors="ignore")
        lines = text.splitlines()
        s = max(1, start_line)
        e = len(lines) if end_line is None else min(len(lines), end_line)
        snippet = "\n".join(lines[s - 1 : e])
        return {
            "path": path,
            "start_line": s,
            "end_line": e,
            "line_count": len(lines),
            "content": snippet,
        }

    def list_prs(self, state: str = "all", limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
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

    def list_issues(self, state: str = "all", limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
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

    def query_graph(self, query: dict[str, Any]) -> dict[str, Any]:
        qtype = query.get("type")
        if qtype == "stats":
            return self.graph.stats()
        if qtype == "module_files":
            module_id = str(query["module_id"])
            files = self.graph.files_in_module(module_id)
            return {
                "module_id": module_id,
                "files": [f.data.get("path") for f in files],
            }
        if qtype == "file_module":
            fp = str(query["file_path"])
            mod = self.graph.get_module_for_file(fp)
            return {"file_path": fp, "module": mod.id if mod else None}
        if qtype == "neighbors":
            node_id = str(query["node_id"])
            radius = int(query.get("radius", 1))
            data = self.graph.neighbors(node_id, radius=radius)
            return {
                "node_id": node_id,
                "radius": radius,
                "nodes": [{"id": n.id, "type": n.type, **n.data} for n in data.values()],
            }
        raise ValueError(f"unsupported query type: {qtype}")


def build_custom_tools(config: dict) -> dict[str, Any]:
    repo = RepoQueryTools(config)
    return {
        "list_files": repo.list_files,
        "read_file": repo.read_file,
        "list_prs": repo.list_prs,
        "read_pr_diff": repo.read_pr_diff,
        "list_issues": repo.list_issues,
        "query_graph": repo.query_graph,
    }
