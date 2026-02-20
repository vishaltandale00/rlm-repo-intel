import { NextRequest, NextResponse } from "next/server";
import { getClusters, getLatestRunId } from "@/lib/store";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id") ?? (await getLatestRunId());
  const clusters = runId ? await getClusters(runId) : [];
  return NextResponse.json(clusters);
}
