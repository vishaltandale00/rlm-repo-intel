/**
 * Neon Postgres-backed store for analysis results.
 * Requires DATABASE_URL env var pointing to a Neon connection string.
 */

import { neon } from "@neondatabase/serverless";

export interface AnalysisSummary {
  repo?: string;
  total_prs_evaluated: number;
  total_modules: number;
  top_prs: Array<{ number: number; rank: number; reason: string }>;
  clusters: number;
  themes: string[];
  last_updated?: string;
}

export interface PREvaluation {
  pr_number: number;
  title: string;
  risk_score: number;
  quality_score: number;
  strategic_value: number;
  novelty_score: number;
  test_alignment: number;
  final_rank_score: number;
  review_summary: string;
  confidence: number;
  impact_scope: string[];
  linked_issues: number[];
  agent_traces?: Record<string, unknown>;
}

export interface PRCluster {
  cluster_id: number;
  members: number[];
  size: number;
  relations: Array<{
    pr_a: number;
    pr_b: number;
    relation_type: string;
    explanation: string;
  }>;
}

export interface AgentTraceStep {
  iteration: number;
  type: "llm_response" | "code_execution" | "iteration_complete" | "subcall_start" | "subcall_complete";
  content: string;
  timestamp: string;
}

export interface RunComparisonRow {
  pr_number: number;
  title: string;
  run_a_score: number | null;
  run_b_score: number | null;
  delta: number | null;
  rank_a: number | null;
  rank_b: number | null;
  justification_a: string;
  justification_b: string;
}

export interface RunComparisonData {
  run_a: {
    id: string;
    meta: RunMeta | null;
    scored_count: number;
    avg_score: number;
  };
  run_b: {
    id: string;
    meta: RunMeta | null;
    scored_count: number;
    avg_score: number;
  };
  rows: RunComparisonRow[];
}

function getSQL() {
  const url = process.env.DATABASE_URL;
  if (!url) return null;
  return neon(url);
}

// Auto-create tables on first use
let initialized = false;
const RUNS_KEY = "rlm:runs";
const CURRENT_RUN_ID_KEY = "rlm:current_run_id";
export const LEGACY_RUN_ID = "legacy";
const LEGACY_SUMMARY_KEY = "rlm:summary";
const LEGACY_EVALUATIONS_KEY = "rlm:evaluations";
const LEGACY_CLUSTERS_KEY = "rlm:clusters";
const LEGACY_RANKING_KEY = "rlm:ranking";
const LEGACY_AGENT_TRACE_KEY = "rlm:agent_trace";

async function ensureTables() {
  if (initialized) return;
  const sql = getSQL();
  if (!sql) return;

  await sql`
    CREATE TABLE IF NOT EXISTS rlm_kv (
      key TEXT PRIMARY KEY,
      value JSONB NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
  `;
  initialized = true;
}

async function readKV<T>(key: string, fallback: T): Promise<T> {
  const sql = getSQL();
  if (!sql) return fallback;
  try {
    await ensureTables();
    const rows = await sql`SELECT value FROM rlm_kv WHERE key = ${key}`;
    if (rows.length === 0) return fallback;
    return rows[0].value as T;
  } catch {
    return fallback;
  }
}

async function writeKV(key: string, value: unknown): Promise<void> {
  const sql = getSQL();
  if (!sql) return;
  try {
    await ensureTables();
    await sql`
      INSERT INTO rlm_kv (key, value, updated_at) 
      VALUES (${key}, ${JSON.stringify(value)}::jsonb, NOW())
      ON CONFLICT (key) DO UPDATE SET value = ${JSON.stringify(value)}::jsonb, updated_at = NOW()
    `;
  } catch {
    // graceful fallback
  }
}

async function deleteKV(key: string): Promise<void> {
  const sql = getSQL();
  if (!sql) return;
  try {
    await ensureTables();
    await sql`DELETE FROM rlm_kv WHERE key = ${key}`;
  } catch {
    // graceful fallback
  }
}

