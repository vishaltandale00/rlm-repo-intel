import { NextRequest, NextResponse } from "next/server";
import { getLatestRunId, getSummary } from "@/lib/store";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id") ?? (await getLatestRunId());
  const summary = runId ? await getSummary(runId) : null;
  return NextResponse.json(summary ?? { total_prs_evaluated: 0, total_modules: 0, clusters: 0, themes: [], top_prs: [] });
}
