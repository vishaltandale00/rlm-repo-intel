from rlm_repo_intel.synthesis.cross_pr import PRPairRelation, _build_clusters, _generate_candidates


def test_generate_candidates_from_module_and_issue_overlap():
    evals = [
        {"pr_number": 1, "impact_scope": ["module:a"], "linked_issues": [10]},
        {"pr_number": 2, "impact_scope": ["module:a"], "linked_issues": []},
        {"pr_number": 3, "impact_scope": [], "linked_issues": [10]},
        {"pr_number": 4, "impact_scope": ["module:b"], "linked_issues": [99]},
    ]
    config = {"limits": {"pair_candidates_max": 100}}

    candidates = _generate_candidates(evals, config)

    assert set(candidates) == {(1, 2), (1, 3)}


def test_generate_candidates_respects_limit_and_skips_bad_rows():
    evals = [
        {"pr_number": 1, "impact_scope": ["module:a"], "linked_issues": []},
        {"pr_number": 2, "impact_scope": ["module:a"], "linked_issues": []},
        {"pr_number": 3, "impact_scope": ["module:a"], "linked_issues": []},
        {"impact_scope": ["module:a"], "linked_issues": []},
        {"pr_number": "4", "impact_scope": ["module:a"], "linked_issues": []},
    ]
    config = {"limits": {"pair_candidates_max": 2}}

    candidates = _generate_candidates(evals, config)

    assert len(candidates) == 2
    assert set(candidates).issubset({(1, 2), (1, 3), (2, 3)})


def test_build_clusters_only_includes_relations_inside_cluster():
    relations = [
        PRPairRelation(1, 2, "redundant", 0.9, "same change"),
        PRPairRelation(2, 3, "composable", 0.8, "can combine"),
    ]

    clusters = _build_clusters(relations)

    assert len(clusters) == 1
    assert clusters[0]["members"] == [1, 2]
    assert len(clusters[0]["relations"]) == 1
    assert clusters[0]["relations"][0]["pr_a"] == 1
    assert clusters[0]["relations"][0]["pr_b"] == 2
