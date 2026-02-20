"""Configuration loader."""

from pathlib import Path

import yaml


DEFAULT_CONFIG = {
    "repo": {
        "owner": "openclaw",
        "name": "openclaw",
        "branch": "main",
    },
    "models": {
        "root": "claude-sonnet-4-20250514",
        "code_worker": "codex-5.3",
        "reasoning_worker": "gemini-3.1-pro",
        "cheap_worker": "claude-haiku",
    },
    "budget": {
        "max_spend_usd": 200,
        "phase1_pct": 20,
        "phase2_pct": 45,
        "phase3_pct": 35,
    },
    "paths": {
        "data_dir": ".rlm-repo-intel",
        "repo_dir": ".rlm-repo-intel/repo",
        "graph_dir": ".rlm-repo-intel/graph",
        "results_dir": ".rlm-repo-intel/results",
    },
    "output": {
        "format": "json",
        "push_to": None,
    },
    "limits": {
        "max_file_tokens": 120_000,
        "confidence_threshold": 0.72,
        "escalation_pct": 0.20,
        "pair_candidates_max": 15_000,
    },
}


def load_config(path: str) -> dict:
    """Load config from YAML file, falling back to defaults."""
    config = DEFAULT_CONFIG.copy()
    config_path = Path(path)

    if config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        _deep_merge(config, user_config)

    # Ensure data directories exist
    for key in ["data_dir", "repo_dir", "graph_dir", "results_dir"]:
        Path(config["paths"][key]).mkdir(parents=True, exist_ok=True)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
