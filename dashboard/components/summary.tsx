"use client";

interface SummaryProps {
  data: {
    total_prs_evaluated: number;
    total_modules: number;
    clusters: number;
    themes: string[];
    last_updated?: string;
  } | null;
}

export function Summary({ data }: SummaryProps) {
  if (!data) return <div className="text-neutral-500">Waiting for data...</div>;

  const cards = [
    { label: "PRs Evaluated", value: data.total_prs_evaluated, color: "text-blue-400" },
    { label: "Modules", value: data.total_modules, color: "text-green-400" },
    { label: "Clusters Found", value: data.clusters, color: "text-amber-400" },
    { label: "Themes", value: data.themes?.length || 0, color: "text-purple-400" },
  ];

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        {cards.map((c) => (
          <div
            key={c.label}
            className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-4"
          >
            <div className={`text-3xl font-bold ${c.color}`}>{c.value}</div>
            <div className="text-neutral-400 text-sm mt-1">{c.label}</div>
          </div>
        ))}
      </div>
      {data.themes?.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {data.themes.map((t) => (
            <span
              key={t}
              className="bg-blue-500/10 text-blue-400 text-xs px-2 py-1 rounded-full"
            >
              {t}
            </span>
          ))}
        </div>
      )}
      {data.last_updated && (
        <div className="text-neutral-600 text-xs mt-2">
          Last updated: {new Date(data.last_updated).toLocaleString()}
        </div>
      )}
    </div>
  );
}
