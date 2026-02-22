import types

from rlm_repo_intel import dashboard_push


class _FakeResponse:
    headers = {"content-type": "application/json"}

    @staticmethod
    def raise_for_status():
        return None

    @staticmethod
    def json():
        return {"ok": True}


def test_post_attaches_bearer_secret_header(monkeypatch):
    captured = {}

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setenv("PUSH_SECRET", "secret-123")
    monkeypatch.setattr(dashboard_push, "requests", types.SimpleNamespace(post=_fake_post))

    dashboard_push._post("summary", {"a": 1}, run_id="run-1")

    assert captured["headers"]["Authorization"] == "Bearer secret-123"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["run_id"] == "run-1"

