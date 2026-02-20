"""Build structural graph from repository — deterministic, no LLM calls."""

import json
import subprocess
from collections import defaultdict
from pathlib import Path

from rich.console import Console

console = Console()

# File extensions we care about for code analysis
CODE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go",
    ".java", ".kt", ".swift", ".c", ".cpp", ".h",
    ".vue", ".svelte",
}

# Files/dirs to skip
SKIP_PATTERNS = {
    "node_modules", "dist", "build", ".git", "__pycache__",
    "vendor", ".next", "coverage", ".turbo",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
}


def build_structural_graph(repo_dir: Path, config: dict) -> dict:
    """Build the initial structural graph from filesystem + git metadata.
    
    This is the deterministic pre-pass — no LLM calls.
    Produces: nodes (files, directories, candidate modules) and edges (contains, imports).
    """
    graph_dir = Path(config["paths"]["graph_dir"])
    graph_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Scan all source files
    files = _scan_files(repo_dir)
    console.print(f"  Found {len(files)} source files")

    # Step 2: Build directory tree with stats
    dir_tree = _build_dir_tree(files, repo_dir)

    # Step 3: Extract import relationships (lightweight regex-based)
    imports = _extract_imports(files, repo_dir)

    # Step 4: Identify candidate modules (directories with meaningful code)
    modules = _identify_modules(dir_tree, files, repo_dir)
    console.print(f"  Identified {len(modules)} candidate modules")

    # Step 5: Get git churn data
    churn = _get_churn_data(repo_dir)

    # Step 6: Assemble graph
    graph = {
        "nodes": [],
        "edges": [],
    }

    # Add file nodes
    for f in files:
        rel_path = str(f.relative_to(repo_dir))
        graph["nodes"].append({
            "id": f"file:{rel_path}",
            "type": "file",
            "path": rel_path,
            "extension": f.suffix,
            "size_bytes": f.stat().st_size,
            "churn_commits": churn.get(rel_path, 0),
        })

    # Add module nodes
    for mod_path, mod_info in modules.items():
        graph["nodes"].append({
            "id": f"module:{mod_path}",
            "type": "module",
            "path": mod_path,
            "file_count": mod_info["file_count"],
            "total_bytes": mod_info["total_bytes"],
            "top_files": mod_info["top_files"][:10],
        })

    # Add contains edges (module -> file)
    module_paths = sorted(modules.keys(), key=len, reverse=True)
    for f in files:
        rel_path = str(f.relative_to(repo_dir))
        for mod_path in module_paths:
            mod_prefix = f"{mod_path}/"
            if rel_path == mod_path or rel_path.startswith(mod_prefix):
                graph["edges"].append({
                    "source": f"module:{mod_path}",
                    "target": f"file:{rel_path}",
                    "type": "contains",
                })
                break

    # Add import edges
    for source_file, imported_files in imports.items():
        for target in imported_files:
            graph["edges"].append({
                "source": f"file:{source_file}",
                "target": f"file:{target}",
                "type": "imports",
            })

    # Save graph
    with open(graph_dir / "structural_graph.json", "w") as f:
        json.dump(graph, f, indent=2)

    # Save summary stats
    stats = {
        "files": len(files),
        "modules": len(modules),
        "import_edges": sum(len(imported) for imported in imports.values()),
        "total_bytes": sum(f.stat().st_size for f in files),
    }
    with open(graph_dir / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    return stats


def _scan_files(repo_dir: Path) -> list[Path]:
    """Scan for source code files, skipping vendored/generated content."""
    files = []
    for f in repo_dir.rglob("*"):
        if not f.is_file():
            continue
        if any(skip in f.parts for skip in SKIP_PATTERNS):
            continue
        if f.suffix in CODE_EXTENSIONS:
            files.append(f)
    return sorted(files)


def _build_dir_tree(files: list[Path], repo_dir: Path) -> dict:
    """Build directory tree with aggregated stats."""
    tree = defaultdict(lambda: {"files": [], "bytes": 0})
    for f in files:
        rel = f.relative_to(repo_dir)
        for parent in rel.parents:
            if str(parent) != ".":
                tree[str(parent)]["files"].append(str(rel))
                tree[str(parent)]["bytes"] += f.stat().st_size
    return dict(tree)


def _identify_modules(dir_tree: dict, files: list[Path], repo_dir: Path) -> dict:
    """Identify candidate modules — directories that represent logical units.
    
    Heuristic: directories 2-3 levels deep with >= 3 source files.
    """
    file_sizes = {
        str(file_path.relative_to(repo_dir)): file_path.stat().st_size for file_path in files
    }

    modules = {}
    for dir_path, info in dir_tree.items():
        depth = len(Path(dir_path).parts)
        if 1 <= depth <= 3 and len(info["files"]) >= 3:
            modules[dir_path] = {
                "file_count": len(info["files"]),
                "total_bytes": info["bytes"],
                "top_files": sorted(
                    info["files"],
                    key=lambda rel_file: -file_sizes.get(rel_file, 0),
                )[:10],
            }
    return modules


def _extract_imports(files: list[Path], repo_dir: Path) -> dict[str, list[str]]:
    """Extract import relationships using simple regex patterns.
    
    Supports: TypeScript/JavaScript imports, Python imports.
    This is intentionally simple — RLM will do deeper analysis later.
    """
    import re

    ts_import_re = re.compile(r"""(?:import|from)\s+['"]([./][^'"]+)['"]""")
    py_import_re = re.compile(r"""(?:from|import)\s+([\w.]+)""")

    imports = {}
    file_set = {str(f.relative_to(repo_dir)) for f in files}

    for f in files:
        rel_path = str(f.relative_to(repo_dir))
        found = set()

        try:
            content = f.read_text(errors="ignore")
        except Exception:
            continue

        if f.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            for match in ts_import_re.finditer(content):
                imported = match.group(1)
                # Resolve relative import to absolute path
                resolved = _resolve_ts_import(rel_path, imported, file_set)
                if resolved and resolved in file_set:
                    found.add(resolved)

        elif f.suffix == ".py":
            for match in py_import_re.finditer(content):
                imported = match.group(1).replace(".", "/")
                candidates = [f"{imported}.py", f"{imported}/__init__.py"]
                for c in candidates:
                    if c in file_set:
                        found.add(c)

        if found:
            imports[rel_path] = list(found)

    return imports


def _resolve_ts_import(source_path: str, import_path: str, file_set: set[str]) -> str | None:
    """Resolve a relative TypeScript/JS import to a file path."""
    if not import_path.startswith("."):
        return None

    source_dir = str(Path(source_path).parent)
    resolved = Path(source_dir) / import_path

    candidates = [str(resolved)]
    if resolved.suffix == "":
        # Try common extensions and index file conventions.
        for ext in [".ts", ".tsx", ".js", ".jsx"]:
            candidates.append(str(Path(str(resolved) + ext)))
        for index_name in ["/index.ts", "/index.tsx", "/index.js", "/index.jsx"]:
            candidates.append(str(Path(str(resolved) + index_name)))

    for candidate in candidates:
        if candidate in file_set:
            return candidate

    return None


def _get_churn_data(repo_dir: Path, limit: int = 1000) -> dict[str, int]:
    """Get file churn (commit count) from git log."""
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:", "--name-only", f"-{limit}"],
            cwd=repo_dir,
            capture_output=True, text=True, timeout=30,
        )
        churn = defaultdict(int)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                churn[line] += 1
        return dict(churn)
    except Exception:
        return {}
