#!/usr/bin/env python3
"""Run frontier RLM analysis in a single persistent REPL session."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running without requiring package installation.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rlm_repo_intel.config import load_config
from rlm_repo_intel.pipeline.rlm_session import create_frontier_rlm


def _ensure_api_keys() -> None:
    anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    google = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    if not (anthropic or google):
        print("[warn] No API keys found in ANTHROPIC_API_KEY, GOOGLE_API_KEY, GEMINI_API_KEY")
    else:
        print(
            "[env] API keys loaded:"
            f" anthropic={'yes' if anthropic else 'no'}"
            f" google={'yes' if google else 'no'}"
        )


def _build_task_prompt(config: dict) -> str:
    owner = config.get("repo", {}).get("owner")
    name = config.get("repo", {}).get("name")
    repo = f"{owner}/{name}" if owner and name else "unknown/unknown"
    data_dir = config["paths"]["data_dir"]
    graph_dir = config["paths"]["graph_dir"]

    return f"""
Analyze repository {repo} end-to-end using the REPL tools.

Objectives:
1. Build high-confidence repository understanding using graph + code evidence.
2. Evaluate PRs for merge readiness and hidden regression risk.
3. Run internal debate with specialist roles:
   - analyst: claude-sonnet-4.6
   - adversary: gemini-3.1-pro
   - risk: claude-sonnet-4.6
   - arbiter: claude-opus-4.6
4. Produce final structured JSON with:
   - executive_summary
   - top_risks[]
   - must_fix_before_merge[]
   - can_defer[]
   - validation_plan[]
   - evidence_refs[]
   - unknowns[]

Context paths:
- data_dir: {data_dir}
- graph_dir: {graph_dir}
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frontier RLM analysis with one persistent REPL session.")
    parser.add_argument("--config", default="rlm-repo-intel.yaml", help="Path to config YAML")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path for final JSON/text (default: <results_dir>/frontier_analysis.json)",
    )
    args = parser.parse_args()

    print(f"[init] Loading config from {args.config}")
    config = load_config(args.config)
    _ensure_api_keys()

    print("[init] Creating frontier RLM session...")
    rlm = create_frontier_rlm(config)
    task_prompt = _build_task_prompt(config)

    print("[run] Starting model-driven REPL analysis...")
    result = rlm.completion(task_prompt)
    response_text = result.response

    results_dir = Path(config["paths"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else results_dir / "frontier_analysis.json"

    try:
        parsed = json.loads(response_text)
        with open(output_path, "w") as f:
            json.dump(parsed, f, indent=2)
    except json.JSONDecodeError:
        with open(output_path, "w") as f:
            f.write(response_text)

    print(f"[done] Analysis complete: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
