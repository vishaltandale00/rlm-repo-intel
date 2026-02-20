# VERSIONING_PLAN.md

## Goal
Turn the current PR-triage pipeline into a reproducible agent development and prompt tuning framework where every run is isolated, attributable, comparable, and auditable.

## Current State (Codebase Analysis)

### Pipeline and prompts
- `scripts/run_triage.py` builds a large inline prompt in `main()` (`prompt = (...)`) and calls `rlm.completion(prompt)`.
- `src/rlm_repo_intel/prompts/root_prompts.py` also defines `ROOT_FRONTIER_PROMPT`, but `scripts/run_triage.py` currently duplicates major prompt logic instead of strictly consuming one canonical source.
- `src/rlm_repo_intel/pipeline/rlm_session.py:create_frontier_rlm()` wires tools `push_partial_results` and `push_trace_step` from `src/rlm_repo_intel/tools/dashboard_callback.py`.

### Run management and push flow
- Dashboard backend already has run scaffolding:
  - `dashboard/lib/store.ts` has `createRunId()`, `startNewCurrentRun()`, per-run keys (`rlm:run:{id}:{kind}`), and legacy compatibility.
  - `dashboard/app/api/push/route.ts` accepts `run_id` and supports `type: "new_run"` to create a run.
- Python push client does **not** pass `run_id`:
  - `src/rlm_repo_intel/dashboard_push.py:_post()` sends `{type, data}` only.
  - `push_summary/push_evaluation/push_clusters/push_ranking/push_trace` do not take `run_id`.
- Consequence: pushes often rely on `getOrCreateCurrentRunId()` in API route, which can mix data if multiple pipelines run close together.

### Dashboard UI
- `dashboard/app/page.tsx` loads one run via `?run=<id>` and renders `DashboardClient`.
- `dashboard/components/dashboard-client.tsx` has a single-run selector and displays one run at a time.
- No side-by-side run comparison, no score delta view, no prompt diff view.

### Data hygiene
- `dashboard/lib/store.ts:cleanupRuns()` keeps only two runs (legacy + “best”), deletes all others.
- No run status taxonomy (`baseline`, `experimental`, `archived`, `deleted`), no retention policy metadata, no explicit archive flow.

## Target Principles
1. Every pipeline execution has a unique immutable `run_id` generated at startup.
2. Every result row is attributable to a `prompt_version` + config/model fingerprint.
3. Prompt text used for the run is persisted with run artifacts for reproducibility.
4. Dashboard can compare two runs on the same PR set and explain deltas.
5. Data lifecycle is explicit: active vs baseline vs archived vs deleted.

---

## 1) Prompt Versioning Plan

### Version identity strategy
Use a dual identifier:
- Human version: SemVer-like experiment label (`pv_major.minor.patch`, example `1.4.0`).
- Immutable content hash: SHA-256 of canonicalized prompt bundle (`prompt_hash`).

Why dual:
- SemVer is readable for tuning workflow.
- Hash guarantees exact content identity and prevents collisions when labels are reused.

### Canonical prompt bundle
Define a canonical prompt payload for hashing and storage:
- `root_system_prompt`: from `src/rlm_repo_intel/prompts/root_prompts.py:ROOT_FRONTIER_PROMPT`.
- `task_prompt`: from `scripts/run_triage.py` (currently inline in `main()`).
- `role_prompts` and `role_models`: from `src/rlm_repo_intel/prompts/root_prompts.py` (`ROLE_SYSTEM`, `ROLE_MODEL`) if used in run.
- `tools_contract`: expected tool names + required call patterns (`push_partial_results`, `push_trace_step`).

Canonicalization rule:
- JSON serialize with sorted keys, normalized newlines, stripped trailing whitespace before hash.

### Storage location
Store prompt bundle in both places:
- Git-tracked prompt sources (source of truth for authored prompt text).
- Run-scoped persisted snapshot in DB (`dashboard/lib/store.ts`) so historical runs are reproducible even after prompt files change.

Proposed run keys:
- `rlm:run:{run_id}:prompt_bundle`
- `rlm:run:{run_id}:prompt_diff_base` (optional cache)

