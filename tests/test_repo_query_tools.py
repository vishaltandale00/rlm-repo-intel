from rlm_repo_intel.tools.repo_query_tools import build_custom_tools


def test_build_custom_tools_exposes_only_github_api_tools(tmp_path):
    config = {
        "paths": {"data_dir": str(tmp_path / "data")},
        "repo": {"owner": "acme", "name": "widget"},
    }

    tools = build_custom_tools(config)

    assert set(tools.keys()) == {"list_prs", "read_pr_diff", "list_issues"}
