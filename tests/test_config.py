from pathlib import Path

import yaml

from rlm_repo_intel.config import DEFAULT_CONFIG, load_config


def test_load_config_merges_and_creates_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "custom.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "repo": {"owner": "octocat", "name": "hello-world"},
                "paths": {
                    "data_dir": "data",
                    "repo_dir": "data/repo",
                    "graph_dir": "data/graph",
                    "results_dir": "data/results",
                },
                "limits": {"ingest_pr_limit": 25},
            }
        )
    )

    config = load_config(str(config_file))

    assert config["repo"]["owner"] == "octocat"
    assert config["repo"]["name"] == "hello-world"
    assert config["repo"]["branch"] == "main"
    assert config["limits"]["ingest_pr_limit"] == 25

    for key in ["data_dir", "repo_dir", "graph_dir", "results_dir"]:
        assert Path(config["paths"][key]).exists()


def test_load_config_does_not_mutate_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = tmp_path / "first.yaml"
    first.write_text(yaml.safe_dump({"repo": {"owner": "changed"}}))

    first_cfg = load_config(str(first))
    second_cfg = load_config(str(tmp_path / "does-not-exist.yaml"))

    assert first_cfg["repo"]["owner"] == "changed"
    assert second_cfg["repo"]["owner"] == DEFAULT_CONFIG["repo"]["owner"]
