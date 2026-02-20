"use client";

import { AgentDebateLog } from "@/components/agent-debate-log";
import { ClusterView } from "@/components/cluster-view";
import { LiveStatusBar } from "@/components/live-status-bar";
import { PRTable } from "@/components/pr-table";
import { Summary } from "@/components/summary";
import { ClusterItem, EvaluationItem, RankingData, SummaryData } from "@/components/types";
import { useMemo, useState } from "react";

interface DashboardClientProps {
  summary: SummaryData | null;
  evaluations: EvaluationItem[];
  clusters: ClusterItem[];
  ranking: RankingData | null;
}

export function DashboardClient({ summary, evaluations, clusters, ranking }: DashboardClientProps) {
  const firstTracePR = useMemo(
    () => evaluations.find((evaluation) => evaluation.agent_traces)?.pr_number ?? null,
    [evaluations]
  );

  const [selectedPR, setSelectedPR] = useState<number | null>(firstTracePR);

  return (
    <div className="grid gap-6">
      <LiveStatusBar summary={summary} evaluatedCount={evaluations.length} />

      <Summary data={summary} />

      <section>
        <h2 className="mb-3 text-lg font-semibold tracking-tight text-neutral-100">Agent Discussion View</h2>
        <p className="mb-3 text-xs text-neutral-400">
          Click any PR from Top PRs or Clusters to open its debate trace here.
        </p>
        <AgentDebateLog evaluations={evaluations} selectedPR={selectedPR} onSelectPR={setSelectedPR} />
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold tracking-tight text-neutral-100">Top PRs</h2>
        <PRTable
          evaluations={evaluations}
          ranking={ranking}
          selectedPR={selectedPR}
          onSelectPR={setSelectedPR}
        />
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold tracking-tight text-neutral-100">PR Clusters</h2>
        <ClusterView
          clusters={clusters}
          evaluations={evaluations}
          selectedPR={selectedPR}
          onSelectPR={setSelectedPR}
        />
      </section>
    </div>
  );
}
