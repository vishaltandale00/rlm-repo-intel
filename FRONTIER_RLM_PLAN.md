# Frontier RLM Plan (Sonnet 4 + Gemini Pro)

## 1) What `llm_query()` actually supports in this `rlm` build

### Source-level behavior
- `LocalREPL` injects:
  - `llm_query(prompt, model=None)`
  - `llm_query_batched(prompts, model=None)`
  - `rlm_query(prompt, model=None)`
- `llm_query` is a direct LM call through `LMHandler` (no recursive REPL loop).
- `rlm_query` uses `_subcall` to spawn a child `RLM` (recursive REPL), unless max depth is reached.

### Can we pass custom `system_prompt` per sub-call?
- There is no explicit `system_prompt` argument in `llm_query`.
- But yes, practically: pass a full OpenAI-style message list to `llm_query`:
  - `[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]`
- Why this works:
  - `LMRequest.prompt` accepts dict/list payloads.
  - Clients (`anthropic`, `openai`, `gemini`, `litellm`) all parse list-of-message prompts and support system instructions.

### Can we pass a different backend per call?
- Not directly. `llm_query` supports `model`, not `backend`.
- Routing behavior:
  - If `model` matches a registered client model name, that client is used.
  - Otherwise depth routing applies (`depth=1` prefers `other_backend_client` if configured).
- Current constructor guard only allows one `other_backend` entry.
- Practical implication: backend choice is indirect via model registration/routing, not an explicit `backend=` arg.

### Child-call system prompt behavior (`rlm_query`)
- `_subcall` creates child `RLM(... custom_system_prompt=self.system_prompt ...)`.
- So child RLM inherits parent system prompt unless you change library code.

## 2) Prompt engineering for true in-REPL debate (root orchestrates internally)

This design keeps debate *inside* the root RLM REPL. Root model (Sonnet 4) chooses when and how to call specialist sub-model prompts.

## Role prompts

```python
CODE_ANALYST_SYSTEM = """
You are a Senior Code Analyst.
Goal: explain actual behavior from evidence only.
Rules:
1. Ground every claim in provided snippets/diffs/metadata.
2. Cite files/functions/lines when available.
3. Distinguish facts vs inference.
4. Output JSON: {summary, key_findings[], unknowns[], evidence_refs[]}
""".strip()

ADVERSARIAL_REVIEWER_SYSTEM = """
You are an Adversarial Reviewer.
Goal: break the proposal/find hidden regressions.
Rules:
1. Attack assumptions, edge cases, failure paths.
2. Prefer concrete exploit/regression scenarios.
3. Classify severity: critical/high/medium/low.
4. Output JSON: {attacks[], likely_regressions[], weak_assumptions[], evidence_refs[]}
""".strip()

RISK_ASSESSOR_SYSTEM = """
You are a Risk Assessor for engineering and product release.
Goal: estimate impact and confidence.
Rules:
1. Score risk dimensions 0-5: correctness, reliability, security, operability.
2. Estimate confidence 0-1 and explain uncertainty drivers.
3. Recommend: ship / ship_with_guards / block.
4. Output JSON: {scores, confidence, recommendation, mitigations[], evidence_refs[]}
""".strip()

ARBITER_SYSTEM = """
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
```

## Root REPL orchestration helpers

```python
import json

ROLE_SYSTEM = {
    "analyst": CODE_ANALYST_SYSTEM,
    "adversary": ADVERSARIAL_REVIEWER_SYSTEM,
    "risk": RISK_ASSESSOR_SYSTEM,
    "arbiter": ARBITER_SYSTEM,
}

ROLE_MODEL = {
    "analyst": "claude-sonnet-4-20250514",
    "adversary": "gemini-3.1-pro",
    "risk": "claude-sonnet-4-20250514",
    "arbiter": "claude-sonnet-4-20250514",
}

def role_query(role: str, task: str, evidence: dict, model: str | None = None):
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
    raw = llm_query(messages, model=model or ROLE_MODEL[role])
    return raw
```

