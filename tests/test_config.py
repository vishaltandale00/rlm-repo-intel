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
    assert config["pipeline"]["max_timeout"] == 7200
    assert config["pipeline"]["max_errors"] == 50
    assert config["pipeline"]["lm_request_timeout_seconds"] == 900
    assert config["pipeline"]["lm_request_retries"] == 2
    assert config["pipeline"]["output_contract_mode"] == "strict_repl"
    assert config["pipeline"]["output_repair_attempts"] == 1
    assert config["pipeline"]["observability"]["enabled"] is True
    assert config["pipeline"]["observability"]["heartbeat_seconds"] == 10

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


def test_load_config_allows_pipeline_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_file = tmp_path / "pipeline.yaml"
    cfg_file.write_text(
        yaml.safe_dump(
            {
                "pipeline": {
                    "max_budget": 111,
                    "max_timeout": 222,
                    "max_errors": 3,
                    "lm_request_timeout_seconds": 444,
                    "lm_request_retries": 5,
                    "max_depth": 4,
                    "max_iterations": 9,
                    "compaction_threshold_pct": 0.44,
                    "output_contract_mode": "hybrid",
                    "output_repair_attempts": 2,
                    "observability": {
                        "enabled": False,
                        "heartbeat_seconds": 3,
                        "capture_stdout_chars": 111,
                        "capture_stderr_chars": 222,
                        "response_preview_chars": 333,
                    },
                }
            }
        )
    )

    config = load_config(str(cfg_file))

    assert config["pipeline"]["max_budget"] == 111
    assert config["pipeline"]["max_timeout"] == 222
    assert config["pipeline"]["max_errors"] == 3
    assert config["pipeline"]["lm_request_timeout_seconds"] == 444
    assert config["pipeline"]["lm_request_retries"] == 5
    assert config["pipeline"]["max_depth"] == 4
    assert config["pipeline"]["max_iterations"] == 9
    assert config["pipeline"]["compaction_threshold_pct"] == 0.44
    assert config["pipeline"]["output_contract_mode"] == "hybrid"
    assert config["pipeline"]["output_repair_attempts"] == 2
    assert config["pipeline"]["observability"]["enabled"] is False
    assert config["pipeline"]["observability"]["heartbeat_seconds"] == 3
    assert config["pipeline"]["observability"]["capture_stdout_chars"] == 111
    assert config["pipeline"]["observability"]["capture_stderr_chars"] == 222
    assert config["pipeline"]["observability"]["response_preview_chars"] == 333
