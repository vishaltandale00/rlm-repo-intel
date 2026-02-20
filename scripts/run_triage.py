#!/usr/bin/env python3
"""Run the RLM PR triage analysis."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_repo_intel.config import load_config
from rlm_repo_intel.dashboard_push import (
    push_clusters,
    push_evaluation,
    push_ranking,
    push_summary,
    push_trace,
)
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
        # Fallback for Python-style repr payloads returned from REPL variables.
        try:
            return ast.literal_eval(response_text)
        except (SyntaxError, ValueError):
            return response_text


def _looks_like_triage_payload(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if not all(isinstance(item, dict) for item in value):
        return False
    required = {"number", "pr_number", "title", "urgency", "quality", "state"}
    first_keys = set(value[0].keys())
    return bool(required & first_keys)


def _looks_like_top_prs_payload(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if not all(isinstance(item, dict) for item in value):
        return False
    required = {"pr_number", "number", "final_score", "elite_rank"}
    first_keys = set(value[0].keys())
    return bool(required & first_keys)


def _looks_like_summary_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "total_open_prs_seen",
        "phase1_candidates_count",
        "deep_analyzed_count",
        "scored_count",
        "elite_count",
    }
    return bool(required & set(value.keys()))


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


def _extract_repl_variable(rlm: Any, name: str, validator: Any | None = None) -> Any | None:
    for ns in _repl_namespaces(rlm):
        if name not in ns:
            continue
        value = ns[name]
        if validator is None or validator(value):
            return value
    return None


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


def main():
    config = load_config("rlm-repo-intel.yaml")
    print("Creating frontier RLM...")
    rlm = create_frontier_rlm(config)

    prompt = (
        "Execute a strict 4-phase PR triage pipeline with anti-shortcut enforcement. "
        "Phase 1 metadata only: get all open PRs, compute an interest score using changedFiles, additions+deletions, "
        "security or fix or breaking title terms, label count, and touches to auth or gateway or config or agents or security paths; "
        "sort descending, take top 300, and store as candidates. "
        "Phase 2 deep analysis: only for those 300 candidates, read each PR diff and changed files with repo context, assess behavior changes, tests, and break risks, "
        "write unique justification paragraphs with specific file references, and score urgency, quality, criticality, risk_if_merged as floats. "
        "For Phase 2, you MUST analyze each PR individually. Do NOT write a loop function that scores PRs in bulk. Instead, take batches of 10-20 PRs at a time, "
        "read each diff, reference specific files from the repo dict, and write unique justifications. If any PR has a generic justification without specific file references, the entire run is invalid. "
        "After each Phase 2 batch, call push_partial_results(scored_prs_list). "
        "Phase 3 scoring calibration: compute final_score = 0.35*urgency + 0.30*quality + 0.20*criticality + 0.15*(10 - risk_if_merged), "
        "force distribution so no more than 15 percent score above 9.0, then sort descending. "
        "Phase 4 elite curation: keep top 100-150 PRs with final_score >= 9.0, raise threshold if more than 150 pass. "
        "Store full results in triage_results, elite list in top_prs, and summary in triage_summary."
    )

    print(f"Running RLM with prompt: {prompt}")
    print("=" * 80)

    result = rlm.completion(prompt)
    response_text = _extract_response_text(result)
    result_payload = _parse_result_payload(result)

    repl_triage_results = _extract_repl_variable(rlm, "triage_results", _looks_like_triage_payload)
    repl_top_prs = _extract_repl_variable(rlm, "top_prs", _looks_like_top_prs_payload)
    repl_triage_summary = _extract_repl_variable(rlm, "triage_summary", _looks_like_summary_payload)

    triage_results_payload = repl_triage_results
    top_prs_payload = repl_top_prs
    triage_summary_payload = repl_triage_summary

    if isinstance(result_payload, dict):
        if triage_results_payload is None and _looks_like_triage_payload(result_payload.get("triage_results")):
            triage_results_payload = result_payload.get("triage_results")
        if top_prs_payload is None and _looks_like_top_prs_payload(result_payload.get("top_prs")):
            top_prs_payload = result_payload.get("top_prs")
        if triage_summary_payload is None and _looks_like_summary_payload(result_payload.get("triage_summary")):
            triage_summary_payload = result_payload.get("triage_summary")

    if triage_results_payload is None:
        fallback_payload = _extract_triage_results_from_repl(rlm)
        if fallback_payload is not None:
            triage_results_payload = fallback_payload

    if triage_results_payload is None:
        triage_results_payload = _find_eval_candidates(result_payload)

    if top_prs_payload is None and isinstance(triage_results_payload, list):
        scored = [item for item in triage_results_payload if isinstance(item, dict)]
        scored.sort(key=lambda item: _to_float(item.get("final_score", item.get("score", 0.0)), 0.0), reverse=True)
        top_prs_payload = scored[:150]

    output_bundle = {
        "triage_results": triage_results_payload,
        "top_prs": top_prs_payload,
        "triage_summary": triage_summary_payload,
        "raw_response": result_payload,
    }

    print("=" * 80)
    print("RLM RESULT:")
    print(response_text)

    # Save local backup first.
    output_path = Path(".rlm-repo-intel/results/triage.json")
    trace_path = Path(".rlm-repo-intel/results/agent_trace.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(response_text)

    try:
        if isinstance(output_bundle, (dict, list)):
            output_path.write_text(json.dumps(output_bundle, indent=2))
        else:
            output_path.write_text(str(output_bundle))
    except (json.JSONDecodeError, TypeError):
        output_path.write_text(str(result))

    print(f"\nResults saved to {output_path}")
    print(f"Agent trace saved to {trace_path}")
    trace_steps = _parse_trace_steps(response_text)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
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
            push_evaluation(evaluation)
        push_summary(summary)
        print(f"Pushed {len(evaluations)} evaluations and summary to dashboard DB.")
    except Exception as exc:
        print(f"Dashboard push failed: {exc}. Local backup is still saved.")

    clusters = _build_clusters(evaluations)
    ranking = _build_ranking_from_top_prs(top_prs_raw) if top_prs_raw else _build_ranking(evaluations)

    try:
        push_clusters(clusters)
        push_ranking(ranking)
        print(
            f"Pushed {len(clusters)} clusters and {len(ranking.get('ranking', []))} ranked PRs to dashboard DB."
        )
    except Exception as exc:
        print(f"Dashboard cluster/ranking push failed: {exc}. Local backup is still saved.")

    try:
        push_trace(trace_steps)
        print(f"Pushed {len(trace_steps)} agent trace steps to dashboard DB.")
    except Exception as exc:
        print(f"Dashboard trace push failed: {exc}. Local backup is still saved.")


if __name__ == "__main__":
    main()
