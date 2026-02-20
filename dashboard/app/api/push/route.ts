import { NextRequest, NextResponse } from "next/server";
import {
  createRunId,
  setSummary,
  appendEvaluation,
  setClusters,
  setRanking,
  setAgentTrace,
} from "@/lib/store";

/**
 * Push endpoint — the local Python pipeline POSTs results here.
 * Accepts: { run_id?: string, type: "summary"|"evaluation"|"evaluations_batch"|"clusters"|"ranking"|"trace", data: ... }
 * Protected by PUSH_SECRET env var.
 */
export async function POST(req: NextRequest) {
  const secret = process.env.PUSH_SECRET;
  if (secret) {
    const auth = req.headers.get("authorization");
    if (auth !== `Bearer ${secret}`) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const body = await req.json();
  const { type, data } = body;
  const runId = typeof body?.run_id === "string" && body.run_id.trim() ? body.run_id.trim() : createRunId();

  switch (type) {
    case "summary":
      await setSummary(runId, data);
      return NextResponse.json({ ok: true, type: "summary", run_id: runId });

    case "evaluation":
      await appendEvaluation(runId, data);
      return NextResponse.json({ ok: true, type: "evaluation", pr: data.pr_number, run_id: runId });

    case "evaluations_batch":
      for (const ev of data) {
        await appendEvaluation(runId, ev);
      }
      return NextResponse.json({ ok: true, type: "evaluations_batch", count: data.length, run_id: runId });

    case "clusters":
      await setClusters(runId, data);
      return NextResponse.json({ ok: true, type: "clusters", run_id: runId });

    case "ranking":
      await setRanking(runId, data);
      return NextResponse.json({ ok: true, type: "ranking", run_id: runId });

    case "trace":
      await setAgentTrace(runId, data);
      return NextResponse.json({ ok: true, type: "trace", count: Array.isArray(data) ? data.length : 0, run_id: runId });

    default:
      return NextResponse.json({ error: `unknown type: ${type}` }, { status: 400 });
  }
}
