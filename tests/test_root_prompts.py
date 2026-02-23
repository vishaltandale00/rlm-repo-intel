from rlm_repo_intel.prompts.root_prompts import ROLE_MODEL, ROLE_SYSTEM, ROOT_FRONTIER_PROMPT, TRIAGE_TASK_PROMPT


def test_root_prompt_contains_required_goal_contract_and_safety():
    assert "triage_results" in ROOT_FRONTIER_PROMPT
    assert "top_prs" in ROOT_FRONTIER_PROMPT
    assert "triage_summary" in ROOT_FRONTIER_PROMPT
    assert "triage_bundle" in ROOT_FRONTIER_PROMPT
    assert "DEFENSIVE EXECUTION" in ROOT_FRONTIER_PROMPT
    assert "Operating model (paper-aligned)" in ROOT_FRONTIER_PROMPT
    assert "def role_query(" in ROOT_FRONTIER_PROMPT
    assert "mode: str = \"rlm\"" in ROOT_FRONTIER_PROMPT
    assert "FINAL_VAR(\"triage_bundle\")" in ROOT_FRONTIER_PROMPT
    assert "{custom_tools_section}" in ROOT_FRONTIER_PROMPT
    assert "scoring_reasoning" in ROOT_FRONTIER_PROMPT
    assert "urgency, quality, criticality, risk_if_merged" in ROOT_FRONTIER_PROMPT


def test_role_maps_match_dashboard_agent_keys():
    expected_keys = {"code_analyst", "adversarial_reviewer", "risk_assessor", "synthesizer"}
    assert set(ROLE_SYSTEM.keys()) == expected_keys
    assert set(ROLE_MODEL.keys()) == expected_keys
    assert all(model.startswith("anthropic/") for model in ROLE_MODEL.values())


def test_triage_task_prompt_is_goal_oriented():
    assert "Triage all open PRs" in TRIAGE_TASK_PROMPT
    assert "delegation-first RLM flow" in TRIAGE_TASK_PROMPT
    assert "triage_bundle" in TRIAGE_TASK_PROMPT
    assert "FINAL_VAR(\"triage_bundle\")" in TRIAGE_TASK_PROMPT


def test_root_prompt_is_safe_for_custom_tools_formatting():
    rendered = ROOT_FRONTIER_PROMPT.format(custom_tools_section="- repo")
    assert "Root Repository Intelligence Model" in rendered
    assert "- repo" in rendered
