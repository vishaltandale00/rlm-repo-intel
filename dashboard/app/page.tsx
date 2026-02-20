import { Summary } from "@/components/summary";
import { PRTable } from "@/components/pr-table";
import { ClusterView } from "@/components/cluster-view";
import { AgentDebateLog } from "@/components/agent-debate-log";

export const dynamic = "force-dynamic";
export const revalidate = 30; // revalidate every 30s for live updates

async function getData() {
  const base = process.env.DATA_URL || "/api";
  try {
    const [summary, evaluations, clusters, ranking] = await Promise.all([
      fetch(`${base}/summary`).then((r) => r.json()).catch(() => null),
      fetch(`${base}/evaluations`).then((r) => r.json()).catch(() => []),
      fetch(`${base}/clusters`).then((r) => r.json()).catch(() => []),
      fetch(`${base}/ranking`).then((r) => r.json()).catch(() => null),
    ]);
    return { summary, evaluations, clusters, ranking };
  } catch {
    return { summary: null, evaluations: [], clusters: [], ranking: null };
  }
}

export default async function Home() {
  const data = await getData();

  return (
    <main className="max-w-7xl mx-auto px-4 py-8">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">
          ⚡ RLM Repo Intel
        </h1>
        <p className="text-neutral-400 mt-1">
          Recursive Language Model analysis of{" "}
          <span className="text-blue-400">openclaw/openclaw</span> — {" "}
          {data.summary?.total_prs_evaluated || 0} PRs evaluated
        </p>
      </header>

      <div className="grid gap-6">
        <Summary data={data.summary} />

        <section>
          <h2 className="text-xl font-semibold mb-4">🏆 Top PRs</h2>
          <PRTable
            evaluations={data.evaluations}
            ranking={data.ranking}
          />
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-4">🔗 PR Clusters</h2>
          <ClusterView clusters={data.clusters} />
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-4">🤖 Agent Reasoning</h2>
          <AgentDebateLog evaluations={data.evaluations} />
        </section>
      </div>

      <footer className="mt-12 text-center text-neutral-600 text-sm">
        Powered by{" "}
        <a
          href="https://github.com/vishaltandale00/rlm-repo-intel"
          className="text-blue-500 hover:underline"
        >
          rlm-repo-intel
        </a>{" "}
        | Based on{" "}
        <a
          href="https://arxiv.org/abs/2512.24601"
          className="text-blue-500 hover:underline"
        >
          Recursive Language Models
        </a>
      </footer>
    </main>
  );
}
