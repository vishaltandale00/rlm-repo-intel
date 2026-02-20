"""Evaluate PRs against the codebase model using RLM."""

import json
import re
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Any

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
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load graph with module cards
    graph = GraphStore(config["paths"]["graph_dir"])
    graph.load()

    # Load architecture model
    arch_path = results_dir / "architecture.json"
    architecture = {}
    if arch_path.exists():
        try:
            with open(arch_path) as f:
                architecture = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[yellow]Warning: failed to load architecture model: {exc}[/]")

    # Load module cards
    cards_path = results_dir / "module_cards.json"
    module_cards = {}
    if cards_path.exists():
        try:
            with open(cards_path) as f:
                module_cards = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[yellow]Warning: failed to load module cards: {exc}[/]")

    # Load PRs
    prs_path = data_dir / "prs" / "all_prs.jsonl"
    prs = []
    if prs_path.exists():
        with open(prs_path) as f:
            for line_number, line in enumerate(f, start=1):
                try:
                    prs.append(json.loads(line))
                except json.JSONDecodeError:
                    console.print(
                        f"[yellow]Warning: skipping malformed PR JSONL row {line_number}[/]"
                    )

    if limit:
        prs = prs[:limit]

    console.print(f"\n[bold]Evaluating {len(prs)} PRs against codebase model...[/]")

    # Load issues for cross-referencing
    issues_path = data_dir / "issues" / "all_issues.jsonl"
    issues_by_number = {}
    if issues_path.exists():
        with open(issues_path) as f:
            for line_number, line in enumerate(f, start=1):
                try:
                    issue = json.loads(line)
                except json.JSONDecodeError:
                    console.print(
                        f"[yellow]Warning: skipping malformed issue JSONL row {line_number}[/]"
                    )
                    continue
                issue_num = issue.get("number")
                if isinstance(issue_num, int):
                    issues_by_number[issue_num] = issue

    # Configure worker RLM
    from ..rlm_factory import try_create_rlm
    worker = try_create_rlm(config["models"]["cheap_worker"], label="eval-worker")

    evaluations = []

    with Progress() as progress:
        task = progress.add_task("Evaluating PRs...", total=len(prs))

        for pr in prs:
            try:
                eval_result = _evaluate_single_pr(
                    pr=pr,
                    graph=graph,
                    module_cards=module_cards,
                    architecture=architecture,
                    issues=issues_by_number,
                    worker=worker,
                )
            except Exception as exc:
                pr_number = int(pr.get("number", 0))
                title = str(pr.get("title") or "(untitled PR)")
                console.print(
                    f"[yellow]Warning: evaluation failed for PR #{pr_number}: {exc}[/]"
                )
                eval_result = PREvaluation(
                    pr_number=pr_number,
                    title=title,
                    impact_scope=[],
                    risk_score=0.5,
                    quality_score=0.5,
                    strategic_value=0.5,
                    novelty_score=0.5,
                    test_alignment=0.5,
                    linked_issues=[],
                    conflict_candidates=[],
                    redundancy_candidates=[],
                    review_summary="Evaluation failed; requires manual review.",
                    confidence=0.2,
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
    changed_files = parse_pr_diff_files(pr.get("diff", "") or "")
    touched_modules = graph.map_files_to_modules(changed_files)

    # Step 2: Build compact evaluation context from graph
    context_cards = {}
    for mod_id in touched_modules:
        if mod_id in module_cards:
            context_cards[mod_id] = module_cards[mod_id]

    # Step 3: Check for linked issues
    linked_issues = extract_issue_refs(pr.get("body", "") or "")

    # Step 4: Build prompt with codebase intelligence
    pr_number = int(pr.get("number", 0))
    pr_title = str(pr.get("title") or "(untitled PR)")
    pr_state = str(pr.get("state") or "unknown")

    prompt = f"""Evaluate this GitHub PR against the codebase understanding.

PR #{pr_number}: {pr_title}
Author: {pr.get('author', {}).get('login', 'unknown')}
State: {pr_state}
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

    parsed = {}
    if worker is not None:
        try:
            result = worker.completion(prompt)
            parsed = _parse_json_response(_extract_completion_text(result))
        except Exception as exc:
            console.print(
                f"[yellow]Warning: RLM evaluation failed for PR #{pr.get('number')}: {exc}[/]"
            )

    ev = PREvaluation(
        pr_number=int(pr.get("number", 0)),
        title=str(pr.get("title") or "(untitled PR)"),
        impact_scope=touched_modules,
        risk_score=_safe_score(parsed.get("risk_score"), default=0.5),
        quality_score=_safe_score(parsed.get("quality_score"), default=0.5),
        strategic_value=_safe_score(parsed.get("strategic_value"), default=0.5),
        novelty_score=_safe_score(parsed.get("novelty_score"), default=0.5),
        test_alignment=_safe_score(parsed.get("test_alignment"), default=0.5),
        linked_issues=linked_issues,
        conflict_candidates=_safe_int_list(parsed.get("conflict_candidates")),
        redundancy_candidates=_safe_int_list(parsed.get("redundancy_candidates")),
        review_summary=str(parsed.get("review_summary") or ""),
        confidence=_safe_score(parsed.get("confidence"), default=0.5),
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
    return extract_issue_refs(text)


def extract_issue_refs(text: str) -> list[int]:
    """Extract unique issue references in encounter order."""
    found = []
    seen = set()
    for match in re.findall(r"#(\d+)", text):
        issue_num = int(match)
        if issue_num in seen:
            continue
        seen.add(issue_num)
        found.append(issue_num)
    return found


def parse_pr_diff_files(diff_text: str) -> list[str]:
    """Parse changed file paths from a unified git diff."""
    if not diff_text:
        return []

    files = []
    seen = set()
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        rhs = parts[3]
        if rhs == "/dev/null":
            continue
        if rhs.startswith("b/"):
            rhs = rhs[2:]
        if rhs not in seen:
            seen.add(rhs)
            files.append(rhs)

    return files


def _parse_json_response(response: str) -> dict:
    """Parse JSON from response, handling markdown."""
    text = response.strip()
    if text.startswith("```"):
        text = _strip_markdown_fence(text)
    return json.loads(text)


def _strip_markdown_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _extract_completion_text(result: Any) -> str:
    if hasattr(result, "response"):
        return str(getattr(result, "response"))
    return str(result)


def _safe_score(value: Any, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def _safe_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        try:
            cleaned.append(int(item))
        except (TypeError, ValueError):
            continue
    return cleaned


def _infer_backend(model_name: str) -> str:
    model = model_name.lower()
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini"):
        return "gemini"
    return "openai"