export interface RunMeta {
  id: string;
  timestamp: string;
  status?: "running" | "completed" | "failed" | "archived";
  kind?: "baseline" | "experimental" | "ab_control" | "ab_treatment";
  prompt_version?: string;
  prompt_hash?: string;
  prompt_label?: string;
  prompt_source_paths?: string[];
  model_name?: string;
  model_root?: string;
  model_workers?: Record<string, string>;
  config_snapshot?: Record<string, unknown>;
  budget?: number;
  start_time?: string;
  started_at?: string;
  end_time?: string;
  ended_at?: string;
  time_elapsed_seconds?: number;
  token_input?: number;
  token_output?: number;
  cost_usd?: number;
  total_prs_seen?: number;
  total_prs_scored?: number;
  prompt_bundle?: Record<string, unknown>;
}

export type RunInfo = RunMeta;

export interface RunEvent {
  event: string;
  [key: string]: unknown;
}

type DataKind = "summary" | "evaluations" | "clusters" | "ranking" | "agent_trace" | "meta" | "prompt_bundle";

function runKey(runId: string, kind: DataKind) {
  return `rlm:run:${runId}:${kind}`;
}

function legacyKey(kind: "summary" | "evaluations" | "clusters" | "ranking" | "agent_trace") {
  switch (kind) {
    case "summary":
      return LEGACY_SUMMARY_KEY;
    case "evaluations":
      return LEGACY_EVALUATIONS_KEY;
    case "clusters":
      return LEGACY_CLUSTERS_KEY;
    case "ranking":
      return LEGACY_RANKING_KEY;
    case "agent_trace":
      return LEGACY_AGENT_TRACE_KEY;
  }
}

function isLegacyScope(runId?: string | null) {
  return !runId || runId === "latest" || runId === LEGACY_RUN_ID;
}

function hasData(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  return value !== null && value !== undefined;
}

function toTimestamp(runId: string, fallback: string) {
  const asNumber = Number(runId);
  if (Number.isFinite(asNumber)) {
    return new Date(asNumber).toISOString();
  }
  const parsed = new Date(runId);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toISOString();
  }
  return fallback;
}

export function createRunId() {
  return String(Date.now());
}

export async function clearCurrentRunId() {
  await deleteKV(CURRENT_RUN_ID_KEY);
}

async function hasLegacyData() {
  const [summary, evaluations, clusters, ranking, trace] = await Promise.all([
    readKV<AnalysisSummary | null>(LEGACY_SUMMARY_KEY, null),
    readKV<PREvaluation[]>(LEGACY_EVALUATIONS_KEY, []),
    readKV<PRCluster[]>(LEGACY_CLUSTERS_KEY, []),
    readKV<Record<string, unknown> | null>(LEGACY_RANKING_KEY, null),
    readKV<AgentTraceStep[]>(LEGACY_AGENT_TRACE_KEY, []),
  ]);
  return [summary, evaluations, clusters, ranking, trace].some(hasData);
}

async function ensureLegacyRunMigration() {
  const existing = await readKV<string[]>(RUNS_KEY, []);
  if (existing.length > 0) return;
  if (!(await hasLegacyData())) return;

  const now = new Date().toISOString();
  await writeKV(RUNS_KEY, [LEGACY_RUN_ID]);
  await writeKV(runKey(LEGACY_RUN_ID, "meta"), {
    id: LEGACY_RUN_ID,
    timestamp: now,
  } satisfies RunInfo);
}

export async function getRunIds(): Promise<string[]> {
  await ensureLegacyRunMigration();
  return readKV(RUNS_KEY, []);
}

async function ensureRun(runId: string) {
  const existing = await getRunIds();
  if (!existing.includes(runId)) {
    await writeKV(RUNS_KEY, [...existing, runId]);
  }
  const currentMeta = await readKV<RunMeta | null>(runKey(runId, "meta"), null);
  if (!currentMeta) {
    await writeKV(runKey(runId, "meta"), {
      id: runId,
      timestamp: toTimestamp(runId, new Date().toISOString()),
      status: "running",
    } satisfies RunMeta);
  }
}

export async function getCurrentRunId(): Promise<string | null> {
  const runId = await readKV<string | null>(CURRENT_RUN_ID_KEY, null);
  if (!runId) return null;
  const runIds = await getRunIds();
  if (runIds.includes(runId)) return runId;
  await clearCurrentRunId();
  return null;
}

export async function getOrCreateCurrentRunId(): Promise<string> {
  const existing = await getCurrentRunId();
  if (existing) return existing;
  const runId = createRunId();
  await ensureRun(runId);
  await writeKV(CURRENT_RUN_ID_KEY, runId);
  return runId;
}

