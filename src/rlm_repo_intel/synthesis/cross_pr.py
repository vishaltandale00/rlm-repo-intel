"""Cross-PR synthesis: find redundancies, conflicts, and produce final ranking."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict, field
from collections import defaultdict
from typing import Any

from rich.console import Console

console = Console()


@dataclass
class PRPairRelation:
    pr_a: int
    pr_b: int
    relation_type: str  # redundant, alternative, conflicting, composable, unrelated
    confidence: float
    explanation: str
    proposal: dict[str, Any] = field(default_factory=dict)
    challenge: dict[str, Any] = field(default_factory=dict)
    resolution_reasoning: str = ""


def run_synthesis(config: dict, top_n: int = 200):
    """Full cross-PR synthesis pipeline."""
    from rlm import RLM

    results_dir = Path(config["paths"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load PR evaluations
    evals = []
    eval_path = results_dir / "pr_evaluations.jsonl"
    if eval_path.exists():
        with open(eval_path) as f:
            for line_number, line in enumerate(f, start=1):
                try:
                    evals.append(json.loads(line))
                except json.JSONDecodeError:
                    console.print(
                        f"[yellow]Warning: skipping malformed evaluation JSONL row {line_number}[/]"
                    )

    console.print(f"\n[bold]Cross-PR synthesis over {len(evals)} evaluations...[/]")

    # Step 1: Generate candidate pairs (cheap — no LLM)
    console.print("  Generating candidate pairs...")
    candidates = _generate_candidates(evals, config)
    console.print(f"  → {len(candidates)} candidate pairs")

    # Step 2: Adjudicate pairs with debate
    console.print("  Adjudicating pairs with multi-agent debate...")
    from ..rlm_factory import try_create_rlm

    worker = try_create_rlm(config["models"]["cheap_worker"], label="synthesis-worker")
    root = try_create_rlm(config["models"]["root"], label="synthesis-root", verbose=True)
    relations = _adjudicate_pairs(candidates, evals, worker, root)
    console.print(f"  → {len(relations)} relations found")

    # Step 3: Build clusters from relations
    clusters = _build_clusters(relations)

    # Step 4: Final ranking with root model
    console.print("  Computing final ranking...")
    ranking = _final_ranking(evals, relations, clusters, root, top_n)

    # Save everything
    with open(results_dir / "pr_relations.jsonl", "w") as f:
        for rel in relations:
            f.write(json.dumps(asdict(rel)) + "\n")

    with open(results_dir / "pr_relation_debates.jsonl", "w") as f:
        for rel in relations:
            debate = {
                "pr_a": rel.pr_a,
                "pr_b": rel.pr_b,
                "relation_type": rel.relation_type,
                "proposal": rel.proposal,
                "challenge": rel.challenge,
                "resolution_reasoning": rel.resolution_reasoning,
            }
            f.write(json.dumps(debate) + "\n")

    with open(results_dir / "pr_clusters.json", "w") as f:
        json.dump(clusters, f, indent=2)

    with open(results_dir / "final_ranking.json", "w") as f:
        json.dump(ranking, f, indent=2)

    console.print(f"\n[bold green]✓ Synthesis complete.[/]")
    console.print(f"  Relations: {len(relations)}")
    console.print(f"  Clusters: {len(clusters)}")
    console.print(f"  Top {top_n} PRs ranked")


def _generate_candidates(evals: list[dict], config: dict) -> list[tuple[int, int]]:
    """Generate candidate PR pairs for comparison.

    Uses cheap heuristics — no LLM calls:
    - Same module overlap
    - Similar issue references
    - Title/description text similarity (would use embeddings in production)
    """
    max_candidates = config.get("limits", {}).get("pair_candidates_max", 15_000)

    # Group by touched module
    module_to_prs = defaultdict(list)
    for ev in evals:
        pr_number = ev.get("pr_number")
        if not isinstance(pr_number, int):
            continue
        for mod in ev.get("impact_scope", []):
            module_to_prs[mod].append(pr_number)

    # Generate pairs from same-module groups
    candidates = set()
    for mod, pr_ids in module_to_prs.items():
        for i, a in enumerate(pr_ids):
            for b in pr_ids[i + 1:]:
                pair = (min(a, b), max(a, b))
                candidates.add(pair)
                if len(candidates) >= max_candidates:
                    return list(candidates)

    # Also pair PRs that reference the same issues
    issue_to_prs = defaultdict(list)
    for ev in evals:
        pr_number = ev.get("pr_number")
        if not isinstance(pr_number, int):
            continue
        for issue_num in ev.get("linked_issues", []):
            issue_to_prs[issue_num].append(pr_number)

    for issue, pr_ids in issue_to_prs.items():
        for i, a in enumerate(pr_ids):
            for b in pr_ids[i + 1:]:
                pair = (min(a, b), max(a, b))
                candidates.add(pair)
                if len(candidates) >= max_candidates:
                    return list(candidates)

    return list(candidates)


def _adjudicate_pairs(
    candidates: list[tuple[int, int]],
    evals: list[dict],
    worker,
    root,
) -> list[PRPairRelation]:
    """Use proposer/challenger/synthesizer agents to determine PR pair relationships."""
    evals_by_number = {
        ev["pr_number"]: ev for ev in evals if isinstance(ev.get("pr_number"), int)
    }
    relations = []

    for pr_a, pr_b in candidates:
        ev_a = evals_by_number.get(pr_a)
        ev_b = evals_by_number.get(pr_b)
        if not ev_a or not ev_b:
            continue

        workspace = {
            "pr_a": {
                "number": pr_a,
                "title": ev_a.get("title", ""),
                "summary": ev_a.get("review_summary", "N/A"),
                "impact_scope": ev_a.get("impact_scope", []),
                "scores": {
                    "risk": ev_a.get("risk_score", 0.5),
                    "quality": ev_a.get("quality_score", 0.5),
                    "strategic_value": ev_a.get("strategic_value", 0.5),
                    "novelty": ev_a.get("novelty_score", 0.5),
                },
            },
            "pr_b": {
                "number": pr_b,
                "title": ev_b.get("title", ""),
                "summary": ev_b.get("review_summary", "N/A"),
                "impact_scope": ev_b.get("impact_scope", []),
                "scores": {
                    "risk": ev_b.get("risk_score", 0.5),
                    "quality": ev_b.get("quality_score", 0.5),
                    "strategic_value": ev_b.get("strategic_value", 0.5),
                    "novelty": ev_b.get("novelty_score", 0.5),
                },
            },
        }

        proposal = _run_relation_proposer(worker, workspace)
        workspace["proposal"] = proposal

        challenge = _run_relation_challenger(worker, workspace)
        workspace["challenge"] = challenge

        resolved = _run_relation_synthesizer(root or worker, workspace)

        relation = resolved.get("relation")
        if relation not in {"redundant", "alternative", "conflicting", "composable", "unrelated"}:
            relation = proposal.get("relation", "unrelated")

        if relation == "unrelated":
            continue

        relations.append(
            PRPairRelation(
                pr_a=pr_a,
                pr_b=pr_b,
                relation_type=relation,
                confidence=_safe_score(
                    resolved.get("confidence"),
                    default=_safe_score(proposal.get("confidence"), default=0.5),
                ),
                explanation=str(
                    resolved.get("explanation")
                    or proposal.get("explanation")
                    or "No explanation provided."
                ),
                proposal=proposal,
                challenge=challenge,
                resolution_reasoning=str(resolved.get("resolution_reasoning") or ""),
            )
        )

    return relations


def _run_relation_proposer(model, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Cross-PR Proposer.
Propose the most likely relationship between these PRs.

REPL variables:
pr_a = {json.dumps(workspace['pr_a'], indent=2)}
pr_b = {json.dumps(workspace['pr_b'], indent=2)}

Classify as one of:
- redundant
- alternative
- conflicting
- composable
- unrelated

Return JSON with:
- relation
- confidence (0-1)
- explanation
- overlap_evidence: list[str]
"""

    fallback = {
        "relation": "unrelated",
        "confidence": 0.3,
        "explanation": "No proposer model output.",
        "overlap_evidence": [],
    }
    return _run_agent(model, prompt, fallback)


