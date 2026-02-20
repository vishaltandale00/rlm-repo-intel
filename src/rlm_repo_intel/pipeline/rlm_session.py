from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from rlm import RLM

from rlm_repo_intel.prompts.root_prompts import ROOT_FRONTIER_PROMPT
from rlm_repo_intel.tools.repo_loader import (
    build_issue_table,
    build_pr_table,
    build_repo_tree,
    load_issues,
    load_prs,
    load_repo_to_repl,
)
from rlm_repo_intel.tools.search_tools import git_blame, git_log, web_search


def create_frontier_rlm(config: dict[str, Any]) -> RLM:
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
    }

    prompt_with_tables = ROOT_FRONTIER_PROMPT

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
