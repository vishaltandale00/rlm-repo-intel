"use client";

import { SummaryData } from "@/components/types";

interface SummaryProps {
  data: SummaryData | null;
}

export function Summary({ data }: SummaryProps) {
  if (!data) return <div className="text-neutral-500">Waiting for data...</div>;

  const cards = [
    { label: "PRs Evaluated", value: data.total_prs_evaluated, color: "text-blue-300" },
    { label: "Modules", value: data.total_modules, color: "text-emerald-300" },
    { label: "Clusters", value: data.clusters, color: "text-amber-300" },
    { label: "Themes", value: data.themes?.length || 0, color: "text-purple-300" },
  ];

  return (
    <div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {cards.map((card) => (
          <div key={card.label} className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4">
            <div className={`font-mono text-2xl font-bold ${card.color}`}>{card.value}</div>
            <div className="mt-1 text-xs uppercase tracking-wide text-neutral-500">{card.label}</div>
          </div>
        ))}
      </div>
      {data.themes?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {data.themes.map((theme) => (
            <span key={theme} className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-1 text-xs text-blue-200">
              {theme}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
