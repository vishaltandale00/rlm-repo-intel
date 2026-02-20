"""Cross-PR synthesis: find redundancies, conflicts, and produce final ranking."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict
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

    # Step 2: Adjudicate pairs with RLM
    console.print("  Adjudicating pairs...")
    from ..rlm_factory import try_create_rlm
    worker = try_create_rlm(config["models"]["cheap_worker"], label="synthesis-worker")
    relations = _adjudicate_pairs(candidates, evals, worker)
    console.print(f"  → {len(relations)} relations found")

    # Step 3: Build clusters from relations
    clusters = _build_clusters(relations)

    # Step 4: Final ranking with root model
    console.print("  Computing final ranking...")
    root = try_create_rlm(config["models"]["root"], label="synthesis-root", verbose=True)
    ranking = _final_ranking(evals, relations, clusters, root, top_n)

    # Save everything
    with open(results_dir / "pr_relations.jsonl", "w") as f:
        for rel in relations:
            f.write(json.dumps(asdict(rel)) + "\n")

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
) -> list[PRPairRelation]:
    """Use RLM worker to determine relationship between PR pairs."""
    if worker is None:
        return []

    evals_by_number = {
        ev["pr_number"]: ev for ev in evals if isinstance(ev.get("pr_number"), int)
    }
    relations = []

    for pr_a, pr_b in candidates:
        ev_a = evals_by_number.get(pr_a)
        ev_b = evals_by_number.get(pr_b)
        if not ev_a or not ev_b:
            continue

        prompt = f"""Compare these two PRs and determine their relationship.

PR #{pr_a}: {ev_a['title']}
Summary: {ev_a.get('review_summary', 'N/A')}
Modules: {ev_a.get('impact_scope', [])}

PR #{pr_b}: {ev_b['title']}
Summary: {ev_b.get('review_summary', 'N/A')}
Modules: {ev_b.get('impact_scope', [])}

Classify as one of:
- redundant: solves same problem with same approach
- alternative: same goal, different approach
- conflicting: incompatible changes
- composable: can be merged together
- unrelated: no meaningful relationship

Return JSON: {{"relation": "...", "confidence": 0.0-1.0, "explanation": "..."}}"""

        try:
            result = worker.completion(prompt)
            parsed = _parse_json_response(_extract_completion_text(result))
        except Exception as exc:
            console.print(
                f"[yellow]Warning: failed to adjudicate PR pair ({pr_a}, {pr_b}): {exc}[/]"
            )
            continue

        relation = parsed.get("relation")
        if relation not in {"redundant", "alternative", "conflicting", "composable", "unrelated"}:
            continue
        if relation == "unrelated":
            continue
        relations.append(PRPairRelation(
            pr_a=pr_a,
            pr_b=pr_b,
            relation_type=relation,
            confidence=_safe_score(parsed.get("confidence"), default=0.5),
            explanation=str(parsed.get("explanation", "")),
        ))

    return relations


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
{json.dumps(clusters[:20], indent=2)}

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
