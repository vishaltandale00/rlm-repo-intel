#!/usr/bin/env python3
"""Run full analysis pipeline and stream updates to dashboard storage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Allow running without requiring package installation.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rlm_repo_intel.config import load_config
from rlm_repo_intel.dashboard_push import (
    push_clusters,
    push_evaluation,
    push_ranking,
    push_summary,
)
from rlm_repo_intel.evaluation import pr_eval
from rlm_repo_intel.graph.store import GraphStore
from rlm_repo_intel.modeling import build_codebase_model
from rlm_repo_intel.rlm_factory import try_create_rlm
from rlm_repo_intel.synthesis import run_synthesis


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                items.append(loaded)
    return items


def _compute_summary(results_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    cards = _read_json(results_dir / "module_cards.json", {})
    ranking = _read_json(results_dir / "final_ranking.json", {})
    clusters = _read_json(results_dir / "pr_clusters.json", [])
    evals = _read_jsonl(results_dir / "pr_evaluations.jsonl")

    total_modules = len(cards) if isinstance(cards, dict) else len(cards or [])
    top_prs = ranking.get("ranking", []) if isinstance(ranking, dict) else []
    themes = ranking.get("themes", []) if isinstance(ranking, dict) else []
    cluster_count = len(clusters) if isinstance(clusters, list) else 0
    repo_cfg = config.get("repo", {})
    owner = repo_cfg.get("owner")
    name = repo_cfg.get("name")

    return {
        "repo": f"{owner}/{name}" if owner and name else None,
        "total_prs_evaluated": len(evals),
        "total_modules": total_modules,
        "top_prs": top_prs[:20] if isinstance(top_prs, list) else [],
        "clusters": cluster_count,
        "themes": themes if isinstance(themes, list) else [],
    }


def _run_phase2_and_push(config: dict[str, Any], limit: int | None) -> int:
    print("[phase2] Loading graph and context...")
    data_dir = Path(config["paths"]["data_dir"])
    results_dir = Path(config["paths"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)

    graph = GraphStore(config["paths"]["graph_dir"])
    graph.load()

    architecture = _read_json(results_dir / "architecture.json", {})
    module_cards = _read_json(results_dir / "module_cards.json", {})

    prs_path = data_dir / "prs" / "all_prs.jsonl"
    prs = _read_jsonl(prs_path)
    if limit is not None:
        prs = prs[:limit]

    issues_path = data_dir / "issues" / "all_issues.jsonl"
    issues_list = _read_jsonl(issues_path)
    issues_by_number: dict[int, dict[str, Any]] = {}
    for issue in issues_list:
        number = issue.get("number")
        if isinstance(number, int):
            issues_by_number[number] = issue

    worker = try_create_rlm(config["models"]["cheap_worker"], label="eval-worker")
    root = try_create_rlm(config["models"]["root"], label="eval-root")

    print(f"[phase2] Evaluating {len(prs)} PRs...")
    evaluations: list[pr_eval.PREvaluation] = []
    pushed = 0
    for idx, pr in enumerate(prs, start=1):
        pr_number = int(pr.get("number", 0))
        try:
            ev = pr_eval._evaluate_single_pr(
                pr=pr,
                graph=graph,
                module_cards=module_cards,
                architecture=architecture,
                issues=issues_by_number,
                worker=worker,
                root=root,
            )
        except Exception as exc:
            print(f"[warn] PR #{pr_number} failed: {exc}")
            continue

        evaluations.append(ev)
        try:
            push_evaluation(asdict(ev))
            pushed += 1
        except Exception as exc:
            print(f"[warn] push failed for evaluation PR #{ev.pr_number}: {exc}")
        print(f"[phase2] {idx}/{len(prs)} PR #{ev.pr_number} evaluated")

    eval_path = results_dir / "pr_evaluations.jsonl"
    with open(eval_path, "w") as f:
        for ev in evaluations:
            f.write(json.dumps(asdict(ev)) + "\n")

    trace_path = results_dir / "pr_reasoning_traces.jsonl"
    with open(trace_path, "w") as f:
        for ev in evaluations:
            trace = {
                "pr_number": ev.pr_number,
                "title": ev.title,
                "agent_outputs": ev.agent_outputs,
                "disagreement_points": ev.disagreement_points,
                "synthesis_reasoning": ev.synthesis_reasoning,
            }
            f.write(json.dumps(trace) + "\n")

    print(f"[phase2] Complete: {len(evaluations)} evaluated, {pushed} pushed to DB")
    return len(evaluations)


def _ensure_api_keys() -> None:
    openai = bool(os.getenv("OPENAI_API_KEY"))
    anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    google = bool(os.getenv("GOOGLE_API_KEY"))
    if not (openai or anthropic or google):
        print("[warn] No API keys found in OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY")
    else:
        print(
            "[env] API keys loaded:"
            f" openai={'yes' if openai else 'no'}"
            f" anthropic={'yes' if anthropic else 'no'}"
            f" google={'yes' if google else 'no'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full analysis pipeline and stream dashboard updates.")
    parser.add_argument("--config", default="rlm-repo-intel.yaml", help="Path to config YAML")
    parser.add_argument("--limit", type=int, default=None, help="Max PRs to evaluate")
    parser.add_argument("--top-n", type=int, default=200, help="Top N PRs for synthesis")
    args = parser.parse_args()

    print(f"[init] Loading config from {args.config}")
    config = load_config(args.config)
    _ensure_api_keys()
    db_url_present = bool(os.getenv("DATABASE_URL"))
    print(f"[init] Dashboard DB push enabled: {'yes' if db_url_present else 'no'}")

    print("[phase1] build_codebase_model starting...")
    build_codebase_model(config)
    print("[phase1] Complete")

    _run_phase2_and_push(config, limit=args.limit)

    print("[phase3] run_synthesis starting...")
    run_synthesis(config, top_n=args.top_n)
    print("[phase3] Complete")

    results_dir = Path(config["paths"]["results_dir"])
    clusters = _read_json(results_dir / "pr_clusters.json", [])
    ranking = _read_json(results_dir / "final_ranking.json", {"ranking": [], "themes": [], "conflicts": []})
    summary = _compute_summary(results_dir, config)

    try:
        push_clusters(clusters)
        push_ranking(ranking)
        push_summary(summary)
        print("[push] Final clusters, ranking, and summary pushed to DB")
    except Exception as exc:
        print(f"[warn] final push failed: {exc}")

    print("[done] Pipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
