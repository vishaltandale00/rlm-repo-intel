import json
import subprocess

from rlm_repo_intel.ingest.github_fetch import _enrich_pr, fetch_prs


def test_enrich_pr_updates_stats_and_diff(monkeypatch):
    def fake_run(cmd, capture_output=True, text=True):
        if cmd[:2] == ["gh", "api"] and cmd[2] == "repos/acme/widget/pulls/42":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"additions": 7, "deletions": 3, "changed_files": 2}),
                stderr="",
            )
        if cmd[:3] == ["gh", "pr", "diff"] and cmd[3] == "42":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="diff --git a/a.py b/a.py\n+print('ok')\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr("rlm_repo_intel.ingest.github_fetch.subprocess.run", fake_run)

    pr = {"number": 42, "additions": 0, "deletions": 0, "changedFiles": 0, "diff": ""}
    enriched = _enrich_pr("acme", "widget", pr)

    assert enriched["additions"] == 7
    assert enriched["deletions"] == 3
    assert enriched["changedFiles"] == 2
    assert "diff --git" in enriched["diff"]


def test_enrich_pr_failure_keeps_stats_and_sets_empty_diff(monkeypatch):
    def fake_run(cmd, capture_output=True, text=True):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr("rlm_repo_intel.ingest.github_fetch.subprocess.run", fake_run)

    pr = {"number": 11, "additions": 5, "deletions": 4, "changedFiles": 3, "diff": "old"}
    enriched = _enrich_pr("acme", "widget", pr)

    assert enriched["additions"] == 5
    assert enriched["deletions"] == 4
    assert enriched["changedFiles"] == 3
    assert enriched["diff"] == ""


def test_fetch_prs_enriches_open_prs_only(tmp_path, monkeypatch):
    open_pr = {
        "number": 101,
        "title": "Open PR",
        "body": "",
        "state": "open",
        "user": {"login": "dev"},
        "labels": [],
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "merged_at": None,
        "closed_at": None,
        "additions": 0,
        "deletions": 0,
        "changed_files": 0,
        "head": {"ref": "feature/open"},
        "base": {"ref": "main"},
        "html_url": "https://example.com/pr/101",
    }
    closed_pr = {
        "number": 102,
        "title": "Closed PR",
        "body": "",
        "state": "closed",
        "user": {"login": "dev2"},
        "labels": [],
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "merged_at": None,
        "closed_at": "2025-01-02T00:00:00Z",
        "additions": 0,
        "deletions": 0,
        "changed_files": 0,
        "head": {"ref": "feature/closed"},
        "base": {"ref": "main"},
        "html_url": "https://example.com/pr/102",
    }

    commands = []
    sleeps = []

    def fake_run(cmd, capture_output=True, text=True):
        commands.append(cmd)
        if cmd[:2] == ["gh", "api"] and "pulls?state=all" in cmd[2] and "page=1" in cmd[2]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([open_pr, closed_pr]), stderr="")
        if cmd[:2] == ["gh", "api"] and "pulls?state=all" in cmd[2] and "page=2" in cmd[2]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        if cmd[:2] == ["gh", "api"] and cmd[2] == "repos/acme/widget/pulls/101":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"additions": 9, "deletions": 1, "changed_files": 4}),
                stderr="",
            )
        if cmd[:3] == ["gh", "pr", "diff"] and cmd[3] == "101":
            return subprocess.CompletedProcess(cmd, 0, stdout="diff --git a/x.py b/x.py\n", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("rlm_repo_intel.ingest.github_fetch.subprocess.run", fake_run)
    monkeypatch.setattr("rlm_repo_intel.ingest.github_fetch.time.sleep", fake_sleep)

    count = fetch_prs("acme", "widget", tmp_path)
    assert count == 2

    rows = []
    for line in (tmp_path / "all_prs.jsonl").read_text().splitlines():
        rows.append(json.loads(line))

    by_number = {row["number"]: row for row in rows}
    assert by_number[101]["changedFiles"] == 4
    assert by_number[101]["additions"] == 9
    assert by_number[101]["deletions"] == 1
    assert by_number[101]["diff"].startswith("diff --git")
    assert by_number[102]["changedFiles"] == 0
    assert by_number[102]["diff"] == ""

    joined = [" ".join(cmd) for cmd in commands]
    assert any("repos/acme/widget/pulls/101" in line for line in joined)
    assert not any("repos/acme/widget/pulls/102" in line for line in joined)
    assert sleeps == [0.1]
