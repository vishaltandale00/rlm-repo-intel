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
