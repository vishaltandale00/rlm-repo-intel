"""Export and publish results."""

import json
from pathlib import Path

import httpx
from rich.console import Console

console = Console()


def export_results(config: dict, fmt: str, output_dir: str, push_url: str | None = None):
    """Export results to files and optionally push to an API."""
    results_dir = Path(config["paths"]["results_dir"])
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Gather all result files
    result_files = {
        "architecture": results_dir / "architecture.json",
        "module_cards": results_dir / "module_cards.json",
        "pr_evaluations": results_dir / "pr_evaluations.jsonl",
        "pr_relations": results_dir / "pr_relations.jsonl",
        "pr_clusters": results_dir / "pr_clusters.json",
        "final_ranking": results_dir / "final_ranking.json",
    }

    # Copy to output dir
    for name, path in result_files.items():
        if path.exists():
            if path.suffix == ".jsonl":
                # Convert JSONL to JSON array for export
                with open(path) as f:
                    items = []
                    for line_number, line in enumerate(f, start=1):
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            console.print(
                                f"  [yellow]Skipping malformed JSONL row {line_number} in {path.name}[/]"
                            )
                with open(out / f"{name}.json", "w") as f:
                    json.dump(items, f, indent=2)
            else:
                import shutil
                shutil.copy(path, out / path.name)
            console.print(f"  Exported {name}")

    # Build combined summary
    summary = _build_summary(results_dir)
    with open(out / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    console.print(f"  Exported summary")

    # Push to API if configured
    if push_url:
        _push_to_api(push_url, summary, result_files, config)

    console.print(f"\n[bold green]✓ Export complete → {output_dir}[/]")


def _build_summary(results_dir: Path) -> dict:
    """Build a combined summary of all analysis."""
    summary = {
        "repo": None,
        "total_prs_evaluated": 0,
        "total_modules": 0,
        "top_prs": [],
        "clusters": 0,
        "themes": [],
    }

    # Module cards
    cards_path = results_dir / "module_cards.json"
    if cards_path.exists():
        cards = _safe_load_json(cards_path)
        if isinstance(cards, dict):
            summary["total_modules"] = len(cards)
        elif isinstance(cards, list):
            summary["total_modules"] = len(cards)

    # Final ranking
    ranking_path = results_dir / "final_ranking.json"
    if ranking_path.exists():
        ranking = _safe_load_json(ranking_path)
        summary["top_prs"] = ranking.get("ranking", [])[:20]
        summary["themes"] = ranking.get("themes", [])

    # PR evaluations
    eval_path = results_dir / "pr_evaluations.jsonl"
    if eval_path.exists():
        with open(eval_path) as f:
            total = 0
            for line in f:
                try:
                    json.loads(line)
                    total += 1
                except json.JSONDecodeError:
                    continue
            summary["total_prs_evaluated"] = total

    # Clusters
    clusters_path = results_dir / "pr_clusters.json"
    if clusters_path.exists():
        clusters = _safe_load_json(clusters_path)
        if isinstance(clusters, list):
            summary["clusters"] = len(clusters)

    return summary


def _push_to_api(base_url: str, summary: dict, result_files: dict, config: dict):
    """Push results to a web API (e.g., Clawmrades)."""
    console.print(f"\n  Pushing results to {base_url}...")

    headers = {}
    # Check for Clawmrades API key
    api_key_path = Path("~/.clawmrades/api-key").expanduser()
    if api_key_path.exists():
        headers["X-API-Key"] = api_key_path.read_text().strip()

    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
            # Push summary
            resp = client.post("/api/analysis/summary", json=summary)
            console.print(f"    Summary: {resp.status_code}")

            # Push individual evaluations
            eval_path = result_files.get("pr_evaluations")
            if eval_path and eval_path.exists():
                with open(eval_path) as f:
                    evals = [json.loads(line) for line in f]

                for ev in evals:
                    pr_num = ev["pr_number"]
                    resp = client.post(f"/api/prs/{pr_num}/analyze", json={
                        "risk_score": ev.get("risk_score", 0.5),
                        "quality_score": ev.get("quality_score", 0.5),
                        "review_summary": ev.get("review_summary", ""),
                        "description": ev.get("title", ""),
                        "has_tests": ev.get("test_alignment", 0) > 0.5,
                        "has_breaking_changes": ev.get("risk_score", 0) > 0.8,
                        "suggested_priority": _score_to_priority(ev.get("strategic_value", 0.5)),
                        "confidence": ev.get("confidence", 0.5),
                    })

                console.print(f"    PR evaluations: {len(evals)} pushed")

    except Exception as e:
        console.print(f"    [red]Push failed: {e}[/]")


def _score_to_priority(score: float) -> str:
    if score >= 0.8:
        return "critical"
    elif score >= 0.6:
        return "high"
    elif score >= 0.3:
        return "medium"
    return "low"


def _safe_load_json(path: Path) -> dict | list:
    try:
        with open(path) as f:
            loaded = json.load(f)
        if isinstance(loaded, (dict, list)):
            return loaded
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"  [yellow]Skipping malformed JSON file {path.name}: {exc}[/]")
    return {}
