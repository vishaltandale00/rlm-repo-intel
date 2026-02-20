import { NextRequest, NextResponse } from "next/server";
import { cleanupRuns } from "@/lib/store";

export async function POST(req: NextRequest) {
  const secret = process.env.PUSH_SECRET;
  if (secret) {
    const auth = req.headers.get("authorization");
    if (auth !== `Bearer ${secret}`) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const result = await cleanupRuns();
  return NextResponse.json({ ok: true, ...result });
}
