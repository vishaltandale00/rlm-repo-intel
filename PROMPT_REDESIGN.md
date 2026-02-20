# Prompt Redesign Plan: Deep Codebase-Aware PR Triage

## Scope and objective
Redesign the PR triage prompt and response contract so the RLM behaves like a codebase owner, performs evidence-backed analysis, and outputs a curated elite list of open PRs.

Target operating outcome:
- Analyze all open PRs for OpenClaw with a multi-phase process.
- Deep-dive only the highest-leverage subset.
- Produce exactly one curated `top_prs` list containing 100-150 PRs, all with `final_score >= 9.0`.
- Every scored PR in deep-analysis phases includes mandatory justification and file-level evidence.

Non-goals for this phase:
- No code changes in this document.
- No scoring model retraining.

## Current failure modes to eliminate
1. Keyword/metadata shortcuts instead of real diff+code reading.
2. Missing `urgency`/`quality` population or zero defaults.
3. Missing justifications.
4. Missing evidence links to files/lines.
5. Throughput-first behavior (2,645 PRs shallow pass).
6. Flat, non-informative score distributions.

## Design principles (prompt philosophy)
1. Ownership mindset
- System prompt frames the model as the accountable owner of OpenClaw quality and stability.
- Explicit consequence framing: unsupported scoring is considered a critical failure.

2. Evidence-first reasoning
- No score is valid without explicit evidence from PR diff and repository files.
- Force distinction between observed facts vs inferred risk.

3. Quality over quantity
- Phase-separated pipeline: broad metadata sweep, then focused deep analysis.
- The expensive reasoning budget is spent on ~300 candidates, not all PRs equally.

4. Hard output contract
- `triage_results` schema with required fields and validation rules.
- Explicit top-list contract to guarantee 100-150 elite PRs and prevent unbounded outputs.

5. Deterministic review discipline
- Enforced workflow checkpoints and minimum evidence requirements.
- Reject incomplete PR records before finalization.

## End-state architecture (prompt-driven behavior)
The root prompt should instruct the model to execute four strict phases and persist artifacts in REPL variables:
- `phase1_candidates`
- `phase2_deep_analysis`
- `phase3_scored`
- `triage_results`

The prompt must prohibit skipping phases and require explicit completion checks between phases.

## Phase plan

### Phase 1: Broad metadata triage (all open PRs -> ~300 candidates)
Input:
- Open PR metadata only: title, labels, changed files count, additions/deletions, touched paths, author activity, issue links.

Actions:
- Iterate all open PRs.
- Assign a coarse `candidate_priority` (0-3) using metadata only.
- Select approximately 300 PRs for deep analysis using transparent criteria:
  - Critical path files touched (auth, permission, data integrity, migrations, API contracts, infra/deploy, billing/security).
  - High blast radius (many files/large deltas/core modules).
  - Incident-related labels/issues.
  - Regression-prone patterns (removal of tests, config changes, concurrency/state logic).

Outputs:
- `phase1_candidates`: list of ~300 PR numbers + reason tags.
- `phase1_summary`: counts by reason tag and excluded volume.

Guardrails:
- Phase 1 must not assign final scores.
- Phase 1 must log why each PR entered candidate pool.

### Phase 2: Deep code analysis (~300 PRs)
Input:
- Full PR diffs for candidates.
- Corresponding repository files from `repo` dict.
- Relevant tests and neighboring modules.

Actions per PR:
1. Read diff hunks, enumerate changed files.
2. For each critical changed file, inspect current repository version/context.
3. Determine what behavior changed, not just what text changed.
4. Check test impact:
- Existing tests covering changed behavior.
- New/updated tests in PR.
- Coverage gaps and untested failure paths.
5. Evaluate downstream dependency impact and compatibility.
6. Capture explicit evidence references.

