from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rlm_repo_intel.dashboard_push import push_evaluation, push_summary, push_trace

_RESULTS_BACKUP_PATH = Path(".rlm-repo-intel/results/live_partial_evaluations.json")
_SUMMARY_BACKUP_PATH = Path(".rlm-repo-intel/results/live_partial_summary.json")
_TRACE_BACKUP_PATH = Path(".rlm-repo-intel/results/live_trace_steps.json")

_pushed_pr_numbers: set[int] = set()
_pushed_fingerprints: set[str] = set()
_latest_by_pr: dict[int, dict[str, Any]] = {}
_latest_misc: dict[str, dict[str, Any]] = {}
_trace_steps: list[dict[str, Any]] = []


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_eval(raw: dict[str, Any]) -> dict[str, Any]:
    pr_number = raw.get("pr_number", raw.get("number", 0))
    title = raw.get("title", "(untitled PR)")

    normalized: dict[str, Any] = {
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
        "review_summary": str(raw.get("review_summary", raw.get("summary", raw.get("reasoning", "")))),
        "confidence": _to_float(raw.get("confidence", 0.5), 0.5),
        "impact_scope": [str(item) for item in raw.get("impact_scope", raw.get("modules", [])) if item is not None],
        "labels": [str(item).strip().lower() for item in raw.get("labels", raw.get("tags", [])) if item is not None],
        "linked_issues": [int(_to_float(item, 0)) for item in raw.get("linked_issues", []) if item is not None],
        "agent_traces": raw.get("agent_traces", raw.get("agent_outputs", {})),
    }

    if raw.get("state") is not None:
        normalized["state"] = str(raw.get("state"))

    return normalized


def _fingerprint(item: dict[str, Any]) -> str:
    return json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _current_evaluations() -> list[dict[str, Any]]:
    evaluations: list[dict[str, Any]] = list(_latest_by_pr.values())
    evaluations.extend(_latest_misc.values())
    return evaluations


def _build_partial_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluations)
    state_counts: dict[str, int] = {}
    total_risk = 0.0
    total_quality = 0.0

    for evaluation in evaluations:
        state = str(evaluation.get("state", "unknown"))
        state_counts[state] = state_counts.get(state, 0) + 1
        total_risk += _to_float(evaluation.get("risk_score", 0.0), 0.0)
        total_quality += _to_float(evaluation.get("quality_score", 0.0), 0.0)

    avg_risk = total_risk / total if total else 0.0
    avg_quality = total_quality / total if total else 0.0

    return {
        "status": "running",
        "unique_prs_pushed": len(_pushed_pr_numbers),
        "total_prs_evaluated": total,
        "state_counts": state_counts,
        "average_risk_score": round(avg_risk, 4),
        "average_quality_score": round(avg_quality, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def push_partial_results(results: list[dict[str, Any]]) -> None:
    """Push incremental PR scoring results to Neon and local backup files."""
    if not isinstance(results, list):
        return

    changed = False

    for item in results:
        if not isinstance(item, dict):
            continue

        normalized = _normalize_eval(item)
        pr_number = int(_to_float(normalized.get("pr_number"), 0))

        if pr_number > 0:
            previous = _latest_by_pr.get(pr_number)
            if previous == normalized:
                continue
            _latest_by_pr[pr_number] = normalized
            _pushed_pr_numbers.add(pr_number)
            changed = True
            if os.getenv("DATABASE_URL"):
                try:
                    push_evaluation(normalized)
                except Exception:
                    pass
            continue

        fp = _fingerprint(normalized)
        if fp in _pushed_fingerprints and fp in _latest_misc:
            continue
        _pushed_fingerprints.add(fp)
        _latest_misc[fp] = normalized
        changed = True
        if os.getenv("DATABASE_URL"):
            try:
                push_evaluation(normalized)
            except Exception:
                pass

    evaluations = _current_evaluations()
    summary = _build_partial_summary(evaluations)

    # Keep summary fresh even when batch had duplicates so dashboard progress heartbeat updates.
    if os.getenv("DATABASE_URL"):
        try:
            push_summary(summary)
        except Exception:
            pass

    _ensure_parent(_RESULTS_BACKUP_PATH)
    _RESULTS_BACKUP_PATH.write_text(json.dumps(evaluations, indent=2))

    _ensure_parent(_SUMMARY_BACKUP_PATH)
    _SUMMARY_BACKUP_PATH.write_text(json.dumps(summary, indent=2))

    if not changed:
        return


def push_trace_step(iteration: int, type: str, content: str) -> None:
    """Append a trace step and push the latest full trace to Neon and local backup."""
    try:
        normalized_iteration = int(iteration)
    except (TypeError, ValueError):
        normalized_iteration = 1

    step_type = str(type or "llm_response").strip().lower()
    if step_type not in {"llm_response", "code_execution"}:
        step_type = "llm_response"

    step = {
        "iteration": max(1, normalized_iteration),
        "type": step_type,
        "content": str(content),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _trace_steps.append(step)
    if os.getenv("DATABASE_URL"):
        try:
            push_trace(_trace_steps)
        except Exception:
            pass

    _ensure_parent(_TRACE_BACKUP_PATH)
    _TRACE_BACKUP_PATH.write_text(json.dumps(_trace_steps, indent=2))
