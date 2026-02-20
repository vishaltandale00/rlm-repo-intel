"use client";

import { useState } from "react";

interface ClusterViewProps {
  clusters: Array<{
    cluster_id: number;
    members: number[];
    size: number;
    relations: Array<{
      pr_a: number;
      pr_b: number;
      relation_type: string;
      explanation: string;
    }>;
  }>;
}

const RELATION_COLORS: Record<string, string> = {
  redundant: "text-red-400 bg-red-400/10",
  alternative: "text-amber-400 bg-amber-400/10",
  conflicting: "text-orange-400 bg-orange-400/10",
  composable: "text-green-400 bg-green-400/10",
};

export function ClusterView({ clusters }: ClusterViewProps) {
  const [expanded, setExpanded] = useState<number | null>(null);

  if (clusters.length === 0) {
    return <div className="text-neutral-500">No clusters detected yet.</div>;
  }

  return (
    <div className="space-y-2">
      {clusters.map((c) => (
        <div
          key={c.cluster_id}
          className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-3 cursor-pointer hover:border-amber-500/50 transition-colors"
          onClick={() => setExpanded(expanded === c.cluster_id ? null : c.cluster_id)}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-amber-400 font-bold">{c.size} PRs</span>
              <span className="text-neutral-400 text-sm">
                #{c.members.join(", #")}
              </span>
            </div>
            <div className="flex gap-1">
              {[...new Set(c.relations.map((r) => r.relation_type))].map((t) => (
                <span
                  key={t}
                  className={`text-xs px-2 py-0.5 rounded-full ${RELATION_COLORS[t] || "text-neutral-400 bg-neutral-800"}`}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>

          {expanded === c.cluster_id && (
            <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-2">
              {c.relations.map((r, i) => (
                <div key={i} className="text-sm">
                  <span className="text-blue-400 font-mono">#{r.pr_a}</span>
                  <span className={`mx-2 text-xs px-2 py-0.5 rounded ${RELATION_COLORS[r.relation_type] || ""}`}>
                    {r.relation_type}
                  </span>
                  <span className="text-blue-400 font-mono">#{r.pr_b}</span>
                  {r.explanation && (
                    <p className="text-neutral-400 text-xs mt-1 ml-4">
                      {r.explanation}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
