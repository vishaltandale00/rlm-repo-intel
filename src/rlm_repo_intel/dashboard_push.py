"""Push analysis artifacts to the dashboard HTTP API."""

from __future__ import annotations

import os
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover - fallback for environments without requests.
    requests = None
    from urllib import request as urllib_request
    import json as json_lib


class DashboardPushError(RuntimeError):
    """Raised when dashboard push operations fail."""


DASHBOARD_API_URL = os.getenv(
    "DASHBOARD_API_URL",
    "https://dashboard-tau-ten-24.vercel.app/api/push",
)


def _post(payload_type: str, data: Any) -> None:
    payload = {"type": payload_type, "data": data}

    try:
        if requests is not None:
            response = requests.post(DASHBOARD_API_URL, json=payload, timeout=15)
            response.raise_for_status()
            return

        body = json_lib.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            DASHBOARD_API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=15):
            return
    except Exception as exc:
        raise DashboardPushError(f"Failed to push {payload_type}") from exc


def push_summary(data: dict[str, Any]) -> None:
    _post("summary", data)


def push_evaluation(data: dict[str, Any]) -> None:
    _post("evaluation", data)


def push_clusters(data: Any) -> None:
    _post("clusters", data)


def push_ranking(data: dict[str, Any]) -> None:
    _post("ranking", data)


def push_trace(trace_steps: list[dict[str, Any]]) -> None:
    _post("trace", trace_steps)
