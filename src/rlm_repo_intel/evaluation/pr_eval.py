"""Evaluate PRs against the codebase model using a multi-agent RLM process."""

import json
import re
from pathlib import Path
from dataclasses import dataclass, asdict, field
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
    agent_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    disagreement_points: list[str] = field(default_factory=list)
    synthesis_reasoning: str = ""


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

    # Configure worker and root RLMs
    from ..rlm_factory import try_create_rlm

    worker = try_create_rlm(config["models"]["cheap_worker"], label="eval-worker")
    root = try_create_rlm(config["models"]["root"], label="eval-root")

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
                    root=root,
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
                    synthesis_reasoning="Pipeline execution failed before synthesis.",
                )
            evaluations.append(eval_result)
            progress.update(task, advance=1)

    # Save evaluations
    eval_path = results_dir / "pr_evaluations.jsonl"
    with open(eval_path, "w") as f:
        for ev in evaluations:
            f.write(json.dumps(asdict(ev)) + "\n")

    trace_path = results_dir / "pr_reasoning_traces.jsonl"
    with open(trace_path, "w") as f:
        for ev in evaluations:
            trace = {
                "pr_number": ev.pr_number,
                "title": ev.title,
                "agent_outputs": ev.agent_outputs,
                "disagreement_points": ev.disagreement_points,
                "synthesis_reasoning": ev.synthesis_reasoning,
            }
            f.write(json.dumps(trace) + "\n")

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
    root,
) -> PREvaluation:
    """Evaluate a single PR with codebase context from the graph."""

    changed_files = parse_pr_diff_files(pr.get("diff", "") or "")
    touched_modules = graph.map_files_to_modules(changed_files)

    context_cards = {}
    for mod_id in touched_modules:
        if mod_id in module_cards:
            context_cards[mod_id] = module_cards[mod_id]

    linked_issues = extract_issue_refs(pr.get("body", "") or "")

    pr_number = int(pr.get("number", 0))
    pr_title = str(pr.get("title") or "(untitled PR)")
    pr_state = str(pr.get("state") or "unknown")

    workspace: dict[str, Any] = {
        "pr_context": {
            "number": pr_number,
            "title": pr_title,
            "state": pr_state,
            "author": pr.get("author", {}).get("login", "unknown"),
            "additions": int(pr.get("additions", 0) or 0),
            "deletions": int(pr.get("deletions", 0) or 0),
            "changed_files_count": int(pr.get("changedFiles", 0) or 0),
            "description": (pr.get("body", "") or "")[:2000],
            "changed_files": changed_files[:200],
            "linked_issues": linked_issues,
        },
        "module_context": context_cards,
        "architecture_context": {
            "layers": architecture.get("layers", []),
            "fragility_map": architecture.get("fragility_map", []),
            "module_groups": architecture.get("module_groups", {}),
        },
        "issues_context": {
            str(issue_num): issues.get(issue_num, {})
            for issue_num in linked_issues[:20]
            if issue_num in issues
        },
    }

    workspace["code_analyst"] = _run_code_analyst(worker, workspace)
    workspace["codebase_expert"] = _run_codebase_expert(worker, workspace)
    workspace["risk_assessor"] = _run_risk_assessor(worker, workspace)
    workspace["adversarial_reviewer"] = _run_adversarial_reviewer(worker, workspace)
    disagreements = _collect_disagreements(workspace)
    workspace["disagreement_points"] = disagreements
    synthesis = _run_synthesizer(root, workspace)

    risk_score = _safe_score(
        synthesis.get("risk_score"),
        default=_safe_score(workspace["risk_assessor"].get("risk_score"), 0.5),
    )
    quality_score = _safe_score(
        synthesis.get("quality_score"),
        default=_safe_score(workspace["code_analyst"].get("quality_score"), 0.5),
    )
    strategic_value = _safe_score(
        synthesis.get("strategic_value"),
        default=_safe_score(workspace["codebase_expert"].get("strategic_value"), 0.5),
    )
    novelty_score = _safe_score(
        synthesis.get("novelty_score"),
        default=_safe_score(workspace["codebase_expert"].get("novelty_score"), 0.5),
    )
    test_alignment = _safe_score(
        synthesis.get("test_alignment"),
        default=_safe_score(workspace["risk_assessor"].get("test_alignment"), 0.5),
    )
    confidence = _safe_score(synthesis.get("confidence"), default=0.5)

    ev = PREvaluation(
        pr_number=pr_number,
        title=pr_title,
        impact_scope=touched_modules,
        risk_score=risk_score,
        quality_score=quality_score,
        strategic_value=strategic_value,
        novelty_score=novelty_score,
        test_alignment=test_alignment,
        linked_issues=linked_issues,
        conflict_candidates=_safe_int_list(synthesis.get("conflict_candidates")),
        redundancy_candidates=_safe_int_list(synthesis.get("redundancy_candidates")),
        review_summary=str(synthesis.get("review_summary") or ""),
        confidence=confidence,
        agent_outputs={
            "code_analyst": workspace["code_analyst"],
            "codebase_expert": workspace["codebase_expert"],
            "risk_assessor": workspace["risk_assessor"],
            "adversarial_reviewer": workspace["adversarial_reviewer"],
            "synthesizer": synthesis,
        },
        disagreement_points=disagreements,
        synthesis_reasoning=str(synthesis.get("synthesis_reasoning") or ""),
    )

    adversarial_rejection = _safe_score(
        workspace["adversarial_reviewer"].get("rejection_confidence"),
        default=0.0,
    )

    ev.final_rank_score = (
        ev.strategic_value * 0.35
        + ev.quality_score * 0.25
        + ev.novelty_score * 0.20
        + (1 - ev.risk_score) * 0.10
        + ev.test_alignment * 0.10
        - adversarial_rejection * 0.10
    )
    ev.final_rank_score = max(0.0, min(1.0, ev.final_rank_score))

    return ev


