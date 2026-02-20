import { NextRequest, NextResponse } from "next/server";
import { getAgentTrace, getLatestRunId, getOrCreateCurrentRunId, setAgentTrace, type AgentTraceStep } from "@/lib/store";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id") ?? (await getLatestRunId());
  const trace = runId ? await getAgentTrace(runId) : [];
  return NextResponse.json(trace);
}

export async function POST(req: NextRequest) {
  const secret = process.env.PUSH_SECRET;
  if (secret) {
    const auth = req.headers.get("authorization");
    if (auth !== `Bearer ${secret}`) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const body = await req.json();
  const trace = Array.isArray(body) ? body : body?.trace;
  const runId =
    typeof body?.run_id === "string" && body.run_id.trim() ? body.run_id.trim() : await getOrCreateCurrentRunId();

  if (!Array.isArray(trace)) {
    return NextResponse.json({ error: "invalid trace payload" }, { status: 400 });
  }

  await setAgentTrace(runId, trace as AgentTraceStep[]);
  return NextResponse.json({ ok: true, count: trace.length, run_id: runId });
}
