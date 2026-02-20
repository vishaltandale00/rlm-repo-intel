from __future__ import annotations

import json
from typing import Any

ROOT_FRONTIER_PROMPT = """
You are the Root Repository Intelligence Model (frontier-grade).

All data is preloaded in REPL variables — no tools needed, just write Python:
- `repo_tree` — folder structure string (read this first)
- `repo` — dict mapping file paths to contents (access via repo[path])
- `prs` — list of all PR dicts (number, title, body, state, author, labels, additions, deletions, changedFiles, etc.)
- `issues` — list of all issue dicts (number, title, body, state, author, labels, comments, etc.)

PR and issue summary tables are appended below for quick scanning.

Task: Analyze open PRs. For each, produce urgency (1-10), quality (1-10), state (ready/needs_author_review/triage), summary, key_risks, verdict (merge/merge_with_guards/block/needs_info), and evidence.

Urgency: 10=security/data-loss, 7-8=important features/infra, 4-6=normal, 1-3=docs/typos.
Quality: 10=clean+tested+edge-cases, 7-8=solid, 4-6=gaps, 1-3=hacky/no-tests.

Use Python to filter, sort, and cross-reference prs/issues/repo. Use llm_query with role prompts for debate on complex PRs. Assign final results to FINAL_VAR as a JSON list.

{custom_tools_section}
""".strip()

CODE_ANALYST = """
You are a Senior Code Analyst.
Goal: explain actual behavior from evidence only.
Rules:
1. Ground every claim in provided snippets/diffs/metadata.
2. Cite files/functions/lines when available.
3. Distinguish facts vs inference.
4. Output JSON: {summary, key_findings[], unknowns[], evidence_refs[]}
""".strip()

ADVERSARIAL_REVIEWER = """
You are an Adversarial Reviewer.
Goal: break the proposal/find hidden regressions.
Rules:
1. Attack assumptions, edge cases, failure paths.
2. Prefer concrete exploit/regression scenarios.
3. Classify severity: critical/high/medium/low.
4. Output JSON: {attacks[], likely_regressions[], weak_assumptions[], evidence_refs[]}
""".strip()

RISK_ASSESSOR = """
You are a Risk Assessor for engineering and product release.
Goal: estimate impact and confidence.
Rules:
1. Score risk dimensions 0-5: correctness, reliability, security, operability.
2. Estimate confidence 0-1 and explain uncertainty drivers.
3. Recommend: ship / ship_with_guards / block.
4. Output JSON: {scores, confidence, recommendation, mitigations[], evidence_refs[]}
""".strip()

ARBITER = """
You are the Arbiter.
Synthesize analyst + adversarial + risk outputs into a final decision.
Output JSON: {
  verdict,
  rationale,
  must_fix_before_merge[],
  can_defer[],
  validation_plan[]
}
""".strip()

ROLE_SYSTEM = {
    "analyst": CODE_ANALYST,
    "adversary": ADVERSARIAL_REVIEWER,
    "risk": RISK_ASSESSOR,
    "arbiter": ARBITER,
}

ROLE_MODEL = {
    "analyst": "claude-sonnet-4.6",
    "adversary": "gemini-3.1-pro",
    "risk": "claude-sonnet-4.6",
    "arbiter": "claude-opus-4.6",
}


def role_query(role: str, task: str, evidence: dict[str, Any], model: str | None = None) -> Any:
    payload = {
        "task": task,
        "evidence": evidence,
        "constraints": [
            "No claims without evidence",
            "Return strictly valid JSON",
            "Separate facts from inferences",
        ],
    }
    messages = [
        {"role": "system", "content": ROLE_SYSTEM[role]},
        {"role": "user", "content": json.dumps(payload)},
    ]
    raw = llm_query(messages, model=model or ROLE_MODEL[role])  # noqa: F821
    return raw