def _run_relation_challenger(model, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Cross-PR Adversarial Reviewer.
Challenge the proposer's relationship claim and argue why it could be wrong.

REPL variables:
pr_a = {json.dumps(workspace['pr_a'], indent=2)}
pr_b = {json.dumps(workspace['pr_b'], indent=2)}
proposal = {json.dumps(workspace['proposal'], indent=2)}

Return JSON with:
- challenge_strength (0-1)
- challenge_points: list[str]
- alternative_relation: one of [redundant, alternative, conflicting, composable, unrelated]
- counter_explanation
"""

    fallback = {
        "challenge_strength": 0.0,
        "challenge_points": [],
        "alternative_relation": workspace["proposal"].get("relation", "unrelated"),
        "counter_explanation": "No challenger model output.",
    }
    return _run_agent(model, prompt, fallback)


def _run_relation_synthesizer(model, workspace: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are Cross-PR Synthesizer.
Resolve disagreement between proposer and adversarial challenger.

REPL variables:
pr_a = {json.dumps(workspace['pr_a'], indent=2)}
pr_b = {json.dumps(workspace['pr_b'], indent=2)}
proposal = {json.dumps(workspace['proposal'], indent=2)}
challenge = {json.dumps(workspace['challenge'], indent=2)}

Return JSON with:
- relation
- confidence (0-1)
- explanation
- resolution_reasoning: explicitly explain why one side was accepted or partially accepted
"""

    fallback = {
        "relation": workspace["proposal"].get("relation", "unrelated"),
        "confidence": _safe_score(workspace["proposal"].get("confidence"), default=0.4),
        "explanation": workspace["proposal"].get("explanation", "No synthesis output."),
        "resolution_reasoning": "Fallback selected proposal due to missing synthesizer output.",
    }
    return _run_agent(model, prompt, fallback)


def _build_clusters(relations: list[PRPairRelation]) -> list[dict]:
    """Build clusters of related PRs using union-find."""
    parent = {}

    def find(x):
        if x not in parent:
            parent[x] = x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        pa, pb = find(a), find(b)
        if pa != pb:
            parent[pa] = pb

    for rel in relations:
        if rel.relation_type in ("redundant", "alternative", "conflicting"):
            union(rel.pr_a, rel.pr_b)

    # Group by cluster root
    clusters_map = defaultdict(list)
    all_prs = set()
    for rel in relations:
        all_prs.add(rel.pr_a)
        all_prs.add(rel.pr_b)

    for pr in all_prs:
        root = find(pr)
        clusters_map[root].append(pr)

    # Build cluster objects
    clusters = []
    for root, members in clusters_map.items():
        if len(members) > 1:
            cluster_relations = [
                asdict(r) for r in relations
                if r.pr_a in members and r.pr_b in members
            ]
            clusters.append({
                "cluster_id": root,
                "members": sorted(set(members)),
                "size": len(set(members)),
                "relations": cluster_relations,
            })

    return sorted(clusters, key=lambda c: -c["size"])


def _final_ranking(
    evals: list[dict],
    relations: list[PRPairRelation],
    clusters: list[dict],
    root_rlm,
    top_n: int,
) -> dict:
    """Use root model to produce final ranking considering all signals."""

    # Pre-rank by composite score
    sorted_evals = sorted(evals, key=lambda e: -e.get("final_rank_score", 0))
    top_candidates = sorted_evals[:top_n * 2]  # 2x for filtering redundants

    summary = {
        "total_prs_evaluated": len(evals),
        "clusters_found": len(clusters),
        "relations_found": len(relations),
        "top_candidates": len(top_candidates),
    }

    prompt = f"""You are producing the final ranking of the top {top_n} PRs from a repository.

Summary: {json.dumps(summary)}

Top candidate PRs (pre-ranked by composite score):
{json.dumps([{
    'number': e['pr_number'],
    'title': e['title'],
    'rank_score': e.get('final_rank_score', 0),
    'risk': e.get('risk_score', 0),
    'quality': e.get('quality_score', 0),
    'strategic_value': e.get('strategic_value', 0),
    'summary': e.get('review_summary', '')[:100],
} for e in top_candidates[:50]], indent=2)}

PR clusters (groups of related/redundant/conflicting PRs):
{json.dumps(clusters[:20], indent=2)[:7000]}

From these candidates, select the top {top_n} PRs worth watching/merging.
For each redundancy cluster, pick the best representative.
Flag any critical conflicts.

Return JSON with:
- ranking: list of {{number, rank, reason}}
- conflicts: list of critical conflict groups
- themes: top 5 themes/areas these PRs address
"""

    if root_rlm is None:
        return {"pre_ranking": [e["pr_number"] for e in sorted_evals[:top_n]]}

    try:
        result = root_rlm.completion(prompt)
        return _parse_json_response(_extract_completion_text(result))
    except Exception as exc:
        console.print(f"[yellow]Warning: final ranking failed: {exc}[/]")
        return {"pre_ranking": [e["pr_number"] for e in sorted_evals[:top_n]]}


def _run_agent(model, prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
    if model is None:
        return fallback

    try:
        result = model.completion(prompt)
        parsed = _parse_json_response(_extract_completion_text(result))
    except Exception as exc:
        console.print(f"[yellow]Warning: synthesis agent failed: {exc}[/]")
        return fallback

    if not isinstance(parsed, dict):
        return fallback

    merged = dict(fallback)
    merged.update(parsed)
    return merged


def _parse_json_response(response: str) -> dict:
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


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


def _infer_backend(model_name: str) -> str:
    model = model_name.lower()
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini"):
        return "gemini"
    return "openai"