## Concrete debate loop in REPL

```python
# Example evidence bundle assembled by root from tools:
# evidence = {"pr": pr_meta, "diff": diff_text, "files": snippets, "issues": linked_issues, "graph": graph_hits}

task = "Review PR #482 for merge readiness and hidden regression risk."

analyst_out = role_query("analyst", task, evidence)
adversary_out = role_query(
    "adversary",
    "Challenge analyst conclusions; find strongest breakages and counterexamples.",
    {"task": task, "evidence": evidence, "analyst": analyst_out},
)
risk_out = role_query(
    "risk",
    "Assess release risk after seeing analyst + adversary.",
    {"task": task, "evidence": evidence, "analyst": analyst_out, "adversary": adversary_out},
)
final_out = role_query(
    "arbiter",
    "Produce final decision and fix list.",
    {
        "task": task,
        "evidence": evidence,
        "analyst": analyst_out,
        "adversary": adversary_out,
        "risk": risk_out,
    },
)

print(final_out)
```

## Root system prompt for frontier orchestration

```python
ROOT_FRONTIER_PROMPT = """
You are the Root Repository Intelligence Model (frontier-grade).

You must orchestrate analysis INSIDE the REPL:
- Gather evidence with tools first.
- Decide when to call specialist sub-model prompts via llm_query.
- Run internal debate: analyst -> adversary -> risk -> arbiter.
- Prefer targeted, high-value subcalls over brute-force scanning.

Routing policy:
1. Use analyst prompt for behavior understanding and architecture mapping.
2. Use adversary prompt when assumptions appear weak or behavior is ambiguous.
3. Use risk prompt after disagreement or when shipping decision is requested.
4. Use arbiter prompt for final merge decision and action list.

Quality bar:
- No unsupported claims.
- Explicit unknowns.
- Final output must include evidence references and concrete next checks.
""".strip()
```

## 3) What changes with frontier models vs small models

## What improves (yes, materially)
- You can push more orchestration into root RLM planning (less brittle hardcoded flow).
- Larger contexts make repo+PR+issue+graph synthesis practical in fewer hops.
- Debate quality is better: stronger self-critique and counterfactual testing.

## What does **not** disappear
- You still need hard resource limits in code (not trust-only prompting).
- Frontier models can still over-explore or loop if unconstrained.

## Recommended configuration shift

```python
root_rlm = RLM(
    backend="anthropic",
    backend_kwargs={"model_name": "claude-sonnet-4-20250514", "api_key": "..."},
    other_backends=["gemini"],
    other_backend_kwargs=[{"model_name": "gemini-3.1-pro", "api_key": "..."}],
    custom_system_prompt=ROOT_FRONTIER_PROMPT,
    custom_tools=build_custom_tools(config),
    custom_sub_tools=build_custom_tools(config),
    persistent=True,
    compaction=True,
    compaction_threshold_pct=0.90,
    max_depth=6,
    max_iterations=48,
    max_tokens=1_500_000,
    max_budget=60.0,
    max_timeout=1800,
    max_errors=5,
    verbose=True,
)
```

## Guardrail stance for frontier models
- Fewer *behavioral* guardrails: yes (allow root to choose specialist calls dynamically).
- Keep *hard* guardrails: absolutely yes.
  - Always cap `max_tokens`, `max_budget`, `max_timeout`, `max_errors`.
  - Keep tool-level limits (`limit`, pagination, bounded line ranges).
  - Keep compaction enabled for long trajectories.

## Optional library patch (if explicit per-call backend/system_prompt is required)

If you want first-class explicit controls instead of message-list workaround:
- Extend REPL API to `llm_query(prompt, model=None, system_prompt=None, backend=None)`.
- Add backend registry in `LMHandler` keyed by backend label.
- Pass `backend` through `LMRequest` and route in `get_client` by `(backend, model)`.

This is optional. For immediate execution, frontier orchestration works today using:
- structured message lists for per-call system prompts,
- `model=` for practical routing.
