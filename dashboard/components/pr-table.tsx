"use client";

import { EvaluationItem, RankingData } from "@/components/types";
import { useMemo, useState } from "react";

interface PRTableProps {
  evaluations: EvaluationItem[];
  ranking: RankingData | null;
  selectedPR: number | null;
  onSelectPR: (prNumber: number) => void;
}

function scoreColor(score: number) {
  if (score >= 8) return "text-emerald-300";
  if (score >= 6) return "text-amber-300";
  return "text-red-300";
}

function ScoreBadge({ score, label }: { score?: number; label: string }) {
  if (score === undefined || Number.isNaN(score)) {
    return (
      <div className="text-center">
        <div className="font-mono text-xs font-bold text-neutral-400">--</div>
        <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
      </div>
    );
  }

  return (
    <div className="text-center">
      <div className={`font-mono text-xs font-bold ${scoreColor(score)}`}>{score.toFixed(1)}</div>
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">{label}</div>
    </div>
  );
}

export function PRTable({ evaluations, ranking, selectedPR, onSelectPR }: PRTableProps) {
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [sortBy, setSortBy] = useState("urgency");

  const filtered = useMemo(() => {
    const lowered = query.trim().toLowerCase();

    return evaluations
      .filter((ev) => {
        if (stateFilter !== "all" && ev.state !== stateFilter) return false;
        if (!lowered) return true;
        return (
          String(ev.pr_number).includes(lowered) ||
          ev.title.toLowerCase().includes(lowered) ||
          (ev.author?.toLowerCase().includes(lowered) ?? false)
        );
      })
      .sort((a, b) => {
        const urgencyA = a.urgency ?? a.risk_score ?? 0;
        const urgencyB = b.urgency ?? b.risk_score ?? 0;
        const qualityA = a.quality ?? a.quality_score ?? 0;
        const qualityB = b.quality ?? b.quality_score ?? 0;
        const rankA = a.final_rank_score ?? (urgencyA + qualityA) / 2;
        const rankB = b.final_rank_score ?? (urgencyB + qualityB) / 2;

        if (sortBy === "quality") return qualityB - qualityA;
        if (sortBy === "rank") return rankB - rankA;
        if (sortBy === "pr") return b.pr_number - a.pr_number;
        return urgencyB - urgencyA;
      });
  }, [evaluations, query, sortBy, stateFilter]);

  if (evaluations.length === 0) {
    return <div className="text-neutral-500">No evaluations yet. Pipeline is running...</div>;
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-2 rounded-lg border border-[var(--border)] bg-[var(--card)] p-3 md:grid-cols-4">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-xs text-neutral-200 outline-none ring-0 focus:border-blue-500"
          placeholder="Filter by PR, title, author"
        />
        <select
          value={stateFilter}
          onChange={(event) => setStateFilter(event.target.value)}
          className="rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-xs text-neutral-200 outline-none"
        >
          <option value="all">All states</option>
          <option value="ready">ready</option>
          <option value="needs_author_review">needs_author_review</option>
          <option value="triage">triage</option>
        </select>
        <select
          value={sortBy}
          onChange={(event) => setSortBy(event.target.value)}
          className="rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-xs text-neutral-200 outline-none"
        >
          <option value="urgency">Sort: urgency</option>
          <option value="quality">Sort: quality</option>
          <option value="rank">Sort: rank</option>
          <option value="pr">Sort: PR number</option>
        </select>
        <div className="self-center text-right text-xs text-neutral-400">{filtered.length} PRs</div>
      </div>

      {filtered.slice(0, 50).map((ev, i) => {
        const rankEntry = ranking?.ranking?.find((r) => r.number === ev.pr_number);
        const isSelected = selectedPR === ev.pr_number;
        const urgency = ev.urgency ?? ev.risk_score;
        const quality = ev.quality ?? ev.quality_score;
        const rank = ev.final_rank_score ?? (urgency !== undefined && quality !== undefined ? (urgency + quality) / 2 : undefined);

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
                <div className="mt-1 flex items-center gap-2 text-[11px] text-neutral-400">
                  <span className="rounded bg-neutral-900 px-1.5 py-0.5">{ev.state || "unknown"}</span>
                  {ev.author ? <span>by {ev.author}</span> : null}
                </div>
                {rankEntry?.reason && (
                  <div className="mt-1 truncate text-xs text-emerald-300/80">{rankEntry.reason}</div>
                )}
              </div>
              <div className="flex shrink-0 gap-3">
                <ScoreBadge score={rank} label="rank" />
                <ScoreBadge score={urgency} label="urgency" />
                <ScoreBadge score={quality} label="quality" />
                <ScoreBadge score={ev.strategic_value} label="value" />
              </div>
            </div>
          </button>
        );
      })}
      {filtered.length === 0 && <div className="text-neutral-500">No PRs match the current filters.</div>}
    </div>
  );
}
