"use client";

import { ClusterView } from "@/components/cluster-view";
import { LiveStatusBar } from "@/components/live-status-bar";
import { PRTable } from "@/components/pr-table";
import { RunComparison } from "@/components/run-comparison";
import { Summary } from "@/components/summary";
import {
  AgentTraceStep,
  ClusterItem,
  EvaluationItem,
  RankingData,
  RunMeta,
  SummaryData,
} from "@/components/types";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

interface RunTab {
  id: string;
  timestamp: string;
}

interface DashboardClientProps {
  runs: RunTab[];
  selectedRunId: string | null;
  selectedRunMeta: RunMeta | null;
  summary: SummaryData | null;
  evaluations: EvaluationItem[];
  clusters: ClusterItem[];
  ranking: RankingData | null;
  trace: AgentTraceStep[];
}

function formatDate(value?: string): string {
  if (!value) return "n/a";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function readTokenCount(meta: RunMeta | null): number | null {
  if (!meta) return null;
  const direct = meta.tokens_used ?? meta.total_tokens;
  if (typeof direct === "number") return direct;
  const hasInput = typeof meta.token_input === "number";
  const hasOutput = typeof meta.token_output === "number";
  if (!hasInput && !hasOutput) return null;
  return (meta.token_input ?? 0) + (meta.token_output ?? 0);
}

function readCost(meta: RunMeta | null): number | null {
  if (!meta) return null;
  const cost = meta.cost_usd ?? meta.total_cost_usd ?? meta.cost;
  return typeof cost === "number" ? cost : null;
}

function RunInfo({
  selectedRunId,
  selectedRunMeta,
  evaluationsCount,
}: {
  selectedRunId: string | null;
  selectedRunMeta: RunMeta | null;
  evaluationsCount: number;
}) {
  const promptVersion = selectedRunMeta?.prompt_hash?.slice(0, 12) ?? selectedRunMeta?.prompt_version ?? "n/a";
  const model = selectedRunMeta?.model_name ?? selectedRunMeta?.model_root ?? "n/a";
  const start = selectedRunMeta?.started_at ?? selectedRunMeta?.start_time ?? selectedRunMeta?.timestamp;
  const tokens = readTokenCount(selectedRunMeta);
  const cost = readCost(selectedRunMeta);

  return (
    <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] px-4 py-3">
      <div className="grid gap-3 text-xs md:grid-cols-6">
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Run ID</div>
          <div className="font-mono text-neutral-200">{selectedRunId ?? "none"}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Prompt Version</div>
          <div className="font-mono text-blue-300">{promptVersion}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Model</div>
          <div className="font-mono text-neutral-200">{model}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Start Time</div>
          <div className="font-mono text-neutral-200">{formatDate(start)}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Tokens Used</div>
          <div className="font-mono text-neutral-200">{tokens !== null ? tokens.toLocaleString() : "n/a"}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Cost</div>
          <div className="font-mono text-neutral-200">{cost !== null ? `$${cost.toFixed(2)}` : "n/a"}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">PRs Analyzed</div>
          <div className="font-mono text-neutral-200">
            {selectedRunMeta?.total_prs_scored ?? selectedRunMeta?.total_prs_seen ?? evaluationsCount}
          </div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Elapsed</div>
          <div className="font-mono text-neutral-200">{formatElapsed(selectedRunMeta)}</div>
        </div>
      </div>
    </section>
  );
}

function formatTraceTime(timestamp: string) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
}

function formatRunLabel(run: RunTab, index: number) {
  if (run.id === "legacy") return "Legacy Run";
  const date = new Date(run.timestamp);
  const dateLabel = Number.isNaN(date.getTime())
    ? run.id
    : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return `Run ${index + 1} - ${dateLabel}`;
}

function formatElapsed(runMeta: RunMeta | null): string {
  if (!runMeta) return "n/a";
  if (typeof runMeta.time_elapsed_seconds === "number") return `${runMeta.time_elapsed_seconds.toFixed(1)}s`;
  const start = runMeta.started_at ?? runMeta.start_time ?? runMeta.timestamp;
  const end = runMeta.ended_at ?? runMeta.end_time;
  const startDate = new Date(start);
  const endDate = end ? new Date(end) : new Date();
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) return "n/a";
  return `${Math.max(0, (endDate.getTime() - startDate.getTime()) / 1000).toFixed(1)}s`;
}

