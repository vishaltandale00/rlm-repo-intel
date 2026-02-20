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

function getSQL() {
  const url = process.env.DATABASE_URL;
  if (!url) return null;
  return neon(url);
}

// Auto-create tables on first use
let initialized = false;

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

export async function getSummary(): Promise<AnalysisSummary | null> {
  return readKV("rlm:summary", null);
}

export async function setSummary(data: AnalysisSummary) {
  data.last_updated = new Date().toISOString();
  await writeKV("rlm:summary", data);
}

export async function getEvaluations(): Promise<PREvaluation[]> {
  return readKV("rlm:evaluations", []);
}

export async function setEvaluations(data: PREvaluation[]) {
  await writeKV("rlm:evaluations", data);
}

export async function appendEvaluation(ev: PREvaluation) {
  const existing = await getEvaluations();
  const idx = existing.findIndex((e) => e.pr_number === ev.pr_number);
  if (idx >= 0) existing[idx] = ev;
  else existing.push(ev);
  await setEvaluations(existing);
}

export async function getClusters(): Promise<PRCluster[]> {
  return readKV("rlm:clusters", []);
}

export async function setClusters(data: PRCluster[]) {
  await writeKV("rlm:clusters", data);
}

export async function getRanking(): Promise<Record<string, unknown> | null> {
  return readKV("rlm:ranking", null);
}

export async function setRanking(data: Record<string, unknown>) {
  await writeKV("rlm:ranking", data);
}
