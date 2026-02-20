import { NextRequest, NextResponse } from "next/server";
import { getLatestRunId, getRanking } from "@/lib/store";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id") ?? (await getLatestRunId());
  const ranking = runId ? await getRanking(runId) : null;
  return NextResponse.json(ranking ?? { ranking: [], themes: [], conflicts: [] });
}