def _run_code_analyst(worker, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Code Analyst in a shared RLM REPL workspace.
Analyze the technical changes from the PR context only.

REPL variables:
pr_context = {json.dumps(workspace['pr_context'], indent=2)}
module_context = {json.dumps(workspace['module_context'], indent=2)[:4000]}

Return strict JSON with:
- technical_summary: short paragraph
- changed_components: list[str]
- quality_score: float 0-1
- risk_score: float 0-1
- test_alignment: float 0-1
- confidence: float 0-1
- reasoning: explicit technical rationale
"""
    fallback = {
        "technical_summary": "No model response; technical review incomplete.",
        "changed_components": workspace["pr_context"].get("changed_files", [])[:10],
        "quality_score": 0.5,
        "risk_score": 0.5,
        "test_alignment": 0.5,
        "confidence": 0.3,
        "reasoning": "Fallback output because model is unavailable.",
    }
    return _run_agent(worker, prompt, fallback)


def _run_codebase_expert(worker, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Codebase Expert in a shared RLM REPL workspace.
Assess architectural fit and value. Use Code Analyst output as an input and challenge weak assumptions.

REPL variables:
pr_context = {json.dumps(workspace['pr_context'], indent=2)}
architecture_context = {json.dumps(workspace['architecture_context'], indent=2)[:3000]}
module_context = {json.dumps(workspace['module_context'], indent=2)[:3000]}
code_analyst = {json.dumps(workspace['code_analyst'], indent=2)}

Return strict JSON with:
- architecture_fit: short paragraph
- strategic_value: float 0-1
- novelty_score: float 0-1
- concern_points: list[str]
- confidence: float 0-1
- reasoning: explicit rationale tied to module cards
"""
    fallback = {
        "architecture_fit": "Insufficient context to validate architecture fit.",
        "strategic_value": 0.5,
        "novelty_score": 0.5,
        "concern_points": [],
        "confidence": 0.3,
        "reasoning": "Fallback output because model is unavailable.",
    }
    return _run_agent(worker, prompt, fallback)


def _run_risk_assessor(worker, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Risk Assessor in a shared RLM REPL workspace.
Focus on regressions, breaking changes, security, and testing risk.

REPL variables:
pr_context = {json.dumps(workspace['pr_context'], indent=2)}
module_context = {json.dumps(workspace['module_context'], indent=2)[:3000]}
code_analyst = {json.dumps(workspace['code_analyst'], indent=2)}
codebase_expert = {json.dumps(workspace['codebase_expert'], indent=2)}

Return strict JSON with:
- risk_score: float 0-1
- test_alignment: float 0-1
- security_risk: float 0-1
- high_risk_items: list[str]
- confidence: float 0-1
- reasoning: explicit rationale with failure modes
"""
    fallback = {
        "risk_score": 0.5,
        "test_alignment": 0.5,
        "security_risk": 0.5,
        "high_risk_items": ["Risk assessor fallback: missing model output."],
        "confidence": 0.3,
        "reasoning": "Fallback output because model is unavailable.",
    }
    return _run_agent(worker, prompt, fallback)


def _run_adversarial_reviewer(worker, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Adversarial Reviewer in a shared RLM REPL workspace.
Your job is to find reasons to REJECT this PR.
Read prior agents and challenge their scores, assumptions, and blind spots.

REPL variables:
pr_context = {json.dumps(workspace['pr_context'], indent=2)}
code_analyst = {json.dumps(workspace['code_analyst'], indent=2)}
codebase_expert = {json.dumps(workspace['codebase_expert'], indent=2)}
risk_assessor = {json.dumps(workspace['risk_assessor'], indent=2)}

Return strict JSON with:
- reject_reasons: list[str]
- challenged_scores: dict with keys from [risk_score, quality_score, strategic_value, novelty_score, test_alignment]
- rejection_confidence: float 0-1
- counter_arguments: short paragraph
- confidence: float 0-1
- reasoning: explicit argument for why this PR may be unsafe or low-value
"""
    fallback = {
        "reject_reasons": [],
        "challenged_scores": {},
        "rejection_confidence": 0.0,
        "counter_arguments": "No adversarial challenge due to missing model output.",
        "confidence": 0.3,
        "reasoning": "Fallback output because model is unavailable.",
    }
    return _run_agent(worker, prompt, fallback)


def _run_synthesizer(root, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are the root Synthesizer in a shared RLM REPL workspace.
You must resolve disagreements explicitly and produce a final PR evaluation.

REPL variables:
pr_context = {json.dumps(workspace['pr_context'], indent=2)}
code_analyst = {json.dumps(workspace['code_analyst'], indent=2)}
codebase_expert = {json.dumps(workspace['codebase_expert'], indent=2)}
risk_assessor = {json.dumps(workspace['risk_assessor'], indent=2)}
adversarial_reviewer = {json.dumps(workspace['adversarial_reviewer'], indent=2)}
disagreement_points = {json.dumps(workspace['disagreement_points'], indent=2)}

Return strict JSON with:
- risk_score: float 0-1
- quality_score: float 0-1
- strategic_value: float 0-1
- novelty_score: float 0-1
- test_alignment: float 0-1
- conflict_candidates: list[int]
- redundancy_candidates: list[int]
- review_summary: 2-3 sentences
- synthesis_reasoning: explain how disagreements were resolved
- confidence: float 0-1
"""

    fallback = _heuristic_synthesis(workspace)
    return _run_agent(root, prompt, fallback)


def _heuristic_synthesis(workspace: dict[str, Any]) -> dict[str, Any]:
    code_analyst = workspace["code_analyst"]
    codebase_expert = workspace["codebase_expert"]
    risk_assessor = workspace["risk_assessor"]
    adversarial = workspace["adversarial_reviewer"]

    risk_score = max(
        _safe_score(code_analyst.get("risk_score"), 0.5),
        _safe_score(risk_assessor.get("risk_score"), 0.5),
    )
    quality_score = _safe_score(code_analyst.get("quality_score"), 0.5)
    strategic_value = _safe_score(codebase_expert.get("strategic_value"), 0.5)
    novelty_score = _safe_score(codebase_expert.get("novelty_score"), 0.5)
    test_alignment = min(
        _safe_score(code_analyst.get("test_alignment"), 0.5),
        _safe_score(risk_assessor.get("test_alignment"), 0.5),
    )

    adversarial_rejection = _safe_score(adversarial.get("rejection_confidence"), 0.0)
    if adversarial_rejection >= 0.7:
        risk_score = min(1.0, risk_score + 0.15)
        novelty_score = max(0.0, novelty_score - 0.10)

    return {
        "risk_score": risk_score,
        "quality_score": quality_score,
        "strategic_value": strategic_value,
        "novelty_score": novelty_score,
        "test_alignment": test_alignment,
        "conflict_candidates": [],
        "redundancy_candidates": [],
        "review_summary": "Heuristic synthesis generated because root model output was unavailable.",
        "synthesis_reasoning": "Used conservative aggregation, prioritized higher risk, and applied adversarial penalty when strong rejection signals existed.",
        "confidence": 0.35,
    }


def _collect_disagreements(workspace: dict[str, Any]) -> list[str]:
    disagreements: list[str] = []

    analyst_risk = _safe_score(workspace["code_analyst"].get("risk_score"), 0.5)
    risk_assessor_risk = _safe_score(workspace["risk_assessor"].get("risk_score"), 0.5)
    if abs(analyst_risk - risk_assessor_risk) >= 0.25:
        disagreements.append(
            "Risk disagreement: code_analyst and risk_assessor provided materially different risk scores."
        )

    expert_novelty = _safe_score(workspace["codebase_expert"].get("novelty_score"), 0.5)
    adversarial_rejection = _safe_score(
        workspace["adversarial_reviewer"].get("rejection_confidence"),
        0.0,
    )
    if expert_novelty >= 0.7 and adversarial_rejection >= 0.6:
        disagreements.append(
            "Value disagreement: codebase_expert sees high novelty, adversarial_reviewer argues for rejection."
        )

    challenged = workspace["adversarial_reviewer"].get("challenged_scores")
    if isinstance(challenged, dict) and challenged:
        disagreements.append("Adversarial reviewer challenged prior scores and requested tighter justification.")

    if not disagreements:
        disagreements.append("No major scoring disagreements detected across agents.")

    return disagreements


def _run_agent(model, prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
    if model is None:
        return fallback

    try:
        result = model.completion(prompt)
        parsed = _parse_json_response(_extract_completion_text(result))
    except Exception as exc:
        console.print(f"[yellow]Warning: agent call failed: {exc}[/]")
        return fallback

    if not isinstance(parsed, dict):
        return fallback

    merged = dict(fallback)
    merged.update(parsed)
    return merged


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
