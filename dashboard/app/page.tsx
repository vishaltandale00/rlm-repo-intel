import { DashboardClient } from "@/components/dashboard-client";
import { EvaluationItem, RunMeta } from "@/components/types";
import {
  getAgentTrace,
  getClusters,
  getEvaluations,
  getLatestRunId,
  getRanking,
  getRunMeta,
  getRuns,
  getSummary,
} from "@/lib/store";

export const dynamic = "force-dynamic";
export const revalidate = 30;

function toNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function normalizeEvaluation(raw: unknown): EvaluationItem | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const prNumber = toNumber(item.pr_number);
  if (!prNumber) return null;

  const urgency = toNumber(item.urgency);
  const quality = toNumber(item.quality);
  const riskScore = toNumber(item.risk_score) ?? urgency;
  const qualityScore = toNumber(item.quality_score) ?? quality;
  const strategicValue = toNumber(item.strategic_value) ?? urgency;
  const finalRankScore =
    toNumber(item.final_rank_score) ??
    toNumber(item.final_score) ??
    toNumber(item.score) ??
    (urgency !== undefined && quality !== undefined ? (urgency + quality) / 2 : undefined);

  return {
    ...item,
    pr_number: prNumber,
    title: typeof item.title === "string" ? item.title : `PR #${prNumber}`,
    author: typeof item.author === "string" ? item.author : undefined,
    state: typeof item.state === "string" ? item.state : undefined,
    urgency,
    quality,
    risk_score: riskScore,
    quality_score: qualityScore,
    strategic_value: strategicValue,
    final_rank_score: finalRankScore,
    final_score: toNumber(item.final_score) ?? finalRankScore,
    review_summary:
      typeof item.review_summary === "string"
        ? item.review_summary
        : typeof item.justification === "string"
          ? item.justification
          : undefined,
    confidence: toNumber(item.confidence),
    impact_scope: Array.isArray(item.impact_scope)
      ? (item.impact_scope as string[])
      : undefined,
    novelty_score: toNumber(item.novelty_score),
    justification: typeof item.justification === "string" ? item.justification : undefined,
    key_risks: Array.isArray(item.key_risks)
      ? (item.key_risks as string[])
      : typeof item.key_risks === "string"
        ? item.key_risks
        : undefined,
    verdict: typeof item.verdict === "string" ? item.verdict : undefined,
    evidence: Array.isArray(item.evidence)
      ? (item.evidence as string[])
      : typeof item.evidence === "string"
        ? item.evidence
        : undefined,
    agent_traces:
      item.agent_traces && typeof item.agent_traces === "object"
        ? (item.agent_traces as EvaluationItem["agent_traces"])
        : undefined,
  };
}

function normalizeRunMeta(raw: unknown, fallbackRunId: string | null): RunMeta | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const tokenInput = toNumber(item.token_input) ?? toNumber(item.input_tokens);
  const tokenOutput = toNumber(item.token_output) ?? toNumber(item.output_tokens);
  const totalTokens = toNumber(item.total_tokens) ?? toNumber(item.tokens_used);
  const computedTotalTokens =
    totalTokens ?? (tokenInput !== undefined || tokenOutput !== undefined ? (tokenInput ?? 0) + (tokenOutput ?? 0) : undefined);

  const costUsd = toNumber(item.cost_usd) ?? toNumber(item.total_cost_usd) ?? toNumber(item.cost);

  return {
    ...item,
    id: typeof item.id === "string" ? item.id : fallbackRunId ?? "legacy",
    timestamp:
      typeof item.timestamp === "string"
        ? item.timestamp
        : typeof item.started_at === "string"
          ? item.started_at
          : typeof item.start_time === "string"
            ? item.start_time
            : new Date().toISOString(),
    prompt_hash: typeof item.prompt_hash === "string" ? item.prompt_hash : undefined,
    prompt_version: typeof item.prompt_version === "string" ? item.prompt_version : undefined,
    model_name: typeof item.model_name === "string" ? item.model_name : undefined,
    model_root: typeof item.model_root === "string" ? item.model_root : undefined,
    started_at: typeof item.started_at === "string" ? item.started_at : undefined,
    start_time: typeof item.start_time === "string" ? item.start_time : undefined,
    token_input: tokenInput,
    token_output: tokenOutput,
    total_tokens: computedTotalTokens,
    tokens_used: computedTotalTokens,
    cost_usd: costUsd,
    total_cost_usd: costUsd,
    cost: costUsd,
  };
}

async function getData(selectedRunId: string | null) {
  const [summary, evaluations, clusters, ranking, trace, runMeta] = await Promise.all([
    getSummary(selectedRunId),
    getEvaluations(selectedRunId),
    getClusters(selectedRunId),
    getRanking(selectedRunId),
    getAgentTrace(selectedRunId),
    getRunMeta(selectedRunId ?? "latest"),
  ]);

  const normalizedEvaluations = Array.isArray(evaluations)
    ? evaluations.map(normalizeEvaluation).filter((value): value is EvaluationItem => Boolean(value))
    : [];

  return {
    summary,
    evaluations: normalizedEvaluations,
    clusters,
    ranking,
    trace,
    runMeta: normalizeRunMeta(runMeta, selectedRunId),
  };
}

type HomeProps = {
  searchParams?: Promise<{ run?: string }>;
};

export default async function Home({ searchParams }: HomeProps) {
  const resolvedSearchParams = searchParams ? await searchParams : undefined;
  const runsDesc = await getRuns();
  const runs = [...runsDesc].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  const latestRunId = await getLatestRunId();
  const requestedRunId = resolvedSearchParams?.run;
  const selectedRunId = requestedRunId && runs.some((run) => run.id === requestedRunId) ? requestedRunId : latestRunId;
  const data = await getData(selectedRunId ?? null);

  const repoName =
    typeof data.summary?.repo === "string" && data.summary.repo.trim() ? data.summary.repo : "openclaw/openclaw";

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-8 rounded-lg border border-[var(--border)] bg-[var(--card)] px-4 py-4">
        <h1 className="text-2xl font-semibold tracking-tight text-neutral-100">RLM Repo Intel</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Recursive Language Model analysis for <span className="font-mono text-blue-300">{repoName}</span>
        </p>
      </header>

      <DashboardClient
        runs={runs}
        selectedRunId={selectedRunId ?? null}
        selectedRunMeta={data.runMeta}
        summary={data.summary}
        evaluations={data.evaluations}
        clusters={data.clusters}
        ranking={data.ranking}
        trace={data.trace}
      />
    </main>
  );
}
