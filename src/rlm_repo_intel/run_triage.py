#!/usr/bin/env python3
"""Run the RLM PR triage analysis."""

from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from rlm_repo_intel.config import load_config
from rlm_repo_intel.dashboard_push import (
    push_run_event,
    push_run_meta,
    push_clusters,
    push_evaluation,
    push_ranking,
    push_summary,
    push_trace,
    start_new_run,
)
from rlm_repo_intel.pipeline.rlm_session import create_frontier_rlm
from rlm_repo_intel.prompts.prompt_registry import get_prompt_version
from rlm_repo_intel.prompts.root_prompts import TRIAGE_TASK_PROMPT
from rlm_repo_intel.rlm_factory import _to_litellm_model_name


class OutputContractError(RuntimeError):
    """Raised when required REPL output variables are missing or invalid."""


_TRIAGE_RESULT_REQUIRED_FIELDS = {
    "pr_number",
    "title",
    "urgency",
    "quality",
    "criticality",
    "risk_if_merged",
    "final_score",
    "justification",
    "evidence",
}
_TRIAGE_SUMMARY_REQUIRED_FIELDS = {
    "total_open_prs_seen",
    "scored_count",
    "elite_count",
    "score_distribution",
}


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
        # Fallback for Python-style repr payloads returned from REPL variables.
        try:
            return ast.literal_eval(response_text)
        except (SyntaxError, ValueError):
            return response_text