export async function startNewCurrentRun(initialMeta?: Partial<RunMeta>, preferredRunId?: string): Promise<string> {
  const runId = preferredRunId && preferredRunId.trim() ? preferredRunId.trim() : createRunId();
  await ensureRun(runId);
  if (initialMeta && Object.keys(initialMeta).length > 0) {
    await setRunMeta(runId, { ...initialMeta, id: runId });
  }
  await writeKV(CURRENT_RUN_ID_KEY, runId);
  return runId;
}

export async function getRuns(): Promise<RunMeta[]> {
  const runIds = await getRunIds();
  const runs = await Promise.all(
    runIds.map(async (id) => {
      const meta = await readKV<RunMeta | null>(runKey(id, "meta"), null);
      return (
        meta ?? {
          id,
          timestamp: toTimestamp(id, new Date(0).toISOString()),
        }
      );
    })
  );
  return runs.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

export async function getLatestRunId(): Promise<string | null> {
  const runs = await getRuns();
  if (runs.length === 0) return null;

  const counts = await Promise.all(runs.map(async (run) => ({ id: run.id, count: await getEvaluationCount(run.id) })));
  const best = counts.reduce((winner, current) => (current.count > winner.count ? current : winner), {
    id: runs[0].id,
    count: -1,
  });

  if (best.count > 0) return best.id;
  if (runs.some((run) => run.id === LEGACY_RUN_ID)) return LEGACY_RUN_ID;
  return runs[0]?.id ?? null;
}

export async function getSummary(runId?: string | null): Promise<AnalysisSummary | null> {
  if (isLegacyScope(runId)) return readKV(legacyKey("summary"), null);
  const scopedRunId = runId as string;
  return readKV(runKey(scopedRunId, "summary"), null);
}

export async function getRunMeta(runId: string): Promise<RunMeta | null> {
  if (!runId || isLegacyScope(runId)) {
    return readKV<RunMeta | null>(runKey(LEGACY_RUN_ID, "meta"), null);
  }
  return readKV<RunMeta | null>(runKey(runId, "meta"), null);
}

export async function setRunMeta(runId: string, data: Partial<RunMeta>) {
  await ensureRun(runId);
  const existing = await readKV<RunMeta | null>(runKey(runId, "meta"), null);
  const merged: RunMeta = {
    ...(existing ?? {
      id: runId,
      timestamp: toTimestamp(runId, new Date().toISOString()),
    }),
    ...data,
    id: runId,
    timestamp: data.timestamp ?? existing?.timestamp ?? toTimestamp(runId, new Date().toISOString()),
  };
  await writeKV(runKey(runId, "meta"), merged);
}

export async function appendRunEvent(runId: string, event: RunEvent) {
  const existing = await getRunMeta(runId);
  const updated: Partial<RunMeta> = {};
  if (event.event === "completed") {
    updated.status = "completed";
    if (typeof event.ended_at === "string") updated.ended_at = event.ended_at;
  } else if (event.event === "failed") {
    updated.status = "failed";
    if (typeof event.ended_at === "string") updated.ended_at = event.ended_at;
  }
  if (Object.keys(updated).length > 0) {
    await setRunMeta(runId, { ...existing, ...updated });
  }
}

export async function getPromptBundle(runId: string): Promise<Record<string, unknown> | null> {
  if (!runId || isLegacyScope(runId)) return null;
  return readKV<Record<string, unknown> | null>(runKey(runId, "prompt_bundle"), null);
}

export async function setPromptBundle(runId: string, bundle: Record<string, unknown>) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "prompt_bundle"), bundle);
}

export async function setSummary(runId: string, data: AnalysisSummary) {
  await ensureRun(runId);
  data.last_updated = new Date().toISOString();
  await writeKV(runKey(runId, "summary"), data);
}

export async function setLegacySummary(data: AnalysisSummary) {
  data.last_updated = new Date().toISOString();
  await writeKV(legacyKey("summary"), data);
}

export async function getEvaluations(runId?: string | null): Promise<PREvaluation[]> {
  if (isLegacyScope(runId)) return readKV(legacyKey("evaluations"), []);
  const scopedRunId = runId as string;
  return readKV(runKey(scopedRunId, "evaluations"), []);
}

