import json
import sys
import types

if "litellm" not in sys.modules:
    async def _dummy_acompletion(**kwargs):  # pragma: no cover - test shim
        raise RuntimeError("not used")

    def _dummy_completion(**kwargs):  # pragma: no cover - test shim
        raise RuntimeError("not used")

    sys.modules["litellm"] = types.SimpleNamespace(
        completion=_dummy_completion,
        acompletion=_dummy_acompletion,
    )

from rlm_repo_intel.pipeline import rlm_session


class FakeRLM:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _base_config(tmp_path):
    return {
        "repo": {"owner": "acme", "name": "widget"},
        "paths": {
            "repo_dir": str(tmp_path / "repo"),
            "data_dir": str(tmp_path / "data"),
            "graph_dir": str(tmp_path / "graph"),
            "results_dir": str(tmp_path / "results"),
        },
        "models": {"root": "claude-sonnet-4-6"},
        "pipeline": {
            "max_budget": 123.0,
            "max_timeout": 321.0,
            "max_errors": 4,
            "lm_request_timeout_seconds": 777.0,
            "lm_request_retries": 6,
            "max_depth": 5,
            "max_iterations": 6,
            "compaction_threshold_pct": 0.42,
        },
    }


def _patch_session_dependencies(monkeypatch):
    monkeypatch.setattr(rlm_session, "RLM", FakeRLM)
    monkeypatch.setattr(rlm_session, "load_repo_to_repl", lambda cfg: {"src/a.py": "print('x')"})
    monkeypatch.setattr(rlm_session, "build_repo_tree", lambda repo: "src/\n  a.py")
    monkeypatch.setattr(rlm_session, "load_prs", lambda cfg: [{"number": 1, "state": "open"}])
    monkeypatch.setattr(rlm_session, "load_issues", lambda cfg: [])
    monkeypatch.setattr(rlm_session, "build_pr_table", lambda prs: "pr_table")
    monkeypatch.setattr(rlm_session, "build_issue_table", lambda issues: "issue_table")


def test_create_frontier_rlm_uses_pipeline_and_loads_graph(tmp_path, monkeypatch):
    _patch_session_dependencies(monkeypatch)
    config = _base_config(tmp_path)

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir(parents=True)
    (graph_dir / "structural_graph.json").write_text(json.dumps({"nodes": [{"id": "file:src/a.py"}], "edges": []}))

    rlm = rlm_session.create_frontier_rlm(config, run_id="run-1")
    kwargs = rlm.kwargs

    assert kwargs["backend"] == "litellm"
    assert kwargs["backend_kwargs"]["model_name"] == "anthropic/claude-sonnet-4-6"
    assert kwargs["backend_kwargs"]["extra_headers"]["anthropic-beta"] == "context-1m-2025-08-07"
    assert kwargs["backend_kwargs"]["timeout"] == 777.0
    assert kwargs["backend_kwargs"]["num_retries"] == 6
    assert kwargs["max_budget"] == 123.0
    assert kwargs["max_timeout"] == 321.0
    assert kwargs["max_errors"] == 4
    assert kwargs["max_depth"] == 5
    assert kwargs["max_iterations"] == 6
    assert kwargs["compaction_threshold_pct"] == 0.42
    assert kwargs["custom_tools"]["structural_graph"]["nodes"][0]["id"] == "file:src/a.py"
    assert callable(kwargs["on_iteration_complete"])
    assert callable(kwargs["on_subcall_start"])
    assert callable(kwargs["on_subcall_complete"])


def test_create_frontier_rlm_handles_missing_graph_and_prefixed_model(tmp_path, monkeypatch):
    _patch_session_dependencies(monkeypatch)
    config = _base_config(tmp_path)
    config["models"]["root"] = "anthropic/claude-opus-4-6"

    rlm = rlm_session.create_frontier_rlm(config, run_id="run-2")
    kwargs = rlm.kwargs

    assert kwargs["backend_kwargs"]["model_name"] == "anthropic/claude-opus-4-6"
    assert "extra_headers" not in kwargs["backend_kwargs"]
    assert kwargs["custom_tools"]["structural_graph"] == {}


def test_patch_local_repl_safe_builtins_enables_globals_and_locals():
    from rlm.environments import local_repl

    safe_builtins = local_repl._SAFE_BUILTINS
    original_globals = safe_builtins.get("globals")
    original_locals = safe_builtins.get("locals")
    original_eval = safe_builtins.get("eval")
    original_exec = safe_builtins.get("exec")
    had_patch_flag = hasattr(local_repl, "_rlm_repo_intel_safe_builtins_patch")
    original_patch_flag = getattr(local_repl, "_rlm_repo_intel_safe_builtins_patch", None)

    try:
        safe_builtins["globals"] = None
        safe_builtins["locals"] = None
        if hasattr(local_repl, "_rlm_repo_intel_safe_builtins_patch"):
            delattr(local_repl, "_rlm_repo_intel_safe_builtins_patch")

        rlm_session._patch_local_repl_safe_builtins()
        assert callable(safe_builtins["globals"])
        assert callable(safe_builtins["locals"])
        assert safe_builtins["eval"] is original_eval
        assert safe_builtins["exec"] is original_exec

        first_globals = safe_builtins["globals"]
        first_locals = safe_builtins["locals"]
        rlm_session._patch_local_repl_safe_builtins()
        assert safe_builtins["globals"] is first_globals
        assert safe_builtins["locals"] is first_locals
    finally:
        safe_builtins["globals"] = original_globals
        safe_builtins["locals"] = original_locals
        safe_builtins["eval"] = original_eval
        safe_builtins["exec"] = original_exec
        if had_patch_flag:
            setattr(local_repl, "_rlm_repo_intel_safe_builtins_patch", original_patch_flag)
        elif hasattr(local_repl, "_rlm_repo_intel_safe_builtins_patch"):
            delattr(local_repl, "_rlm_repo_intel_safe_builtins_patch")
