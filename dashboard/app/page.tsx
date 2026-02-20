import { DashboardClient } from "@/components/dashboard-client";
import { getSummary, getEvaluations, getClusters, getRanking } from "@/lib/store";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [summary, evaluations, clusters, ranking] = await Promise.all([
    getSummary(),
    getEvaluations(),
    getClusters(),
    getRanking(),
  ]);

  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <header className="mb-8 rounded-lg border border-[var(--border)] bg-[var(--card)] px-4 py-4">
        <h1 className="text-2xl font-semibold tracking-tight text-neutral-100">RLM Repo Intel</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Recursive Language Model analysis for <span className="font-mono text-blue-300">openclaw/openclaw</span>
        </p>
      </header>

      <DashboardClient
        summary={summary}
        evaluations={evaluations}
        clusters={clusters}
        ranking={ranking}
      />
    </main>
  );
}
