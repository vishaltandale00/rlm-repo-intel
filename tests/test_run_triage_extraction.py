import sys
import types
import json
from datetime import datetime, timezone
from pathlib import Path

if "litellm" not in sys.modules:
    async def _dummy_acompletion(**kwargs):  # pragma: no cover - test shim
        raise RuntimeError("not used")

    def _dummy_completion(**kwargs):  # pragma: no cover - test shim
        raise RuntimeError("not used")

    sys.modules["litellm"] = types.SimpleNamespace(
        completion=_dummy_completion,
        acompletion=_dummy_acompletion,
    )

from rlm_repo_intel.run_triage import (
    _build_clusters,
    _build_repair_prompt,
    _classify_liveness,
    _extract_contract_from_repl,
    _extract_named_repl_variables,
    _extract_raw_iterations,
    _extract_title_theme,
    _heartbeat_snapshot,
    _normalize_eval,
    _observability_cfg,
    _output_contract_mode,
    _output_repair_attempts,
    _run_artifact_paths,
    triage_status,
)


class FakeEnv:
    def __init__(self, locals_ns=None, namespace_ns=None, globals_ns=None):
        self.locals = locals_ns if locals_ns is not None else {}
        self.namespace = namespace_ns if namespace_ns is not None else {}
        self.globals = globals_ns if globals_ns is not None else {}


class FakeRLM:
    def __init__(self, env):
        self._persistent_env = env


def test_extract_named_repl_variables_prefers_locals():
    env = FakeEnv(
        locals_ns={
            "triage_results": [{"pr_number": 1, "title": "A", "urgency": 7.1, "quality": 6.8, "state": "open"}],
            "top_prs": [{"pr_number": 1, "final_score": 9.1, "elite_rank": 1}],
            "triage_summary": {"total_open_prs_seen": 1, "scored_count": 1, "elite_count": 1},
        },
        namespace_ns={
            "triage_results": [{"pr_number": 2, "title": "B", "urgency": 2.0, "quality": 2.0, "state": "open"}],
        },
    )
    rlm = FakeRLM(env)

    extracted = _extract_named_repl_variables(rlm)

    assert extracted["triage_results"][0]["pr_number"] == 1
    assert extracted["top_prs"][0]["pr_number"] == 1
    assert extracted["triage_summary"]["scored_count"] == 1


def test_extract_named_repl_variables_uses_namespace_when_locals_missing():
    env = FakeEnv(
        locals_ns={},
        namespace_ns={
            "triage_results": [{"pr_number": 5, "title": "X", "urgency": 5.0, "quality": 5.0, "state": "open"}],
            "top_prs": [{"pr_number": 5, "final_score": 8.0, "elite_rank": 3}],
            "triage_summary": {"total_open_prs_seen": 9, "scored_count": 4, "elite_count": 1},
        },
    )
    rlm = FakeRLM(env)

    extracted = _extract_named_repl_variables(rlm)

    assert extracted["triage_results"][0]["pr_number"] == 5
    assert extracted["top_prs"][0]["elite_rank"] == 3
    assert extracted["triage_summary"]["total_open_prs_seen"] == 9


def test_extract_contract_prefers_bundle_when_present():
    env = FakeEnv(
        locals_ns={
            "triage_bundle": {
                "triage_results": [
                    {
                        "pr_number": 10,
                        "title": "Bundle",
                        "author": "octocat",
                        "state": "ready",
                        "urgency": 7.0,
                        "quality": 7.0,
                        "criticality": 7.0,
                        "risk_if_merged": 3.0,
                        "final_score": 8.0,
                        "merge_recommendation": "merge_now",
                        "justification": "reason",
                        "key_risks": ["docs sync"],
                        "evidence": [],
                        "scoring_reasoning": {
                            "urgency": "Touches auth gating path.",
                            "quality": "Tests cover changed branch paths.",
                            "criticality": "Security-sensitive module path.",
                            "risk_if_merged": "Small blast radius, rollback ready.",
                        },
                    }
                ],
                "top_prs": [{"pr_number": 10, "final_score": 8.0}],
                "triage_summary": {
                    "total_open_prs_seen": 1,
                    "scored_count": 1,
                    "elite_count": 0,
                    "score_distribution": {"8-9": 1},
                },
            },
            "triage_results": [{"pr_number": 1, "title": "Named", "urgency": 5.0, "quality": 5.0, "state": "open"}],
        }
    )
    rlm = FakeRLM(env)

    contract = _extract_contract_from_repl(rlm)

    assert contract["source"] == "triage_bundle"
    assert contract["triage_results"][0]["pr_number"] == 10
    assert contract["issues"] == []


def test_extract_contract_reports_missing_fields():
    env = FakeEnv(
        locals_ns={
            "triage_results": [{"pr_number": 1}],
            "top_prs": [{"pr_number": 1}],
            "triage_summary": {"total_open_prs_seen": 1},
        }
    )
    rlm = FakeRLM(env)

    contract = _extract_contract_from_repl(rlm)

    assert contract["issues"]
    assert any("triage_summary missing required fields" in issue for issue in contract["issues"])


