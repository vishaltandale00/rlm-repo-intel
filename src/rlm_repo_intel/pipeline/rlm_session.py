from __future__ import annotations

import builtins
import json
from functools import partial
from pathlib import Path
from typing import Any

import litellm
from rlm.clients.litellm import LiteLLMClient
from rlm.logger.rlm_logger import RLMLogger
from rlm.utils.token_utils import MODEL_CONTEXT_LIMITS

# Patch rlms context limit used by compaction logic.
MODEL_CONTEXT_LIMITS["claude-sonnet-4-6"] = 1_000_000

from rlm import RLM

from rlm_repo_intel.prompts.root_prompts import ROLE_MODEL, ROLE_SYSTEM, ROOT_FRONTIER_PROMPT
from rlm_repo_intel.tools.dashboard_callback import (
    push_partial_results,
    push_trace_step,
    reset_run_state,
    set_run_context,
)
from rlm_repo_intel.tools.repo_loader import (
    build_issue_table,
    build_pr_table,
    build_repo_tree,
    load_issues,
    load_prs,
    load_repo_to_repl,
)
from rlm_repo_intel.tools.search_tools import git_blame, git_log, web_search


def _patch_rlm_litellm_kwargs_passthrough() -> None:
    """
    Ensure backend_kwargs (e.g. extra_headers) reach litellm.completion().

    rlms 0.1.1 stores unknown backend kwargs on BaseLM.kwargs but does not
    forward them in LiteLLMClient completion calls.
    """
    if getattr(LiteLLMClient, "_rlm_repo_intel_kwargs_passthrough_patch", False):
        return

    def _strip_json_markdown_fences(content: Any) -> Any:
        if not isinstance(content, str):
            return content

        text = content.strip()
        if text.startswith("```json") and text.endswith("```"):
            body = text[len("```json"):]
        elif text.startswith("```") and text.endswith("```"):
            body = text[len("```"):]
        else:
            return content

        if body.startswith("\n"):
            body = body[1:]
        if body.endswith("\n```"):
            body = body[:-4]
        elif body.endswith("```"):
            body = body[:-3]
        return body.strip()

    def _build_kwargs(
        client: LiteLLMClient, messages: list[dict[str, Any]], model: str
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "timeout": client.timeout}
        if client.api_key:
            kwargs["api_key"] = client.api_key
        if client.api_base:
            kwargs["api_base"] = client.api_base
        kwargs.update(client.kwargs)
        return kwargs

    def _completion(
        client: LiteLLMClient, prompt: str | list[dict[str, Any]], model: str | None = None
    ) -> str:
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        elif isinstance(prompt, list) and all(isinstance(item, dict) for item in prompt):
            messages = prompt
        else:
            raise ValueError(f"Invalid prompt type: {type(prompt)}")

        selected_model = model or client.model_name
        if not selected_model:
            raise ValueError("Model name is required for LiteLLM client.")

        response = litellm.completion(**_build_kwargs(client, messages, selected_model))
        client._track_cost(response, selected_model)
        return _strip_json_markdown_fences(response.choices[0].message.content)

    async def _acompletion(
        client: LiteLLMClient, prompt: str | list[dict[str, Any]], model: str | None = None
    ) -> str:
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        elif isinstance(prompt, list) and all(isinstance(item, dict) for item in prompt):
            messages = prompt
        else:
            raise ValueError(f"Invalid prompt type: {type(prompt)}")

        selected_model = model or client.model_name
        if not selected_model:
            raise ValueError("Model name is required for LiteLLM client.")

        response = await litellm.acompletion(**_build_kwargs(client, messages, selected_model))
        client._track_cost(response, selected_model)
        return _strip_json_markdown_fences(response.choices[0].message.content)

    LiteLLMClient.completion = _completion
    LiteLLMClient.acompletion = _acompletion
    LiteLLMClient._rlm_repo_intel_kwargs_passthrough_patch = True


def _patch_local_repl_safe_builtins() -> None:
    """
    Re-enable globals()/locals() in LocalREPL safe builtins.

    Prompt bootstrap cells rely on globals() checks, but rlms blocks these by
    default. Keep eval/exec blocked.
    """
    try:
        from rlm.environments import local_repl
    except Exception:
        return

    if getattr(local_repl, "_rlm_repo_intel_safe_builtins_patch", False):
        return

    safe_builtins = getattr(local_repl, "_SAFE_BUILTINS", None)
    if not isinstance(safe_builtins, dict):
        return

    safe_builtins["globals"] = builtins.globals
    safe_builtins["locals"] = builtins.locals
    local_repl._rlm_repo_intel_safe_builtins_patch = True


