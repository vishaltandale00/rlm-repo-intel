from click.testing import CliRunner

from rlm_repo_intel.cli import main


def test_cli_exposes_ingest_and_triage_only():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "triage" in result.output
    assert "triage-status" in result.output
    assert "  model" not in result.output
    assert "  evaluate-prs" not in result.output
    assert "  synthesize" not in result.output
    assert "  export" not in result.output


def test_cli_triage_reraises_runtime_error(monkeypatch):
    import rlm_repo_intel.run_triage as run_triage

    def _raise_failure(config):  # pragma: no cover - exercised via click runner
        del config
        raise RuntimeError("boom")

    monkeypatch.setattr(run_triage, "main", _raise_failure)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["triage"])

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "boom"


def test_cli_triage_status_reports_status(monkeypatch):
    import rlm_repo_intel.run_triage as run_triage

    monkeypatch.setattr(
        run_triage,
        "triage_status",
        lambda config, run_id: {
            "run_id": run_id or "latest",
            "classification": "actively_reasoning",
            "exit_code": 2,
        },
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["triage-status", "--run-id", "run-123"])

    assert result.exit_code == 2
    assert "run-123" in result.output
    assert "actively_reasoning" in result.output
