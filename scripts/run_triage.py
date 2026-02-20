#!/usr/bin/env python3
"""Run the RLM PR triage analysis."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_repo_intel.config import load_config
from rlm_repo_intel.dashboard_push import push_evaluation, push_summary
from rlm_repo_intel.pipeline.rlm_session import create_frontier_rlm


def _extract_response_text(result: Any) -> str:
    if hasattr(result, "response"):
        return str(result.response)
    return str(result)


def _parse_result_payload(result: Any) -> Any:
    if isinstance(result, (dict, list)):
        return result
    response_text = _extract_response_text(result)
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return response_text


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _find_eval_candidates(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list) and all(isinstance(item, dict) for item in obj):
        return obj

    if isinstance(obj, dict):
        for key in ("evaluations", "results", "prs", "triage", "items"):
            value = obj.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value

        for value in obj.values():
            found = _find_eval_candidates(value)
            if found:
                return found

    if isinstance(obj, list):
        for value in obj:
            found = _find_eval_candidates(value)
            if found:
                return found

    return []


def _normalize_eval(raw: dict[str, Any]) -> dict[str, Any]:
    pr_number = raw.get("pr_number", raw.get("number", 0))
    title = raw.get("title", "(untitled PR)")
    impact_scope = raw.get("impact_scope", raw.get("modules", []))
    linked_issues = raw.get("linked_issues", [])

    normalized = {
        "pr_number": int(_to_float(pr_number, 0)),
        "title": str(title),
        "risk_score": _to_float(raw.get("risk_score", raw.get("risk", raw.get("urgency", 0.5))), 0.5),
        "quality_score": _to_float(raw.get("quality_score", raw.get("quality", 0.5)), 0.5),
        "strategic_value": _to_float(raw.get("strategic_value", 0.5), 0.5),
        "novelty_score": _to_float(raw.get("novelty_score", 0.5), 0.5),
        "test_alignment": _to_float(raw.get("test_alignment", 0.5), 0.5),
        "final_rank_score": _to_float(
            raw.get("final_rank_score", raw.get("rank_score", raw.get("score", 0.0))), 0.0
        ),
        "review_summary": str(
            raw.get("review_summary", raw.get("summary", raw.get("reasoning", "")))
        ),
        "confidence": _to_float(raw.get("confidence", 0.5), 0.5),
        "impact_scope": [str(item) for item in _to_list(impact_scope)],
        "linked_issues": [int(_to_float(item, 0)) for item in _to_list(linked_issues)],
        "agent_traces": raw.get("agent_traces", raw.get("agent_outputs", {})),
    }

    if raw.get("state") is not None:
        normalized["state"] = str(raw.get("state"))

    return normalized


def _build_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluations)
    state_counts: dict[str, int] = {}
    for ev in evaluations:
        state = str(ev.get("state", "unknown"))
        state_counts[state] = state_counts.get(state, 0) + 1

    avg_risk = sum(ev.get("risk_score", 0.0) for ev in evaluations) / total if total else 0.0
    avg_quality = sum(ev.get("quality_score", 0.0) for ev in evaluations) / total if total else 0.0
    avg_rank = sum(ev.get("final_rank_score", 0.0) for ev in evaluations) / total if total else 0.0

    return {
        "total_prs_evaluated": total,
        "total_modules": 0,
        "clusters": 0,
        "themes": [],
        "state_counts": state_counts,
        "average_risk_score": round(avg_risk, 4),
        "average_quality_score": round(avg_quality, 4),
        "average_final_rank_score": round(avg_rank, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    config = load_config("rlm-repo-intel.yaml")
    print("Creating frontier RLM...")
    rlm = create_frontier_rlm(config)

    prompt = "Analyze the first 5 open PRs. Filter prs for state=='open', take the first 5. Score each for urgency and quality, assign a state, and produce the full triage JSON. This is a test run — keep it to exactly 5 PRs."

    print(f"Running RLM with prompt: {prompt}")
    print("=" * 80)

    result = rlm.completion(prompt)
    result_payload = _parse_result_payload(result)

    print("=" * 80)
    print("RLM RESULT:")
    print(_extract_response_text(result))

    # Save local backup first.
    output_path = Path(".rlm-repo-intel/results/triage.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if isinstance(result_payload, (dict, list)):
            output_path.write_text(json.dumps(result_payload, indent=2))
        else:
            output_path.write_text(str(result_payload))
    except (json.JSONDecodeError, TypeError):
        output_path.write_text(str(result))

    print(f"\nResults saved to {output_path}")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set, skipping dashboard push.")
        return

    evaluations_raw = _find_eval_candidates(result_payload)
    evaluations = [_normalize_eval(item) for item in evaluations_raw]
    summary = _build_summary(evaluations)

    try:
        for evaluation in evaluations:
            push_evaluation(evaluation)
        push_summary(summary)
        print(f"Pushed {len(evaluations)} evaluations and summary to dashboard DB.")
    except Exception as exc:
        print(f"Dashboard push failed: {exc}. Local backup is still saved.")


if __name__ == "__main__":
    main()
