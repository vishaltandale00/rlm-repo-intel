import { NextResponse } from "next/server";
import { getClusters } from "@/lib/store";

export async function GET() {
  const clusters = await getClusters();
  return NextResponse.json(clusters);
}
