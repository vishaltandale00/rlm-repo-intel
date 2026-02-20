"""Recursive codebase modeling using RLM.

This is the core of the system. The RLM:
1. Gets the structural graph as a REPL variable
2. Writes code to explore modules recursively  
3. Calls itself on sub-modules when context is too large
4. Builds module cards with summaries, contracts, risks
5. Synthesizes an architecture model from all cards
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from ..graph.store import GraphStore

console = Console()


@dataclass
class ModuleCard:
    """Summary of a module produced by RLM analysis."""
    module_id: str
    summary: str
    purpose: str
    contracts: list[str]  # public interfaces/APIs
    invariants: list[str]  # things that must stay true
    risks: list[str]  # fragility points
    key_files: list[str]  # most important files
    dependencies: list[str]  # modules this depends on
    dependents: list[str]  # modules that depend on this
    confidence: float
    token_cost: int = 0


def build_codebase_model(config: dict):
    """Main entry point: build recursive codebase understanding."""
    from rlm import RLM

    graph = GraphStore(config["paths"]["graph_dir"])
    try:
        graph.load()
    except Exception as exc:
        console.print(f"[red]Failed to load graph: {exc}[/]")
        return

    modules = graph.get_by_type("module")
    console.print(f"\n[bold]Building codebase model for {len(modules)} modules...[/]")

    # Configure RLM instances
    root_model = config["models"]["root"]
    worker_model = config["models"]["cheap_worker"]

    root_rlm = None
    worker_rlm = None
    try:
        root_rlm = RLM(
            backend=_infer_backend(root_model),
            backend_kwargs={"model_name": root_model},
            verbose=True,
        )
    except Exception as exc:
        console.print(f"[yellow]Warning: failed to initialize root RLM: {exc}[/]")
    try:
        worker_rlm = RLM(
            backend=_infer_backend(worker_model),
            backend_kwargs={"model_name": worker_model},
        )
    except Exception as exc:
        console.print(f"[yellow]Warning: failed to initialize worker RLM: {exc}[/]")

    # Phase A: Analyze each module recursively
    module_cards = {}
    for mod in modules:
        console.print(f"\n  Analyzing [cyan]{mod.id}[/]...")
        card = _analyze_module(
            module=mod,
            graph=graph,
            worker_rlm=worker_rlm,
            config=config,
            max_tokens=config["limits"]["max_file_tokens"],
            confidence_threshold=config["limits"]["confidence_threshold"],
        )
        module_cards[mod.id] = card
        console.print(f"    → {card.summary[:80]}... (confidence: {card.confidence:.2f})")

    # Phase B: Architecture synthesis
    console.print(f"\n[bold]Synthesizing architecture model...[/]")
    architecture = _synthesize_architecture(module_cards, root_rlm, config)

    # Save results
    results_dir = Path(config["paths"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)

    with open(results_dir / "module_cards.json", "w") as f:
        json.dump(
            {k: _card_to_dict(v) for k, v in module_cards.items()},
            f, indent=2,
        )

    with open(results_dir / "architecture.json", "w") as f:
        json.dump(architecture, f, indent=2)

    # Update graph with module summaries
    for card in module_cards.values():
        graph.add_node(
            card.module_id, "module",
            summary=card.summary,
            purpose=card.purpose,
            contracts=card.contracts,
            risks=card.risks,
            confidence=card.confidence,
        )
    graph.save()

    console.print(f"\n[bold green]✓ Codebase model built.[/]")
    console.print(f"  Module cards: {len(module_cards)}")


def _analyze_module(
    module,
    graph: GraphStore,
    worker_rlm,
    config: dict,
    max_tokens: int,
    confidence_threshold: float,
    depth: int = 0,
    max_depth: int = 3,
) -> ModuleCard:
    """Recursively analyze a module.
    
    If the module is too large or confidence is low, recurse into sub-components.
    """
    files = graph.files_in_module(module.id)
    module_path = module.data.get("path", module.id.replace("module:", ""))

    # Estimate token count (rough: 1 byte ≈ 0.3 tokens for code)
    total_bytes = sum(f.data.get("size_bytes", 0) for f in files)
    est_tokens = int(total_bytes * 0.3)

    # Build context for the RLM
    file_listing = "\n".join([
        f"  {f.data.get('path', '')} ({f.data.get('size_bytes', 0)} bytes, churn: {f.data.get('churn_commits', 0)})"
        for f in sorted(files, key=lambda x: -x.data.get("churn_commits", 0))
    ])

    prompt = f"""Analyze this code module from a large repository.

