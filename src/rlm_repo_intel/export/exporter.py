"""Export and publish results."""

import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from rlm_repo_intel import dashboard_push
from rlm_repo_intel.dashboard_push import (
    push_clusters,
    push_evaluation,
    push_ranking,
    push_summary,
    push_trace,
    start_new_run,
)

console = Console()


def export_results(config: dict, fmt: str, output_dir: str, push: bool = False):
    """Export results to files and optionally push to the dashboard."""
    results_dir = Path(config["paths"]["results_dir"])
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Gather all result files
    result_files = {
        "architecture": results_dir / "architecture.json",
        "module_cards": results_dir / "module_cards.json",
        "pr_evaluations": results_dir / "pr_evaluations.jsonl",
        "pr_reasoning_traces": results_dir / "pr_reasoning_traces.jsonl",
        "pr_relations": results_dir / "pr_relations.jsonl",
        "pr_relation_debates": results_dir / "pr_relation_debates.jsonl",
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

    # Push to dashboard if requested
    if push:
        _push_to_dashboard(summary, result_files)

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


def _push_to_dashboard(summary: dict, result_files: dict):
    """Push exported artifacts to the Vercel dashboard API."""
    console.print(f"\n  Pushing results to dashboard {dashboard_push.DASHBOARD_API_URL}...")

    def _load_json_file(path: Path):
        with open(path) as f:
            return json.load(f)

    def _load_jsonl_file(path: Path):
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _push_optional(payload_type: str, data, run_id: str):
        # module_cards and other export-only artifacts do not yet have dedicated wrappers.
        dashboard_push._post(payload_type, data, run_id=run_id)

    try:
        run_meta = {
            "repo": summary.get("repo"),
            "model": "anthropic/claude-sonnet-4-6",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "total_modules": summary.get("total_modules", 0),
            "total_prs_evaluated": summary.get("total_prs_evaluated", 0),
        }
        run_id = start_new_run(run_meta)
        push_summary(summary, run_id=run_id)
        console.print("    Summary: pushed")

        module_cards_path = result_files.get("module_cards")
        if module_cards_path and module_cards_path.exists():
            _push_optional("module_cards", _load_json_file(module_cards_path), run_id=run_id)
            console.print("    Module cards: pushed")

        eval_path = result_files.get("pr_evaluations")
        if eval_path and eval_path.exists():
            evaluations = _load_jsonl_file(eval_path)
            for evaluation in evaluations:
                push_evaluation(evaluation, run_id=run_id)
            console.print(f"    PR evaluations: {len(evaluations)} pushed")

        clusters_path = result_files.get("pr_clusters")
        if clusters_path and clusters_path.exists():
            push_clusters(_load_json_file(clusters_path), run_id=run_id)
            console.print("    Clusters: pushed")

        ranking_path = result_files.get("final_ranking")
        if ranking_path and ranking_path.exists():
            push_ranking(_load_json_file(ranking_path), run_id=run_id)
            console.print("    Final ranking: pushed")

        trace_path = result_files.get("pr_reasoning_traces")
        if trace_path and trace_path.exists():
            push_trace(_load_jsonl_file(trace_path), run_id=run_id)
            console.print("    Reasoning traces: pushed")

        relations_path = result_files.get("pr_relations")
        if relations_path and relations_path.exists():
            _push_optional("relations", _load_jsonl_file(relations_path), run_id=run_id)
            console.print("    PR relations: pushed")

        debates_path = result_files.get("pr_relation_debates")
        if debates_path and debates_path.exists():
            _push_optional("relation_debates", _load_jsonl_file(debates_path), run_id=run_id)
            console.print("    Relation debates: pushed")

        architecture_path = result_files.get("architecture")
        if architecture_path and architecture_path.exists():
            _push_optional("architecture", _load_json_file(architecture_path), run_id=run_id)
            console.print("    Architecture: pushed")
    except Exception as e:
        console.print(f"    [red]Dashboard push failed: {e}[/]")


def _safe_load_json(path: Path) -> dict | list:
    try:
        with open(path) as f:
            loaded = json.load(f)
        if isinstance(loaded, (dict, list)):
            return loaded
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"  [yellow]Skipping malformed JSON file {path.name}: {exc}[/]")
    return {}
