#!/usr/bin/env python3
"""Fetch PR diffs from GitHub API for all open PRs in JSONL data."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


INPUT_PATH = Path(".rlm-repo-intel/prs/all_prs.jsonl")
OUTPUT_PATH = Path(".rlm-repo-intel/prs/all_prs_with_diffs.jsonl")
REPO = "openclaw/openclaw"
API_BASE = f"https://api.github.com/repos/{REPO}/pulls"
REQUEST_DELAY_SECONDS = 0.2
PROGRESS_EVERY = 100


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github.v3.diff",
        "Authorization": f"Bearer {token}",
        "User-Agent": "rlm-repo-intel/fetch-diffs",
    }


def _sleep_for_rate_limit(response: httpx.Response) -> None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset_epoch = response.headers.get("X-RateLimit-Reset")
    if remaining is None or reset_epoch is None:
        return

    try:
        remaining_int = int(remaining)
        reset_time = int(reset_epoch)
    except ValueError:
        return

    if remaining_int > 1:
        return

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    sleep_seconds = max(reset_time - now_epoch + 1, 1)
    print(f"[rate-limit] Remaining={remaining_int}. Sleeping {sleep_seconds}s until reset.")
    time.sleep(sleep_seconds)


def _fetch_diff(client: httpx.Client, pr_number: int) -> str:
    url = f"{API_BASE}/{pr_number}"
    response = client.get(url, timeout=30.0)
    _sleep_for_rate_limit(response)

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

    return response.text


def main() -> int:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set. Export it before running this script.")
        return 1

    if not INPUT_PATH.exists():
        print(f"Input file not found: {INPUT_PATH}")
        return 1

    total = 0
    open_count = 0
    fetched = 0
    failed = 0

    with httpx.Client(headers=_github_headers(token), follow_redirects=True) as client:
        with INPUT_PATH.open() as in_file, OUTPUT_PATH.open("w") as out_file:
            for line_number, line in enumerate(in_file, start=1):
                line = line.strip()
                if not line:
                    continue

                total += 1
                try:
                    pr = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"[warn] malformed JSON at line {line_number}: {exc}")
                    continue

                if pr.get("state") == "open":
                    open_count += 1
                    pr_number = pr.get("number")
                    diff_text = ""

                    if isinstance(pr_number, int):
                        try:
                            diff_text = _fetch_diff(client, pr_number)
                            fetched += 1
                        except Exception as exc:
                            failed += 1
                            print(f"[error] failed to fetch diff for PR #{pr_number}: {exc}")
                    else:
                        failed += 1
                        print(f"[error] invalid PR number at line {line_number}: {pr_number}")

                    pr["diff"] = diff_text

                    if open_count % PROGRESS_EVERY == 0:
                        print(
                            f"[progress] processed_open={open_count} fetched={fetched} failed={failed}"
                        )

                    time.sleep(REQUEST_DELAY_SECONDS)

                out_file.write(json.dumps(pr, ensure_ascii=False) + "\n")

    shutil.copy2(OUTPUT_PATH, INPUT_PATH)
    print(
        "Done."
        f" total={total} open={open_count} fetched={fetched} failed={failed}"
        f" output={OUTPUT_PATH} replaced={INPUT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
