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
  type: "llm_response" | "code_execution";
  content: string;
  timestamp: string;
}

function getSQL() {
  const url = process.env.DATABASE_URL;
  if (!url) return null;
  return neon(url);
}

// Auto-create tables on first use
let initialized = false;
const RUNS_KEY = "rlm:runs";

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

export interface RunInfo {
  id: string;
  timestamp: string;
}

function runKey(runId: string, kind: "summary" | "evaluations" | "clusters" | "ranking" | "agent_trace" | "meta") {
  return `rlm:run:${runId}:${kind}`;
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

export async function getRunIds(): Promise<string[]> {
  return readKV(RUNS_KEY, []);
}

async function ensureRun(runId: string) {
  const existing = await getRunIds();
  if (!existing.includes(runId)) {
    await writeKV(RUNS_KEY, [...existing, runId]);
  }
  const currentMeta = await readKV<RunInfo | null>(runKey(runId, "meta"), null);
  if (!currentMeta) {
    await writeKV(runKey(runId, "meta"), {
      id: runId,
      timestamp: toTimestamp(runId, new Date().toISOString()),
    } satisfies RunInfo);
  }
}

export async function getRuns(): Promise<RunInfo[]> {
  const runIds = await getRunIds();
  const runs = await Promise.all(
    runIds.map(async (id) => {
      const meta = await readKV<RunInfo | null>(runKey(id, "meta"), null);
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
  return runs[0]?.id ?? null;
}

export async function getSummary(runId: string): Promise<AnalysisSummary | null> {
  return readKV(runKey(runId, "summary"), null);
}

export async function setSummary(runId: string, data: AnalysisSummary) {
  await ensureRun(runId);
  data.last_updated = new Date().toISOString();
  await writeKV(runKey(runId, "summary"), data);
}

export async function getEvaluations(runId: string): Promise<PREvaluation[]> {
  return readKV(runKey(runId, "evaluations"), []);
}

export async function setEvaluations(runId: string, data: PREvaluation[]) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "evaluations"), data);
}

export async function appendEvaluation(runId: string, ev: PREvaluation) {
  const existing = await getEvaluations(runId);
  const idx = existing.findIndex((e) => e.pr_number === ev.pr_number);
  if (idx >= 0) existing[idx] = ev;
  else existing.push(ev);
  await setEvaluations(runId, existing);
}

export async function getClusters(runId: string): Promise<PRCluster[]> {
  return readKV(runKey(runId, "clusters"), []);
}

export async function setClusters(runId: string, data: PRCluster[]) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "clusters"), data);
}

export async function getRanking(runId: string): Promise<Record<string, unknown> | null> {
  return readKV(runKey(runId, "ranking"), null);
}

export async function setRanking(runId: string, data: Record<string, unknown>) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "ranking"), data);
}

export async function getAgentTrace(runId: string): Promise<AgentTraceStep[]> {
  return readKV(runKey(runId, "agent_trace"), []);
}

export async function setAgentTrace(runId: string, data: AgentTraceStep[]) {
  await ensureRun(runId);
  await writeKV(runKey(runId, "agent_trace"), data);
}
