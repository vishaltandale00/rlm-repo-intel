import { NextRequest, NextResponse } from "next/server";
import { getEvaluations, getLatestRunId } from "@/lib/store";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id") ?? (await getLatestRunId());
  const evals = runId ? await getEvaluations(runId) : [];
  return NextResponse.json(evals);
}
