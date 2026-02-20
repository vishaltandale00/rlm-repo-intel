"use client";

import { useState } from "react";

interface PRTableProps {
  evaluations: Array<{
    pr_number: number;
    title: string;
    risk_score: number;
    quality_score: number;
    strategic_value: number;
    final_rank_score: number;
    review_summary: string;
    confidence: number;
    impact_scope: string[];
  }>;
  ranking: { ranking?: Array<{ number: number; rank: number; reason: string }> } | null;
}

function ScoreBadge({ score, label }: { score: number; label: string }) {
  const color =
    score >= 0.7 ? "text-green-400" : score >= 0.4 ? "text-amber-400" : "text-red-400";
  return (
    <div className="text-center">
      <div className={`text-sm font-mono font-bold ${color}`}>{score.toFixed(2)}</div>
      <div className="text-[10px] text-neutral-500">{label}</div>
    </div>
  );
}

export function PRTable({ evaluations, ranking }: PRTableProps) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [sortBy, setSortBy] = useState<string>("final_rank_score");

  const sorted = [...evaluations].sort(
    (a, b) => (b[sortBy as keyof typeof b] as number) - (a[sortBy as keyof typeof a] as number)
  );

  if (sorted.length === 0) {
    return <div className="text-neutral-500">No evaluations yet. Pipeline is running...</div>;
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2 text-xs mb-2">
        {["final_rank_score", "strategic_value", "risk_score", "quality_score"].map((key) => (
          <button
            key={key}
            onClick={() => setSortBy(key)}
            className={`px-2 py-1 rounded ${
              sortBy === key ? "bg-blue-500/20 text-blue-400" : "bg-neutral-800 text-neutral-400"
            }`}
          >
            {key.replace("_", " ").replace("score", "").trim()}
          </button>
        ))}
      </div>

      {sorted.slice(0, 50).map((ev, i) => {
        const rankEntry = ranking?.ranking?.find((r) => r.number === ev.pr_number);
        return (
          <div
            key={ev.pr_number}
            className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-3 cursor-pointer hover:border-blue-500/50 transition-colors"
            onClick={() => setExpanded(expanded === ev.pr_number ? null : ev.pr_number)}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-neutral-500 text-sm w-6">#{i + 1}</span>
                <div>
                  <span className="text-blue-400 font-mono text-sm">PR #{ev.pr_number}</span>
                  <span className="text-neutral-300 ml-2 text-sm">{ev.title}</span>
                </div>
              </div>
              <div className="flex gap-4">
                <ScoreBadge score={ev.final_rank_score} label="rank" />
                <ScoreBadge score={ev.strategic_value} label="value" />
                <ScoreBadge score={ev.risk_score} label="risk" />
                <ScoreBadge score={ev.quality_score} label="quality" />
              </div>
            </div>

            {expanded === ev.pr_number && (
              <div className="mt-3 pt-3 border-t border-[var(--border)]">
                <p className="text-neutral-300 text-sm">{ev.review_summary}</p>
                {ev.impact_scope?.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {ev.impact_scope.map((m) => (
                      <span
                        key={m}
                        className="bg-neutral-800 text-neutral-400 text-xs px-2 py-0.5 rounded"
                      >
                        {m}
                      </span>
                    ))}
                  </div>
                )}
                {rankEntry?.reason && (
                  <div className="mt-2 text-xs text-green-400/80">
                    🏆 {rankEntry.reason}
                  </div>
                )}
                <div className="text-xs text-neutral-600 mt-1">
                  Confidence: {ev.confidence?.toFixed(2)}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
