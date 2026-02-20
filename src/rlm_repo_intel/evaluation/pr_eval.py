"""Evaluate PRs against the codebase model using RLM."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict

from rich.console import Console
from rich.progress import Progress

from ..graph.store import GraphStore

console = Console()


@dataclass
class PREvaluation:
    pr_number: int
    title: str
    impact_scope: list[str]  # modules affected
    risk_score: float
    quality_score: float
    strategic_value: float  # how well it aligns with codebase needs
    novelty_score: float  # does it add something new vs redundant
    test_alignment: float  # does it have/need tests
    linked_issues: list[int]
    conflict_candidates: list[int]
    redundancy_candidates: list[int]
    review_summary: str
    confidence: float
    final_rank_score: float = 0.0


def evaluate_all_prs(config: dict, limit: int | None = None):
    """Evaluate all PRs against the codebase model."""
    from rlm import RLM

    data_dir = Path(config["paths"]["data_dir"])
    results_dir = Path(config["paths"]["results_dir"])

    # Load graph with module cards
    graph = GraphStore(config["paths"]["graph_dir"])
    graph.load()

    # Load architecture model
    arch_path = results_dir / "architecture.json"
    architecture = {}
    if arch_path.exists():
        with open(arch_path) as f:
            architecture = json.load(f)

    # Load module cards
    cards_path = results_dir / "module_cards.json"
    module_cards = {}
    if cards_path.exists():
        with open(cards_path) as f:
            module_cards = json.load(f)

    # Load PRs
    prs_path = data_dir / "prs" / "all_prs.jsonl"
    prs = []
    if prs_path.exists():
        with open(prs_path) as f:
            for line in f:
                prs.append(json.loads(line))

    if limit:
        prs = prs[:limit]

    console.print(f"\n[bold]Evaluating {len(prs)} PRs against codebase model...[/]")

    # Load issues for cross-referencing
    issues_path = data_dir / "issues" / "all_issues.jsonl"
    issues_by_number = {}
    if issues_path.exists():
        with open(issues_path) as f:
            for line in f:
                issue = json.loads(line)
                issues_by_number[issue["number"]] = issue

    # Configure worker RLM
    worker = RLM(
        backend="openai",
        backend_kwargs={"model_name": config["models"]["cheap_worker"]},
    )

    evaluations = []

    with Progress() as progress:
        task = progress.add_task("Evaluating PRs...", total=len(prs))

        for pr in prs:
            eval_result = _evaluate_single_pr(
                pr=pr,
                graph=graph,
                module_cards=module_cards,
                architecture=architecture,
                issues=issues_by_number,
                worker=worker,
            )
            evaluations.append(eval_result)
            progress.update(task, advance=1)

    # Save evaluations
    eval_path = results_dir / "pr_evaluations.jsonl"
    with open(eval_path, "w") as f:
        for ev in evaluations:
            f.write(json.dumps(asdict(ev)) + "\n")

    console.print(f"\n[bold green]✓ Evaluated {len(evaluations)} PRs.[/]")

    # Print top 10
    evaluations.sort(key=lambda e: -e.final_rank_score)
    console.print("\n[bold]Top 10 PRs:[/]")
    for i, ev in enumerate(evaluations[:10], 1):
        console.print(
            f"  {i}. PR #{ev.pr_number} — {ev.title[:60]} "
            f"(rank: {ev.final_rank_score:.2f}, risk: {ev.risk_score:.2f})"
        )


def _evaluate_single_pr(
    pr: dict,
    graph: GraphStore,
    module_cards: dict,
    architecture: dict,
    issues: dict,
    worker,
) -> PREvaluation:
    """Evaluate a single PR with codebase context from the graph."""

    # Step 1: Identify what this PR touches
    # (In production, we'd parse the diff. For now, use metadata)
    changed_files = []  # Would come from PR diff parsing
    touched_modules = graph.map_files_to_modules(changed_files)

    # Step 2: Build compact evaluation context from graph
    context_cards = {}
    for mod_id in touched_modules:
        if mod_id in module_cards:
            context_cards[mod_id] = module_cards[mod_id]

    # Step 3: Check for linked issues
    linked_issues = _extract_issue_refs(pr.get("body", "") or "")

    # Step 4: Build prompt with codebase intelligence
    prompt = f"""Evaluate this GitHub PR against the codebase understanding.

PR #{pr['number']}: {pr['title']}
Author: {pr.get('author', {}).get('login', 'unknown')}
State: {pr['state']}
+{pr.get('additions', 0)} -{pr.get('deletions', 0)}, {pr.get('changedFiles', 0)} files changed

Description:
{(pr.get('body', '') or '')[:2000]}

Modules affected: {', '.join(touched_modules) or 'unknown'}

Relevant module context:
{json.dumps(context_cards, indent=2)[:3000]}

Linked issues: {linked_issues}

Score this PR (0-1 for each):
- risk_score: likelihood of introducing bugs or breaking changes
- quality_score: code quality, clarity, completeness
- strategic_value: how valuable is this change for the project
- novelty_score: does this add something genuinely new (vs redundant with existing PRs)
- test_alignment: adequate test coverage for the changes

Also provide:
- review_summary: 2-3 sentence assessment
- confidence: your confidence in this evaluation

Return as JSON."""

    result = worker.completion(prompt)

    try:
        parsed = _parse_json_response(result.response)
    except Exception:
        parsed = {}

    ev = PREvaluation(
        pr_number=pr["number"],
        title=pr["title"],
        impact_scope=touched_modules,
        risk_score=parsed.get("risk_score", 0.5),
        quality_score=parsed.get("quality_score", 0.5),
        strategic_value=parsed.get("strategic_value", 0.5),
        novelty_score=parsed.get("novelty_score", 0.5),
        test_alignment=parsed.get("test_alignment", 0.5),
        linked_issues=linked_issues,
        conflict_candidates=parsed.get("conflict_candidates", []),
        redundancy_candidates=parsed.get("redundancy_candidates", []),
        review_summary=parsed.get("review_summary", ""),
        confidence=parsed.get("confidence", 0.5),
    )

    # Compute composite rank score
    ev.final_rank_score = (
        ev.strategic_value * 0.35
        + ev.quality_score * 0.25
        + ev.novelty_score * 0.20
        + (1 - ev.risk_score) * 0.10
        + ev.test_alignment * 0.10
    )

    return ev


def _extract_issue_refs(text: str) -> list[int]:
    """Extract issue references (#123) from PR body."""
    import re
    return [int(m) for m in re.findall(r"#(\d+)", text)]


def _parse_json_response(response: str) -> dict:
    """Parse JSON from response, handling markdown."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    return json.loads(text)