_patch_rlm_litellm_kwargs_passthrough()
_patch_local_repl_safe_builtins()


def create_frontier_rlm(config: dict[str, Any], run_id: str | None = None) -> RLM:
    from rlm_repo_intel.rlm_factory import _to_litellm_model_name

    # Load everything into memory
    repo = load_repo_to_repl(config)
    repo_tree = build_repo_tree(repo)
    prs = load_prs(config)
    issues = load_issues(config)
    graph_path = Path(config["paths"]["graph_dir"]) / "structural_graph.json"
    structural_graph: dict[str, Any] = {}
    if graph_path.exists():
        try:
            structural_graph = json.loads(graph_path.read_text())
        except (json.JSONDecodeError, OSError):
            structural_graph = {}

    repo_dir = (
        Path(config["paths"]["repo_dir"]) / config["repo"]["owner"] / config["repo"]["name"]
    )

    # All data goes into REPL variables — no tools needed except llm_query/rlm_query
    # Also precompute summary tables as REPL vars so the model can print() them
    pr_table = build_pr_table(prs)
    issue_table = build_issue_table(issues)

    set_run_context(run_id)
    reset_run_state()

    custom_tools = {
        "repo": repo,
        "repo_tree": repo_tree,
        "prs": prs,
        "issues": issues,
        "pr_table": pr_table,
        "issue_table": issue_table,
        "structural_graph": structural_graph,
        "ROLE_SYSTEM": ROLE_SYSTEM,
        "ROLE_MODEL": ROLE_MODEL,
        "web_search": web_search,
        "git_log": partial(git_log, repo_dir=str(repo_dir)),
        "git_blame": partial(git_blame, repo_dir=str(repo_dir)),
        "push_partial_results": push_partial_results,
        "push_trace_step": push_trace_step,
    }

    prompt_with_tables = ROOT_FRONTIER_PROMPT
    pipeline_cfg = config.get("pipeline", {})
    observability_cfg = pipeline_cfg.get("observability", {})
    request_timeout_seconds = float(pipeline_cfg.get("lm_request_timeout_seconds", 900.0))
    request_retries = int(pipeline_cfg.get("lm_request_retries", 2))
    model_name = _to_litellm_model_name(config.get("models", {}).get("root", "claude-sonnet-4-6"))
    canonical_model = model_name.split("/", 1)[-1].lower()
    backend_kwargs: dict[str, Any] = {
        "model_name": model_name,
        "timeout": request_timeout_seconds,
        "num_retries": request_retries,
    }
    if canonical_model == "claude-sonnet-4-6":
        backend_kwargs["extra_headers"] = {"anthropic-beta": "context-1m-2025-08-07"}
    logger = RLMLogger(log_dir=None) if bool(observability_cfg.get("enabled", True)) else None

    def _on_iteration_complete(depth: int, iteration: int, cost: float) -> None:
        push_trace_step(
            iteration,
            "iteration_complete",
            f"depth={depth} cost=${float(cost):.4f}",
        )

    def _on_subcall_start(depth: int, system_prompt: str, task: str) -> None:
        del system_prompt
        preview = (task or "").strip().replace("\n", " ")
        push_trace_step(depth, "subcall_start", f"Sub-agent: {preview[:200]}")

    def _on_subcall_complete(depth: int, task: str, cost: float, result: str | None) -> None:
        del task, result
        push_trace_step(depth, "subcall_complete", f"Sub-agent done: cost=${float(cost):.4f}")

    return RLM(
        backend="litellm",
        backend_kwargs=backend_kwargs,
        custom_system_prompt=prompt_with_tables,
        custom_tools=custom_tools,
        custom_sub_tools={},  # sub-agents get no tools, just llm_query
        logger=logger,
        persistent=True,
        compaction=True,
        compaction_threshold_pct=float(pipeline_cfg.get("compaction_threshold_pct", 0.55)),
        max_depth=int(pipeline_cfg.get("max_depth", 6)),
        max_iterations=int(pipeline_cfg.get("max_iterations", 48)),
        max_budget=float(pipeline_cfg.get("max_budget", 2000.0)),
        max_timeout=float(pipeline_cfg.get("max_timeout", 7200.0)),
        max_errors=int(pipeline_cfg.get("max_errors", 50)),
        on_iteration_complete=_on_iteration_complete,
        on_subcall_start=_on_subcall_start,
        on_subcall_complete=_on_subcall_complete,
        verbose=True,
    )