function comparisonScore(item: PREvaluation): number {
  const maybe = item as PREvaluation & { final_score?: number; score?: number };
  const candidate = maybe.final_score ?? item.final_rank_score ?? maybe.score;
  return typeof candidate === "number" && Number.isFinite(candidate) ? candidate : 0;
}

function comparisonJustification(item: PREvaluation): string {
  const maybe = item as PREvaluation & { justification?: string };
  if (typeof maybe.justification === "string" && maybe.justification.trim()) return maybe.justification;
  if (typeof item.review_summary === "string" && item.review_summary.trim()) return item.review_summary;
  return "";
}

function averageScore(items: PREvaluation[]): number {
  if (items.length === 0) return 0;
  const total = items.reduce((sum, item) => sum + comparisonScore(item), 0);
  return total / items.length;
}

export async function getRunComparison(runA: string, runB: string): Promise<RunComparisonData> {
  const [metaA, metaB, evalsA, evalsB] = await Promise.all([
    getRunMeta(runA),
    getRunMeta(runB),
    getEvaluations(runA),
    getEvaluations(runB),
  ]);

  const sortedA = [...evalsA].sort((a, b) => comparisonScore(b) - comparisonScore(a));
  const sortedB = [...evalsB].sort((a, b) => comparisonScore(b) - comparisonScore(a));

  const rankA = new Map(sortedA.map((item, index) => [item.pr_number, index + 1]));
  const rankB = new Map(sortedB.map((item, index) => [item.pr_number, index + 1]));
  const mapA = new Map(evalsA.map((item) => [item.pr_number, item]));
  const mapB = new Map(evalsB.map((item) => [item.pr_number, item]));

  const allPRs = new Set<number>([...mapA.keys(), ...mapB.keys()]);
  const rows: RunComparisonRow[] = [];

  for (const prNumber of allPRs) {
    const a = mapA.get(prNumber);
    const b = mapB.get(prNumber);
    const scoreA = a ? comparisonScore(a) : null;
    const scoreB = b ? comparisonScore(b) : null;
    const delta =
      scoreA !== null && scoreB !== null ? Number((scoreB - scoreA).toFixed(4)) : null;
    rows.push({
      pr_number: prNumber,
      title: a?.title ?? b?.title ?? `PR #${prNumber}`,
      run_a_score: scoreA,
      run_b_score: scoreB,
      delta,
      rank_a: rankA.get(prNumber) ?? null,
      rank_b: rankB.get(prNumber) ?? null,
      justification_a: a ? comparisonJustification(a) : "",
      justification_b: b ? comparisonJustification(b) : "",
    });
  }

  rows.sort((left, right) => {
    const leftAbs = left.delta === null ? -1 : Math.abs(left.delta);
    const rightAbs = right.delta === null ? -1 : Math.abs(right.delta);
    return rightAbs - leftAbs;
  });

  return {
    run_a: {
      id: runA,
      meta: metaA,
      scored_count: evalsA.length,
      avg_score: Number(averageScore(evalsA).toFixed(4)),
    },
    run_b: {
      id: runB,
      meta: metaB,
      scored_count: evalsB.length,
      avg_score: Number(averageScore(evalsB).toFixed(4)),
    },
    rows,
  };
}

export async function setEvaluations(runId: string, data: PREvaluation[]) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "evaluations"), data);
}

export async function setLegacyEvaluations(data: PREvaluation[]) {
  await writeKV(legacyKey("evaluations"), data);
}

export async function appendEvaluation(runId: string, ev: PREvaluation) {
  const existing = await getEvaluations(runId);
  const idx = existing.findIndex((e) => e.pr_number === ev.pr_number);
  if (idx >= 0) existing[idx] = ev;
  else existing.push(ev);
  await setEvaluations(runId, existing);
}

export async function appendLegacyEvaluation(ev: PREvaluation) {
  const existing = await getEvaluations(LEGACY_RUN_ID);
  const idx = existing.findIndex((e) => e.pr_number === ev.pr_number);
  if (idx >= 0) existing[idx] = ev;
  else existing.push(ev);
  await setLegacyEvaluations(existing);
}

