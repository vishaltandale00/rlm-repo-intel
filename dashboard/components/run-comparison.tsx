"use client";

import { useEffect, useMemo, useState } from "react";

type RunTab = {
  id: string;
  timestamp: string;
};

type ComparisonRow = {
  pr_number: number;
  title: string;
  run_a_score: number | null;
  run_b_score: number | null;
  delta: number | null;
  rank_a: number | null;
  rank_b: number | null;
  justification_a: string;
  justification_b: string;
};

type ComparisonPayload = {
  run_a: {
    id: string;
    scored_count: number;
    avg_score: number;
    meta: { prompt_hash?: string; model_name?: string; cost_usd?: number; time_elapsed_seconds?: number } | null;
  };
  run_b: {
    id: string;
    scored_count: number;
    avg_score: number;
    meta: { prompt_hash?: string; model_name?: string; cost_usd?: number; time_elapsed_seconds?: number } | null;
  };
  rows: ComparisonRow[];
};

interface RunComparisonProps {
  runs: RunTab[];
  defaultRunId?: string | null;
}

function fmtScore(value: number | null) {
  if (value === null || Number.isNaN(value)) return "n/a";
  return value.toFixed(2);
}

function fmtDelta(value: number | null) {
  if (value === null || Number.isNaN(value)) return "n/a";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function fmtText(value?: string) {
  if (!value || !value.trim()) return "n/a";
  return value;
}

export function RunComparison({ runs, defaultRunId }: RunComparisonProps) {
  if (runs.length === 0) {
    return (
      <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4">
        <h2 className="mb-3 text-lg font-semibold tracking-tight text-neutral-100">Run Comparison</h2>
        <p className="text-sm text-neutral-400">No runs available to compare yet.</p>
      </section>
    );
  }

  const initialRunA = defaultRunId && runs.some((run) => run.id === defaultRunId) ? defaultRunId : (runs[0]?.id ?? "");
  const initialRunB =
    runs.find((run) => run.id !== initialRunA)?.id ??
    runs[1]?.id ??
    runs[0]?.id ??
    "";

  const [runA, setRunA] = useState(initialRunA);
  const [runB, setRunB] = useState(initialRunB);
  const [selectedPR, setSelectedPR] = useState<number | null>(null);
  const [payload, setPayload] = useState<ComparisonPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runA || !runB) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/compare?a=${encodeURIComponent(runA)}&b=${encodeURIComponent(runB)}`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.text();
          throw new Error(body || "Failed to load comparison");
        }
        return res.json();
      })
      .then((data: ComparisonPayload) => {
        if (cancelled) return;
        setPayload(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPayload(null);
        setError(err instanceof Error ? err.message : "Failed to load comparison");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runA, runB]);

  const rows = payload?.rows ?? [];

  useEffect(() => {
    if (rows.length === 0) {
      setSelectedPR(null);
      return;
    }
    if (!selectedPR || !rows.some((row) => row.pr_number === selectedPR)) {
      setSelectedPR(rows[0]?.pr_number ?? null);
    }
  }, [rows, selectedPR]);

  const selectedRow = useMemo(
    () => rows.find((row) => row.pr_number === selectedPR) ?? null,
    [rows, selectedPR]
  );

  return (
    <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4">
      <h2 className="mb-3 text-lg font-semibold tracking-tight text-neutral-100">Run Comparison</h2>

      <div className="mb-4 grid gap-3 md:grid-cols-2">
        <label className="text-xs text-neutral-300">
          <span className="mb-1 block uppercase tracking-wide text-neutral-500">Run A</span>
          <select
            className="w-full rounded border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm text-neutral-100"
            value={runA}
            onChange={(event) => setRunA(event.target.value)}
          >
            {runs.map((run) => (
              <option key={`a-${run.id}`} value={run.id}>
                {run.id}
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs text-neutral-300">
          <span className="mb-1 block uppercase tracking-wide text-neutral-500">Run B</span>
          <select
            className="w-full rounded border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm text-neutral-100"
            value={runB}
            onChange={(event) => setRunB(event.target.value)}
          >
            {runs.map((run) => (
              <option key={`b-${run.id}`} value={run.id}>
                {run.id}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p className="text-sm text-neutral-400">Loading comparison...</p>}
      {error && <p className="text-sm text-red-300">{error}</p>}

      {!loading && !error && payload && (
        <div className="space-y-4">
          <div className="grid gap-3 text-xs md:grid-cols-2">
            <div className="rounded border border-neutral-700 bg-black/20 p-3">
              <div className="mb-2 font-semibold text-neutral-200">Run A Metadata</div>
              <div>Prompt: {fmtText(payload.run_a.meta?.prompt_hash?.slice(0, 12))}</div>
              <div>Model: {fmtText(payload.run_a.meta?.model_name)}</div>
              <div>Cost: {payload.run_a.meta?.cost_usd !== undefined ? `$${payload.run_a.meta.cost_usd.toFixed(2)}` : "n/a"}</div>
              <div>Duration: {payload.run_a.meta?.time_elapsed_seconds !== undefined ? `${payload.run_a.meta.time_elapsed_seconds.toFixed(1)}s` : "n/a"}</div>
              <div>PRs Scored: {payload.run_a.scored_count}</div>
              <div>Avg Score: {payload.run_a.avg_score.toFixed(2)}</div>
            </div>
            <div className="rounded border border-neutral-700 bg-black/20 p-3">
              <div className="mb-2 font-semibold text-neutral-200">Run B Metadata</div>
              <div>Prompt: {fmtText(payload.run_b.meta?.prompt_hash?.slice(0, 12))}</div>
              <div>Model: {fmtText(payload.run_b.meta?.model_name)}</div>
              <div>Cost: {payload.run_b.meta?.cost_usd !== undefined ? `$${payload.run_b.meta.cost_usd.toFixed(2)}` : "n/a"}</div>
              <div>Duration: {payload.run_b.meta?.time_elapsed_seconds !== undefined ? `${payload.run_b.meta.time_elapsed_seconds.toFixed(1)}s` : "n/a"}</div>
              <div>PRs Scored: {payload.run_b.scored_count}</div>
              <div>Avg Score: {payload.run_b.avg_score.toFixed(2)}</div>
            </div>
          </div>

          <div className="overflow-auto rounded border border-neutral-700">
            <table className="w-full min-w-[860px] text-left text-xs">
              <thead className="bg-neutral-900 text-neutral-400">
                <tr>
                  <th className="px-3 py-2">PR</th>
                  <th className="px-3 py-2">Title</th>
                  <th className="px-3 py-2">Run A</th>
                  <th className="px-3 py-2">Run B</th>
                  <th className="px-3 py-2">Delta</th>
                  <th className="px-3 py-2">Rank A</th>
                  <th className="px-3 py-2">Rank B</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const selected = row.pr_number === selectedPR;
                  return (
                    <tr
                      key={row.pr_number}
                      onClick={() => setSelectedPR(row.pr_number)}
                      className={`cursor-pointer border-t border-neutral-800 ${selected ? "bg-blue-500/10" : "hover:bg-neutral-900/60"}`}
                    >
                      <td className="px-3 py-2 font-mono text-blue-300">#{row.pr_number}</td>
                      <td className="max-w-[320px] truncate px-3 py-2 text-neutral-200">{row.title}</td>
                      <td className="px-3 py-2">{fmtScore(row.run_a_score)}</td>
                      <td className="px-3 py-2">{fmtScore(row.run_b_score)}</td>
                      <td className={`px-3 py-2 font-mono ${row.delta !== null && row.delta > 0 ? "text-emerald-300" : "text-amber-200"}`}>
                        {fmtDelta(row.delta)}
                      </td>
                      <td className="px-3 py-2">{row.rank_a ?? "n/a"}</td>
                      <td className="px-3 py-2">{row.rank_b ?? "n/a"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selectedRow && (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded border border-neutral-700 bg-black/20 p-3 text-xs">
                <div className="mb-2 font-semibold text-neutral-200">Run A Justification (PR #{selectedRow.pr_number})</div>
                <p className="whitespace-pre-wrap text-neutral-300">{selectedRow.justification_a || "n/a"}</p>
              </div>
              <div className="rounded border border-neutral-700 bg-black/20 p-3 text-xs">
                <div className="mb-2 font-semibold text-neutral-200">Run B Justification (PR #{selectedRow.pr_number})</div>
                <p className="whitespace-pre-wrap text-neutral-300">{selectedRow.justification_b || "n/a"}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
