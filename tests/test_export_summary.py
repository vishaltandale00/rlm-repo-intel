import json

from rlm_repo_intel.export.exporter import _build_summary


def test_build_summary_aggregates_results_and_ignores_bad_jsonl(tmp_path):
    results_dir = tmp_path

    (results_dir / "module_cards.json").write_text(
        json.dumps({"module:a": {"summary": "a"}, "module:b": {"summary": "b"}})
    )
    (results_dir / "final_ranking.json").write_text(
        json.dumps(
            {
                "ranking": [{"number": i} for i in range(30)],
                "themes": ["reliability", "performance"],
            }
        )
    )
    (results_dir / "pr_evaluations.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"pr_number": 1}),
                "not-json",
                json.dumps({"pr_number": 2}),
            ]
        )
    )
    (results_dir / "pr_clusters.json").write_text(json.dumps([{"cluster_id": 1}, {"cluster_id": 2}]))

    summary = _build_summary(results_dir)

    assert summary["total_modules"] == 2
    assert summary["total_prs_evaluated"] == 2
    assert summary["clusters"] == 2
    assert len(summary["top_prs"]) == 20
    assert summary["themes"] == ["reliability", "performance"]


def test_build_summary_handles_malformed_json_files(tmp_path):
    results_dir = tmp_path
    (results_dir / "module_cards.json").write_text("{bad json")
    (results_dir / "final_ranking.json").write_text("{bad json")
    (results_dir / "pr_clusters.json").write_text("{bad json")

    summary = _build_summary(results_dir)

    assert summary == {
        "repo": None,
        "total_prs_evaluated": 0,
        "total_modules": 0,
        "top_prs": [],
        "clusters": 0,
        "themes": [],
    }
