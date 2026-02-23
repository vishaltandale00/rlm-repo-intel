#!/usr/bin/env python3
"""Run the RLM PR triage analysis."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
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
from rlm_repo_intel.tools.dashboard_callback import get_partial_progress

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


class OutputContractError(RuntimeError):
    """Raised when required REPL output variables are missing or invalid."""


_TRIAGE_RESULT_REQUIRED_FIELDS = {
    "pr_number",
    "title",
    "author",
    "state",
    "urgency",
    "quality",
    "criticality",
    "risk_if_merged",
    "final_score",
    "merge_recommendation",
    "justification",
    "key_risks",
    "evidence",
    "scoring_reasoning",
}
_SCORING_REASONING_REQUIRED_FIELDS = {
    "urgency",
    "quality",
    "criticality",
    "risk_if_merged",
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
            scoring_reasoning = item.get("scoring_reasoning")
            if not isinstance(scoring_reasoning, dict):
                issues.append(
                    f"triage_results[{idx}].scoring_reasoning must be a dict with per-score rationale"
                )
                break
            missing_reasoning = [
                key
                for key in sorted(_SCORING_REASONING_REQUIRED_FIELDS)
                if not str(scoring_reasoning.get(key, "")).strip()
            ]
            if missing_reasoning:
                issues.append(
                    f"triage_results[{idx}].scoring_reasoning missing required keys: "
                    + ", ".join(missing_reasoning)
                )
                break
            merge_recommendation = str(item.get("merge_recommendation", "")).strip().lower()
            if merge_recommendation != "merge_now":
                must_fix_before_merge = [str(value).strip() for value in _to_list(item.get("must_fix_before_merge"))]
                if not any(must_fix_before_merge):
                    issues.append(
                        f"triage_results[{idx}].must_fix_before_merge must be non-empty when "
                        "merge_recommendation is not merge_now"
                    )
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


def _write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _append_jsonl_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")


def _run_artifact_paths(results_dir: Path, run_id: str) -> dict[str, Any]:
    run_dir = results_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_run_id_path = results_dir / "latest_run_id"
    _write_text_file(latest_run_id_path, run_id + "\n")
    return {
        "run_dir": run_dir,
        "latest_run_id_path": latest_run_id_path,
        "output_path": run_dir / "triage.json",
        "trace_path": run_dir / "agent_trace.txt",
        "raw_iterations_path": run_dir / "raw_iterations.json",
        "heartbeat_path": run_dir / "run_heartbeat.json",
        "events_path": run_dir / "run_events.jsonl",
        "legacy_output_path": results_dir / "triage.json",
        "legacy_trace_path": results_dir / "agent_trace.txt",
        "legacy_raw_iterations_path": results_dir / "raw_iterations.json",
        "legacy_heartbeat_path": results_dir / "run_heartbeat.json",
    }


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds_since(value: str | None, now: datetime) -> float:
    parsed = _parse_iso8601(value)
    if parsed is None:
        return 0.0
    return max(0.0, (now - parsed).total_seconds())


def _new_liveness_state(started_at: datetime) -> dict[str, Any]:
    started_iso = started_at.isoformat()
    return {
        "classification": "idle",
        "last_progress_at": started_iso,
        "seconds_since_progress": 0.0,
        "lm": {
            "calls_started": 0,
            "calls_completed": 0,
            "calls_failed": 0,
            "calls_in_flight": 0,
            "timeouts": 0,
            "retries": 0,
            "last_call_started_at": None,
            "last_call_completed_at": None,
            "last_call_duration_ms": None,
            "total_call_time_ms": 0,
        },
        "subcalls": {
            "started": 0,
            "completed": 0,
            "in_flight": 0,
            "oldest_in_flight_seconds": 0.0,
        },
        "network": {
            "samples_collected": 0,
            "established_connections": 0,
            "bytes_sent_delta": 0,
            "bytes_recv_delta": 0,
            "last_io_at": None,
        },
        "_subcall_started_at": [],
        "_network_prev": None,
    }


def _mark_phase(state: dict[str, Any], phase: str) -> None:
    state["phase"] = phase
    state["phase_entered_at"] = datetime.now(timezone.utc).isoformat()


def _note_progress(liveness: dict[str, Any], when: str | None = None) -> None:
    liveness["last_progress_at"] = when or datetime.now(timezone.utc).isoformat()


def _sample_network_activity(pid: int, previous: dict[str, Any] | None) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    established_connections = 0
    bytes_sent = None
    bytes_recv = None

    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            connections = proc.connections(kind="tcp")
            established_connections = sum(1 for conn in connections if conn.status == "ESTABLISHED")
        except Exception:
            established_connections = 0
        try:
            global_io = psutil.net_io_counters()
            if global_io is not None:
                bytes_sent = int(getattr(global_io, "bytes_sent", 0))
                bytes_recv = int(getattr(global_io, "bytes_recv", 0))
        except Exception:
            bytes_sent = None
            bytes_recv = None
    else:
        try:
            result = subprocess.run(
                ["lsof", "-nP", "-p", str(pid), "-iTCP", "-sTCP:ESTABLISHED"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            established_connections = max(0, len(lines) - 1) if result.returncode == 0 else 0
        except Exception:
            established_connections = 0

    prev_sent = previous.get("bytes_sent") if isinstance(previous, dict) else None
    prev_recv = previous.get("bytes_recv") if isinstance(previous, dict) else None
    bytes_sent_delta = (
        max(0, int(bytes_sent - prev_sent))
        if isinstance(bytes_sent, int) and isinstance(prev_sent, int)
        else 0
    )
    bytes_recv_delta = (
        max(0, int(bytes_recv - prev_recv))
        if isinstance(bytes_recv, int) and isinstance(prev_recv, int)
        else 0
    )
    return {
        "timestamp": now_iso,
        "established_connections": int(established_connections),
        "bytes_sent": bytes_sent,
        "bytes_recv": bytes_recv,
        "bytes_sent_delta": int(bytes_sent_delta),
        "bytes_recv_delta": int(bytes_recv_delta),
    }


def _classify_liveness(
    phase: str,
    liveness: dict[str, Any],
    now: datetime,
    stall_threshold_seconds: float,
) -> str:
    phase_value = str(phase or "").lower()
    if phase_value in {"completed", "completed_local_only"}:
        return "completed"
    if phase_value.startswith("failed"):
        return "failed"

    lm = liveness.get("lm", {})
    subcalls = liveness.get("subcalls", {})
    network = liveness.get("network", {})

    calls_in_flight = int(lm.get("calls_in_flight", 0))
    subcalls_in_flight = int(subcalls.get("in_flight", 0))
    established_connections = int(network.get("established_connections", 0))
    bytes_sent_delta = int(network.get("bytes_sent_delta", 0))
    bytes_recv_delta = int(network.get("bytes_recv_delta", 0))
    seconds_since_progress = _seconds_since(str(liveness.get("last_progress_at", "")), now)

    if seconds_since_progress <= 90 and (
        int(lm.get("calls_completed", 0)) > 0
        or int(subcalls.get("completed", 0)) > 0
        or bytes_sent_delta > 0
        or bytes_recv_delta > 0
    ):
        return "actively_reasoning"

    if calls_in_flight > 0 or subcalls_in_flight > 0:
        if seconds_since_progress >= stall_threshold_seconds:
            return "suspected_stall"
        if established_connections > 0 or bytes_sent_delta > 0 or bytes_recv_delta > 0:
            return "waiting_on_provider"
        return "waiting_on_provider"

    if seconds_since_progress >= stall_threshold_seconds:
        return "suspected_stall"
    if phase_value in {"starting", "writing_local_artifacts"}:
        return "actively_reasoning"
    return "idle"


def _heartbeat_snapshot(
    run_id: str,
    prompt_hash: str,
    started_at: datetime,
    phase: str,
    repair_attempts_used: int,
    raw_iterations: list[dict[str, Any]],
    phase_entered_at: str | None = None,
    liveness: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    stall_threshold_seconds: float = 300.0,
    rlm: Any | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    elapsed = max(0.0, (now - started_at).total_seconds())
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
    liveness_payload: dict[str, Any] = {}
    if isinstance(liveness, dict):
        classification = _classify_liveness(
            phase=phase,
            liveness=liveness,
            now=now,
            stall_threshold_seconds=stall_threshold_seconds,
        )
        liveness_payload = {
            "classification": classification,
            "last_progress_at": liveness.get("last_progress_at"),
            "seconds_since_progress": round(
                _seconds_since(str(liveness.get("last_progress_at", "")), now),
                2,
            ),
            "last_error_type": liveness.get("last_error_type"),
            "last_error_message": liveness.get("last_error_message"),
            "lm": dict(liveness.get("lm", {})),
            "subcalls": dict(liveness.get("subcalls", {})),
            "network": {
                "samples_collected": int(liveness.get("network", {}).get("samples_collected", 0)),
                "established_connections": int(
                    liveness.get("network", {}).get("established_connections", 0)
                ),
                "bytes_sent_delta": int(liveness.get("network", {}).get("bytes_sent_delta", 0)),
                "bytes_recv_delta": int(liveness.get("network", {}).get("bytes_recv_delta", 0)),
                "last_io_at": liveness.get("network", {}).get("last_io_at"),
            },
        }
    return {
        "timestamp": now.isoformat(),
        "run_id": run_id,
        "prompt_hash": prompt_hash,
        "phase": phase,
        "phase_entered_at": phase_entered_at,
        "phase_elapsed_seconds": round(_seconds_since(phase_entered_at, now), 2),
        "elapsed_seconds": round(elapsed, 2),
        "repair_attempts_used": repair_attempts_used,
        "last_iteration_seen": last_iteration_seen,
        "last_block_seen": last_block_seen,
        "progress": dict(progress or {}),
        "liveness": liveness_payload,
    }


def _start_heartbeat_thread(
    heartbeat_paths: tuple[Path, ...],
    run_id: str,
    prompt_hash: str,
    started_at: datetime,
    interval_seconds: int,
    state: dict[str, Any],
    state_lock: threading.Lock,
    stall_threshold_seconds: float,
    run_events_path: Path,
    rlm: Any | None,
    stop_event: threading.Event,
) -> threading.Thread:
    interval = max(1, int(interval_seconds))
    pid = os.getpid()

    def _loop() -> None:
        while not stop_event.is_set():
            with state_lock:
                liveness = state.get("liveness")
                if isinstance(liveness, dict):
                    subcalls = liveness.get("subcalls")
                    starts = liveness.get("_subcall_started_at")
                    if isinstance(subcalls, dict) and isinstance(starts, list):
                        if starts:
                            oldest_seconds = max(0.0, _seconds_since(str(starts[0]), datetime.now(timezone.utc)))
                        else:
                            oldest_seconds = 0.0
                        subcalls["oldest_in_flight_seconds"] = round(oldest_seconds, 2)
                    network = liveness.get("network")
                    if isinstance(network, dict):
                        sample = _sample_network_activity(
                            pid=pid,
                            previous=liveness.get("_network_prev"),
                        )
                        network["samples_collected"] = int(network.get("samples_collected", 0)) + 1
                        network["established_connections"] = int(sample["established_connections"])
                        network["bytes_sent_delta"] = int(sample["bytes_sent_delta"])
                        network["bytes_recv_delta"] = int(sample["bytes_recv_delta"])
                        if sample["bytes_sent_delta"] > 0 or sample["bytes_recv_delta"] > 0:
                            network["last_io_at"] = sample["timestamp"]
                            _note_progress(liveness, sample["timestamp"])
                        liveness["_network_prev"] = sample
                state["progress"] = get_partial_progress()
                snapshot = _heartbeat_snapshot(
                    run_id=run_id,
                    prompt_hash=prompt_hash,
                    started_at=started_at,
                    phase=str(state.get("phase", "unknown")),
                    phase_entered_at=state.get("phase_entered_at"),
                    repair_attempts_used=int(state.get("repair_attempts_used", 0)),
                    raw_iterations=list(state.get("raw_iterations", [])),
                    liveness=dict(state.get("liveness", {})),
                    progress=dict(state.get("progress", {})),
                    stall_threshold_seconds=stall_threshold_seconds,
                    rlm=rlm,
                )
            for heartbeat_path in heartbeat_paths:
                try:
                    _write_json_file(heartbeat_path, snapshot)
                except OSError:
                    pass
            if stop_event.wait(interval):
                break

    thread = threading.Thread(target=_loop, name="triage-heartbeat", daemon=True)
    thread.start()
    _append_jsonl_event(
        run_events_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "event": "heartbeat_started",
        },
    )
    return thread


def _stop_heartbeat_thread(
    heartbeat_paths: tuple[Path, ...],
    run_id: str,
    prompt_hash: str,
    started_at: datetime,
    state: dict[str, Any],
    state_lock: threading.Lock,
    stall_threshold_seconds: float,
    run_events_path: Path,
    stop_event: threading.Event,
    thread: threading.Thread | None,
) -> None:
    with state_lock:
        snapshot = _heartbeat_snapshot(
            run_id=run_id,
            prompt_hash=prompt_hash,
            started_at=started_at,
            phase=str(state.get("phase", "stopped")),
            phase_entered_at=state.get("phase_entered_at"),
            repair_attempts_used=int(state.get("repair_attempts_used", 0)),
            raw_iterations=list(state.get("raw_iterations", [])),
            liveness=dict(state.get("liveness", {})),
            progress=dict(state.get("progress", {})),
            stall_threshold_seconds=stall_threshold_seconds,
            rlm=None,
        )
    for heartbeat_path in heartbeat_paths:
        try:
            _write_json_file(heartbeat_path, snapshot)
        except OSError:
            pass
    stop_event.set()
    if thread is not None:
        thread.join(timeout=2)
    _append_jsonl_event(
        run_events_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "event": "heartbeat_stopped",
        },
    )


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

    fallback = re.match(r"^([a-z]+)[:\s_/-]+([a-z0-9_/-]+)", text)
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


def _normalize_scoring_reasoning(raw_scoring_reasoning: Any) -> dict[str, str] | None:
    if not isinstance(raw_scoring_reasoning, dict):
        return None
    normalized: dict[str, str] = {}
    for key, value in raw_scoring_reasoning.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[str(key)] = text
    return normalized


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
    scoring_reasoning_raw = raw.get("scoring_reasoning")
    scoring_reasoning = _normalize_scoring_reasoning(scoring_reasoning_raw)

    # Do not invent model reasoning here; normalization is shape/type canonicalization only.
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

    if scoring_reasoning is not None:
        normalized["scoring_reasoning"] = scoring_reasoning
    elif scoring_reasoning_raw is not None:
        normalized["scoring_reasoning"] = scoring_reasoning_raw

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
    results_dir.mkdir(parents=True, exist_ok=True)

    observability = _observability_cfg(config)
    observability_enabled = bool(observability.get("enabled", True))

    pipeline_cfg = config.get("pipeline", {})
    lm_request_timeout = float(pipeline_cfg.get("lm_request_timeout_seconds", 900.0))
    lm_request_retries = int(pipeline_cfg.get("lm_request_retries", 2))
    stall_threshold_seconds = max(300.0, lm_request_timeout * max(1, lm_request_retries + 1))

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

    run_metadata["id"] = run_id
    artifacts = _run_artifact_paths(results_dir=results_dir, run_id=run_id)
    output_path: Path = artifacts["output_path"]
    trace_path: Path = artifacts["trace_path"]
    raw_iterations_path: Path = artifacts["raw_iterations_path"]
    heartbeat_path: Path = artifacts["heartbeat_path"]
    events_path: Path = artifacts["events_path"]
    legacy_output_path: Path = artifacts["legacy_output_path"]
    legacy_trace_path: Path = artifacts["legacy_trace_path"]
    legacy_raw_iterations_path: Path = artifacts["legacy_raw_iterations_path"]
    legacy_heartbeat_path: Path = artifacts["legacy_heartbeat_path"]
    heartbeat_paths = (heartbeat_path, legacy_heartbeat_path)

    _append_jsonl_event(
        events_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "event": "run_started",
            "prompt_hash": prompt_hash,
        },
    )

    raw_iterations: list[dict[str, Any]] = []
    state_lock = threading.Lock()
    heartbeat_state: dict[str, Any] = {
        "phase": "starting",
        "phase_entered_at": start_time.isoformat(),
        "repair_attempts_used": 0,
        "raw_iterations": raw_iterations,
        "liveness": _new_liveness_state(start_time),
        "progress": get_partial_progress(),
    }

    def _telemetry_lm_start(payload: dict[str, Any]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with state_lock:
            liveness = heartbeat_state.get("liveness", {})
            lm = liveness.get("lm", {})
            lm["calls_started"] = int(lm.get("calls_started", 0)) + 1
            lm["calls_in_flight"] = int(lm.get("calls_in_flight", 0)) + 1
            lm["last_call_started_at"] = now_iso
            lm["retries"] = int(lm.get("retries", 0)) + int(payload.get("num_retries", 0) or 0)

    def _telemetry_lm_success(payload: dict[str, Any]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        duration_ms = max(0, int(payload.get("duration_ms", 0) or 0))
        with state_lock:
            liveness = heartbeat_state.get("liveness", {})
            lm = liveness.get("lm", {})
            lm["calls_completed"] = int(lm.get("calls_completed", 0)) + 1
            lm["calls_in_flight"] = max(0, int(lm.get("calls_in_flight", 0)) - 1)
            lm["last_call_completed_at"] = now_iso
            lm["last_call_duration_ms"] = duration_ms
            lm["total_call_time_ms"] = int(lm.get("total_call_time_ms", 0)) + duration_ms
            _note_progress(liveness, now_iso)

    def _telemetry_lm_failure(payload: dict[str, Any]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        duration_ms = max(0, int(payload.get("duration_ms", 0) or 0))
        with state_lock:
            liveness = heartbeat_state.get("liveness", {})
            lm = liveness.get("lm", {})
            lm["calls_failed"] = int(lm.get("calls_failed", 0)) + 1
            lm["calls_in_flight"] = max(0, int(lm.get("calls_in_flight", 0)) - 1)
            lm["last_call_completed_at"] = now_iso
            lm["last_call_duration_ms"] = duration_ms
            lm["total_call_time_ms"] = int(lm.get("total_call_time_ms", 0)) + duration_ms
            if bool(payload.get("is_timeout", False)):
                lm["timeouts"] = int(lm.get("timeouts", 0)) + 1
            _note_progress(liveness, now_iso)

    def _telemetry_subcall_start(payload: dict[str, Any]) -> None:
        del payload
        now_iso = datetime.now(timezone.utc).isoformat()
        with state_lock:
            liveness = heartbeat_state.get("liveness", {})
            subcalls = liveness.get("subcalls", {})
            starts = liveness.get("_subcall_started_at", [])
            subcalls["started"] = int(subcalls.get("started", 0)) + 1
            subcalls["in_flight"] = int(subcalls.get("in_flight", 0)) + 1
            if isinstance(starts, list):
                starts.append(now_iso)

    def _telemetry_subcall_complete(payload: dict[str, Any]) -> None:
        del payload
        now_iso = datetime.now(timezone.utc).isoformat()
        with state_lock:
            liveness = heartbeat_state.get("liveness", {})
            subcalls = liveness.get("subcalls", {})
            starts = liveness.get("_subcall_started_at", [])
            subcalls["completed"] = int(subcalls.get("completed", 0)) + 1
            subcalls["in_flight"] = max(0, int(subcalls.get("in_flight", 0)) - 1)
            if isinstance(starts, list) and starts:
                starts.pop(0)
            _note_progress(liveness, now_iso)

    telemetry_hooks = {
        "lm_start": _telemetry_lm_start,
        "lm_success": _telemetry_lm_success,
        "lm_failure": _telemetry_lm_failure,
        "subcall_start": _telemetry_subcall_start,
        "subcall_complete": _telemetry_subcall_complete,
    }

    print("Creating frontier RLM...")
    rlm = create_frontier_rlm(config, run_id=run_id, telemetry_hooks=telemetry_hooks)
    prompt = TRIAGE_TASK_PROMPT

    print(f"Running RLM with prompt: {prompt}")
    print("=" * 80)

    contract_mode = _output_contract_mode(config)
    max_repair_attempts = _output_repair_attempts(config)
    strict_repl_mode = contract_mode == "strict_repl"
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if observability_enabled:
        heartbeat_thread = _start_heartbeat_thread(
            heartbeat_paths=heartbeat_paths,
            run_id=run_id,
            prompt_hash=prompt_hash,
            started_at=start_time,
            interval_seconds=int(observability.get("heartbeat_seconds", 10)),
            state=heartbeat_state,
            state_lock=state_lock,
            stall_threshold_seconds=stall_threshold_seconds,
            run_events_path=events_path,
            rlm=rlm,
            stop_event=heartbeat_stop,
        )

    with state_lock:
        _mark_phase(heartbeat_state, "waiting_first_response")
    _append_jsonl_event(
        events_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "event": "root_turn_started",
            "turn": 1,
        },
    )
    print("Starting root LM turn 1...")
    root_start = time.perf_counter()
    try:
        result = rlm.completion(prompt)
    except Exception as exc:
        with state_lock:
            _mark_phase(heartbeat_state, "failed_root_completion")
            liveness = heartbeat_state.get("liveness", {})
            liveness["last_error_type"] = type(exc).__name__
            liveness["last_error_message"] = str(exc)
        _append_jsonl_event(
            events_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "event": "run_failed",
                "phase": "failed_root_completion",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        if observability_enabled and not heartbeat_stop.is_set():
            _stop_heartbeat_thread(
                heartbeat_paths=heartbeat_paths,
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=start_time,
                state=heartbeat_state,
                state_lock=state_lock,
                stall_threshold_seconds=stall_threshold_seconds,
                run_events_path=events_path,
                stop_event=heartbeat_stop,
                thread=heartbeat_thread,
            )
        raise
    final_result = result
    root_elapsed = time.perf_counter() - root_start
    _append_jsonl_event(
        events_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "event": "root_turn_completed",
            "turn": 1,
            "duration_seconds": round(root_elapsed, 3),
        },
    )
    print(f"Root LM turn 1 returned in {root_elapsed:.2f}s")
    with state_lock:
        raw_iterations.extend(_extract_raw_iterations(result, "root", observability))
        _mark_phase(heartbeat_state, "root_completion_received")
        _note_progress(heartbeat_state.get("liveness", {}))
    contract_state = _extract_contract_from_repl(rlm)
    repair_attempts_used = 0

    while contract_state["issues"] and repair_attempts_used < max_repair_attempts:
        repair_attempts_used += 1
        with state_lock:
            heartbeat_state["repair_attempts_used"] = repair_attempts_used
        repair_prompt = _build_repair_prompt(contract_state["issues"])
        with state_lock:
            _mark_phase(heartbeat_state, f"repairing_{repair_attempts_used}")
        print(f"Starting repair LM turn {repair_attempts_used}...")
        repair_start = time.perf_counter()
        repair_result = rlm.completion(repair_prompt)
        final_result = repair_result
        repair_elapsed = time.perf_counter() - repair_start
        print(f"Repair LM turn {repair_attempts_used} returned in {repair_elapsed:.2f}s")
        response_text = _extract_response_text(repair_result)
        result_payload = _parse_result_payload(repair_result)
        with state_lock:
            raw_iterations.extend(
                _extract_raw_iterations(repair_result, f"repair_{repair_attempts_used}", observability)
            )
            _note_progress(heartbeat_state.get("liveness", {}))
        contract_state = _extract_contract_from_repl(rlm)
        with state_lock:
            _mark_phase(heartbeat_state, "repair_completion_received")

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
    with state_lock:
        _mark_phase(heartbeat_state, "writing_local_artifacts")

    print("=" * 80)
    print("RLM RESULT:")
    print(response_text)

    # Save local backup first.
    _write_text_file(trace_path, response_text)
    _write_text_file(legacy_trace_path, response_text)
    raw_iterations_payload = {
        "run_id": run_id,
        "prompt_hash": prompt_hash,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "iterations": raw_iterations,
    }
    _write_json_file(raw_iterations_path, raw_iterations_payload)
    _write_json_file(legacy_raw_iterations_path, raw_iterations_payload)

    try:
        if isinstance(output_bundle, (dict, list)):
            _write_json_file(output_path, output_bundle)
            _write_json_file(legacy_output_path, output_bundle)
        else:
            _write_text_file(output_path, str(output_bundle))
            _write_text_file(legacy_output_path, str(output_bundle))
    except (json.JSONDecodeError, TypeError):
        _write_text_file(output_path, str(final_result))
        _write_text_file(legacy_output_path, str(final_result))

    print(f"\nResults saved to {output_path}")
    print(f"Agent trace saved to {trace_path}")
    print(f"Raw iteration trace saved to {raw_iterations_path}")
    trace_steps = _parse_trace_steps(response_text)

    if strict_repl_mode and contract_state["issues"]:
        issues_text = "; ".join(contract_state["issues"])
        with state_lock:
            _mark_phase(heartbeat_state, "failed_contract")
        if observability_enabled:
            _stop_heartbeat_thread(
                heartbeat_paths=heartbeat_paths,
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=start_time,
                state=heartbeat_state,
                state_lock=state_lock,
                stall_threshold_seconds=stall_threshold_seconds,
                run_events_path=events_path,
                stop_event=heartbeat_stop,
                thread=heartbeat_thread,
            )
        raise OutputContractError(
            "Output contract failed in strict_repl mode after "
            f"{repair_attempts_used} repair attempt(s): {issues_text}"
        )

    if not database_url:
        with state_lock:
            _mark_phase(heartbeat_state, "completed_local_only")
            _note_progress(heartbeat_state.get("liveness", {}))
        if observability_enabled:
            _stop_heartbeat_thread(
                heartbeat_paths=heartbeat_paths,
                run_id=run_id,
                prompt_hash=prompt_hash,
                started_at=start_time,
                state=heartbeat_state,
                state_lock=state_lock,
                stall_threshold_seconds=stall_threshold_seconds,
                run_events_path=events_path,
                stop_event=heartbeat_stop,
                thread=heartbeat_thread,
            )
        _append_jsonl_event(
            events_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "event": "run_completed_local_only",
            },
        )
        print("DATABASE_URL not set, skipping dashboard push.")
        return

    with state_lock:
        _mark_phase(heartbeat_state, "pushing_dashboard")
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
        with state_lock:
            _note_progress(heartbeat_state.get("liveness", {}))
        print(f"Pushed {len(evaluations)} evaluations and summary to dashboard DB.")
    except Exception as exc:
        print(f"Dashboard push failed: {exc}. Local backup is still saved.")

    clusters = _build_clusters(evaluations)
    ranking = _build_ranking_from_top_prs(top_prs_raw) if top_prs_raw else _build_ranking(evaluations)

    try:
        push_clusters(clusters, run_id=run_id)
        push_ranking(ranking, run_id=run_id)
        with state_lock:
            _note_progress(heartbeat_state.get("liveness", {}))
        print(
            f"Pushed {len(clusters)} clusters and {len(ranking.get('ranking', []))} ranked PRs to dashboard DB."
        )
    except Exception as exc:
        print(f"Dashboard cluster/ranking push failed: {exc}. Local backup is still saved.")

    try:
        push_trace(trace_steps, run_id=run_id)
        with state_lock:
            _note_progress(heartbeat_state.get("liveness", {}))
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

    with state_lock:
        _mark_phase(heartbeat_state, "completed")
        _note_progress(heartbeat_state.get("liveness", {}))
    if observability_enabled:
        _stop_heartbeat_thread(
            heartbeat_paths=heartbeat_paths,
            run_id=run_id,
            prompt_hash=prompt_hash,
            started_at=start_time,
            state=heartbeat_state,
            state_lock=state_lock,
            stall_threshold_seconds=stall_threshold_seconds,
            run_events_path=events_path,
            stop_event=heartbeat_stop,
            thread=heartbeat_thread,
        )
    _append_jsonl_event(
        events_path,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "event": "run_completed",
            "total_prs_seen": len(evaluations_raw),
            "total_prs_scored": len(evaluations),
        },
    )


def _resolve_latest_run_id(results_dir: Path) -> str | None:
    latest_run_path = results_dir / "latest_run_id"
    if latest_run_path.exists():
        latest = latest_run_path.read_text().strip()
        if latest:
            return latest
    legacy_heartbeat_path = results_dir / "run_heartbeat.json"
    if legacy_heartbeat_path.exists():
        try:
            payload = json.loads(legacy_heartbeat_path.read_text())
        except json.JSONDecodeError:
            return None
        run_id = str(payload.get("run_id", "")).strip()
        return run_id or None
    return None


def triage_status(config: dict[str, Any] | None = None, run_id: str | None = None) -> dict[str, Any]:
    if config is None:
        config = load_config("rlm-repo-intel.yaml")
    results_dir = Path(config["paths"]["results_dir"])
    selected_run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None
    if not selected_run_id:
        selected_run_id = _resolve_latest_run_id(results_dir)
    if not selected_run_id:
        return {
            "run_id": None,
            "status": "unavailable",
            "message": "No run ID found. Start triage first.",
            "exit_code": 5,
        }

    run_heartbeat_path = results_dir / "runs" / selected_run_id / "run_heartbeat.json"
    legacy_heartbeat_path = results_dir / "run_heartbeat.json"
    heartbeat_path = run_heartbeat_path if run_heartbeat_path.exists() else legacy_heartbeat_path
    if not heartbeat_path.exists():
        return {
            "run_id": selected_run_id,
            "status": "unavailable",
            "message": f"Heartbeat not found for run {selected_run_id}",
            "heartbeat_path": str(heartbeat_path),
            "exit_code": 5,
        }

    try:
        heartbeat = json.loads(heartbeat_path.read_text())
    except json.JSONDecodeError:
        return {
            "run_id": selected_run_id,
            "status": "unavailable",
            "message": f"Heartbeat is invalid JSON at {heartbeat_path}",
            "heartbeat_path": str(heartbeat_path),
            "exit_code": 5,
        }

    phase = str(heartbeat.get("phase", "unknown"))
    liveness = heartbeat.get("liveness", {}) if isinstance(heartbeat.get("liveness"), dict) else {}
    classification = str(liveness.get("classification", "idle"))
    recommendations = {
        "completed": "Run completed successfully.",
        "failed": "Run failed. Check trace and raw iterations for error context.",
        "suspected_stall": "Likely stalled. Inspect provider/network logs or restart run.",
        "waiting_on_provider": "Still waiting on upstream provider response.",
        "actively_reasoning": "Run is actively making progress.",
        "idle": "Run is alive but currently idle.",
    }
    exit_code_map = {
        "completed": 0,
        "failed": 4,
        "suspected_stall": 3,
    }
    return {
        "run_id": selected_run_id,
        "phase": phase,
        "classification": classification,
        "elapsed_seconds": heartbeat.get("elapsed_seconds"),
        "last_iteration_seen": heartbeat.get("last_iteration_seen"),
        "last_block_seen": heartbeat.get("last_block_seen"),
        "heartbeat_path": str(heartbeat_path),
        "recommendation": recommendations.get(classification, "Run status is available."),
        "heartbeat": heartbeat,
        "exit_code": int(exit_code_map.get(classification, 2)),
    }


if __name__ == "__main__":
    main()
