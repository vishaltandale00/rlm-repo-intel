"use client";

import { EvaluationItem, RankingData } from "@/components/types";

interface PRTableProps {
  evaluations: EvaluationItem[];
  ranking: RankingData | null;
  selectedPR: number | null;
  onSelectPR: (prNumber: number) => void;
}

function ScoreBadge({ score, label }: { score: number; label: string }) {
  const color =
    score >= 0.7 ? "text-emerald-300" : score >= 0.4 ? "text-amber-300" : "text-red-300";
  return (
    <div className="text-center">
      <div className={`font-mono text-xs font-bold ${color}`}>{score.toFixed(2)}</div>
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
    </div>
  );
}

export function PRTable({ evaluations, ranking, selectedPR, onSelectPR }: PRTableProps) {
  const sorted = [...evaluations].sort((a, b) => b.final_rank_score - a.final_rank_score);

  if (sorted.length === 0) {
    return <div className="text-neutral-500">No evaluations yet. Pipeline is running...</div>;
  }

  return (
    <div className="space-y-2">
      {sorted.slice(0, 30).map((ev, i) => {
        const rankEntry = ranking?.ranking?.find((r) => r.number === ev.pr_number);
        const isSelected = selectedPR === ev.pr_number;

        return (
          <button
            key={ev.pr_number}
            type="button"
            className={`w-full rounded-lg border px-3 py-3 text-left transition-colors ${
              isSelected
                ? "border-blue-500/70 bg-blue-500/10"
                : "border-[var(--border)] bg-[var(--card)] hover:border-blue-500/40"
            }`}
            onClick={() => onSelectPR(ev.pr_number)}
          >
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="text-xs text-neutral-500">Top #{i + 1}</div>
                <div className="truncate text-sm text-neutral-200">
                  <span className="font-mono text-blue-300">PR #{ev.pr_number}</span> {ev.title}
                </div>
                {rankEntry?.reason && (
                  <div className="mt-1 truncate text-xs text-emerald-300/80">{rankEntry.reason}</div>
                )}
              </div>
              <div className="flex shrink-0 gap-3">
                <ScoreBadge score={ev.final_rank_score} label="rank" />
                <ScoreBadge score={ev.strategic_value} label="value" />
                <ScoreBadge score={ev.risk_score} label="risk" />
                <ScoreBadge score={ev.quality_score} label="quality" />
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
