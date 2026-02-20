from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import litellm
from rlm.clients.litellm import LiteLLMClient
from rlm.utils.token_utils import MODEL_CONTEXT_LIMITS

# Patch rlms context limit used by compaction logic.
MODEL_CONTEXT_LIMITS["claude-sonnet-4-6"] = 1_000_000

from rlm import RLM

from rlm_repo_intel.prompts.root_prompts import ROOT_FRONTIER_PROMPT
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


_patch_rlm_litellm_kwargs_passthrough()


def create_frontier_rlm(config: dict[str, Any], run_id: str | None = None) -> RLM:
    # Load everything into memory
    repo = load_repo_to_repl(config)
    repo_tree = build_repo_tree(repo)
    prs = load_prs(config)
    issues = load_issues(config)
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
        "web_search": web_search,
        "git_log": partial(git_log, repo_dir=str(repo_dir)),
        "git_blame": partial(git_blame, repo_dir=str(repo_dir)),
        "push_partial_results": push_partial_results,
        "push_trace_step": push_trace_step,
    }

    prompt_with_tables = ROOT_FRONTIER_PROMPT

    return RLM(
        backend="litellm",
        backend_kwargs={
            "model_name": "anthropic/claude-sonnet-4-6",
            "extra_headers": {"anthropic-beta": "context-1m-2025-08-07"},
        },
        custom_system_prompt=prompt_with_tables,
        custom_tools=custom_tools,
        custom_sub_tools={},  # sub-agents get no tools, just llm_query
        persistent=True,
        compaction=True,
        compaction_threshold_pct=0.60,
        max_depth=6,
        max_iterations=48,
        max_budget=2000.0,
        verbose=True,
    )
