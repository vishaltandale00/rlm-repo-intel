# True RLM Architecture Plan

This plan upgrades the current pipeline into a true REPL-first RLM flow where the model explores the repository by calling injected Python `custom_tools`, then uses `llm_query()` for deeper targeted reasoning.

## 1) `custom_tools` injected into the RLM REPL

These are Python-callable functions injected into `RLM(..., custom_tools=...)` so model-generated REPL code can query repo/PR/issue/graph data directly.

```python
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
```

RLM construction shape:

```python
from rlm import RLM

root_rlm = RLM(
    backend="anthropic",
    backend_kwargs={"model_name": "claude-sonnet-4-20250514", "api_key": "..."},
    custom_tools=build_custom_tools(config),
    custom_sub_tools=build_custom_tools(config),
    other_backends=[{
        "backend": "anthropic",
        "backend_kwargs": {"model_name": "claude-haiku", "api_key": "..."},
    }],
    persistent=True,
    compaction=True,
    max_depth=4,
    max_iterations=24,
    verbose=True,
)
```

## 2) Root prompt that starts the RLM session

```python
ROOT_PROMPT = """
You are the root repository intelligence model.

You are in a Python REPL with these callable tools:
- list_files(prefix='', limit=500)
- read_file(path, start_line=1, end_line=None)
- list_prs(state='all', limit=200, offset=0)
- read_pr_diff(pr_number)
- list_issues(state='all', limit=200, offset=0)
- query_graph(query_dict)
- llm_query(prompt, system_prompt=None, backend=None)

Operating protocol:
1. Always gather evidence by calling tools before making claims.
2. Keep data pulls scoped (line ranges, prefixes, bounded limits).
3. Use llm_query() only after selecting a focused sub-problem with explicit context.
4. Emit machine-readable outputs (dict/list/json-safe objects) in final cells.
5. If evidence is insufficient, return explicit unknowns and next queries.

Primary goal:
Build high-confidence repository understanding and PR evaluations grounded in code, graph structure, PR diffs, and linked issues.
""".strip()
```

## 3) Example of what the model would generate in the REPL

```python
# 1) Scope modules with highest churn/size
stats = query_graph({"type": "stats"})
stats

# 2) Pull candidate PRs for analysis
prs = list_prs(state="open", limit=50)
large_prs = [p for p in prs if (p.get("changedFiles") or 0) >= 8]
large_prs[:5]

# 3) Analyze one PR with concrete evidence
pr_num = large_prs[0]["number"]
pr_data = read_pr_diff(pr_num)
changed = pr_data["changed_files"][:20]

file_summaries = []
for path in changed[:8]:
    snippet = read_file(path, start_line=1, end_line=220)
    file_summaries.append({
        "path": path,
        "line_count": snippet["line_count"],
        "head": snippet["content"][:1200],
    })

sub_prompt = {
    "pr": {"number": pr_num, "changed_files": changed},
    "files": file_summaries,
}

analysis = llm_query(
    prompt=f"Evaluate technical risk and test gaps for PR #{pr_num}: {sub_prompt}",
    system_prompt="You are a strict code reviewer. Return JSON with risk_score, failure_modes, required_tests.",
)
analysis

# 4) Produce structured result
result = {
    "pr_number": pr_num,
    "changed_files": changed,
    "analysis": analysis,
}
result
```

## 4) Multi-agent debate design (`llm_query` system prompts)

Use three sub-agent calls, then one root synthesis call.

```python
PROPOSER_SYS = """
You are Proposer. Build the strongest evidence-backed claim from code and diff context.
Return JSON: claims[], confidence, risks[], opportunities[]
""".strip()

CHALLENGER_SYS = """
You are Challenger. Attack weak assumptions, missing evidence, and hidden regressions.
Return JSON: rebuttals[], missing_evidence[], revised_risk
""".strip()

ARBITER_SYS = """
You are Arbiter. Resolve proposer/challenger conflict using only provided evidence.
Return JSON: accepted_claims[], rejected_claims[], final_scores, rationale
""".strip()

PR_SYNTH_SYS = """
You are Final Synthesizer. Convert debate output into production decision fields.
Return JSON: risk_score, quality_score, strategic_value, novelty_score, test_alignment, review_summary, confidence
""".strip()
```

Debate flow in REPL:

```python
proposal = llm_query(prompt=context_blob, system_prompt=PROPOSER_SYS, backend="cheap_worker")
challenge = llm_query(prompt={"context": context_blob, "proposal": proposal}, system_prompt=CHALLENGER_SYS, backend="cheap_worker")
arbitration = llm_query(prompt={"context": context_blob, "proposal": proposal, "challenge": challenge}, system_prompt=ARBITER_SYS, backend="root")
final = llm_query(prompt={"context": context_blob, "arbitration": arbitration}, system_prompt=PR_SYNTH_SYS, backend="root")
```

## 5) How results flow to Neon

Current project already has Neon writer helpers in `src/rlm_repo_intel/dashboard_push.py`.

