"""CLI entry point for rlm-repo-intel."""

import click

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
@click.option("--budget", type=float, help="Max spend in USD for this phase")
@click.pass_context
def triage(ctx, budget):
    """Run the RLM triage session — model decides its own approach."""
    from .run_triage import main as run_triage_main

    cfg = ctx.obj["config"]
    if budget is not None:
        cfg.setdefault("pipeline", {})["max_budget"] = budget
    try:
        run_triage_main(config=cfg)
    except Exception:
        raise


if __name__ == "__main__":
    main()
