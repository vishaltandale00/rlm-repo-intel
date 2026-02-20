from __future__ import annotations

from typing import Any

from rlm import RLM

from rlm_repo_intel.prompts.root_prompts import ROOT_FRONTIER_PROMPT
from rlm_repo_intel.tools.repo_loader import (
    build_repo_tree,
    build_pr_table,
    build_issue_table,
    load_repo_to_repl,
    load_prs,
    load_issues,
)


def create_frontier_rlm(config: dict[str, Any]) -> RLM:
    # Load everything into memory
    repo = load_repo_to_repl(config)
    repo_tree = build_repo_tree(repo)
    prs = load_prs(config)
    issues = load_issues(config)

    # Build summary tables for the prompt
    pr_table = build_pr_table(prs)
    issue_table = build_issue_table(issues)

    # All data goes into REPL variables — no tools needed except llm_query/rlm_query
    custom_tools = {
        "repo": repo,
        "repo_tree": repo_tree,
        "prs": prs,
        "issues": issues,
    }

    # Append tables to system prompt so the LLM sees them immediately
    prompt_with_tables = f"{ROOT_FRONTIER_PROMPT}\n\n{pr_table}\n\n{issue_table}"

    return RLM(
        backend="litellm",
        backend_kwargs={"model_name": "anthropic/claude-sonnet-4-20250514"},
        custom_system_prompt=prompt_with_tables,
        custom_tools=custom_tools,
        custom_sub_tools={},  # sub-agents get no tools, just llm_query
        persistent=True,
        compaction=True,
        max_depth=6,
        max_iterations=48,
        max_budget=2000.0,
        verbose=True,
    )
