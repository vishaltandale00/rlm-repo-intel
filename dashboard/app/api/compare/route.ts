import { NextRequest, NextResponse } from "next/server";

import { getRunComparison } from "@/lib/store";

export async function GET(req: NextRequest) {
  const runA = req.nextUrl.searchParams.get("a");
  const runB = req.nextUrl.searchParams.get("b");

  if (!runA || !runB) {
    return NextResponse.json({ error: "Both query params 'a' and 'b' are required." }, { status: 400 });
  }

  const comparison = await getRunComparison(runA, runB);
  return NextResponse.json(comparison);
}
