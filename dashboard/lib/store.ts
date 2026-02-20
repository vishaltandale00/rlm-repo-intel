/**
 * Static JSON file store.
 * Data lives in public/data/ — committed to repo, served as static files.
 * Pipeline writes to these files, pushes to git, Vercel auto-deploys.
 */

import { readFile } from "fs/promises";
import { join } from "path";

const DATA_DIR = join(process.cwd(), "public", "data");

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

async function readJSON<T>(filename: string, fallback: T): Promise<T> {
  try {
    const data = await readFile(join(DATA_DIR, filename), "utf-8");
    return JSON.parse(data);
  } catch {
    return fallback;
  }
}

export async function getSummary(): Promise<AnalysisSummary | null> {
  return readJSON("summary.json", null);
}

export async function getEvaluations(): Promise<PREvaluation[]> {
  return readJSON("evaluations.json", []);
}

export async function getClusters(): Promise<PRCluster[]> {
  return readJSON("clusters.json", []);
}

export async function getRanking(): Promise<Record<string, unknown> | null> {
  return readJSON("ranking.json", null);
}

// Write functions not needed — pipeline writes directly to public/data/
// and pushes via git. Keeping stubs for API compatibility.
export async function setSummary() {}
export async function setEvaluations() {}
export async function appendEvaluation() {}
export async function setClusters() {}
export async function setRanking() {}
