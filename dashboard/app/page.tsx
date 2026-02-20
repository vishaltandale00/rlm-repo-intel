import { DashboardClient } from "@/components/dashboard-client";
import { EvaluationItem } from "@/components/types";
import { headers } from "next/headers";

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

async function getBaseApiUrl() {
  const configured = process.env.DATA_URL?.trim();
  if (configured && /^https?:\/\//i.test(configured)) {
    return configured.replace(/\/$/, "");
  }

  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host");
  const proto = h.get("x-forwarded-proto") ?? "https";
  const origin = host ? `${proto}://${host}` : "http://localhost:3000";
  const prefix = configured?.startsWith("/") ? configured : "/api";
  return `${origin}${prefix}`.replace(/\/$/, "");
}

async function getData() {
  const base = await getBaseApiUrl();
  const fetchJson = async (path: string) => {
    const response = await fetch(`${base}${path}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Failed fetch: ${path}`);
    return response.json();
  };

  try {
    const [summary, evaluations, clusters, ranking, trace] = await Promise.all([
      fetchJson("/summary").catch(() => null),
      fetchJson("/evaluations").catch(() => []),
      fetchJson("/clusters").catch(() => []),
      fetchJson("/ranking").catch(() => null),
      fetchJson("/trace").catch(() => []),
    ]);

    const normalizedEvaluations = Array.isArray(evaluations)
      ? evaluations.map(normalizeEvaluation).filter((value): value is EvaluationItem => Boolean(value))
      : [];

    return { summary, evaluations: normalizedEvaluations, clusters, ranking, trace };
  } catch {
    return { summary: null, evaluations: [], clusters: [], ranking: null, trace: [] };
  }
}

export default async function Home() {
  const data = await getData();

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-8 rounded-lg border border-[var(--border)] bg-[var(--card)] px-4 py-4">
        <h1 className="text-2xl font-semibold tracking-tight text-neutral-100">RLM Repo Intel</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Recursive Language Model analysis for <span className="font-mono text-blue-300">openclaw/openclaw</span>
        </p>
      </header>

      <DashboardClient
        summary={data.summary}
        evaluations={data.evaluations}
        clusters={data.clusters}
        ranking={data.ranking}
        trace={data.trace}
      />
    </main>
  );
}