def test_extract_contract_requires_scoring_reasoning_keys():
    env = FakeEnv(
        locals_ns={
            "triage_results": [
                {
                    "pr_number": 1,
                    "title": "Needs reasoning fields",
                    "author": "dev",
                    "state": "ready",
                    "urgency": 7.0,
                    "quality": 6.0,
                    "criticality": 5.0,
                    "risk_if_merged": 3.0,
                    "final_score": 6.6,
                    "merge_recommendation": "merge_now",
                    "justification": "Grounded in diff evidence.",
                    "key_risks": ["edge behavior"],
                    "evidence": [],
                    "scoring_reasoning": {"urgency": "High review pressure."},
                }
            ],
            "top_prs": [{"pr_number": 1, "final_score": 6.6}],
            "triage_summary": {
                "total_open_prs_seen": 1,
                "scored_count": 1,
                "elite_count": 0,
                "score_distribution": {"6-7": 1},
            },
        }
    )
    rlm = FakeRLM(env)

    contract = _extract_contract_from_repl(rlm)

    assert contract["issues"]
    assert any("scoring_reasoning missing required keys" in issue for issue in contract["issues"])


def test_extract_contract_requires_must_fix_for_non_merge_now():
    env = FakeEnv(
        locals_ns={
            "triage_results": [
                {
                    "pr_number": 2,
                    "title": "Blocked without must-fix list",
                    "author": "dev",
                    "state": "ready",
                    "urgency": 8.0,
                    "quality": 4.0,
                    "criticality": 8.0,
                    "risk_if_merged": 8.0,
                    "final_score": 5.6,
                    "merge_recommendation": "block",
                    "justification": "Risk exceeds acceptable threshold.",
                    "key_risks": ["data corruption path"],
                    "evidence": [],
                    "scoring_reasoning": {
                        "urgency": "Production impact is immediate.",
                        "quality": "Low confidence in test coverage.",
                        "criticality": "Core write path is affected.",
                        "risk_if_merged": "Likely incident if shipped.",
                    },
                }
            ],
            "top_prs": [{"pr_number": 2, "final_score": 5.6}],
            "triage_summary": {
                "total_open_prs_seen": 1,
                "scored_count": 1,
                "elite_count": 0,
                "score_distribution": {"5-6": 1},
            },
        }
    )
    rlm = FakeRLM(env)

    contract = _extract_contract_from_repl(rlm)

    assert contract["issues"]
    assert any("must_fix_before_merge must be non-empty" in issue for issue in contract["issues"])


def test_normalize_eval_preserves_scoring_reasoning_without_fabrication():
    normalized_without_reasoning = _normalize_eval(
        {
            "pr_number": 11,
            "title": "No reasoning yet",
            "urgency": 6.0,
            "quality": 6.0,
            "criticality": 6.0,
            "risk_if_merged": 6.0,
            "final_score": 6.0,
        }
    )
    assert "scoring_reasoning" not in normalized_without_reasoning

    normalized_with_reasoning = _normalize_eval(
        {
            "pr_number": 12,
            "title": "Reasoning included",
            "urgency": 6.0,
            "quality": 6.0,
            "criticality": 6.0,
            "risk_if_merged": 6.0,
            "final_score": 6.0,
            "scoring_reasoning": {
                "urgency": "Hotfix urgency due to incoming release.",
                "quality": "Well-tested but not fuzzed.",
                "criticality": "Shared auth path touched.",
                "risk_if_merged": "Rollback path is straightforward.",
            },
        }
    )
    assert normalized_with_reasoning["scoring_reasoning"]["urgency"] == "Hotfix urgency due to incoming release."


def test_repair_prompt_mentions_final_var():
    prompt = _build_repair_prompt(["triage_results missing required fields"])
    assert "FINAL_VAR(\"triage_bundle\")" in prompt


def test_output_contract_mode_and_repair_attempts_defaults():
    config = {"pipeline": {}}
    assert _output_contract_mode(config) == "strict_repl"
    assert _output_repair_attempts(config) == 1


class _FakeCompletion:
    def __init__(self, metadata):
        self.metadata = metadata


def test_extract_raw_iterations_from_logger_metadata():
    result = _FakeCompletion(
        {
            "iterations": [
                {
                    "iteration": 1,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "iteration_time": 1.25,
                    "response": "hello",
                    "code_blocks": [
                        {
                            "code": "print('x')",
                            "result": {
                                "execution_time": 0.35,
                                "stdout": "x\n",
                                "stderr": "",
                                "final_answer": None,
                            },
                        }
                    ],
                }
            ]
        }
    )
    obs = _observability_cfg({"pipeline": {}})
    rows = _extract_raw_iterations(result, "root", obs)

    assert len(rows) == 1
    assert rows[0]["completion_label"] == "root"
    assert rows[0]["iteration"] == 1
    assert rows[0]["code_blocks"][0]["execution_time_seconds"] == 0.35
    assert rows[0]["code_blocks"][0]["stdout_preview"] == "x\n"


