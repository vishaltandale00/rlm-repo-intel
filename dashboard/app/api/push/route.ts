import { NextRequest, NextResponse } from "next/server";

/**
 * Push endpoint — kept as a no-op for API compatibility.
 * Data is now delivered via git commits to public/data/.
 */
export async function POST(req: NextRequest) {
  return NextResponse.json({
    ok: false,
    message: "Push API disabled. Data is served from static files in public/data/. Commit and push to update.",
  }, { status: 410 });
}
