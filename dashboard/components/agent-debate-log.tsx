"use client";

import { useState } from "react";

interface AgentDebateLogProps {
  evaluations: Array<{
    pr_number: number;
    title: string;
    agent_traces?: {
      code_analyst?: { reasoning?: string; risk_score?: number; quality_score?: number };
      codebase_expert?: { reasoning?: string; strategic_value?: number; novelty_score?: number };
      risk_assessor?: { reasoning?: string; risk_score?: number };
      adversarial_reviewer?: { reasoning?: string; rejection_confidence?: number; counter_arguments?: string[] };
      synthesizer?: { synthesis_reasoning?: string };
      disagreement_points?: string[];
    };
  }>;
}

const AGENT_COLORS: Record<string, string> = {
  code_analyst: "border-blue-500 text-blue-400",
  codebase_expert: "border-green-500 text-green-400",
  risk_assessor: "border-amber-500 text-amber-400",
  adversarial_reviewer: "border-red-500 text-red-400",
  synthesizer: "border-purple-500 text-purple-400",
};

const AGENT_ICONS: Record<string, string> = {
  code_analyst: "🔍",
  codebase_expert: "🏗️",
  risk_assessor: "⚠️",
  adversarial_reviewer: "👹",
  synthesizer: "⚖️",
};

export function AgentDebateLog({ evaluations }: AgentDebateLogProps) {
  const [selectedPR, setSelectedPR] = useState<number | null>(null);

  const withTraces = evaluations.filter((e) => e.agent_traces);
  if (withTraces.length === 0) {
    return <div className="text-neutral-500">Agent reasoning traces will appear here during processing.</div>;
  }

  const selected = withTraces.find((e) => e.pr_number === selectedPR) || withTraces[0];

  return (
    <div className="grid md:grid-cols-[200px_1fr] gap-4">
      <div className="space-y-1 max-h-96 overflow-y-auto">
        {withTraces.slice(0, 30).map((ev) => (
          <button
            key={ev.pr_number}
            onClick={() => setSelectedPR(ev.pr_number)}
            className={`w-full text-left text-sm px-2 py-1.5 rounded transition-colors ${
              selected?.pr_number === ev.pr_number
                ? "bg-blue-500/20 text-blue-400"
                : "text-neutral-400 hover:bg-neutral-800"
            }`}
          >
            #{ev.pr_number}
          </button>
        ))}
      </div>

      {selected?.agent_traces && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-neutral-300">
            PR #{selected.pr_number}: {selected.title}
          </h3>

          {Object.entries(selected.agent_traces).map(([agent, trace]) => {
            if (agent === "disagreement_points") {
              const points = trace as string[];
              if (!points?.length) return null;
              return (
                <div key={agent} className="bg-amber-500/5 border border-amber-500/30 rounded-lg p-3">
                  <div className="text-amber-400 text-sm font-semibold mb-1">
                    ⚡ Disagreement Points
                  </div>
                  <ul className="text-xs text-neutral-300 space-y-1">
                    {points.map((p, i) => (
                      <li key={i}>• {p}</li>
                    ))}
                  </ul>
                </div>
              );
            }

            const agentTrace = trace as Record<string, unknown>;
            const color = AGENT_COLORS[agent] || "border-neutral-500 text-neutral-400";
            const icon = AGENT_ICONS[agent] || "🤖";

            return (
              <div
                key={agent}
                className={`border-l-2 ${color.split(" ")[0]} bg-[var(--card)] rounded-r-lg p-3`}
              >
                <div className={`text-sm font-semibold ${color.split(" ")[1]} mb-1`}>
                  {icon} {agent.replace("_", " ")}
                </div>
                <div className="text-xs text-neutral-300">
                  {String(agentTrace.reasoning || agentTrace.synthesis_reasoning || JSON.stringify(agentTrace, null, 2))}
                </div>
                {Array.isArray(agentTrace.counter_arguments) && (
                  <div className="mt-1 text-xs text-red-300/80">
                    Counter: {(agentTrace.counter_arguments as string[]).join("; ")}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