def _looks_like_triage_payload(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if not value:
        return True
    if not all(isinstance(item, dict) for item in value):
        return False
    required = {"number", "pr_number", "title", "urgency", "quality", "state"}
    first_keys = set(value[0].keys())
    return bool(required & first_keys)


def _looks_like_top_prs_payload(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if not value:
        return True
    if not all(isinstance(item, dict) for item in value):
        return False
    required = {"pr_number", "number", "final_score", "elite_rank"}
    first_keys = set(value[0].keys())
    return bool(required & first_keys)


def _looks_like_summary_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required_primary = {
        "total_open_prs_seen",
        "scored_count",
        "elite_count",
    }
    required_legacy = {
        "total_open_prs_seen",
        "phase1_candidates_count",
        "deep_analyzed_count",
        "scored_count",
        "elite_count",
    }
    keys = set(value.keys())
    return required_primary.issubset(keys) or bool(required_legacy & keys)


def _repl_namespaces(rlm: Any) -> list[dict[str, Any]]:
    env = getattr(rlm, "_persistent_env", None)
    if env is None:
        return []
    namespaces: list[dict[str, Any]] = []
    for attr in ("locals", "namespace", "globals"):
        ns = getattr(env, attr, None)
        if isinstance(ns, dict):
            namespaces.append(ns)
    return namespaces


def _extract_named_repl_variables(rlm: Any) -> dict[str, Any]:
    """Extract required triage variables, preferring persistent env locals."""
    results: dict[str, Any] = {}
    targets = {
        "triage_results": _looks_like_triage_payload,
        "top_prs": _looks_like_top_prs_payload,
        "triage_summary": _looks_like_summary_payload,
    }
    env = getattr(rlm, "_persistent_env", None)
    namespaces: list[dict[str, Any]] = []
    if env is not None:
        locals_ns = getattr(env, "locals", None)
        if isinstance(locals_ns, dict):
            namespaces.append(locals_ns)
        for attr in ("namespace", "globals"):
            ns = getattr(env, attr, None)
            if isinstance(ns, dict):
                namespaces.append(ns)

    for ns in namespaces:
        for name, validator in targets.items():
            if name in results or name not in ns:
                continue
            value = ns[name]
            if validator(value):
                results[name] = value
    return results


def _read_named_repl_values(rlm: Any, names: tuple[str, ...]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for ns in _repl_namespaces(rlm):
        for name in names:
            if name not in values and name in ns:
                values[name] = ns[name]
    return values


def _extract_bundle_from_repl(rlm: Any) -> dict[str, Any] | None:
    values = _read_named_repl_values(rlm, ("triage_bundle",))
    bundle = values.get("triage_bundle")
    if isinstance(bundle, dict):
        return bundle
    return None


def _contract_issues(
    triage_results: Any,
    top_prs: Any,
    triage_summary: Any,
) -> list[str]:
    issues: list[str] = []

    if not isinstance(triage_results, list):
        issues.append("triage_results must be a list")
    else:
        for idx, item in enumerate(triage_results):
            if not isinstance(item, dict):
                issues.append(f"triage_results[{idx}] must be a dict")
                continue
            missing = _TRIAGE_RESULT_REQUIRED_FIELDS - set(item.keys())
            if missing:
                issues.append(
                    f"triage_results[{idx}] missing required fields: {', '.join(sorted(missing))}"
                )
                # One concrete example is enough for repair prompt clarity.
                break

    if not isinstance(top_prs, list):
        issues.append("top_prs must be a list")
    else:
        for idx, item in enumerate(top_prs):
            if not isinstance(item, dict):
                issues.append(f"top_prs[{idx}] must be a dict")
                continue
            has_pr_number = "pr_number" in item or "number" in item
            if not has_pr_number:
                issues.append(f"top_prs[{idx}] requires pr_number or number")
                break
            if "final_score" not in item:
                issues.append(f"top_prs[{idx}] requires final_score")
                break

    if not isinstance(triage_summary, dict):
        issues.append("triage_summary must be a dict")
    else:
        missing_summary = _TRIAGE_SUMMARY_REQUIRED_FIELDS - set(triage_summary.keys())
        if missing_summary:
            issues.append(
                f"triage_summary missing required fields: {', '.join(sorted(missing_summary))}"
            )

    return issues


def _extract_contract_from_repl(rlm: Any) -> dict[str, Any]:
    bundle = _extract_bundle_from_repl(rlm)
    named = _read_named_repl_values(rlm, ("triage_results", "top_prs", "triage_summary"))

    triage_results = named.get("triage_results")
    top_prs = named.get("top_prs")
    triage_summary = named.get("triage_summary")
    source = "named_vars"

    if isinstance(bundle, dict):
        triage_results = bundle.get("triage_results", triage_results)
        top_prs = bundle.get("top_prs", top_prs)
        triage_summary = bundle.get("triage_summary", triage_summary)
        source = "triage_bundle"

    issues = _contract_issues(triage_results, top_prs, triage_summary)
    return {
        "triage_results": triage_results,
        "top_prs": top_prs,
        "triage_summary": triage_summary,
        "triage_bundle": bundle,
        "issues": issues,
        "source": source,
    }


def _build_repair_prompt(issues: list[str]) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in issues) if issues else "- unknown output contract issue"
    return (
        "Output contract repair required.\n"
        "Only do the minimum needed to fix these issues in the current REPL session:\n"
        f"{issue_lines}\n"
        "Set triage_results, top_prs, triage_summary, then set triage_bundle as a dict with those keys.\n"
        "Run finalize_outputs() if available.\n"
        "End your response with exactly: FINAL_VAR(\"triage_bundle\")"
    )


def _output_contract_mode(config: dict[str, Any]) -> str:
    mode = str(config.get("pipeline", {}).get("output_contract_mode", "strict_repl")).strip().lower()
    if mode not in {"strict_repl", "hybrid"}:
        return "strict_repl"
    return mode


def _output_repair_attempts(config: dict[str, Any]) -> int:
    try:
        attempts = int(config.get("pipeline", {}).get("output_repair_attempts", 1))
    except (TypeError, ValueError):
        return 1
    return max(0, attempts)


def _observability_cfg(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "enabled": True,
        "heartbeat_seconds": 10,
        "capture_stdout_chars": 4000,
        "capture_stderr_chars": 4000,
        "response_preview_chars": 2000,
    }
    raw = config.get("pipeline", {}).get("observability", {})
    if isinstance(raw, dict):
        merged = dict(defaults)
        merged.update(raw)
    else:
        merged = defaults
    return merged


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if limit < 0 or len(text) <= limit:
        return text
    return text[:limit]


def _extract_raw_iterations(
    completion_result: Any,
    completion_label: str,
    observability: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata = getattr(completion_result, "metadata", None)
    if not isinstance(metadata, dict):
        return []
    iterations = metadata.get("iterations")
    if not isinstance(iterations, list):
        return []

    stdout_limit = int(observability.get("capture_stdout_chars", 4000))
    stderr_limit = int(observability.get("capture_stderr_chars", 4000))
    response_limit = int(observability.get("response_preview_chars", 2000))

    records: list[dict[str, Any]] = []
    for iteration_entry in iterations:
        if not isinstance(iteration_entry, dict):
            continue
        code_blocks = iteration_entry.get("code_blocks")
        code_block_entries: list[dict[str, Any]] = []
        if isinstance(code_blocks, list):
            for idx, block in enumerate(code_blocks, start=1):
                if not isinstance(block, dict):
                    continue
                result = block.get("result")
                if not isinstance(result, dict):
                    result = {}
                stdout = str(result.get("stdout", ""))
                stderr = str(result.get("stderr", ""))
                code_block_entries.append(
                    {
                        "index": idx,
                        "code": str(block.get("code", "")),
                        "execution_time_seconds": float(result.get("execution_time") or 0.0),
                        "stdout_preview": _truncate_text(stdout, stdout_limit),
                        "stderr_preview": _truncate_text(stderr, stderr_limit),
                        "stdout_chars": len(stdout),
                        "stderr_chars": len(stderr),
                        "final_answer": result.get("final_answer"),
                    }
                )

        response_text = str(iteration_entry.get("response", ""))
        records.append(
            {
                "completion_label": completion_label,
                "iteration": int(iteration_entry.get("iteration", len(records) + 1)),
                "timestamp": str(iteration_entry.get("timestamp", "")),
                "iteration_time_seconds": float(iteration_entry.get("iteration_time") or 0.0),
                "response_preview": _truncate_text(response_text, response_limit),
                "response_chars": len(response_text),
                "code_blocks": code_block_entries,
            }
        )
    return records


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _heartbeat_snapshot(
    run_id: str,
    prompt_hash: str,
    started_at: datetime,
    phase: str,
    repair_attempts_used: int,
    raw_iterations: list[dict[str, Any]],
    rlm: Any | None = None,
) -> dict[str, Any]:
    elapsed = max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
    last_iteration_seen = 0
    last_block_seen = 0
    if raw_iterations:
        last = raw_iterations[-1]
        last_iteration_seen = int(last.get("iteration", 0))
        blocks = last.get("code_blocks")
        if isinstance(blocks, list):
            last_block_seen = len(blocks)
    if rlm is not None:
        logger = getattr(rlm, "logger", None)
        if logger is not None and hasattr(logger, "get_trajectory"):
            try:
                trajectory = logger.get_trajectory()
            except Exception:
                trajectory = None
            if isinstance(trajectory, dict):
                iterations = trajectory.get("iterations")
                if isinstance(iterations, list) and iterations:
                    latest = iterations[-1]
                    if isinstance(latest, dict):
                        live_iteration = int(latest.get("iteration", 0))
                        if live_iteration > last_iteration_seen:
                            last_iteration_seen = live_iteration
                            live_blocks = latest.get("code_blocks")
                            last_block_seen = len(live_blocks) if isinstance(live_blocks, list) else 0
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "prompt_hash": prompt_hash,
        "phase": phase,
        "elapsed_seconds": round(elapsed, 2),
        "repair_attempts_used": repair_attempts_used,
        "last_iteration_seen": last_iteration_seen,
        "last_block_seen": last_block_seen,
    }


def _start_heartbeat_thread(
    heartbeat_path: Path,
    run_id: str,
    prompt_hash: str,
    started_at: datetime,
    interval_seconds: int,
    state: dict[str, Any],
    rlm: Any | None,
    stop_event: threading.Event,
) -> threading.Thread:
    interval = max(1, int(interval_seconds))

    def _loop() -> None:
        while not stop_event.is_set():
            snapshot = _heartbeat_snapshot(
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=started_at,
                phase=str(state.get("phase", "unknown")),
                repair_attempts_used=int(state.get("repair_attempts_used", 0)),
                raw_iterations=list(state.get("raw_iterations", [])),
                rlm=rlm,
            )
            try:
                _write_json_file(heartbeat_path, snapshot)
            except OSError:
                pass
            if stop_event.wait(interval):
                break

    thread = threading.Thread(target=_loop, name="triage-heartbeat", daemon=True)
    thread.start()
    return thread


def _stop_heartbeat_thread(
    heartbeat_path: Path,
    run_id: str,
    prompt_hash: str,
    started_at: datetime,
    state: dict[str, Any],
    stop_event: threading.Event,
    thread: threading.Thread | None,
) -> None:
    snapshot = _heartbeat_snapshot(
        run_id=run_id,
        prompt_hash=prompt_hash,
        started_at=started_at,
        phase=str(state.get("phase", "stopped")),
        repair_attempts_used=int(state.get("repair_attempts_used", 0)),
        raw_iterations=list(state.get("raw_iterations", [])),
        rlm=None,
    )
    try:
        _write_json_file(heartbeat_path, snapshot)
    except OSError:
        pass
    stop_event.set()
    if thread is not None:
        thread.join(timeout=2)


def _extract_triage_results_from_repl(rlm: Any) -> Any | None:
    # rlm LocalREPL keeps user variables in env.locals and reserved tools in env.globals.
    namespaces = _repl_namespaces(rlm)
    if not namespaces:
        return None

    # Prefer explicit names first.
    preferred_names = (
        "triage_results",
        "final_var",
        "final_results",
        "results",
        "output",
    )
    for name in preferred_names:
        for ns in namespaces:
            if name in ns and _looks_like_triage_payload(ns[name]):
                return ns[name]

    # Fall back to best matching list-of-dicts payload in REPL locals.
    candidates: list[list[dict[str, Any]]] = []
    for ns in namespaces:
        for value in ns.values():
            if _looks_like_triage_payload(value):
                candidates.append(value)
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _normalize_score(score: Any) -> float:
    numeric = _to_float(score, 0.0)
    if numeric > 1.0:
        numeric = numeric / 10.0
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def _extract_labels(raw: dict[str, Any]) -> list[str]:
    labels = raw.get("labels", raw.get("tags", []))
    items = _to_list(labels)
    normalized: list[str] = []
    for item in items:
        if isinstance(item, dict):
            label = item.get("name")
            if label:
                normalized.append(str(label).strip().lower())
        elif item is not None:
            normalized.append(str(item).strip().lower())
    return [label for label in normalized if label]


def _extract_module_prefixes(impact_scope: list[str]) -> list[str]:
    prefixes: list[str] = []
    for item in impact_scope:
        value = item.strip()
        if not value:
            continue
        if "/" in value:
            prefix = value.split("/", 1)[0]
        elif ":" in value:
            prefix = value.split(":", 1)[0]
        else:
            prefix = value.split(".", 1)[0]
        prefix = prefix.strip().lower()
        if prefix:
            prefixes.append(prefix)
    return prefixes


def _extract_title_theme(title: str) -> str | None:
    text = title.strip().lower()
    if not text:
        return None

    conventional = re.match(r"^([a-z]+)\(([^)]+)\)", text)
    if conventional:
        return f"{conventional.group(1)}({conventional.group(2)})"

    fallback = re.match(r"^([a-z]+)[:\\s\\-_/]+([a-z0-9_\\-/]+)", text)
    if fallback:
        return f"{fallback.group(1)}({fallback.group(2).split('/')[0]})"

    return None


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

    urgency = _to_float(raw.get("urgency", raw.get("risk_score", raw.get("risk", 0.5))), 0.5)
    quality = _to_float(raw.get("quality", raw.get("quality_score", 0.5)), 0.5)
    risk_if_merged = _to_float(raw.get("risk_if_merged", raw.get("risk", raw.get("risk_score", 0.5))), 0.5)
    criticality = _to_float(raw.get("criticality", raw.get("strategic_value", 0.5)), 0.5)
    final_score = _to_float(
        raw.get("final_score", raw.get("final_rank_score", raw.get("rank_score", raw.get("score", 0.0)))),
        0.0,
    )
    justification = str(raw.get("justification", raw.get("review_summary", raw.get("summary", ""))))
    evidence = _normalize_evidence(raw.get("evidence", []))
    key_risks = [str(item) for item in _to_list(raw.get("key_risks", []))]
    must_fix_before_merge = [str(item) for item in _to_list(raw.get("must_fix_before_merge", []))]
    merge_recommendation = str(raw.get("merge_recommendation", raw.get("verdict", ""))).strip()

    normalized = {
        "pr_number": int(_to_float(pr_number, 0)),
        "title": str(title),
        "author": str(raw.get("author", "")),
        "urgency": urgency,
        "quality": quality,
        "risk_if_merged": risk_if_merged,
        "criticality": criticality,
        "final_score": final_score,
        "merge_recommendation": merge_recommendation,
        "justification": justification,
        "key_risks": key_risks,
        "must_fix_before_merge": must_fix_before_merge,
        "evidence": evidence,
        "risk_score": urgency,
        "quality_score": quality,
        "strategic_value": criticality,
        "novelty_score": _to_float(raw.get("novelty_score", 0.5), 0.5),
        "test_alignment": _to_float(raw.get("test_alignment", 0.5), 0.5),
        "final_rank_score": final_score,
        "review_summary": justification,
        "confidence": _to_float(raw.get("confidence", 0.5), 0.5),
        "impact_scope": [str(item) for item in _to_list(impact_scope)],
        "labels": _extract_labels(raw),
        "linked_issues": [int(_to_float(item, 0)) for item in _to_list(linked_issues)],
        "agent_traces": raw.get("agent_traces", raw.get("agent_outputs", {})),
    }

    if raw.get("state") is not None:
        normalized["state"] = str(raw.get("state"))

    return normalized


def _normalize_evidence(raw_evidence: Any) -> list[dict[str, Any]]:
    evidence_items: list[dict[str, Any]] = []
    for item in _to_list(raw_evidence):
        if isinstance(item, dict):
            evidence_items.append(
                {
                    "file": str(item.get("file", "")),
                    "reference_type": str(item.get("reference_type", "")),
                    "detail": str(item.get("detail", "")),
                    "line_hint": str(item.get("line_hint", "")),
                }
            )
            continue
        if item is None:
            continue
        evidence_items.append(
            {
                "file": "",
                "reference_type": "note",
                "detail": str(item),
                "line_hint": "",
            }
        )
    return evidence_items


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


def _normalize_summary(
    triage_summary: dict[str, Any] | None,
    evaluations: list[dict[str, Any]],
    top_prs: list[dict[str, Any]],
) -> dict[str, Any]:
    base = _build_summary(evaluations)
    if not triage_summary:
        return base

    normalized = dict(base)
    normalized.update(triage_summary)
    normalized["total_prs_evaluated"] = int(
        _to_float(
            triage_summary.get(
                "scored_count",
                triage_summary.get("deep_analyzed_count", triage_summary.get("total_open_prs_seen", len(evaluations))),
            ),
            len(evaluations),
        )
    )
    normalized["total_modules"] = int(_to_float(triage_summary.get("total_modules", 0), 0))
    normalized["clusters"] = int(_to_float(triage_summary.get("clusters", 0), 0))
    normalized["themes"] = [str(item) for item in _to_list(triage_summary.get("themes", []))]
    if not normalized["themes"]:
        normalized["themes"] = [f"elite_count:{len(top_prs)}"]
    normalized["timestamp"] = datetime.now(timezone.utc).isoformat()
    return normalized


def _build_clusters(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}

    def add_cluster(name: str, theme: str, pr_number: int) -> None:
        existing = clusters.get(name)
        if existing is None:
            clusters[name] = {"name": name, "pr_numbers": {pr_number}, "theme": theme}
            return
        existing["pr_numbers"].add(pr_number)

    for ev in evaluations:
        pr_number = int(_to_float(ev.get("pr_number"), 0))
        if pr_number <= 0:
            continue

        for label in _to_list(ev.get("labels")):
            label_text = str(label).strip().lower()
            if label_text:
                add_cluster(f"label:{label_text}", f"label '{label_text}'", pr_number)

        impact_scope = [str(item) for item in _to_list(ev.get("impact_scope"))]
        for prefix in _extract_module_prefixes(impact_scope):
            add_cluster(f"module:{prefix}", f"module '{prefix}'", pr_number)

        title_theme = _extract_title_theme(str(ev.get("title", "")))
        if title_theme:
            add_cluster(f"title:{title_theme}", f"title pattern '{title_theme}'", pr_number)

    result: list[dict[str, Any]] = []
    cluster_id = 1
    for cluster in clusters.values():
        pr_numbers = sorted(cluster["pr_numbers"])
        if len(pr_numbers) < 2:
            continue
        relations: list[dict[str, Any]] = []
        for pr_a, pr_b in combinations(pr_numbers, 2):
            relations.append(
                {
                    "pr_a": pr_a,
                    "pr_b": pr_b,
                    "relation_type": "related",
                    "explanation": f"Grouped by {cluster['theme']}",
                }
            )
        result.append(
            {
                "cluster_id": cluster_id,
                "members": pr_numbers,
                "size": len(pr_numbers),
                "relations": relations,
            }
        )
        cluster_id += 1

    result.sort(key=lambda item: item["size"], reverse=True)
    return result


def _build_ranking(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    ranking_items: list[dict[str, Any]] = []

    for ev in evaluations:
        final_rank_score = _to_float(ev.get("final_rank_score"), 0.0)
        if final_rank_score > 0.0:
            rank_score = _normalize_score(final_rank_score)
        else:
            urgency = _normalize_score(ev.get("risk_score"))
            quality = _normalize_score(ev.get("quality_score"))
            rank_score = round((urgency * 0.6) + (quality * 0.4), 4)

        ranking_items.append(
            {
                "pr_number": int(_to_float(ev.get("pr_number"), 0)),
                "title": str(ev.get("title", "")),
                "rank_score": rank_score,
                "state": str(ev.get("state", "unknown")),
                "reason": str(ev.get("review_summary", "")).strip(),
            }
        )

    ranking_items.sort(key=lambda item: item["rank_score"], reverse=True)
    top_50 = ranking_items[:50]
    ranking_view: list[dict[str, Any]] = []
    for index, item in enumerate(top_50, start=1):
        reason = item["reason"] or f"score={item['rank_score']:.2f}; state={item['state']}"
        ranking_view.append(
            {
                "number": item["pr_number"],
                "rank": index,
                "reason": reason,
                "score": item["rank_score"],
            }
        )

    return {
        "ranking": ranking_view,
        "total_evaluated": len(evaluations),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_ranking_from_top_prs(top_prs: list[dict[str, Any]]) -> dict[str, Any]:
    ranking_view: list[dict[str, Any]] = []

    for index, item in enumerate(top_prs, start=1):
        pr_number = int(_to_float(item.get("pr_number", item.get("number", 0)), 0))
        if pr_number <= 0:
            continue
        elite_rank = int(_to_float(item.get("elite_rank", index), index))
        final_score = _to_float(item.get("final_score", item.get("score", 0.0)), 0.0)
        reason = str(item.get("justification", item.get("review_summary", ""))).strip()
        if not reason:
            reason = f"final_score={final_score:.2f}"
        ranking_view.append(
            {
                "number": pr_number,
                "rank": elite_rank,
                "reason": reason,
                "score": final_score,
            }
        )

    ranking_view.sort(key=lambda item: item["rank"])
    return {
        "ranking": ranking_view,
        "total_evaluated": len(ranking_view),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _parse_trace_steps(raw_text: str) -> list[dict[str, Any]]:
    text = raw_text.strip()
    if not text:
        return []

    steps: list[dict[str, Any]] = []
    current_iteration = 1
    current_type = "llm_response"
    buffer: list[str] = []
    in_code_block = False
    now = datetime.now(timezone.utc).isoformat()

    marker_re = re.compile(r"^\s*(?:#+\s*)?(?:iteration|iter|step)\s*[:#-]?\s*(\d+)\b", re.IGNORECASE)
    fence_re = re.compile(r"^\s*```")

    def flush_buffer() -> None:
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        buffer.clear()
        if not content:
            return
        steps.append(
            {
                "iteration": current_iteration,
                "type": current_type,
                "content": content,
                "timestamp": now,
            }
        )

    for line in text.splitlines():
        marker_match = marker_re.match(line)
        if marker_match and not in_code_block:
            flush_buffer()
            current_iteration = int(marker_match.group(1))
            current_type = "llm_response"
            continue

        if fence_re.match(line):
            if in_code_block:
                buffer.append(line)
                flush_buffer()
                in_code_block = False
                current_type = "llm_response"
                continue
            flush_buffer()
            in_code_block = True
            current_type = "code_execution"
            buffer.append(line)
            continue

        buffer.append(line)

    flush_buffer()

    if not steps:
        return [
            {
                "iteration": 1,
                "type": "llm_response",
                "content": text,
                "timestamp": now,
            }
        ]

    return steps


def main(config: dict[str, Any] | None = None):
    if config is None:
        config = load_config("rlm-repo-intel.yaml")
    start_time = datetime.now(timezone.utc)
    local_run_id = start_time.strftime("%Y%m%dT%H%M%S%fZ")
    prompt_version = get_prompt_version()
    prompt_hash = str(prompt_version.get("hash", "unknown"))
    model_name = _to_litellm_model_name(config.get("models", {}).get("root", "claude-sonnet-4-6"))
    budget = float(config.get("pipeline", {}).get("max_budget", 2000.0))

    run_metadata: dict[str, Any] = {
        "id": local_run_id,
        "status": "running",
        "kind": "experimental",
        "prompt_version": prompt_hash,
        "prompt_hash": prompt_hash,
        "prompt_label": prompt_hash[:12],
        "prompt_source_paths": [
            "src/rlm_repo_intel/prompts/root_prompts.py",
            "src/rlm_repo_intel/run_triage.py",
        ],
        "prompt_bundle": prompt_version.get("bundle", {}),
        "model_root": model_name,
        "model_name": model_name,
        "config_snapshot": config,
        "start_time": start_time.isoformat(),
        "started_at": start_time.isoformat(),
        "budget": budget,
        "token_input": 0,
        "token_output": 0,
        "cost_usd": 0.0,
        "total_prs_seen": 0,
        "total_prs_scored": 0,
    }
    results_dir = Path(config["paths"]["results_dir"])
    output_path = results_dir / "triage.json"
    trace_path = results_dir / "agent_trace.txt"
    raw_iterations_path = results_dir / "raw_iterations.json"
    heartbeat_path = results_dir / "run_heartbeat.json"
    results_dir.mkdir(parents=True, exist_ok=True)

    observability = _observability_cfg(config)
    observability_enabled = bool(observability.get("enabled", True))

    database_url = os.getenv("DATABASE_URL")
    run_id = local_run_id
    if database_url:
        try:
            run_id = start_new_run(run_metadata, run_id=local_run_id)
            run_metadata["id"] = run_id
            push_run_meta(run_metadata, run_id=run_id)
            print(f"Initialized run: {run_id} (prompt={prompt_hash[:12]})")
        except Exception as exc:
            print(f"Failed to initialize run on dashboard: {exc}. Continuing with local run ID {run_id}.")

    print("Creating frontier RLM...")
    rlm = create_frontier_rlm(config, run_id=run_id)
    prompt = TRIAGE_TASK_PROMPT

    print(f"Running RLM with prompt: {prompt}")
    print("=" * 80)

    contract_mode = _output_contract_mode(config)
    max_repair_attempts = _output_repair_attempts(config)
    strict_repl_mode = contract_mode == "strict_repl"
    raw_iterations: list[dict[str, Any]] = []
    heartbeat_state: dict[str, Any] = {
        "phase": "starting",
        "repair_attempts_used": 0,
        "raw_iterations": raw_iterations,
    }
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if observability_enabled:
        heartbeat_thread = _start_heartbeat_thread(
            heartbeat_path=heartbeat_path,
            run_id=run_id,
            prompt_hash=prompt_hash,
            started_at=start_time,
            interval_seconds=int(observability.get("heartbeat_seconds", 10)),
            state=heartbeat_state,
            rlm=rlm,
            stop_event=heartbeat_stop,
        )

    heartbeat_state["phase"] = "waiting_first_response"
    print("Starting root LM turn 1...")
    root_start = time.perf_counter()
    try:
        result = rlm.completion(prompt)
    except Exception:
        heartbeat_state["phase"] = "failed_root_completion"
        if observability_enabled and not heartbeat_stop.is_set():
            _stop_heartbeat_thread(
                heartbeat_path=heartbeat_path,
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=start_time,
                state=heartbeat_state,
                stop_event=heartbeat_stop,
                thread=heartbeat_thread,
            )
        raise
    final_result = result
    root_elapsed = time.perf_counter() - root_start
    print(f"Root LM turn 1 returned in {root_elapsed:.2f}s")
    raw_iterations.extend(_extract_raw_iterations(result, "root", observability))
    heartbeat_state["phase"] = "root_completion_received"
    contract_state = _extract_contract_from_repl(rlm)
    repair_attempts_used = 0

    while contract_state["issues"] and repair_attempts_used < max_repair_attempts:
        repair_attempts_used += 1
        heartbeat_state["repair_attempts_used"] = repair_attempts_used
        repair_prompt = _build_repair_prompt(contract_state["issues"])
        heartbeat_state["phase"] = f"repairing_{repair_attempts_used}"
        print(f"Starting repair LM turn {repair_attempts_used}...")
        repair_start = time.perf_counter()
        repair_result = rlm.completion(repair_prompt)
        final_result = repair_result
        repair_elapsed = time.perf_counter() - repair_start
        print(f"Repair LM turn {repair_attempts_used} returned in {repair_elapsed:.2f}s")
        response_text = _extract_response_text(repair_result)
        result_payload = _parse_result_payload(repair_result)
        raw_iterations.extend(
            _extract_raw_iterations(repair_result, f"repair_{repair_attempts_used}", observability)
        )
        contract_state = _extract_contract_from_repl(rlm)
        heartbeat_state["phase"] = "repair_completion_received"

    response_text = _extract_response_text(final_result)
    result_payload = _parse_result_payload(final_result)

    triage_results_payload = contract_state.get("triage_results")
    top_prs_payload = contract_state.get("top_prs")
    triage_summary_payload = contract_state.get("triage_summary")

    if contract_state["issues"] and not strict_repl_mode:
        if triage_results_payload is None:
            parsed_candidates = _find_eval_candidates(result_payload)
            triage_results_payload = parsed_candidates if parsed_candidates else None
        if triage_results_payload is None:
            fallback_payload = _extract_triage_results_from_repl(rlm)
            if fallback_payload is not None:
                triage_results_payload = fallback_payload

        if top_prs_payload is None and isinstance(triage_results_payload, list):
            scored = sorted(
                [item for item in triage_results_payload if isinstance(item, dict)],
                key=lambda item: _to_float(item.get("final_score", 0.0), 0.0),
                reverse=True,
            )
            top_prs_payload = scored[:150]

    output_bundle = {
        "triage_results": triage_results_payload,
        "top_prs": top_prs_payload,
        "triage_summary": triage_summary_payload,
        "raw_response": result_payload,
        "triage_bundle": contract_state.get("triage_bundle"),
        "debug": {
            "raw_iterations_path": str(raw_iterations_path),
            "heartbeat_path": str(heartbeat_path),
            "last_iteration_seen": int(raw_iterations[-1]["iteration"]) if raw_iterations else 0,
            "last_block_seen": len(raw_iterations[-1].get("code_blocks", [])) if raw_iterations else 0,
        },
        "contract_status": {
            "mode": contract_mode,
            "source": contract_state.get("source"),
            "repair_attempts_used": repair_attempts_used,
            "repair_attempts_max": max_repair_attempts,
            "issues": list(contract_state.get("issues", [])),
            "valid": len(contract_state.get("issues", [])) == 0,
        },
    }
    heartbeat_state["phase"] = "writing_local_artifacts"

    print("=" * 80)
    print("RLM RESULT:")
    print(response_text)

    # Save local backup first.
    trace_path.write_text(response_text)
    raw_iterations_payload = {
        "run_id": run_id,
        "prompt_hash": prompt_hash,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "iterations": raw_iterations,
    }
    _write_json_file(raw_iterations_path, raw_iterations_payload)

    try:
        if isinstance(output_bundle, (dict, list)):
            output_path.write_text(json.dumps(output_bundle, indent=2))
        else:
            output_path.write_text(str(output_bundle))
    except (json.JSONDecodeError, TypeError):
        output_path.write_text(str(final_result))

    print(f"\nResults saved to {output_path}")
    print(f"Agent trace saved to {trace_path}")
    print(f"Raw iteration trace saved to {raw_iterations_path}")
    trace_steps = _parse_trace_steps(response_text)

    if strict_repl_mode and contract_state["issues"]:
        issues_text = "; ".join(contract_state["issues"])
        heartbeat_state["phase"] = "failed_contract"
        if observability_enabled:
            _stop_heartbeat_thread(
                heartbeat_path=heartbeat_path,
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=start_time,
                state=heartbeat_state,
                stop_event=heartbeat_stop,
                thread=heartbeat_thread,
            )
        raise OutputContractError(
            "Output contract failed in strict_repl mode after "
            f"{repair_attempts_used} repair attempt(s): {issues_text}"
        )

    if not database_url:
        heartbeat_state["phase"] = "completed_local_only"
        if observability_enabled:
            _stop_heartbeat_thread(
                heartbeat_path=heartbeat_path,
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=start_time,
                state=heartbeat_state,
                stop_event=heartbeat_stop,
                thread=heartbeat_thread,
            )
        print("DATABASE_URL not set, skipping dashboard push.")
        return

    evaluations_raw = _to_list(triage_results_payload)
    evaluations = [_normalize_eval(item) for item in evaluations_raw if isinstance(item, dict)]
    top_prs_raw = _to_list(top_prs_payload)
    summary = _normalize_summary(
        triage_summary_payload if isinstance(triage_summary_payload, dict) else None,
        evaluations,
        top_prs_raw,
    )

    try:
        for evaluation in evaluations:
            push_evaluation(evaluation, run_id=run_id)
        push_summary(summary, run_id=run_id)
        print(f"Pushed {len(evaluations)} evaluations and summary to dashboard DB.")
    except Exception as exc:
        print(f"Dashboard push failed: {exc}. Local backup is still saved.")

    clusters = _build_clusters(evaluations)
    ranking = _build_ranking_from_top_prs(top_prs_raw) if top_prs_raw else _build_ranking(evaluations)

    try:
        push_clusters(clusters, run_id=run_id)
        push_ranking(ranking, run_id=run_id)
        print(
            f"Pushed {len(clusters)} clusters and {len(ranking.get('ranking', []))} ranked PRs to dashboard DB."
        )
    except Exception as exc:
        print(f"Dashboard cluster/ranking push failed: {exc}. Local backup is still saved.")

    try:
        push_trace(trace_steps, run_id=run_id)
        print(f"Pushed {len(trace_steps)} agent trace steps to dashboard DB.")
    except Exception as exc:
        print(f"Dashboard trace push failed: {exc}. Local backup is still saved.")

    end_time = datetime.now(timezone.utc)
    elapsed_seconds = max(0.0, (end_time - start_time).total_seconds())
    run_metadata.update(
        {
            "status": "completed",
            "end_time": end_time.isoformat(),
            "ended_at": end_time.isoformat(),
            "time_elapsed_seconds": round(elapsed_seconds, 2),
            "total_prs_seen": len(evaluations_raw),
            "total_prs_scored": len(evaluations),
        }
    )
    try:
        push_run_meta(run_metadata, run_id=run_id)
        push_run_event({"event": "completed", "ended_at": end_time.isoformat()}, run_id=run_id)
    except Exception as exc:
        print(f"Dashboard run-finalization push failed: {exc}.")

    heartbeat_state["phase"] = "completed"
    if observability_enabled:
        _stop_heartbeat_thread(
            heartbeat_path=heartbeat_path,
            run_id=run_id,
            prompt_hash=prompt_hash,
            started_at=start_time,
            state=heartbeat_state,
            stop_event=heartbeat_stop,
            thread=heartbeat_thread,
        )


if __name__ == "__main__":
    main()
