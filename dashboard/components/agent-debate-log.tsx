"use client";

import { EvaluationItem } from "@/components/types";
import { useEffect, useMemo, useState } from "react";

interface AgentDebateLogProps {
  evaluations: EvaluationItem[];
  selectedPR: number | null;
  onSelectPR: (prNumber: number) => void;
}

type AgentKey =
  | "code_analyst"
  | "codebase_expert"
  | "risk_assessor"
  | "adversarial_reviewer"
  | "synthesizer";

const AGENT_ORDER: AgentKey[] = [
  "code_analyst",
  "codebase_expert",
  "risk_assessor",
  "adversarial_reviewer",
  "synthesizer",
];

const AGENT_LABELS: Record<AgentKey, string> = {
  code_analyst: "Code Analyst",
  codebase_expert: "Codebase Expert",
  risk_assessor: "Risk Assessor",
  adversarial_reviewer: "Adversarial Reviewer",
  synthesizer: "Synthesizer",
};

function scorePairs(agent: AgentKey, trace: Record<string, unknown>): Array<[string, number]> {
  const pick = (k: string) => {
    const v = trace[k];
    return typeof v === "number" ? v : null;
  };

  if (agent === "code_analyst") {
    return [
      ["risk", pick("risk_score")],
      ["quality", pick("quality_score")],
    ].filter((x): x is [string, number] => x[1] !== null);
  }

  if (agent === "codebase_expert") {
    return [
      ["value", pick("strategic_value")],
      ["novelty", pick("novelty_score")],
    ].filter((x): x is [string, number] => x[1] !== null);
  }

  if (agent === "risk_assessor") {
    return [["risk", pick("risk_score")]].filter((x): x is [string, number] => x[1] !== null);
  }

  if (agent === "adversarial_reviewer") {
    return [["reject", pick("rejection_confidence")]].filter(
      (x): x is [string, number] => x[1] !== null
    );
  }

  return [];
}

function Bubble({
  agent,
  text,
  scores,
  counterArguments,
}: {
  agent: AgentKey;
  text: string;
  scores: Array<[string, number]>;
  counterArguments?: string[];
}) {
  const alignment =
    agent === "adversarial_reviewer"
      ? "justify-end"
      : agent === "synthesizer"
        ? "justify-center"
        : "justify-start";

  const bubbleClass =
    agent === "code_analyst"
      ? "max-w-[85%] border-blue-500/50 bg-blue-500/12"
      : agent === "codebase_expert"
        ? "max-w-[85%] border-emerald-500/50 bg-emerald-500/12"
        : agent === "risk_assessor"
          ? "max-w-[85%] border-amber-500/50 bg-amber-500/12"
          : agent === "adversarial_reviewer"
            ? "max-w-[85%] border-red-500/50 bg-red-500/12"
            : "max-w-[78%] border-purple-500/50 bg-purple-500/14";

  return (
    <div className={`flex ${alignment}`}>
      <div className={`rounded-lg border p-3 ${bubbleClass}`}>
        <div className="mb-1 flex items-center justify-between gap-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral-200">
            {AGENT_LABELS[agent]}
          </div>
          {scores.length > 0 && (
            <div className="flex gap-1">
              {scores.map(([label, value]) => (
                <span
                  key={label}
                  className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[10px] text-neutral-200"
                >
                  {label}:{value.toFixed(2)}
                </span>
              ))}
            </div>
          )}
        </div>

        <p className="whitespace-pre-wrap text-sm text-neutral-100">{text}</p>

        {counterArguments && counterArguments.length > 0 && (
          <div className="mt-2 rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-100">
            {counterArguments.join("; ")}
          </div>
        )}
      </div>
    </div>
  );
}

export function AgentDebateLog({ evaluations, selectedPR, onSelectPR }: AgentDebateLogProps) {
  const withTraces = useMemo(
    () => evaluations.filter((evaluation) => evaluation.agent_traces),
    [evaluations]
  );

  const [expandedPR, setExpandedPR] = useState<number | null>(selectedPR);

  useEffect(() => {
    if (selectedPR !== null) {
      setExpandedPR(selectedPR);
    }
  }, [selectedPR]);

  if (withTraces.length === 0) {
    return <div className="text-neutral-500">Agent reasoning traces will appear here during processing.</div>;
  }

  return (
    <div className="space-y-3">
      {withTraces.slice(0, 40).map((evaluation) => {
        const isExpanded = expandedPR === evaluation.pr_number;
        const traces = evaluation.agent_traces;
        const disagreements = traces?.disagreement_points ?? [];

        return (
          <div key={evaluation.pr_number} className="rounded-lg border border-[var(--border)] bg-[var(--card)]">
            <button
              type="button"
              onClick={() => {
                onSelectPR(evaluation.pr_number);
                setExpandedPR(isExpanded ? null : evaluation.pr_number);
              }}
              className={`flex w-full items-center justify-between gap-3 px-3 py-3 text-left transition-colors ${
                selectedPR === evaluation.pr_number ? "bg-blue-500/10" : "hover:bg-neutral-900/60"
              }`}
            >
              <div className="min-w-0">
                <div className="font-mono text-xs text-blue-300">PR #{evaluation.pr_number}</div>
                <div className="truncate text-sm text-neutral-200">{evaluation.title}</div>
              </div>
              <div className="shrink-0 rounded bg-neutral-900 px-2 py-1 font-mono text-xs text-neutral-300">
                {isExpanded ? "Hide debate" : "Open debate"}
              </div>
            </button>

            {isExpanded && traces && (
              <div className="space-y-2 border-t border-[var(--border)] px-3 py-3">
                {AGENT_ORDER.map((agent, index) => {
                  const trace = traces[agent];
                  if (!trace) return null;

                  const traceObj = trace as Record<string, unknown>;
                  const text = String(
                    traceObj.reasoning ?? traceObj.synthesis_reasoning ?? "No reasoning available."
                  );

                  return (
                    <div key={`${evaluation.pr_number}-${agent}`} className="space-y-2">
                      <Bubble
                        agent={agent}
                        text={text}
                        scores={scorePairs(agent, traceObj)}
                        counterArguments={
                          Array.isArray(traceObj.counter_arguments)
                            ? (traceObj.counter_arguments as string[])
                            : undefined
                        }
                      />
                      {index === 2 && disagreements.length > 0 && (
                        <div className="rounded border border-yellow-500/40 bg-yellow-500/12 px-3 py-2 text-xs text-yellow-100">
                          <span className="mr-1 font-semibold uppercase tracking-wide">Disagreement:</span>
                          {disagreements.join(" | ")}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
