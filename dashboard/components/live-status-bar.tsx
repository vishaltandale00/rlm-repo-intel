"use client";

import { SummaryData } from "@/components/types";

interface LiveStatusBarProps {
  summary: SummaryData | null;
  evaluatedCount: number;
}

function asCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function LiveStatusBar({ summary, evaluatedCount }: LiveStatusBarProps) {
  const progress = summary?.total_prs_evaluated ?? evaluatedCount;
  const phase = summary?.current_phase ?? summary?.phase ?? "evaluating";

  const explicitCost = summary?.cost_estimate_usd ?? summary?.cost_estimate;
  const heuristicCost = progress * 0.004;
  const cost = explicitCost ?? heuristicCost;

  return (
    <div className="mb-6 rounded-lg border border-[var(--border)] bg-[var(--card)]/90 px-4 py-3">
      <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Pipeline</div>
          <div className="font-mono text-sm text-blue-300">{progress}/5000 PRs evaluated</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Current phase</div>
          <div className="font-mono text-sm text-amber-300">{phase}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Cost estimate</div>
          <div className="font-mono text-sm text-emerald-300">{asCurrency(cost)}</div>
        </div>
        <div>
          <div className="text-neutral-500 uppercase tracking-wide">Last update</div>
          <div className="font-mono text-sm text-neutral-300">
            {summary?.last_updated ? new Date(summary.last_updated).toLocaleString() : "waiting for data"}
          </div>
        </div>
      </div>
    </div>
  );
}
