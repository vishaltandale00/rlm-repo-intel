import json

from rlm_repo_intel.tools import dashboard_callback


def test_push_trace_step_accepts_extended_types_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_callback, "_TRACE_BACKUP_PATH", tmp_path / "trace.json")
    monkeypatch.setattr(dashboard_callback, "_push_or_log", lambda name, fn, *args: None)

    dashboard_callback.reset_run_state()

    dashboard_callback.push_trace_step(3, "subcall_start", "start")
    dashboard_callback.push_trace_step(4, "subcall_complete", "done")
    dashboard_callback.push_trace_step(5, "iteration_complete", "iter")
    dashboard_callback.push_trace_step(6, "unknown_event", "fallback")

    payload = json.loads((tmp_path / "trace.json").read_text())
    assert payload[0]["type"] == "subcall_start"
    assert payload[1]["type"] == "subcall_complete"
    assert payload[2]["type"] == "iteration_complete"
    assert payload[3]["type"] == "llm_response"


def test_push_partial_results_dedupes_pr_evaluation_pushes(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_callback, "_RESULTS_BACKUP_PATH", tmp_path / "evals.json")
    monkeypatch.setattr(dashboard_callback, "_SUMMARY_BACKUP_PATH", tmp_path / "summary.json")
    calls = []

    def _fake_push(name, fn, *args):
        del fn
        calls.append((name, args))

    monkeypatch.setattr(dashboard_callback, "_push_or_log", _fake_push)
    dashboard_callback.reset_run_state()

    dashboard_callback.push_partial_results(
        [{"pr_number": 123, "title": "A", "urgency": 8.0, "quality": 8.0}]
    )
    dashboard_callback.push_partial_results(
        [{"pr_number": 123, "title": "A-updated", "urgency": 8.5, "quality": 8.5}]
    )

    eval_pushes = [call for call in calls if call[0] == "evaluation"]
    summary_pushes = [call for call in calls if call[0] == "summary"]
    assert len(eval_pushes) == 1
    assert len(summary_pushes) == 2

    progress = dashboard_callback.get_partial_progress()
    assert progress["partial_push_count"] == 1
    assert progress["partial_unique_prs"] == 1


def test_push_partial_results_preserves_scoring_reasoning(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_callback, "_RESULTS_BACKUP_PATH", tmp_path / "evals.json")
    monkeypatch.setattr(dashboard_callback, "_SUMMARY_BACKUP_PATH", tmp_path / "summary.json")
    calls = []

    def _fake_push(name, fn, *args):
        del fn
        calls.append((name, args))

    monkeypatch.setattr(dashboard_callback, "_push_or_log", _fake_push)
    dashboard_callback.reset_run_state()

    dashboard_callback.push_partial_results(
        [
            {
                "pr_number": 77,
                "title": "Reasoning",
                "urgency": 7.5,
                "quality": 6.5,
                "scoring_reasoning": {
                    "urgency": "Release train deadline.",
                    "quality": "Integration tests are partial.",
                },
            }
        ]
    )

    eval_pushes = [call for call in calls if call[0] == "evaluation"]
    assert len(eval_pushes) == 1
    payload = eval_pushes[0][1][0]
    assert payload["scoring_reasoning"]["urgency"] == "Release train deadline."
    assert payload["scoring_reasoning"]["quality"] == "Integration tests are partial."
