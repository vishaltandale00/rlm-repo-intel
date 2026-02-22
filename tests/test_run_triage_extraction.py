import sys
import types
from datetime import datetime, timezone

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
    _build_repair_prompt,
    _extract_contract_from_repl,
    _extract_named_repl_variables,
    _extract_raw_iterations,
    _heartbeat_snapshot,
    _observability_cfg,
    _output_contract_mode,
    _output_repair_attempts,
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
                        "urgency": 7.0,
                        "quality": 7.0,
                        "criticality": 7.0,
                        "risk_if_merged": 3.0,
                        "final_score": 8.0,
                        "justification": "reason",
                        "evidence": [],
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
