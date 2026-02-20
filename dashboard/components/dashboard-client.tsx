"use client";

import { AgentDebateLog } from "@/components/agent-debate-log";
import { ClusterView } from "@/components/cluster-view";
import { LiveStatusBar } from "@/components/live-status-bar";
import { PRTable } from "@/components/pr-table";
import { Summary } from "@/components/summary";
import {
  AgentTraceStep,
  ClusterItem,
  EvaluationItem,
  RankingData,
  SummaryData,
} from "@/components/types";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

interface DashboardClientProps {
  summary: SummaryData | null;
  evaluations: EvaluationItem[];
  clusters: ClusterItem[];
  ranking: RankingData | null;
  trace: AgentTraceStep[];
}

function formatTraceTime(timestamp: string) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
}

export function DashboardClient({ summary, evaluations, clusters, ranking, trace }: DashboardClientProps) {
  const router = useRouter();
  const firstTracePR = useMemo(
    () =>
      evaluations.find((evaluation) => evaluation.agent_traces)?.pr_number ??
      evaluations[0]?.pr_number ??
      null,
    [evaluations]
  );

  const [selectedPR, setSelectedPR] = useState<number | null>(firstTracePR);

  useEffect(() => {
    if (selectedPR === null && firstTracePR !== null) {
      setSelectedPR(firstTracePR);
    }
  }, [firstTracePR, selectedPR]);

  useEffect(() => {
    const id = window.setInterval(() => {
      router.refresh();
    }, 15000);
    return () => window.clearInterval(id);
  }, [router]);

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

      <section>
        <details className="rounded-lg border border-neutral-800 bg-neutral-950/80">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold tracking-tight text-neutral-100">
            Agent Trace
          </summary>
          <div className="border-t border-neutral-800 px-4 py-4">
            {trace.length === 0 ? (
              <p className="text-xs text-neutral-400">No agent trace available.</p>
            ) : (
              <div className="space-y-3">
                {trace.map((step, index) => {
                  const isLLM = step.type === "llm_response";
                  return (
                    <article
                      key={`${step.iteration}-${step.type}-${step.timestamp}-${index}`}
                      className="rounded-md border border-neutral-800 bg-black/70 p-3"
                    >
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-semibold text-neutral-200">Iteration {step.iteration}</span>
                        <span
                          className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                            isLLM ? "bg-cyan-900/60 text-cyan-200" : "bg-amber-900/60 text-amber-200"
                          }`}
                        >
                          {isLLM ? "LLM Response" : "Code Execution"}
                        </span>
                        <span className="ml-auto text-neutral-500">{formatTraceTime(step.timestamp)}</span>
                      </div>
                      <pre className="max-h-56 overflow-auto rounded border border-neutral-800 bg-neutral-950 p-3 text-xs leading-relaxed whitespace-pre-wrap text-neutral-200">
                        {step.content}
                      </pre>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </details>
      </section>
    </div>
  );
}
