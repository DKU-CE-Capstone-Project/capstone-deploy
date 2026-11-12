import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import local_source
import usage_store


def test_ledger_deduplicates_retries_and_keeps_late_events_in_cumulative_cost(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        usage_store, "now", lambda: datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    )
    path = tmp_path / "ledger.sqlite3"
    ledger = usage_store.UsageStore(path)
    ledger.initialize()
    event = {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "event_id": "0f60ba03874d48a68dd2c928d7ba201d",
        "kind": "sdk_call",
        "timestamp": "2026-11-06T10:00:00Z",
        "provider": "gemini",
        "operation": "embed_batch",
        "model": "gemini-embedding-001",
        "input_tokens": 32,
        "prompt": "fixture body that must never be stored",
    }
    record = {"log": json.dumps(event), "source": "stdout"}
    ledger.ingest([record, record, {"log": "ordinary line", "source": "stdout"}])

    restarted = usage_store.UsageStore(path)
    restarted.initialize()
    restarted.ingest(
        [
            record,
            {
                "source": "stdout",
                "log": json.dumps(
                    event
                    | {
                        "event_id": "1234567890abcdef1234567890abcdef",
                        "timestamp": "2026-11-01T00:05:00+09:00",
                    }
                ),
            },
        ]
    )
    with restarted.connect() as db:
        assert all(
            "prompt" not in row[0]
            for row in db.execute("SELECT payload FROM usage_events")
        )
    usage = restarted.usage(24)
    assert usage["observed_events"] == 1
    assert usage["providers"][0]["last_event_at"] == "2026-11-06T10:00:00.000000+00:00"
    assert restarted.costs("day")["calls"] == 1
    assert restarted.costs("month")["calls"] == 2
    cumulative = restarted.costs("all")
    assert cumulative["calls"] == 2
    assert cumulative["estimated_usd"] == 0.0000096
    assert cumulative["measurement_start"] == "2026-10-31T15:05:00+00:00"


def test_report_job_lookup_migrates_existing_ledger_and_counts_each_call_once(
    tmp_path, monkeypatch
):
    received_at = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(usage_store, "now", lambda: received_at)
    monkeypatch.delenv("DIFFBOT_COST_PLAN", raising=False)
    job_id = "97a0bf73-21b5-4d89-85cb-1303a010d56a"
    other_job = "2f34a75b-977e-4fc7-afc6-2b7b73b4bb55"
    event = {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "event_id": "a" * 32,
        "timestamp": "2026-11-06T10:00:00Z",
        "kind": "sdk_call",
        "job_id": job_id,
        "stage": "analysis",
        "provider": "gemini",
        "operation": "generate",
        "model": "gemini-3.5-flash-lite",
        "service_tier": "standard",
        "input_tokens": 1000,
        "output_tokens": 200,
        "thought_tokens": 50,
    }

    def record(**changes):
        return {"source": "stdout", "log": json.dumps(event | changes)}

    ledger = usage_store.UsageStore(tmp_path / "ledger.sqlite3")
    with ledger.connect() as db, db:
        db.execute("""CREATE TABLE usage_events (
            event_id TEXT PRIMARY KEY, event_time TEXT NOT NULL,
            payload TEXT NOT NULL, cost_key TEXT
        )""")
        db.execute(
            "INSERT INTO usage_events VALUES (?, ?, ?, ?)",
            usage_store.event_row(record(), received_at)[:4],
        )
    ledger.initialize()
    assert ledger.costs("all")["calls"] == 1
    assert ledger.job_usage(job_id)["state"] == "no_data"

    ledger.ingest(
        [
            record(),
            record(job_id=other_job),  # An existing association survives replay.
            record(event_id="b" * 32, job_id=other_job),
            record(event_id="c" * 32, stage="critic", output_tokens=None),
            record(event_id="d" * 32, kind="cache_hit"),
            record(event_id="e" * 32, job_id="invalid"),
        ]
    )
    assert ledger.costs("all")["calls"] == 4
    assert ledger.job_usage(other_job)["calls"] == 1
    assert ledger.job_usage("invalid")["state"] == "no_data"

    monkeypatch.setattr(usage_store, "store", ledger)
    with TestClient(usage_store.app) as client:
        response = client.get("/report-job-usage", params={"job_id": job_id.upper()})
        assert response.status_code == 200
        assert client.get("/report-job-usage?job_id=invalid").status_code == 422
    result = response.json()
    assert result["job_id"] == job_id
    assert result["estimated_usd"] == 0.000925
    assert result["priced_calls"] == result["unpriced_calls"] == 1
    analysis, critic = result["rows"]
    assert analysis["stage"] == "analysis"
    assert analysis["sdk_calls"] == analysis["cache_hits"] == 1
    assert analysis["token_sums"]["input_tokens"] == 1000
    assert analysis["token_sums"]["output_tokens"] == 200
    assert critic["stage"] == "critic"
    assert critic["estimated_usd"] is None
    assert critic["token_observations"]["output_tokens"] == 0