Required artifact per PR:
- `analysis_facts`: concrete observations from code/diff.
- `analysis_inferences`: risk/quality interpretations derived from facts.
- `evidence`: 3-8 references with file paths (line/hunk context when available).

Guardrails:
- If `evidence` count < 3 for non-trivial PR (`changedFiles >= 3`), mark as incomplete and re-analyze.
- If justification contains generic language without file references, mark invalid.

### Phase 3: Precision scoring and justification (~300 PRs)
Scoring dimensions (all float, 1.0-10.0):
- `urgency`: operational/business time sensitivity and impact if delayed.
- `quality`: implementation quality, maintainability, test rigor, correctness posture.
- `risk_if_merged`: probability/severity of regression or incident.
- `criticality`: architectural/system importance of impacted area.

Primary ranking score:
- `final_score = 0.35*urgency + 0.30*quality + 0.20*criticality + 0.15*(10 - risk_if_merged)`
- Keep two decimals.

Scoring policy:
- 5.0-7.9: normal backlog PRs.
- 8.0-8.9: high-value but not elite immediate-merge tier.
- 9.0-10.0: elite critical and high-quality merge-now candidates.

Mandatory per-PR fields:
- `justification`: 80-220 words, concrete and specific.
- `merge_recommendation`: `merge_now | merge_after_fixes | hold`.
- `must_fix_before_merge`: required when recommendation is not `merge_now`.

Guardrails:
- Reject any PR result where `urgency==0`, `quality==0`, or missing justification.
- Reject scores with no differentiation (for example if stdev of `final_score` is too low).

### Phase 4: Elite list curation (top 100-150 with >=9.0)
Actions:
1. Filter Phase 3 results to `final_score >= 9.0`.
2. Sort descending by `final_score`, then by `urgency`, then by `criticality`.
3. Curate final list size to 100-150:
- Hard cap at 150.
- Target 120 by default.
- If >150 pass threshold, keep top 150 and record cutoff rationale.
- If <100 pass threshold, perform calibration pass (relative normalization + tie-break review) but do not fabricate evidence.
4. Produce final curated list and rationale summary.

Output:
- `top_prs`: elite list only (100-150 entries).
- `triage_results`: full scored set for audited candidates (not necessarily all open PRs deeply analyzed).

## Prompt structure redesign
Use a layered prompt contract:

1. System prompt (identity + non-negotiables)
- Owner accountability framing.
- Strict no-shortcut policy.
- Evidence and justification mandates.
- Phase order is mandatory.

2. Task prompt (run-specific constraints)
- Dataset scope (`prs`, `repo`, `issues`, etc.).
- Numeric target constraints (candidate count, top list size, 9.0+ threshold).
- Output schema requirements.

3. Self-check checklist (must execute before final output)
- All required fields present.
- No zeroed urgency/quality.
- Evidence attached for each scored PR.
- Top list count within 100-150 and all >=9.0.

## Output schema contract

### `triage_results` (required, list)
Each item (required fields unless marked optional):
- `pr_number` (int)
- `title` (string)
- `author` (string)
- `state` (`ready | needs_author_review | triage`)
- `urgency` (float 1.0-10.0)
- `quality` (float 1.0-10.0)
- `risk_if_merged` (float 1.0-10.0)
- `criticality` (float 1.0-10.0)
- `final_score` (float 1.0-10.0)
- `merge_recommendation` (`merge_now | merge_after_fixes | hold`)
- `justification` (string, mandatory)
- `key_risks` (list[string], min 1)
- `must_fix_before_merge` (list[string], optional, required if not `merge_now`)
- `evidence` (list[object], min 2)

`evidence` object:
- `file` (string path from `repo` dict)
- `reference_type` (`diff_hunk | repo_file | test_file | issue_link`)
- `detail` (string with concrete code-behavior note)
- `line_hint` (string, optional)

### `top_prs` (required, list)
- Subset of `triage_results`.
- Length must be 100-150.
- Every item must satisfy `final_score >= 9.0`.
- Include `elite_rank` (1..N).

