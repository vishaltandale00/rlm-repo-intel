import { NextRequest, NextResponse } from "next/server";
import {
  setSummary,
  appendEvaluation,
  setClusters,
  setRanking,
} from "@/lib/store";

/**
 * Push endpoint — the local Python pipeline POSTs results here.
 * Accepts: { type: "summary"|"evaluation"|"clusters"|"ranking", data: ... }
 * Protected by PUSH_SECRET env var.
 */
export async function POST(req: NextRequest) {
  const secret = process.env.PUSH_SECRET;
  if (secret) {
    const auth = req.headers.get("authorization");
    if (auth !== `Bearer ${secret}`) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const body = await req.json();
  const { type, data } = body;

  switch (type) {
    case "summary":
      await setSummary(data);
      return NextResponse.json({ ok: true, type: "summary" });

    case "evaluation":
      await appendEvaluation(data);
      return NextResponse.json({ ok: true, type: "evaluation", pr: data.pr_number });

    case "evaluations_batch":
      for (const ev of data) {
        await appendEvaluation(ev);
      }
      return NextResponse.json({ ok: true, type: "evaluations_batch", count: data.length });

    case "clusters":
      await setClusters(data);
      return NextResponse.json({ ok: true, type: "clusters" });

    case "ranking":
      await setRanking(data);
      return NextResponse.json({ ok: true, type: "ranking" });

    default:
      return NextResponse.json({ error: `unknown type: ${type}` }, { status: 400 });
  }
}