function RunSelector({ runs, selectedRunId }: { runs: RunTab[]; selectedRunId: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const onSelect = (runId: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("run", runId);
    router.push(`${pathname}?${params.toString()}`);
  };

  if (runs.length === 0) {
    return (
      <section className="rounded-lg border border-neutral-800 bg-neutral-950/80 px-4 py-3 text-sm text-neutral-400">
        No runs yet.
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-950/80 p-2">
      {runs.length > 10 ? (
        <label className="block text-xs text-neutral-300">
          <span className="mb-2 block font-medium uppercase tracking-wide text-neutral-400">Select Run</span>
          <select
            className="w-full rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100"
            value={selectedRunId ?? runs[0]?.id ?? ""}
            onChange={(event) => onSelect(event.target.value)}
          >
            {runs.map((run, index) => (
              <option key={run.id} value={run.id}>
                {formatRunLabel(run, index)}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="flex flex-wrap gap-2">
          {runs.map((run, index) => {
            const selected = run.id === selectedRunId;
            return (
              <button
                key={run.id}
                type="button"
                onClick={() => onSelect(run.id)}
                className={`rounded-md border px-3 py-2 text-xs font-medium transition ${
                  selected
                    ? "border-blue-400/50 bg-blue-500/20 text-blue-200"
                    : "border-neutral-700 bg-neutral-900 text-neutral-300 hover:border-neutral-500"
                }`}
              >
                {formatRunLabel(run, index)}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function DashboardClient({
  runs,
  selectedRunId,
  selectedRunMeta,
  summary,
  evaluations,
  clusters,
  ranking,
  trace,
}: DashboardClientProps) {
  const router = useRouter();
  const firstTracePR = useMemo(
    () =>
      evaluations.find((evaluation) => evaluation.agent_traces)?.pr_number ??
      evaluations[0]?.pr_number ??
      null,
    [evaluations]
  );

  const [selectedPR, setSelectedPR] = useState<number | null>(firstTracePR);
  const [viewMode, setViewMode] = useState<"analysis" | "comparison">("analysis");

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
      <RunSelector runs={runs} selectedRunId={selectedRunId} />
      <RunInfo selectedRunId={selectedRunId} selectedRunMeta={selectedRunMeta} evaluationsCount={evaluations.length} />

      <section className="rounded-lg border border-neutral-800 bg-neutral-950/80 p-2">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setViewMode("analysis")}
            className={`rounded-md border px-3 py-2 text-xs font-medium transition ${
              viewMode === "analysis"
                ? "border-blue-400/50 bg-blue-500/20 text-blue-200"
                : "border-neutral-700 bg-neutral-900 text-neutral-300 hover:border-neutral-500"
            }`}
          >
            Analysis
          </button>
          <button
            type="button"
            onClick={() => setViewMode("comparison")}
            className={`rounded-md border px-3 py-2 text-xs font-medium transition ${
              viewMode === "comparison"
                ? "border-blue-400/50 bg-blue-500/20 text-blue-200"
                : "border-neutral-700 bg-neutral-900 text-neutral-300 hover:border-neutral-500"
            }`}
          >
            Comparison
          </button>
        </div>
      </section>

      {viewMode === "comparison" ? (
        <RunComparison runs={runs} defaultRunId={selectedRunId} />
      ) : (
        <>
          <LiveStatusBar summary={summary} evaluatedCount={evaluations.length} />

          <Summary data={summary} />

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
                      const typeLabelMap: Record<AgentTraceStep["type"], string> = {
                        llm_response: "LLM Response",
                        code_execution: "Code Execution",
                        iteration_complete: "Iteration Complete",
                        subcall_start: "Subcall Start",
                        subcall_complete: "Subcall Complete",
                      };
                      const typeLabel = typeLabelMap[step.type] ?? "LLM Response";
                      const isLLMLike = step.type === "llm_response" || step.type === "iteration_complete";
                      return (
                        <article
                          key={`${step.iteration}-${step.type}-${step.timestamp}-${index}`}
                          className="rounded-md border border-neutral-800 bg-black/70 p-3"
                        >
                          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                            <span className="font-semibold text-neutral-200">Iteration {step.iteration}</span>
                            <span
                              className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                                isLLMLike ? "bg-cyan-900/60 text-cyan-200" : "bg-amber-900/60 text-amber-200"
                              }`}
                            >
                              {typeLabel}
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
        </>
      )}
    </div>
  );
}
