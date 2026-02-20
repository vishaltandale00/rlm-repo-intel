import { NextRequest, NextResponse } from "next/server";
import { getAgentTrace, setAgentTrace, type AgentTraceStep } from "@/lib/store";

export async function GET() {
  const trace = await getAgentTrace();
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

  if (!Array.isArray(trace)) {
    return NextResponse.json({ error: "invalid trace payload" }, { status: 400 });
  }

  await setAgentTrace(trace as AgentTraceStep[]);
  return NextResponse.json({ ok: true, count: trace.length });
}
