import { NextResponse } from "next/server";
import { getEvaluations } from "@/lib/store";

export async function GET() {
  const evals = await getEvaluations();
  return NextResponse.json(evals);
}
