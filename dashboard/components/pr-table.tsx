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

function scoreBadgeStyle(score?: number) {
  if (score === undefined || Number.isNaN(score)) return "border-neutral-700 bg-neutral-900 text-neutral-400";
  return `border-transparent text-black`;
}

function scoreBadgeColor(score?: number) {
  if (score === undefined || Number.isNaN(score)) return undefined;
  const clamped = Math.max(0, Math.min(10, score));
  const hue = Math.round((clamped / 10) * 120);
  return { backgroundColor: `hsl(${hue} 85% 60%)` };
}

function ScoreBadge({ score, label }: { score?: number; label: string }) {
  if (score === undefined || Number.isNaN(score)) {
    return <span className="inline-flex min-w-12 justify-center rounded border border-neutral-700 bg-neutral-900 px-2 py-1 font-mono text-xs text-neutral-400">--</span>;
  }

  return (
    <span
      className={`inline-flex min-w-12 justify-center rounded border px-2 py-1 font-mono text-xs font-semibold ${scoreBadgeStyle(score)} ${scoreColor(score)}`}
      style={scoreBadgeColor(score)}
      aria-label={`${label} score ${score.toFixed(1)}`}
    >
      {score.toFixed(1)}
    </span>
  );
}

function stateBadgeClass(state?: string) {
  switch (state) {
    case "ready":
      return "bg-emerald-900/40 text-emerald-200 border-emerald-500/40";
    case "needs_author_review":
      return "bg-amber-900/40 text-amber-200 border-amber-500/40";
    case "triage":
      return "bg-neutral-800 text-neutral-200 border-neutral-600";
    default:
      return "bg-neutral-900 text-neutral-300 border-neutral-700";
  }
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

      <div className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--card)]">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-neutral-900/70 text-left text-xs uppercase tracking-wide text-neutral-400">
              <tr>
                <th className="px-3 py-2">PR#</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2 text-center">Urgency</th>
                <th className="px-3 py-2 text-center">Quality</th>
                <th className="px-3 py-2 text-center">Final Score</th>
                <th className="px-3 py-2">State</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 50).map((ev) => {
                const rankEntry = ranking?.ranking?.find((r) => r.number === ev.pr_number);
                const isSelected = selectedPR === ev.pr_number;
                const urgency = ev.urgency ?? ev.risk_score;
                const quality = ev.quality ?? ev.quality_score;
                const rank =
                  ev.final_score ??
                  ev.final_rank_score ??
                  (urgency !== undefined && quality !== undefined ? (urgency + quality) / 2 : undefined);

                return (
                  <tr
                    key={ev.pr_number}
                    className={`cursor-pointer border-t border-neutral-800 text-neutral-200 transition-colors ${
                      isSelected ? "bg-blue-500/10" : "hover:bg-neutral-900/60"
                    }`}
                    onClick={() => onSelectPR(ev.pr_number)}
                  >
                    <td className="px-3 py-2 align-top">
                      <a
                        href={`https://github.com/openclaw/openclaw/pull/${ev.pr_number}`}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-blue-300 hover:underline"
                        onClick={(event) => event.stopPropagation()}
                      >
                        #{ev.pr_number}
                      </a>
                    </td>
                    <td className="max-w-xl px-3 py-2 align-top">
                      <div className="truncate" title={ev.title}>{ev.title}</div>
                      {ev.author ? <div className="text-xs text-neutral-500">by {ev.author}</div> : null}
                      {rankEntry?.reason ? <div className="truncate text-xs text-emerald-300/80">{rankEntry.reason}</div> : null}
                    </td>
                    <td className="px-3 py-2 text-center align-top">
                      <ScoreBadge score={urgency} label="urgency" />
                    </td>
                    <td className="px-3 py-2 text-center align-top">
                      <ScoreBadge score={quality} label="quality" />
                    </td>
                    <td className="px-3 py-2 text-center align-top">
                      <ScoreBadge score={rank} label="final score" />
                    </td>
                    <td className="px-3 py-2 align-top">
                      <span className={`inline-flex rounded border px-2 py-1 text-xs ${stateBadgeClass(ev.state)}`}>
                        {ev.state || "unknown"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      {filtered.length === 0 && <div className="text-neutral-500">No PRs match the current filters.</div>}
    </div>
  );
}