### `triage_summary` (required, object)
- `total_open_prs_seen`
- `phase1_candidates_count`
- `deep_analyzed_count`
- `scored_count`
- `elite_count`
- `score_distribution` (bucketed histogram)
- `validation_checks` (pass/fail map)

## Anti-shortcut enforcement plan
1. Explicit prohibitions in prompt
- "Do not score PRs from metadata alone beyond Phase 1."
- "Do not use keyword-only heuristics as final evidence."

2. Evidence quota checks
- Every deep-scored PR must include evidence entries mapped to real file paths.
- At least one evidence item must come from a changed file in the diff.
- At least one evidence item must discuss testing (existing or missing).

3. Justification quality checks
- Ban generic text patterns (for example: "looks good", "seems fine") without references.
- Require at least two concrete claims tied to evidence.

4. Analysis trace artifacts
- For each PR keep compact trace fields:
  - `files_read_count`
  - `diff_hunks_reviewed`
  - `tests_reviewed`
- Use these as verification signals in final QA.

5. Final validation gate
- If any required field/evidence rule fails, model must return `validation_failed` with defect list instead of final ranking.

## Token and budget strategy

### Budget split
- Phase 1: ~15% tokens (broad metadata sweep).
- Phase 2: ~60% tokens (deep analysis on ~300 PRs).
- Phase 3: ~15% tokens (scoring calibration + normalization).
- Phase 4: ~10% tokens (elite curation + QA checks).

### Depth controls
- Phase 1 uses metadata only and no long-form reasoning.
- Phase 2 applies tiered depth:
  - High-risk candidates: full deep pass (more files and tests).
  - Medium-risk candidates: moderate pass.
  - Low-risk candidates within candidate pool: concise but evidence-backed pass.

### `llm_query` usage policy
Use `llm_query` for narrow sub-analyses only:
- Security risk deep checks.
- Test gap extraction.
- Architecture impact reasoning on specific module clusters.

Keep main REPL responsible for:
- Phase orchestration.
- Candidate selection.
- Score assembly.
- Final validation and curation.

Rule:
- Never call `llm_query` in bulk for all PRs blindly.
- Only call when a PR passes candidate/depth criteria or when disagreement exists.

## Implementation checklist for follow-up agent
1. Replace current root triage prompt text with phased redesign contract.
2. Add strict output schema instructions for `triage_results`, `top_prs`, `triage_summary`.
3. Add explicit validation/checklist block in prompt.
4. Ensure prompt requires storing phase artifacts in named variables.
5. Add calibration instructions for enforcing elite list size and threshold.
6. Add anti-shortcut language and evidence quotas.
7. Verify dashboard/export compatibility with new fields (if needed in later code task).

## Acceptance criteria
1. Output includes all three artifacts: `triage_results`, `top_prs`, `triage_summary`.
2. `top_prs` count is between 100 and 150 inclusive.
3. Every `top_prs` entry has `final_score >= 9.0` and non-empty justification.
4. 100% of deep-scored PRs have evidence with concrete repository file references.
5. `urgency` and `quality` are populated floats with non-trivial distribution.
6. Validation checks report pass status; otherwise run is marked failed.

## Risks and mitigations
- Risk: Too few truly elite PRs to hit 100 while keeping quality bar.
- Mitigation: Calibration pass with transparent threshold review, plus explicit warning when quality bar cannot be met without dilution.

- Risk: Token exhaustion during Phase 2.
- Mitigation: Tiered depth policy and focused `llm_query` calls only for high-uncertainty PRs.

- Risk: Schema drift breaks downstream tooling.
- Mitigation: Keep compatibility mapping in implementation phase and add field-normalization tests.

## Deliverable from this plan
A redesigned prompt + validation contract that forces deep, codebase-aware triage and yields a curated elite list (100-150 PRs, all >=9.0) with defensible evidence-backed reasoning.
