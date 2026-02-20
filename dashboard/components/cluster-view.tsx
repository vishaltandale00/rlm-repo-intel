"use client";

import { ClusterItem, ClusterRelation, EvaluationItem } from "@/components/types";
import { useMemo, useState } from "react";

interface ClusterViewProps {
  clusters: ClusterItem[];
  evaluations: EvaluationItem[];
  onSelectPR: (prNumber: number) => void;
  selectedPR: number | null;
}

const RELATION_COLORS: Record<string, string> = {
  redundant: "border-red-500/40 bg-red-500/10 text-red-300",
  alternative: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  conflicting: "border-orange-500/40 bg-orange-500/10 text-orange-300",
  composable: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
};

function ClusterDebate({ relation }: { relation: ClusterRelation }) {
  const proposer = relation.proposer_reasoning ?? relation.debate?.proposer;
  const challenger = relation.challenger_reasoning ?? relation.debate?.challenger;

  if (!proposer && !challenger) {
    return (
      <div className="mt-2 rounded border border-neutral-700 bg-neutral-900/70 p-2 text-xs text-neutral-400">
        {relation.explanation || "No proposer/challenger trace provided."}
      </div>
    );
  }

  return (
    <div className="mt-2 grid gap-2 md:grid-cols-2">
      <div className="rounded border border-blue-500/30 bg-blue-500/10 p-2 text-xs text-blue-100">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-blue-300">Proposer</div>
        {proposer || relation.explanation || "No proposer note."}
      </div>
      <div className="rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-100">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-red-300">Challenger</div>
        {challenger || "No challenger note."}
      </div>
    </div>
  );
}

export function ClusterView({ clusters, evaluations, onSelectPR, selectedPR }: ClusterViewProps) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const evalMap = useMemo(
    () => new Map(evaluations.map((ev) => [ev.pr_number, ev])),
    [evaluations]
  );

  if (clusters.length === 0) {
    return <div className="text-neutral-500">No clusters detected yet.</div>;
  }

  return (
    <div className="space-y-3">
      {clusters.map((cluster) => {
        const isExpanded = expanded === cluster.cluster_id;

        return (
          <div key={cluster.cluster_id} className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-3">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 text-left"
              onClick={() => setExpanded(isExpanded ? null : cluster.cluster_id)}
            >
              <div>
                <div className="text-sm font-semibold text-amber-300">Cluster #{cluster.cluster_id}</div>
                <div className="text-xs text-neutral-400">
                  {cluster.size} PRs: #{cluster.members.join(", #")}
                </div>
              </div>
              <div className="flex flex-wrap justify-end gap-1">
                {[...new Set(cluster.relations.map((r) => r.relation_type))].map((type) => (
                  <span
                    key={type}
                    className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                      RELATION_COLORS[type] || "border border-neutral-700 bg-neutral-800 text-neutral-300"
                    }`}
                  >
                    {type}
                  </span>
                ))}
              </div>
            </button>

            {isExpanded && (
              <div className="mt-3 space-y-3 border-t border-[var(--border)] pt-3">
                <div className="grid gap-2 md:grid-cols-2">
                  {cluster.members.map((pr) => {
                    const ev = evalMap.get(pr);
                    const isSelected = selectedPR === pr;
                    const analyst = ev?.agent_traces?.code_analyst;
                    const expert = ev?.agent_traces?.codebase_expert;
                    const assessor = ev?.agent_traces?.risk_assessor;
                    const adversarial = ev?.agent_traces?.adversarial_reviewer;

                    return (
                      <button
                        key={pr}
                        type="button"
                        onClick={() => onSelectPR(pr)}
                        className={`rounded border px-3 py-2 text-left transition-colors ${
                          isSelected
                            ? "border-blue-500/70 bg-blue-500/10"
                            : "border-neutral-700 bg-neutral-900/60 hover:border-blue-500/40"
                        }`}
                      >
                        <div className="text-sm text-blue-300 font-mono">PR #{pr}</div>
                        <div className="mt-1 text-xs text-neutral-400 line-clamp-1">{ev?.title || "Missing metadata"}</div>
                        <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-neutral-400">
                          <span className="rounded bg-black/20 px-1.5 py-0.5">
                            analyst q {analyst?.quality_score?.toFixed(2) ?? "--"}
                          </span>
                          <span className="rounded bg-black/20 px-1.5 py-0.5">
                            expert n {expert?.novelty_score?.toFixed(2) ?? "--"}
                          </span>
                          <span className="rounded bg-black/20 px-1.5 py-0.5">
                            assessor r {assessor?.risk_score?.toFixed(2) ?? "--"}
                          </span>
                          <span className="rounded bg-black/20 px-1.5 py-0.5">
                            adversary rej {adversarial?.rejection_confidence?.toFixed(2) ?? "--"}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>

                <div className="space-y-2">
                  {cluster.relations.map((relation, index) => (
                    <div
                      key={`${cluster.cluster_id}-${index}`}
                      className={`rounded border p-2 ${
                        RELATION_COLORS[relation.relation_type] ||
                        "border-neutral-700 bg-neutral-900/60 text-neutral-300"
                      }`}
                    >
                      <div className="text-xs">
                        <span className="font-mono">#{relation.pr_a}</span>
                        <span className="mx-2 rounded bg-black/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
                          {relation.relation_type}
                        </span>
                        <span className="font-mono">#{relation.pr_b}</span>
                      </div>
                      <ClusterDebate relation={relation} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
