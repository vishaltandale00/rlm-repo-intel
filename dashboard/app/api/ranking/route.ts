import { NextResponse } from "next/server";
import { getRanking } from "@/lib/store";

export async function GET() {
  const ranking = await getRanking();
  return NextResponse.json(ranking ?? { ranking: [], themes: [], conflicts: [] });
}