### Associate run with prompt version
Add to run metadata (`rlm:run:{run_id}:meta`):
- `prompt_version`, `prompt_hash`, `prompt_label`, `prompt_source_paths`.

### Prompt comparison on dashboard
Add prompt diff endpoint/view:
- API: `GET /api/prompts/diff?run_a=<id>&run_b=<id>` (server-side text diff).
- UI component: `dashboard/components/prompt-diff.tsx`.
- Show:
  - changed sections
  - added/removed constraints
  - hash + version labels

---

## 2) Run Management Plan

### Required run metadata schema
Extend `RunInfo` in `dashboard/lib/store.ts` into full `RunMeta`:
- `id`
- `status` (`running|completed|failed|archived`)
- `kind` (`baseline|experimental|ab_control|ab_treatment`)
- `prompt_version`
- `prompt_hash`
- `model_root`
- `model_workers` (map)
- `config_snapshot` (budget/limits/iteration settings)
- `dataset_fingerprint` (hash of PR ids + repo/ref)
- `started_at`, `ended_at`
- `token_input`, `token_output`, `cost_usd`
- `total_prs_seen`, `total_prs_scored`
- `notes`, `tags`

### run_id lifecycle
- At startup, `scripts/run_triage.py` must request `type: "new_run"` once and capture returned `run_id`.
- Pass `run_id` into all push paths:
  - direct pushes in `scripts/run_triage.py` (`push_evaluation`, `push_summary`, `push_clusters`, `push_ranking`, `push_trace`)
  - streaming pushes from `src/rlm_repo_intel/tools/dashboard_callback.py` (`push_partial_results`, `push_trace_step`)
- Finalize run with explicit end event (`status=completed|failed`, `ended_at`).

### Concrete code touchpoints
- `src/rlm_repo_intel/dashboard_push.py`: make all `push_*` accept optional `run_id`; include it in POST payload.
- `src/rlm_repo_intel/tools/dashboard_callback.py`: add run context setter/resetter so internal pushes include current run.
- `src/rlm_repo_intel/pipeline/rlm_session.py`: wire run-aware push callbacks into `custom_tools`.
- `scripts/run_triage.py`: generate/start run once, pass through entire execution.
- `dashboard/app/api/push/route.ts`: add `run_meta` and `run_event` types; validate required fields for run finalization.

### Separation guarantee
All reads in dashboard should use explicit run IDs and avoid implicit global current-run behavior except for a fallback on landing page.

---

## 3) Results Comparison Plan

### Comparison data API
Add new route:
- `GET /api/compare?run_a=<id>&run_b=<id>`

Server logic (in route, backed by `dashboard/lib/store.ts`):
- Load evaluations for both runs.
- Join by `pr_number`.
- Compute per-PR deltas:
  - `delta_final_score`
  - `delta_urgency`
  - `delta_quality`
  - `delta_risk_if_merged`
  - rank movement
- Produce distribution histograms (fixed bins, eg 0-1...9-10).

### Dashboard comparison UI
Add components:
- `dashboard/components/run-compare-selector.tsx` (pick base/candidate).
- `dashboard/components/score-distribution-compare.tsx` (overlay histograms).
- `dashboard/components/pr-delta-table.tsx` (largest gains/regressions).
- `dashboard/components/prompt-impact-panel.tsx` (maps prompt diff sections to score movements).

### “What changed and why” heuristics
For each PR delta row, show:
- score delta magnitude
- changed prompt sections (from prompt diff)
- confidence note: heuristic attribution, not causal proof

Attribution method (first pass):
- section-level prompt change tags
- correlate with PR labels/impact_scope buckets
- report as inferred impact, not deterministic causality

---

## 4) Prompt Tuning Workflow Plan

### Standard iteration loop
1. Edit prompt source (single canonical location).
2. Compute `prompt_hash` + set `prompt_version` label.
3. Start run (`run_id`) with frozen config snapshot.
4. Execute pipeline and stream partials tagged by run.
5. Compare candidate run against baseline run.
6. Promote candidate to `baseline` if metrics improve.