def test_agent_diagnostics_are_kept_without_changing_calls_or_costs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        usage_store, "now", lambda: datetime(2026, 11, 12, 3, tzinfo=timezone.utc)
    )
    job_id = "97a0bf73-21b5-4d89-85cb-1303a010d56a"
    base = {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "timestamp": "2026-11-12T02:00:00Z",
        "job_id": job_id,
        "stage": "bear",
    }
    call = base | {
        "event_id": "a" * 32,
        "kind": "sdk_call",
        "provider": "gemini",
        "operation": "generate",
        "model": "gemini-3.5-flash-lite",
        "service_tier": "standard",
        "input_tokens": 1000,
        "output_tokens": 200,
        "thought_tokens": 50,
    }
    diagnostic = base | {"provider": "application", "operation": "debate_analyst"}
    rejected = diagnostic | {
        "event_id": "b" * 32,
        "kind": "agent_output_validation",
        "outcome": "error",
        "error_type": "invalid_argument_schema",
        "validation_issues": [
            {
                "path": ["arguments", 0, "text"],
                "type": "string_too_long",
                "input": "private response",
            },
            {"path": ["private response"], "type": "missing"},
        ],
        "input_tokens": 7,
        "response": "private response",
    }
    trimmed = diagnostic | {
        "event_id": "c" * 32,
        "kind": "agent_output_normalized",
        "outcome": "success",
        "discarded_arguments": 2,
    }
    ledger = usage_store.UsageStore(tmp_path / "ledger.sqlite3")
    ledger.initialize()
    ledger.ingest(
        [
            {"source": "stdout", "log": json.dumps(event)}
            for event in (
                call,
                rejected,
                rejected,
                trimmed,
                trimmed | {"event_id": "d" * 32, "kind": "agent_output_unknown"},
            )
        ]
    )

    with ledger.connect() as db:
        stored = {
            row[0]: (json.loads(row[1]), row[2], row[3])
            for row in db.execute(
                "SELECT event_id, payload, cost_key, job_id FROM usage_events"
            )
        }
    assert stored.keys() == {"a" * 32, "b" * 32, "c" * 32}
    payload, cost_key, stored_job = stored["b" * 32]
    assert cost_key is None and stored_job == job_id
    assert payload == {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "event_id": "b" * 32,
        "kind": "agent_output_validation",
        "provider": "application",
        "operation": "debate_analyst",
        "stage": "bear",
        "outcome": "error",
        "error_type": "invalid_argument_schema",
        "validation_issues": [
            {"path": ["arguments", 0, "text"], "type": "string_too_long"}
        ],
    }
    assert stored["c" * 32][0]["discarded_arguments"] == 2
    assert "error_type" not in stored["a" * 32][0]

    usage = ledger.usage(24)
    assert usage["observed_events"] == 3
    assert [row["sdk_calls"] for row in usage["providers"]] == [1]
    rejected_row, trimmed_row = usage["agent_diagnostics"]
    assert rejected_row["events"] == 1
    assert rejected_row["issues"] == [
        {"path": "arguments.*.text", "type": "string_too_long", "count": 1}
    ]
    assert rejected_row["last_event_at"] == "2026-11-12T02:00:00.000000+00:00"
    assert trimmed_row["kind"] == "agent_output_normalized"
    assert trimmed_row["discarded_arguments"] == 2

    assert ledger.costs("all")["calls"] == 1
    assert len(ledger.daily_usage("external", None, None)["rows"]) == 1
    job = ledger.job_usage(job_id)
    assert job["calls"] == job["priced_calls"] == 1
    assert job["estimated_usd"] == 0.000925
    assert [row["kind"] for row in job["diagnostics"]] == [
        "agent_output_validation",
        "agent_output_normalized",
    ]

    # A response can be rejected without any provider call, for example when
    # no provider is configured. "state" still describes provider calls only.
    other_job = "2f34a75b-977e-4fc7-afc6-2b7b73b4bb55"
    alone = rejected | {"event_id": "e" * 32, "job_id": other_job}
    ledger.ingest([{"source": "stdout", "log": json.dumps(alone)}])
    job = ledger.job_usage(other_job)
    assert job["state"] == "no_data" and job["calls"] == 0 and job["rows"] == []
    assert [row["events"] for row in job["diagnostics"]] == [1]
    assert ledger.job_usage(job_id)["diagnostics"][0]["events"] == 1


