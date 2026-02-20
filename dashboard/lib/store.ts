import { kv } from "@vercel/kv";

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

const KV_KEYS = {
  summary: "rlm:summary",
  evaluations: "rlm:evaluations",
  clusters: "rlm:clusters",
  ranking: "rlm:ranking",
};

function hasKVConfig() {
  return Boolean(process.env.KV_REST_API_URL && process.env.KV_REST_API_TOKEN);
}

async function readKV<T>(key: string, fallback: T): Promise<T> {
  if (!hasKVConfig()) return fallback;
  try {
    const value = await kv.get<T>(key);
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

async function writeKV(key: string, value: unknown): Promise<void> {
  if (!hasKVConfig()) return;
  try {
    await kv.set(key, value);
  } catch {
    // no-op: graceful fallback when KV is unavailable
  }
}

export async function getSummary(): Promise<AnalysisSummary | null> {
  return readKV(KV_KEYS.summary, null);
}

export async function setSummary(data: AnalysisSummary) {
  data.last_updated = new Date().toISOString();
  await writeKV(KV_KEYS.summary, data);
}

export async function getEvaluations(): Promise<PREvaluation[]> {
  return readKV(KV_KEYS.evaluations, []);
}

export async function setEvaluations(data: PREvaluation[]) {
  await writeKV(KV_KEYS.evaluations, data);
}

export async function appendEvaluation(ev: PREvaluation) {
  const existing = await getEvaluations();
  // Replace if exists, append if new
  const idx = existing.findIndex((e) => e.pr_number === ev.pr_number);
  if (idx >= 0) existing[idx] = ev;
  else existing.push(ev);
  await setEvaluations(existing);
}

export async function getClusters(): Promise<PRCluster[]> {
  return readKV(KV_KEYS.clusters, []);
}

export async function setClusters(data: PRCluster[]) {
  await writeKV(KV_KEYS.clusters, data);
}

export async function getRanking(): Promise<Record<string, unknown> | null> {
  return readKV(KV_KEYS.ranking, null);
}

export async function setRanking(data: Record<string, unknown>) {
  await writeKV(KV_KEYS.ranking, data);
}
