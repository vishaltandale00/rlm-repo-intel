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


def _post(payload_type: str, data: Any, run_id: str | None = None) -> Any:
    payload: dict[str, Any] = {"type": payload_type, "data": data}
    if run_id:
        payload["run_id"] = run_id

    try:
        if requests is not None:
            response = requests.post(DASHBOARD_API_URL, json=payload, timeout=15)
            response.raise_for_status()
            if response.headers.get("content-type", "").startswith("application/json"):
                return response.json()
            return None

        body = json_lib.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            DASHBOARD_API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=15) as resp:
            response_body = resp.read().decode("utf-8").strip()
            if not response_body:
                return None
            return json_lib.loads(response_body)
    except Exception as exc:
        raise DashboardPushError(f"Failed to push {payload_type}") from exc


def start_new_run(run_meta: dict[str, Any], run_id: str | None = None) -> str:
    response = _post("new_run", run_meta, run_id=run_id)
    if not isinstance(response, dict) or not response.get("run_id"):
        raise DashboardPushError("Failed to initialize new run")
    return str(response["run_id"])


def push_run_meta(data: dict[str, Any], run_id: str) -> None:
    _post("run_meta", data, run_id=run_id)


def push_run_event(data: dict[str, Any], run_id: str) -> None:
    _post("run_event", data, run_id=run_id)


def push_summary(data: dict[str, Any], run_id: str | None = None) -> None:
    _post("summary", data, run_id=run_id)


def push_evaluation(data: dict[str, Any], run_id: str | None = None) -> None:
    _post("evaluation", data, run_id=run_id)


def push_clusters(data: Any, run_id: str | None = None) -> None:
    _post("clusters", data, run_id=run_id)


def push_ranking(data: dict[str, Any], run_id: str | None = None) -> None:
    _post("ranking", data, run_id=run_id)


def push_trace(trace_steps: list[dict[str, Any]], run_id: str | None = None) -> None:
    _post("trace", trace_steps, run_id=run_id)