export async function getClusters(runId?: string | null): Promise<PRCluster[]> {
  if (isLegacyScope(runId)) return readKV(legacyKey("clusters"), []);
  const scopedRunId = runId as string;
  return readKV(runKey(scopedRunId, "clusters"), []);
}

export async function setClusters(runId: string, data: PRCluster[]) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "clusters"), data);
}

export async function setLegacyClusters(data: PRCluster[]) {
  await writeKV(legacyKey("clusters"), data);
}

export async function getRanking(runId?: string | null): Promise<Record<string, unknown> | null> {
  if (isLegacyScope(runId)) return readKV(legacyKey("ranking"), null);
  const scopedRunId = runId as string;
  return readKV(runKey(scopedRunId, "ranking"), null);
}

export async function setRanking(runId: string, data: Record<string, unknown>) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "ranking"), data);
}

export async function setLegacyRanking(data: Record<string, unknown>) {
  await writeKV(legacyKey("ranking"), data);
}

export async function getAgentTrace(runId?: string | null): Promise<AgentTraceStep[]> {
  if (isLegacyScope(runId)) return readKV(legacyKey("agent_trace"), []);
  const scopedRunId = runId as string;
  return readKV(runKey(scopedRunId, "agent_trace"), []);
}

export async function setAgentTrace(runId: string, data: AgentTraceStep[]) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "agent_trace"), data);
}

export async function setLegacyAgentTrace(data: AgentTraceStep[]) {
  await writeKV(legacyKey("agent_trace"), data);
}

async function getEvaluationCount(runId: string) {
  const evaluations = await getEvaluations(runId);
  return Array.isArray(evaluations) ? evaluations.length : 0;
}

async function deleteRun(runId: string) {
  const runIds = await getRunIds();
  if (!runIds.includes(runId)) return;

  if (runId === LEGACY_RUN_ID) {
    await Promise.all([
      deleteKV(LEGACY_SUMMARY_KEY),
      deleteKV(LEGACY_EVALUATIONS_KEY),
      deleteKV(LEGACY_CLUSTERS_KEY),
      deleteKV(LEGACY_RANKING_KEY),
      deleteKV(LEGACY_AGENT_TRACE_KEY),
    ]);
  } else {
    await Promise.all([
      deleteKV(runKey(runId, "meta")),
      deleteKV(runKey(runId, "prompt_bundle")),
      deleteKV(runKey(runId, "summary")),
      deleteKV(runKey(runId, "evaluations")),
      deleteKV(runKey(runId, "clusters")),
      deleteKV(runKey(runId, "ranking")),
      deleteKV(runKey(runId, "agent_trace")),
    ]);
  }

  await writeKV(
    RUNS_KEY,
    runIds.filter((id) => id !== runId)
  );
}

export interface CleanupRunsResult {
  legacy_run_id: string | null;
  best_run_id: string | null;
  kept_run_ids: string[];
  deleted_run_ids: string[];
}

export async function cleanupRuns(): Promise<CleanupRunsResult> {
  const runs = await getRuns();
  if (runs.length === 0) {
    return {
      legacy_run_id: null,
      best_run_id: null,
      kept_run_ids: [],
      deleted_run_ids: [],
    };
  }

  const runCounts = await Promise.all(
    runs.map(async (run) => ({
      runId: run.id,
      evaluationCount: await getEvaluationCount(run.id),
    }))
  );

  const legacyDataRun = runCounts.reduce((winner, current) =>
    current.evaluationCount > winner.evaluationCount ? current : winner
  );
  const bestRunId = await getLatestRunId();

  const keep = new Set<string>();
  if (legacyDataRun.runId) keep.add(legacyDataRun.runId);
  if (bestRunId) keep.add(bestRunId);

  const deleted: string[] = [];
  for (const run of runs) {
    if (keep.has(run.id)) continue;
    await deleteRun(run.id);
    deleted.push(run.id);
  }

  const currentRunId = await getCurrentRunId();
  if (currentRunId && !keep.has(currentRunId)) {
    if (bestRunId && keep.has(bestRunId)) {
      await writeKV(CURRENT_RUN_ID_KEY, bestRunId);
    } else {
      await clearCurrentRunId();
    }
  }

  return {
    legacy_run_id: legacyDataRun.runId ?? null,
    best_run_id: bestRunId,
    kept_run_ids: Array.from(keep),
    deleted_run_ids: deleted,
  };
}
