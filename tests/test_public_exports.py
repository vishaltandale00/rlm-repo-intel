from rlm_repo_intel.evaluation import evaluate_all_prs
from rlm_repo_intel.export import export_results
from rlm_repo_intel.modeling import build_codebase_model
from rlm_repo_intel.synthesis import run_synthesis


def test_public_entrypoints_are_exported():
    assert callable(build_codebase_model)
    assert callable(evaluate_all_prs)
    assert callable(run_synthesis)
    assert callable(export_results)
