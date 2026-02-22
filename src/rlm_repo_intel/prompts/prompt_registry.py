from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rlm_repo_intel.prompts.root_prompts import ROLE_MODEL, ROLE_SYSTEM, ROOT_FRONTIER_PROMPT, TRIAGE_TASK_PROMPT

_PROMPTS_DIR = Path(__file__).resolve().parent
_VERSIONS_DIR = _PROMPTS_DIR / "versions"
_REGISTRY_PATH = _PROMPTS_DIR / "registry.json"

_TOOLS_CONTRACT = {
    "required_tools": ["push_partial_results", "push_trace_step", "llm_query", "rlm_query"],
    "optional_tools": ["role_query", "web_search", "git_log", "git_blame"],
    "required_outputs": ["triage_results", "top_prs", "triage_summary", "triage_bundle"],
}


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def _canonical_bundle() -> dict[str, Any]:
    return {
        "root_system_prompt": _normalize_text(ROOT_FRONTIER_PROMPT),
        "task_prompt": _normalize_text(TRIAGE_TASK_PROMPT),
        "role_prompts": {key: _normalize_text(value) for key, value in ROLE_SYSTEM.items()},
        "role_models": dict(ROLE_MODEL),
        "tools_contract": dict(_TOOLS_CONTRACT),
    }


def _bundle_hash(bundle: dict[str, Any]) -> str:
    canonical = json.dumps(bundle, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_registry() -> dict[str, Any]:
    if not _REGISTRY_PATH.exists():
        return {"versions": {}}
    try:
        loaded = json.loads(_REGISTRY_PATH.read_text())
        if isinstance(loaded, dict) and isinstance(loaded.get("versions"), dict):
            return loaded
    except json.JSONDecodeError:
        pass
    return {"versions": {}}


def _write_registry(registry: dict[str, Any]) -> None:
    _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2, sort_keys=True))


def get_prompt_version() -> dict[str, Any]:
    bundle = _canonical_bundle()
    prompt_hash = _bundle_hash(bundle)
    timestamp = datetime.now(timezone.utc).isoformat()
    version_path = _VERSIONS_DIR / f"{prompt_hash}.json"

    _VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not version_path.exists():
        version_path.write_text(json.dumps(bundle, indent=2, sort_keys=True))

    registry = _read_registry()
    versions = registry.setdefault("versions", {})
    if prompt_hash not in versions:
        versions[prompt_hash] = {
            "hash": prompt_hash,
            "timestamp": timestamp,
            "path": str(version_path.relative_to(_PROMPTS_DIR)),
        }
        _write_registry(registry)
    else:
        timestamp = str(versions[prompt_hash].get("timestamp", timestamp))

    return {
        "hash": prompt_hash,
        "text": bundle["task_prompt"],
        "timestamp": timestamp,
        "bundle": bundle,
    }