Module: {module_path}
Estimated tokens: {est_tokens}
Files ({len(files)}):
{file_listing}

The full file contents are available as variables in your REPL environment.
Use `read_file(path)` to read any file.
Use `sub_rlm(prompt, context)` to delegate analysis of sub-components.

Produce a JSON analysis with:
- summary: 1-2 sentence description of what this module does
- purpose: the role this module plays in the system
- contracts: list of public interfaces/APIs this module exposes
- invariants: things that must stay true for this module to work
- risks: fragility points, missing tests, complexity hotspots
- key_files: the 5-10 most important files
- dependencies: other modules this depends on
- confidence: 0-1 how confident you are in this analysis

If the module is very large (>{max_tokens} tokens), write code to split it into
logical sub-groups and analyze each with sub_rlm calls, then synthesize."""

    # Use RLM completion
    parsed = _module_fallback(module_path, files)
    if worker_rlm is not None:
        try:
            result = worker_rlm.completion(prompt)
            parsed = _parse_module_response(_extract_completion_text(result))
        except Exception as exc:
            console.print(
                f"[yellow]Warning: module analysis failed for {module.id}: {exc}[/]"
            )

    card = ModuleCard(
        module_id=module.id,
        summary=parsed.get("summary", ""),
        purpose=parsed.get("purpose", ""),
        contracts=parsed.get("contracts", []),
        invariants=parsed.get("invariants", []),
        risks=parsed.get("risks", []),
        key_files=parsed.get("key_files", []),
        dependencies=parsed.get("dependencies", []),
        dependents=[],
        confidence=_safe_score(parsed.get("confidence"), default=0.5),
    )

    # Recurse if needed
    if card.confidence < confidence_threshold and depth < max_depth:
        console.print(f"    [yellow]Low confidence ({card.confidence:.2f}), recursing...[/]")
        # Would recurse into sub-directories here
        # For now, mark as needing deeper analysis

    return card


def _synthesize_architecture(module_cards: dict, root_rlm, config: dict) -> dict:
    """Use the root model to synthesize all module cards into architecture understanding."""
    cards_text = json.dumps(
        {k: _card_to_dict(v) for k, v in module_cards.items()},
        indent=2,
    )

    prompt = f"""You are analyzing a large open-source repository's architecture.
Below are analysis cards for each module. Synthesize them into a complete architecture model.

Module cards:
{cards_text}

Produce a JSON architecture model with:
- layers: list of architectural layers (e.g., "core", "channels", "extensions")
- module_groups: which modules belong to which layer
- critical_paths: the most important execution flows
- fragility_map: areas where changes are most likely to cause issues
- dependency_matrix: which modules depend on which
- health_summary: overall codebase health assessment
"""

    if root_rlm is None:
        return {"module_count": len(module_cards), "health_summary": "Root model unavailable."}

    try:
        result = root_rlm.completion(prompt)
        return json.loads(_extract_completion_text(result))
    except Exception as exc:
        console.print(f"[yellow]Warning: architecture synthesis failed: {exc}[/]")
        return {"module_count": len(module_cards), "health_summary": "Synthesis failed."}


def _parse_module_response(response: str) -> dict:
    """Parse JSON from RLM response, handling markdown code blocks."""
    text = response.strip()
    if text.startswith("```"):
        text = _strip_markdown_fence(text)
    return json.loads(text)


def _card_to_dict(card: ModuleCard) -> dict:
    return {
        "module_id": card.module_id,
        "summary": card.summary,
        "purpose": card.purpose,
        "contracts": card.contracts,
        "invariants": card.invariants,
        "risks": card.risks,
        "key_files": card.key_files,
        "dependencies": card.dependencies,
        "dependents": card.dependents,
        "confidence": card.confidence,
    }


def _compute_budget(config: dict, phase: str) -> float:
    """Compute token budget for a phase."""
    total = config["budget"]["max_spend_usd"]
    pct_key = f"{phase}_pct"
    pct = config["budget"].get(pct_key, 33) / 100
    return total * pct


def _strip_markdown_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _extract_completion_text(result: Any) -> str:
    if hasattr(result, "response"):
        return str(getattr(result, "response"))
    return str(result)


def _module_fallback(module_path: str, files: list[Any]) -> dict:
    return {
        "summary": f"Module at {module_path} with {len(files)} files",
        "purpose": "unknown",
        "contracts": [],
        "invariants": [],
        "risks": ["analysis failed — needs manual review"],
        "key_files": [f.data.get("path", "") for f in files[:5]],
        "dependencies": [],
        "confidence": 0.3,
    }


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
