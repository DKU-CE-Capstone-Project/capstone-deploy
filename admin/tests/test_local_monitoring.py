import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
    assert restarted.usage(24)["observed_events"] == 1
    assert restarted.costs("day")["calls"] == 1
    assert restarted.costs("month")["calls"] == 2
    cumulative = restarted.costs("all")
    assert cumulative["calls"] == 2
    assert cumulative["estimated_usd"] == 0.0000096
    assert cumulative["measurement_start"] == "2026-10-31T15:05:00+00:00"


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