### Reproducibility requirements
Persist with every run:
- prompt bundle snapshot
- model IDs
- config snapshot (`budget`, `limits`, `max_iterations`, `max_budget`)
- dataset fingerprint (exact PR set used)

### A/B test support
Add A/B mode in `scripts/run_triage.py` (plan only):
- Same PR set, two prompt bundles, two run IDs (`ab_control`, `ab_treatment`) under shared `experiment_id`.
- Store `experiment_id` in `RunMeta`.
- Dashboard compare view filters by `experiment_id`.

### Config management
Move run-time knobs into explicit run snapshot object built at startup from:
- `rlm-repo-intel.yaml`
- runtime overrides/CLI args
- effective model values from `create_frontier_rlm()`

---

## 5) Data Hygiene Plan

### Lifecycle states
Introduce run states and actions:
- `baseline`: protected from deletion unless forced.
- `experimental`: normal retention policy.
- `archived`: hidden from default view but recoverable.
- `deleted`: hard delete.

### Replace current cleanup behavior
Current `cleanupRuns()` is too aggressive for experimentation. Replace with policy-based cleanup:
- keep all `baseline` runs
- keep last N `experimental` runs
- archive runs older than retention window
- hard-delete only archived runs older than second threshold

### Operational endpoints
- `POST /api/runs/:id/archive`
- `POST /api/runs/:id/restore`
- `DELETE /api/runs/:id`
- `POST /api/runs/cleanup` with explicit policy payload

### Safety controls
- require `PUSH_SECRET` auth for mutating run endpoints
- add dry-run cleanup mode to preview deletions

---

## 6) Dashboard Updates Needed

### New views/components
- Run metadata panel (prompt version/hash, models, cost, timings, dataset fingerprint).
- Compare mode (two-run selector + summary deltas).
- Prompt diff panel.
- Per-PR delta table with sortable columns.
- Run tagging controls (`baseline`, `experimental`, `archived`).

### Existing file touchpoints
- `dashboard/app/page.tsx`: support compare params (`run_a`, `run_b`) in addition to current `run`.
- `dashboard/components/dashboard-client.tsx`: add compare mode state and panes.
- `dashboard/components/types.ts`: add `RunMeta`, `PromptBundle`, `PRDelta` types.
- `dashboard/lib/store.ts`: add prompt/meta storage and run lifecycle helpers.
- `dashboard/app/api/*`: add compare and run lifecycle routes.

### Display requirements
For each selected run show:
- `run_id`
- `prompt_version` + `prompt_hash`
- model(s)
- start/end/duration
- token + cost totals
- PR counts
- status + tags

---

## Implementation Phases (for coding agent)

### Phase 1: Data model + run plumbing
- Extend store metadata schema.
- Add run start/finalize events.
- Thread `run_id` through Python push paths.
- Persist prompt bundle snapshot per run.

### Phase 2: Comparison backend
- Build compare API and delta calculations.
- Add histogram and per-PR delta outputs.

### Phase 3: Dashboard compare UX
- Add dual run selector and compare panels.
- Add prompt diff UI.

### Phase 4: Prompt tuning and A/B workflow
- Add experiment metadata and A/B run linking.
- Add baseline promotion flow.

### Phase 5: Data hygiene and retention
- Replace aggressive cleanup with policy-based archive/delete.

---

## Acceptance Criteria
- Every pushed artifact (`summary`, `evaluation`, `clusters`, `ranking`, `trace`) includes and persists correct `run_id`.
- Every run has immutable `prompt_hash` and stored prompt snapshot.
- Dashboard can compare any two runs and show score distributions + PR deltas.
- Dashboard can render prompt diff between two runs.
- Runs can be marked baseline/experimental/archived and cleaned up via explicit policy.
- A/B paired runs can be created and compared under shared `experiment_id`.

## Known Risks / Constraints
- Current storage is KV-on-JSON (`rlm_kv`); large prompt bundles and traces may increase payload size and latency.
- Historical legacy data may not have prompt metadata; UI must handle missing values gracefully.
- Prompt-to-score attribution is inferential; must be labeled as heuristic, not causal proof.