def test_heartbeat_snapshot_uses_last_iteration_and_block():
    rows = [
        {"iteration": 1, "code_blocks": []},
        {"iteration": 2, "code_blocks": [{"index": 1}, {"index": 2}]},
    ]
    snapshot = _heartbeat_snapshot(
        run_id="run-1",
        prompt_hash="hash-1",
        started_at=datetime.now(timezone.utc),
        phase="iterating",
        repair_attempts_used=1,
        raw_iterations=rows,
    )

    assert snapshot["last_iteration_seen"] == 2
    assert snapshot["last_block_seen"] == 2


def test_heartbeat_snapshot_includes_liveness_payload():
    started_at = datetime.now(timezone.utc)
    liveness = {
        "last_progress_at": started_at.isoformat(),
        "lm": {"calls_started": 1, "calls_completed": 1, "calls_failed": 0, "calls_in_flight": 0},
        "subcalls": {"started": 1, "completed": 1, "in_flight": 0, "oldest_in_flight_seconds": 0.0},
        "network": {"samples_collected": 1, "established_connections": 0, "bytes_sent_delta": 0, "bytes_recv_delta": 0},
    }
    snapshot = _heartbeat_snapshot(
        run_id="run-1",
        prompt_hash="hash-1",
        started_at=started_at,
        phase="waiting_first_response",
        phase_entered_at=started_at.isoformat(),
        repair_attempts_used=0,
        raw_iterations=[],
        liveness=liveness,
        stall_threshold_seconds=300,
    )

    assert snapshot["liveness"]["classification"] in {"actively_reasoning", "idle"}
    assert "lm" in snapshot["liveness"]
    assert "network" in snapshot["liveness"]


def test_classify_liveness_identifies_waiting_and_stall():
    now = datetime.now(timezone.utc)
    liveness_waiting = {
        "last_progress_at": now.isoformat(),
        "lm": {"calls_in_flight": 1, "calls_completed": 0},
        "subcalls": {"in_flight": 0, "completed": 0},
        "network": {"established_connections": 1, "bytes_sent_delta": 0, "bytes_recv_delta": 0},
    }
    assert _classify_liveness("waiting_first_response", liveness_waiting, now, 300) == "waiting_on_provider"

    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    liveness_stalled = {
        "last_progress_at": old.isoformat(),
        "lm": {"calls_in_flight": 1, "calls_completed": 0},
        "subcalls": {"in_flight": 0, "completed": 0},
        "network": {"established_connections": 0, "bytes_sent_delta": 0, "bytes_recv_delta": 0},
    }
    assert _classify_liveness("waiting_first_response", liveness_stalled, now, 300) == "suspected_stall"


def test_run_artifact_paths_creates_run_dir_and_latest(tmp_path):
    paths = _run_artifact_paths(tmp_path, "run-abc")

    assert Path(paths["run_dir"]).exists()
    latest = tmp_path / "latest_run_id"
    assert latest.exists()
    assert latest.read_text().strip() == "run-abc"
    assert str(paths["heartbeat_path"]).endswith("runs/run-abc/run_heartbeat.json")


def test_triage_status_reads_latest_run_heartbeat(tmp_path):
    runs_dir = tmp_path / "runs" / "run-xyz"
    runs_dir.mkdir(parents=True)
    (tmp_path / "latest_run_id").write_text("run-xyz\n")
    heartbeat = {
        "run_id": "run-xyz",
        "phase": "waiting_first_response",
        "elapsed_seconds": 12.0,
        "last_iteration_seen": 0,
        "last_block_seen": 0,
        "liveness": {"classification": "waiting_on_provider"},
    }
    (runs_dir / "run_heartbeat.json").write_text(json.dumps(heartbeat))

    status = triage_status(config={"paths": {"results_dir": str(tmp_path)}}, run_id=None)

    assert status["run_id"] == "run-xyz"
    assert status["classification"] == "waiting_on_provider"
    assert status["exit_code"] == 2


def test_extract_title_theme_handles_conventional_and_fallback_patterns():
    assert _extract_title_theme("fix(gateway): auth update") == "fix(gateway)"
    assert _extract_title_theme("feat: gateway/auth-rate-limit") == "feat(gateway)"
    assert _extract_title_theme("chore - gateway/tooling") == "chore(gateway)"
    assert _extract_title_theme("unstructuredtitle") is None


def test_build_clusters_uses_title_theme_without_pattern_errors():
    evaluations = [
        {
            "pr_number": 1,
            "title": "feat: gateway/auth-rate-limit",
            "labels": [],
            "impact_scope": [],
        },
        {
            "pr_number": 2,
            "title": "feat: gateway/logging",
            "labels": [],
            "impact_scope": [],
        },
    ]

    clusters = _build_clusters(evaluations)

    assert clusters
    assert any(cluster["size"] == 2 for cluster in clusters)
