from pathlib import Path

from rlm_repo_intel.ingest import run_ingest


def test_run_ingest_scopes_repo_and_caps_prs(tmp_path, monkeypatch):
    calls = {}

    def fake_clone(owner, name, branch, repo_dir):
        calls["clone"] = {
            "owner": owner,
            "name": name,
            "branch": branch,
            "repo_dir": repo_dir,
        }

    def fake_fetch_prs(owner, name, output_dir, batch_size=100, max_items=None):
        calls["fetch_prs"] = {
            "owner": owner,
            "name": name,
            "output_dir": output_dir,
            "batch_size": batch_size,
            "max_items": max_items,
        }
        return 3

    def fake_build(repo_dir, config):
        calls["build"] = {"repo_dir": repo_dir}
        return {"files": 0, "modules": 0}

    monkeypatch.setattr("rlm_repo_intel.ingest.clone_or_pull", fake_clone)
    monkeypatch.setattr("rlm_repo_intel.ingest.fetch_prs", fake_fetch_prs)
    monkeypatch.setattr("rlm_repo_intel.ingest.build_structural_graph", fake_build)

    config = {
        "paths": {
            "repo_dir": str(tmp_path / "repo"),
            "data_dir": str(tmp_path / "data"),
            "graph_dir": str(tmp_path / "graph"),
            "results_dir": str(tmp_path / "results"),
        },
        "limits": {"ingest_pr_limit": 100},
    }

    run_ingest(
        owner="openclaw",
        name="openclaw",
        branch="main",
        include_prs=True,
        include_issues=False,
        config=config,
    )

    expected_repo_dir = Path(config["paths"]["repo_dir"]) / "openclaw" / "openclaw"
    assert calls["clone"]["repo_dir"] == expected_repo_dir
    assert calls["fetch_prs"]["max_items"] == 100
    assert calls["fetch_prs"]["output_dir"] == Path(config["paths"]["data_dir"]) / "prs"
    assert calls["build"]["repo_dir"] == expected_repo_dir
