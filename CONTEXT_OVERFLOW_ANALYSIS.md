# Context Overflow Analysis

## Incident

Crash:

`litellm.BadRequestError: AnthropicException - input length and max_tokens exceed context limit: 143543 + 64000 > 200000`

Observed in compaction flow (`.venv/lib/python3.14/site-packages/rlm/core/rlm.py:566`) while `compaction=True`.

## Executive Summary

The root cause is **not** custom tool payload serialization into the prompt.

The real failure mode is:
1. Compaction trigger only checks current input tokens against `compaction_threshold_pct * context_limit`.
2. It does **not** reserve output tokens (`max_tokens`) for the next call.
3. With Anthropic 200k context and effective `max_tokens=64000`, the safe input ceiling is about **136k**, not 170k.
4. At 143,543 input tokens, compaction was not triggered (below 170k), but the next call failed: `143543 + 64000 > 200000`.

Additionally, compaction itself uses the same completion path, so if compaction triggers near 170k input, the compaction call can also exceed context.

## 1) How compaction works in rlms

### Trigger

- `compaction=True` enables checks per iteration.
- `_get_compaction_status()` computes:
  - `max_tokens = get_context_limit(model_name)`
  - `current_tokens = count_tokens(message_history, model_name)`
  - `threshold_tokens = int(compaction_threshold_pct * max_tokens)`
  - Source: `.venv/lib/python3.14/site-packages/rlm/core/rlm.py:526-534`
- Default `compaction_threshold_pct` is `0.85`.
  - Source: `.venv/lib/python3.14/site-packages/rlm/core/rlm.py:72`

### Action

When threshold is reached, `_compact_history()` sends a summarization completion built from full `message_history + summary request`, then replaces history with a compacted summary scaffold.
- Source: `.venv/lib/python3.14/site-packages/rlm/core/rlm.py:541-583`

## 2) Pipeline config findings (`src/rlm_repo_intel/pipeline/rlm_session.py`)

Configured:
- `compaction=True` (`src/rlm_repo_intel/pipeline/rlm_session.py:68`)
- `max_iterations=48` (`src/rlm_repo_intel/pipeline/rlm_session.py:70`)
- `max_budget=2000.0` (`src/rlm_repo_intel/pipeline/rlm_session.py:71`)

Not configured:
- `compaction_threshold_pct` (so default `0.85` is used)
- `max_tokens` (RLM total token budget guard is unset)
- No explicit per-call output token cap in this RLM construction

Model:
- `anthropic/claude-sonnet-4-20250514`
- Source: `src/rlm_repo_intel/pipeline/rlm_session.py:63`

## 3) System prompt size and custom tools section

`ROOT_FRONTIER_PROMPT` includes `{custom_tools_section}` placeholder.
- Source: `src/rlm_repo_intel/prompts/root_prompts.py:99`

The rlms prompt builder calls `format_tools_for_prompt(custom_tools)`.
- Source: `.venv/lib/python3.14/site-packages/rlm/utils/prompts.py:146-156`

`format_tools_for_prompt()` does **not** serialize large dict/list values. For non-callables without explicit description, it only emits type-level lines like:
- ``- `repo`: A custom dict value``
- ``- `prs`: A custom list value``
- Source: `.venv/lib/python3.14/site-packages/rlm/environments/base_env.py:96-127`

Measured locally with synthetic large `repo/prs/issues` payloads (same order of magnitude as yours):
- Root system prompt chars: `4738`
- Initial message history tokens: `1091`
- Delta between small vs huge tool payloads: `0` tokens

Conclusion: `{custom_tools_section}` is not causing 100k+ token growth.

## 4) How custom_tools are actually injected

`custom_tools` are injected into REPL globals/locals, not prompt text:
- Passed into environment kwargs: `.venv/lib/python3.14/site-packages/rlm/core/rlm.py:234-238`
- Local REPL setup injects callables to `globals`, non-callables to `locals`:
  - `.venv/lib/python3.14/site-packages/rlm/environments/local_repl.py:198-207`

So large objects (`repo`, `prs`, `issues`) are available as REPL variables, which is the correct mechanism.

## 5) Why compaction still overflowed

For this model family, context limit is 200k in rlms token table.
- Source: `.venv/lib/python3.14/site-packages/rlm/utils/token_utils.py:36-41`

With default threshold `0.85`, compaction waits until ~170k input tokens.

But failure shows request was validated as:
- input: `143,543`
- output reservation (`max_tokens`): `64,000`
- total: `207,543` > `200,000`

So compaction check is mathematically too late because it ignores output reservation.

This also explains why compaction call can fail: `_compact_history()` sends another completion on near-threshold history, with the same output reservation behavior.

## 6) Concrete fixes

### Immediate pipeline mitigation

1. Set `compaction_threshold_pct` low enough to reserve completion budget.
   - For 200k context and 64k output reservation, maximum safe input is ~136k.
   - Recommended start: `0.60-0.65` (gives headroom for wrappers and token estimation error).
2. Reduce per-call output cap for root model (for orchestration turns, 2k-8k is usually enough).

### Library-level fix (recommended)

Update compaction decision to reserve output tokens and safety margin:

`compact_when current_input_tokens >= (context_limit - reserved_output_tokens - safety_margin)`

Where:
- `reserved_output_tokens` is explicit model call budget (not implicit provider default)
- `safety_margin` e.g. `2k-8k`

Also apply this rule to `_compact_history()` summarization calls and force low summary output budget (e.g. `max_tokens=512-1024`).

### LiteLLM client fix

`LiteLLMClient` currently builds kwargs from `model/messages/timeout` and does not forward generic backend kwargs to `litellm.completion()`.
- Source: `.venv/lib/python3.14/site-packages/rlm/clients/litellm.py:46-53`

This prevents reliably setting `max_tokens` via `backend_kwargs` at RLM construction.

Fix: explicitly pass allowed completion params (at least `max_tokens`, `temperature`, `top_p`, stop fields, etc.) from client config into each call.

## Final Root Cause

Primary: **Compaction threshold logic is output-unaware** (input-only check).

Secondary: **LiteLLM backend path does not expose an explicit per-call output cap from `backend_kwargs`**, making large implicit output reservations more likely.

Not root cause: **custom_tools serialization into prompt/context**.