def test_daily_requests_use_korean_midnight_and_paginate_matching_events(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        usage_store, "now", lambda: datetime(2026, 11, 8, 0, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(usage_store, "DAILY_PAGE_SIZE", 2)
    ledger = usage_store.UsageStore(tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(usage_store, "store", ledger)

    def record(index, timestamp, kind="http_attempt"):
        return {
            "source": "stdout",
            "log": json.dumps(
                {
                    "event_type": "econmind_usage",
                    "schema_version": 1,
                    "event_id": f"{index:032x}",
                    "timestamp": timestamp,
                    "kind": kind,
                    "provider": "naver",
                    "operation": f"operation_{index}",
                    "route": "/api/v2/maps",
                    "method": "POST",
                    "outcome": "success",
                    "prompt": "private",
                }
            ),
        }

    midnight = "2026-11-06T15:00:00Z"
    boundary = record(1, midnight)
    with TestClient(usage_store.app) as client:
        ledger.ingest(
            [
                boundary,
                boundary,
                record(2, midnight, "sdk_call"),
                record(3, midnight),
                record(4, "2026-11-06T14:59:59Z"),
                record(5, "2026-11-07T15:00:00Z", "cache_hit"),
                record(6, "2026-11-07T15:00:00Z", "api_request"),
            ]
        )
        path = "/usage/daily"
        external = {"kind": "external"}
        first = client.get(path, params=external).json()
        assert first["date"] == "2026-11-07"
        assert [row["operation"] for row in first["rows"]] == [
            "operation_3",
            "operation_2",
        ]
        assert first["rows"][1]["kind"] == "sdk_call"
        assert not {"prompt", "route", "event_id"} & first["rows"][0].keys()

        ledger.ingest([record(7, "2026-11-07T14:59:59Z")])
        next_page = client.get(
            path,
            params=external | {"day": first["date"], "cursor": first["next_cursor"]},
        ).json()
        assert [row["operation"] for row in next_page["rows"]] == ["operation_1"]
        assert next_page["next_cursor"] is None

        previous = client.get(path, params=external | {"day": "2026-11-06"}).json()
        assert [row["operation"] for row in previous["rows"]] == ["operation_4"]
        service = client.get(path, params={"kind": "service"}).json()
        assert service["date"] == "2026-11-08"
        assert service["rows"] == [
            {
                "timestamp": "2026-11-07T15:00:00.000000+00:00",
                "route": "/api/v2/maps",
                "method": "POST",
                "outcome": "success",
            }
        ]
        assert (
            client.get(path, params=external | {"day": "2026-11-08"}).json()["rows"]
            == []
        )
        assert (
            client.get(
                path, params=external | {"cursor": first["next_cursor"]}
            ).status_code
            == 422
        )


def test_vm_usage_queries_require_private_token_and_forward_filters(monkeypatch):
    import vm_source

    token = "a" * 32
    job_id = "97a0bf73-21b5-4d89-85cb-1303a010d56a"
    monkeypatch.setenv("VM_COLLECTOR_URL", "http://10.0.0.2:18090")
    monkeypatch.setenv("VM_COLLECTOR_TOKEN", token)
    monkeypatch.setattr(app, "source", vm_source.VmSource())
    monkeypatch.setattr(app, "read_token", token)

    def read(session, url, params):
        assert session.headers["Authorization"] == "Bearer " + token
        if url.endswith("/api/usage/daily"):
            assert params == {"kind": "service", "day": "2026-11-07", "cursor": None}
            return {
                "state": "ok",
                "date": params["day"],
                "rows": [],
                "next_cursor": None,
            }
        assert url == "http://10.0.0.2:18090/api/report-job-usage"
        assert params == {"job_id": job_id}
        return {"state": "no_data", "job_id": job_id}

    monkeypatch.setattr(vm_source, "read", read)
    client = TestClient(app.app)
    path = "/api/report-job-usage?job_id=" + job_id.upper()
    assert client.get(path).status_code == 403
    response = client.get(path, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.json() == {"state": "no_data", "job_id": job_id}
    path = "/api/usage/daily?kind=service&day=2026-11-07"
    assert client.get(path).status_code == 403
    response = client.get(path, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.json()["date"] == "2026-11-07"


def test_local_overview_returns_usage_when_host_collector_is_down(monkeypatch):
    monkeypatch.delenv("LOCAL_BACKEND_STATUS_URL", raising=False)

    def read(_session, url, params=None):
        if "host-metrics" in url:
            raise requests.ConnectionError
        assert url == "http://usage-store:8080/usage" and params == {"hours": 24}
        return {"state": "ok", "observed_events": 1}

    monkeypatch.setattr(local_source, "read", read)
    result = local_source.LocalSource().snapshot(24)
    assert result["mode"] == "local"
    assert result["metrics"]["cpu"]["state"] == "unavailable"
    assert result["usage"]["observed_events"] == 1
    assert result["connection"] == {"state": "not_configured"}


def test_vm_collector_failure_preserves_mongodb_status(monkeypatch):
    from vm_source import VmSource

    monkeypatch.setenv("VM_COLLECTOR_URL", "http://10.0.0.2:18090")
    monkeypatch.setenv("VM_COLLECTOR_TOKEN", "a" * 64)
    source = VmSource()
    monkeypatch.setattr(source, "connection", lambda: {"state": "connected"})

    def unavailable(_path, _params):
        raise requests.ConnectionError

    monkeypatch.setattr(source, "collector_data", unavailable)
    result = source.snapshot(24)
    assert result["mode"] == "vm"
    assert result["metrics"]["cpu"]["state"] == "unavailable"
    assert result["usage"]["state"] == "unavailable"
    assert result["connection"] == {"state": "connected"}
