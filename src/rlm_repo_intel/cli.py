"""CLI entry point for rlm-repo-intel."""

import click
from pathlib import Path

from .config import load_config


@click.group()
@click.option("--config", "-c", default="rlm-repo-intel.yaml", help="Config file path")
@click.pass_context
def main(ctx, config):
    """RLM-powered repository intelligence."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@main.command()
@click.option("--repo", required=True, help="GitHub repo (owner/name)")
@click.option("--branch", default="main", help="Branch to analyze")
@click.option("--include-prs/--no-prs", default=True, help="Fetch PRs")
@click.option("--include-issues/--no-issues", default=True, help="Fetch issues")
@click.pass_context
def ingest(ctx, repo, branch, include_prs, include_issues):
    """Ingest a GitHub repository — clone code, fetch PRs and issues."""
    from .ingest import run_ingest

    owner, name = repo.split("/")
    run_ingest(
        owner=owner,
        name=name,
        branch=branch,
        include_prs=include_prs,
        include_issues=include_issues,
        config=ctx.obj["config"],
    )


@main.command()
@click.option("--root-model", help="Override root model")
@click.option("--worker-model", help="Override worker model")
@click.pass_context
def model(ctx, root_model, worker_model):
    """Build codebase understanding model using RLM recursive decomposition."""
    from .modeling import build_codebase_model

    cfg = ctx.obj["config"]
    if root_model:
        cfg["models"]["root"] = root_model
    if worker_model:
        cfg["models"]["cheap_worker"] = worker_model
    build_codebase_model(cfg)


@main.command(name="evaluate-prs")
@click.option("--budget", type=float, help="Max spend in USD for this phase")
@click.option("--limit", type=int, help="Max PRs to evaluate (for testing)")
@click.pass_context
def evaluate_prs(ctx, budget, limit):
    """Evaluate all PRs against the codebase model."""
    from .evaluation import evaluate_all_prs

    cfg = ctx.obj["config"]
    if budget:
        cfg["budget"]["max_spend_usd"] = budget
    evaluate_all_prs(cfg, limit=limit)


@main.command()
@click.option("--top-n", type=int, default=200, help="Number of top PRs to surface")
@click.pass_context
def synthesize(ctx, top_n):
    """Cross-PR synthesis — find redundancies, conflicts, rank top PRs."""
    from .synthesis import run_synthesis

    run_synthesis(ctx.obj["config"], top_n=top_n)


@main.command()
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json")
@click.option("--output", "-o", default="results/", help="Output directory")
@click.option("--push", is_flag=True, help="Push results to dashboard")
@click.pass_context
def export(ctx, fmt, output, push):
    """Export results to files and optionally push to the dashboard."""
    from .export import export_results

    export_results(ctx.obj["config"], fmt=fmt, output_dir=output, push=push)


if __name__ == "__main__":
    main()
