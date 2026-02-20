import { NextResponse } from "next/server";
import { getSummary } from "@/lib/store";

export async function GET() {
  const summary = await getSummary();
  return NextResponse.json(summary ?? { total_prs_evaluated: 0, total_modules: 0, clusters: 0, themes: [], top_prs: [] });
}
