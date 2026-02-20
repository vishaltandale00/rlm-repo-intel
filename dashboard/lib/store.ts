/**
 * Simple in-memory + file-backed store for analysis results.
 * In production, replace with a database. For now, the local Python pipeline
 * pushes JSON to the /api/push endpoint and we serve it back.
 */

import { readFile, writeFile, mkdir } from "fs/promises";
import { join } from "path";

const DATA_DIR = process.env.DATA_DIR || ".data";

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

async function ensureDir() {
  try {
    await mkdir(DATA_DIR, { recursive: true });
  } catch {}
}

async function readJSON<T>(filename: string, fallback: T): Promise<T> {
  try {
    const data = await readFile(join(DATA_DIR, filename), "utf-8");
    return JSON.parse(data);
  } catch {
    return fallback;
  }
}

async function writeJSON(filename: string, data: unknown) {
  await ensureDir();
  await writeFile(join(DATA_DIR, filename), JSON.stringify(data, null, 2));
}

export async function getSummary(): Promise<AnalysisSummary | null> {
  return readJSON("summary.json", null);
}

export async function setSummary(data: AnalysisSummary) {
  data.last_updated = new Date().toISOString();
  await writeJSON("summary.json", data);
}

export async function getEvaluations(): Promise<PREvaluation[]> {
  return readJSON("evaluations.json", []);
}

export async function setEvaluations(data: PREvaluation[]) {
  await writeJSON("evaluations.json", data);
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
  return readJSON("clusters.json", []);
}

export async function setClusters(data: PRCluster[]) {
  await writeJSON("clusters.json", data);
}

export async function getRanking(): Promise<Record<string, unknown> | null> {
  return readJSON("ranking.json", null);
}

export async function setRanking(data: Record<string, unknown>) {
  await writeJSON("ranking.json", data);
}
