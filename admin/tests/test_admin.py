import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import sources


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in (
        "GCP_PROJECT_ID",
        "GCP_VM_INSTANCE_ID",
        "BACKEND_STATUS_URL",
        "ADMIN_STATUS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    def reject(*args, **kwargs):
        raise AssertionError("Unexpected external request")

    monkeypatch.setattr(requests.sessions.Session, "request", reject)
    app.cache.clear()


def entry(event_id, kind="sdk_call", **fields):
    return {
        "jsonPayload": dict(
            event_type="econmind_usage",
            schema_version=1,
            event_id=event_id,
            kind=kind,
            provider="gemini",
            model="fixture-model",
            operation="generate",
            **fields,
        )
    }


def test_duplicate_entries_missing_usage_cache_and_reexecution():
    event = entry("1", input_tokens=100, cached_input_tokens=80, output_tokens=20)
    result = sources.summarize(
        [
            event,
            event,
            entry("2"),
            entry("3", kind="cache_hit"),
            entry("4", input_tokens=10, run_id="retry"),
        ]
    )
    row = result["providers"][0]
    assert result["observed_events"] == 4
    assert row["sdk_calls"] == 3 and row["cache_hits"] == 1
    assert row["token_sums"]["input_tokens"] == 110
    assert row["token_observations"]["input_tokens"] == 2
    assert row["token_sums"]["cached_input_tokens"] == 80
    assert not result["routes"]


def test_explicit_http_attempts_remain_separate_from_sdk_and_routes():
    result = sources.summarize(
        [
            entry("1", "http_attempt", outcome="error"),
            entry("2"),
            entry(
                "3",
                "api_request",
                route="/api/v2/maps/{map_id}",
                method="GET",
                outcome="success",
            ),
        ]
    )
    row = result["providers"][0]
    assert row["http_attempts"] == 1 and row["sdk_calls"] == 1 and row["errors"] == 1
    assert result["routes"][0]["requests"] == 1


def test_usage_pagination_and_cap_are_explicit(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "fixture-project")
    monkeypatch.setenv("GCP_VM_INSTANCE_ID", "123")
    session = Mock()
    session.post.return_value.json.return_value = {
        "entries": [entry("same")],
        "nextPageToken": "next",
    }
    result = sources.CloudSource().usage(session, 24)
    assert session.post.call_count == 10
    assert not result["complete"] and result["observed_events"] == 1
    assert session.post.call_args.kwargs["json"]["pageToken"] == "next"
    query = session.post.call_args.kwargs["json"]["filter"]
    assert 'instance_id="123"' in query and 'resource.type="gce_instance"' in query


def test_usage_empty_intermediate_page_is_not_end(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "fixture-project")
    monkeypatch.setenv("GCP_VM_INSTANCE_ID", "123")
    session = Mock()
    session.post.return_value.json.side_effect = [
        {"nextPageToken": "next"},
        {"entries": [entry("1")]},
    ]
    result = sources.CloudSource().usage(session, 1)
    assert result["complete"] and result["observed_events"] == 1


def test_status_endpoint_failure_is_unknown_not_db_down(monkeypatch):
    monkeypatch.setenv(
        "BACKEND_STATUS_URL", "https://fixture.invalid/internal/admin/status"
    )
    monkeypatch.setenv("ADMIN_STATUS_TOKEN", "x" * 32)
    get = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(sources.requests, "get", get)
    assert sources.CloudSource().connection()["state"] == "unknown"
    assert get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "state", ["connected", "unreachable", "disabled", "unexpected"]
)
def test_status_only_passes_allowlisted_connection_state(monkeypatch, state):
    monkeypatch.setenv(
        "BACKEND_STATUS_URL", "https://fixture.invalid/internal/admin/status"
    )
    monkeypatch.setenv("ADMIN_STATUS_TOKEN", "x" * 32)
    response = Mock()
    response.json.return_value = {"mongodb": state, "secret": "must not leak"}
    monkeypatch.setattr(sources.requests, "get", Mock(return_value=response))
    result = sources.CloudSource().connection()
    assert result["state"] == (state if state != "unexpected" else "unknown")
    assert "secret" not in json.dumps(result)


def test_metrics_partial_failure_keeps_other_series():
    good = Mock()
    good.json.return_value = {
        "timeSeries": [
            {
                "points": [
                    {
                        "interval": {"endTime": "2026-11-02T01:00:00Z"},
                        "value": {"doubleValue": 0.25},
                    }
                ]
            }
        ]
    }
    session = Mock()
    session.get.side_effect = [
        good,
        requests.Timeout(),
        Mock(json=Mock(return_value={})),
    ]
    result = sources.CloudSource().metrics(session)
    assert result["cpu"]["series"][0]["points"][0]["value"] == 25
    assert result["memory"]["state"] == "unavailable"
    assert result["disk"]["state"] == "no_data"


def test_local_unconfigured_app_readonly_headers_and_bounds(monkeypatch):
    monkeypatch.setattr(app, "source", sources.CloudSource())
    client = TestClient(app.app)
    response = client.get("/api/overview")
    assert response.json()["usage"]["state"] == "not_configured"
    assert response.json()["connection"]["state"] == "not_configured"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/api/overview?hours=169").status_code == 422
    assert client.post("/api/overview").status_code == 405
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_snapshot_cache_and_error_sanitization(monkeypatch):
    source = Mock()
    source.snapshot.return_value = {"ok": True}
    monkeypatch.setattr(app, "source", source)
    client = TestClient(app.app)
    assert client.get("/api/overview").json() == {"ok": True}
    client.get("/api/overview")
    assert source.snapshot.call_count == 1
    source.snapshot.side_effect = RuntimeError("secret connection URI")
    response = client.get("/api/overview?hours=1")
    assert response.status_code == 503 and "secret" not in response.text


def test_malformed_status_body_is_unknown_and_does_not_break_other_sources(monkeypatch):
    monkeypatch.setenv(
        "BACKEND_STATUS_URL", "https://fixture.invalid/internal/admin/status"
    )
    monkeypatch.setenv("ADMIN_STATUS_TOKEN", "x" * 32)
    response = Mock()
    response.json.return_value = []
    monkeypatch.setattr(sources.requests, "get", Mock(return_value=response))
    assert sources.CloudSource().connection()["state"] == "unknown"
