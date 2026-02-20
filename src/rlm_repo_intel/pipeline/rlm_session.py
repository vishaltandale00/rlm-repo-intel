from __future__ import annotations

from typing import Any

from rlm import RLM

from rlm_repo_intel.prompts.root_prompts import ROOT_FRONTIER_PROMPT
from rlm_repo_intel.tools.repo_query_tools import build_custom_tools


def create_frontier_rlm(config: dict[str, Any]) -> RLM:
    custom_tools = build_custom_tools(config)
    return RLM(
        backend="litellm",
        backend_kwargs={"model_name": "claude-sonnet-4.6"},
        custom_system_prompt=ROOT_FRONTIER_PROMPT,
        custom_tools=custom_tools,
        custom_sub_tools=custom_tools,
        persistent=True,
        compaction=True,
        max_depth=6,
        max_iterations=48,
        max_budget=60.0,
        verbose=True,
    )
