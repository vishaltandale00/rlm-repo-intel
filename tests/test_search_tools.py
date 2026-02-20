from __future__ import annotations

from rlm_repo_intel.tools.search_tools import git_blame, git_log, web_search


def test_web_search_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    results = web_search("cve express.js")

    assert results == []


def test_git_log_handles_missing_repo_or_file_gracefully(tmp_path):
    results = git_log("src/missing.py", repo_dir=str(tmp_path), n=5)

    assert len(results) == 1
    assert "error" in results[0]


def test_git_blame_handles_missing_repo_or_file_gracefully(tmp_path):
    results = git_blame("src/missing.py", repo_dir=str(tmp_path))

    assert len(results) == 1
    assert "error" in results[0]