Flow:
1. REPL run emits per-PR structured JSON objects.
2. Evaluator writes local artifacts (`pr_evaluations.jsonl`, `pr_reasoning_traces.jsonl`, `pr_relations.jsonl`, `final_ranking.json`).
3. Push step maps artifacts to Neon keys:
- `push_summary({...})` -> `rlm:summary`
- `push_evaluation(pr_eval)` -> `rlm:evaluations`
- `push_clusters(clusters)` -> `rlm:clusters`
- `push_ranking(ranking)` -> `rlm:ranking`
4. Dashboard reads from `rlm_kv` in Neon.

Recommended write hook:

```python
from rlm_repo_intel.dashboard_push import (
    push_summary,
    push_evaluation,
    push_clusters,
    push_ranking,
)

for ev in evaluations:
    push_evaluation(ev)

push_clusters(clusters)
push_ranking(ranking)
push_summary({
    "total_prs_evaluated": len(evaluations),
    "clusters": len(clusters),
    "top_prs": ranking.get("ranking", [])[:20],
})
```

## 6) Token cost estimate

Given context numbers (4,674 files, 5,000 PRs, 4,620 issues), full raw-context prompting is not viable; tool-query REPL is the cost control.

Assumptions:
- Module analysis: ~172 modules.
- Avg module evidence pull per module: 8k-12k tokens.
- PR evaluation: 5,000 PRs, but bounded evidence pull per PR (diff metadata + selected snippets) ~2k-5k tokens.
- Debate calls per PR: 3 cheap + 1 root synthesis (root on compact context).

Rough run envelope:
- Phase A module modeling: `172 * 10k = 1.72M` tokens.
- Phase B PR pass (all 5k): `5,000 * 3k = 15M` tokens to cheap workers.
- Root synthesis on each PR compact pack (~700 tokens avg): `~3.5M` tokens.
- Cross-PR pair adjudication (candidate-capped to 15k): `15,000 * ~1.5k = 22.5M` tokens cheap+root mix.

Total ballpark: `~43M` tokens end-to-end with strict tool-scoping.

Cost control levers:
- Lower `pair_candidates_max` from 15,000 when budget constrained.
- Run two-pass PR eval: cheap filter on all PRs, full debate only on top K.
- Enforce `read_file(..., end_line=...)` usage in root prompt protocol.

## 7) Concrete implementation plan (files to change)

1. `src/rlm_repo_intel/modeling/recursive_repo_model.py`
- Replace direct monolithic prompting with `completion(prompt=full_context, root_prompt=ROOT_PROMPT)`.
- Inject `custom_tools` and use true REPL exploration for module analysis.
- Add explicit `llm_query()`-based sub-analysis paths.

2. `src/rlm_repo_intel/evaluation/pr_eval.py`
- Build `RepoQueryTools` once and pass as `custom_tools`.
- Replace current string-only agent calls with REPL tool-driven steps:
  - read PR diff via `read_pr_diff`
  - fetch code slices via `read_file`
  - query module structure via `query_graph`
  - run debate through `llm_query` with role system prompts
- Persist full debate transcript JSON into `pr_reasoning_traces.jsonl`.

3. `src/rlm_repo_intel/synthesis/cross_pr.py`
- Rework pair adjudication to use same debate prompts via `llm_query`.
- Use `query_graph` evidence for conflict/redundancy decisions.
- Keep candidate cap and add hard token guards in pair context construction.

4. `src/rlm_repo_intel/rlm_factory.py`
- Extend `create_rlm(...)` to accept and forward:
  - `custom_tools`
  - `custom_sub_tools`
  - `other_backends`
  - `persistent`, `compaction`, `max_depth`, `max_iterations`
- Add lightweight defaults for recursive usage.

5. `src/rlm_repo_intel/config.py`
- Add configuration blocks:
  - `rlm`: recursion/runtime knobs (`persistent`, `compaction`, `max_depth`, `max_iterations`)
  - `tool_limits`: max files/list limits, max read lines, max PR slice size
  - `debate`: enabled agents, model routing, per-stage token caps

6. `src/rlm_repo_intel/dashboard_push.py`
- No schema change required; wire this module into evaluation/synthesis completion paths.
- Add batched push helper for large runs (reduce connection churn).

7. `src/rlm_repo_intel/export/exporter.py`
- Include debate artifacts and tool-evidence excerpts in export payload.
- Preserve existing JSONL -> JSON conversion behavior.

8. New file: `src/rlm_repo_intel/tools/repo_query_tools.py`
- Home for `RepoQueryTools` and `build_custom_tools(config)`.
- Shared by modeling/evaluation/synthesis.

9. New file: `src/rlm_repo_intel/prompts/root_prompts.py`
- Root prompt constants and debate system prompts.

10. New file: `src/rlm_repo_intel/pipeline/rlm_session.py`
- Session bootstrap utility to create root/worker RLMs with tool injection and backend routing.

11. `src/rlm_repo_intel/cli.py`
- Add optional flags for true-RLM mode toggles (`--true-rlm/--legacy`, token guard overrides).

Execution order:
1. Introduce `repo_query_tools.py` + prompt constants.
2. Upgrade `rlm_factory.py` + config schema.
3. Migrate `pr_eval.py` to REPL/tool-driven debate.
4. Migrate `recursive_repo_model.py` and `cross_pr.py`.
5. Wire Neon pushes from evaluation/synthesis outputs.
6. Export + CLI polish.
