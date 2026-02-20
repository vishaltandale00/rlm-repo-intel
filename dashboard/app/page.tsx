import { DashboardClient } from "@/components/dashboard-client";

export const dynamic = "force-dynamic";
export const revalidate = 30;

async function getData() {
  const base = process.env.DATA_URL || "/api";

  try {
    const [summary, evaluations, clusters, ranking] = await Promise.all([
      fetch(`${base}/summary`).then((response) => response.json()).catch(() => null),
      fetch(`${base}/evaluations`).then((response) => response.json()).catch(() => []),
      fetch(`${base}/clusters`).then((response) => response.json()).catch(() => []),
      fetch(`${base}/ranking`).then((response) => response.json()).catch(() => null),
    ]);

    return { summary, evaluations, clusters, ranking };
  } catch {
    return { summary: null, evaluations: [], clusters: [], ranking: null };
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
      />
    </main>
  );
}
