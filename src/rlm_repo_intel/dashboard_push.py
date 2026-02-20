"""Write analysis artifacts directly into Neon Postgres-backed dashboard storage."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import Json


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS rlm_kv (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


class DashboardPushError(RuntimeError):
    """Raised when dashboard DB operations cannot proceed."""


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise DashboardPushError("DATABASE_URL is not set")
    return url


def _connect():
    return psycopg2.connect(_database_url())


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(TABLE_DDL)
    conn.commit()


def _write_kv(key: str, value: Any) -> None:
    with _connect() as conn:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rlm_kv (key, value, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key)
                DO UPDATE SET value = %s::jsonb, updated_at = NOW()
                """,
                (key, Json(value), Json(value)),
            )
        conn.commit()


def _read_kv(key: str, fallback: Any) -> Any:
    with _connect() as conn:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM rlm_kv WHERE key = %s", (key,))
            row = cur.fetchone()
        return row[0] if row else fallback


def push_summary(data: dict[str, Any]) -> None:
    payload = dict(data)
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    _write_kv("rlm:summary", payload)


def push_evaluation(data: dict[str, Any]) -> None:
    existing = _read_kv("rlm:evaluations", [])
    if not isinstance(existing, list):
        existing = []

    pr_number = data.get("pr_number")
    idx = -1
    if pr_number is not None:
        for i, ev in enumerate(existing):
            if isinstance(ev, dict) and ev.get("pr_number") == pr_number:
                idx = i
                break

    if idx >= 0:
        existing[idx] = data
    else:
        existing.append(data)

    _write_kv("rlm:evaluations", existing)


def push_clusters(data: Any) -> None:
    _write_kv("rlm:clusters", data)


def push_ranking(data: dict[str, Any]) -> None:
    _write_kv("rlm:ranking", data)


def push_trace(trace_steps: list[dict[str, Any]]) -> None:
    normalized: list[dict[str, Any]] = []
    for step in trace_steps:
        if not isinstance(step, dict):
            continue

        step_type = str(step.get("type", "llm_response"))
        if step_type not in {"llm_response", "code_execution"}:
            step_type = "llm_response"

        iteration = step.get("iteration", 1)
        try:
            iteration = int(iteration)
        except (TypeError, ValueError):
            iteration = 1

        timestamp = step.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp.strip():
            timestamp = datetime.now(timezone.utc).isoformat()

        content = step.get("content", "")
        normalized.append(
            {
                "iteration": iteration,
                "type": step_type,
                "content": str(content),
                "timestamp": timestamp,
            }
        )

    _write_kv("rlm:agent_trace", normalized)
