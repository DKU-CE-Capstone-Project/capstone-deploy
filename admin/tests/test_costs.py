import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import billing
import pricing
import sources


def event(**fields):
    return {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "event_id": "sample",
        "kind": "sdk_call",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "operation": "generate",
        "service_tier": "standard",
        "input_tokens": 1000,
        "output_tokens": 40,
        "thought_tokens": 60,
        "total_tokens": 1100,
        **fields,
    }


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"cached_input_tokens": 100}, 0.000523),
        ({"cached_input_tokens": 100, "thought_tokens": None}, 0.000523),
        ({"cached_input_tokens": 100, "service_tier": "flex"}, 0.000262),
        (
            {
                "model": "gemini-embedding-001",
                "operation": "embed_batch",
                "input_tokens": 1000000,
            },
            0.15,
        ),
        (
            {
                "provider": "openai",
                "model": "gpt-6-luna",
                "operation": "decision",
                "kind": "http_attempt",
                "output_tokens": 10000,
            },
            0.0001,
        ),
        (
            {
                "provider": "openai",
                "model": "gpt-6-luna",
                "operation": "decision",
                "kind": "http_attempt",
                "input_tokens": 300000,
            },
            0.06,
        ),
        (
            {
                "provider": "openai",
                "model": "gpt-6-luna",
                "operation": "decision",
                "kind": "http_attempt",
                "cached_input_tokens": 100,
                "cache_creation_tokens": 100,
            },
            0.0000935,
        ),
        (
            {
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "cached_input_tokens": 100,
                "cache_creation_tokens": 200,
                "output_tokens": 100,
            },
            0.0088,
        ),
    ],
)
def test_provider_billing_semantics_and_processing_tiers(fields, expected):
    amount, _ = pricing.event_cost(event(**fields), "unknown")
    assert float(amount) == pytest.approx(expected)


@pytest.mark.parametrize(
    "fields",
    [
        {"input_tokens": None},
        {"output_tokens": None},
        {"model": "unregistered-model"},
        {"service_tier": "unknown"},
        {"cached_input_tokens": 1001},
        {"thought_tokens": None, "total_tokens": None},
        {"total_tokens": 1},
        {"input_tokens": True},
        {"cached_input_tokens": -1},
    ],
)
def test_missing_or_invalid_tokens_and_rates_remain_unpriced(fields):
    assert pricing.event_cost(event(**fields), "unknown")[0] is None


def test_user_confirmed_free_plans_are_distinct_from_unknown_plans():
    assert pricing.event_cost(event(provider="diffbot"), "unknown")[0] is None
    assert pricing.event_cost(event(provider="diffbot"), "free_academic")[0] == 0
    assert (
        pricing.event_cost(event(provider="naver", operation="news_search"), "unknown")[
            0
        ]
        == 0
    )


def test_cost_estimates_deduplicate_and_do_not_count_cache_or_service_requests():
    good = {"jsonPayload": event()}
    entries = [
        good,
        good,
        {"jsonPayload": event(event_id="missing", input_tokens=None)},
        {"jsonPayload": event(event_id="cache", kind="cache_hit")},
        {"jsonPayload": event(event_id="request", kind="api_request")},
    ]
    result = pricing.estimate_external(entries)
    assert result["estimated_usd"] == pytest.approx(0.00055)
    assert (
        result["calls"] == 2
        and result["priced_calls"] == 1
        and result["unpriced_calls"] == 1
    )
    assert result["providers"][0]["cache_hits"] == 1
    assert not result["complete"]
    assert pricing.estimate_external([good], complete=False)["logs_complete"] is False
    unknown = pricing.estimate_external([{"jsonPayload": event(model="unknown")}])
    assert unknown["estimated_usd"] is None
    assert pricing.estimate_external([])["state"] == "no_data"


def billing_result(rows=None, **extra):
    names = [
        "service",
        "currency",
        "gross_cost",
        "credits",
        "data_through",
        "last_export",
        "records",
    ]
    rows = (
        rows
        if rows is not None
        else [
            [
                "Compute Engine",
                "KRW",
                "100.25",
                "-100.25",
                "2026-11-02T00:00:00Z",
                "2026-11-02T01:00:00Z",
                "2",
            ]
        ]
    )
    return {
        "jobComplete": True,
        "schema": {"fields": [{"name": name} for name in names]},
        "rows": [{"f": [{"v": value} for value in row]} for row in rows],
        **extra,
    }


def query(session):
    return billing.query_billing(
        session,
        project="fixture-project",
        table="fixture-project.billing.export",
        location="US",
        timestamp=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )


def test_billing_query_is_project_filtered_and_counts_credits_without_multiplying_costs():
    session = Mock()
    session.post.return_value.json.return_value = billing_result()
    result = query(session)
    assert result["state"] == "ok" and result["net_cost"] == 0
    assert result["gross_cost"] == 100.25 and result["credits"] == -100.25
    body = session.post.call_args.kwargs["json"]
    assert "WHERE project.id = @project" in body["query"]
    assert (
        "FROM UNNEST(credits) c" in body["query"] and "CROSS JOIN" not in body["query"]
    )
    assert body["maximumBytesBilled"] == str(128 * 2**20)
    assert body["useLegacySql"] is False
    params = {p["name"]: p["parameterValue"]["value"] for p in body["queryParameters"]}
    assert params["project"] == "fixture-project"
    assert params["month_start"] == "2026-11-01T00:00:00+09:00"


@pytest.mark.parametrize(
    ("status", "state"),
    [(404, "export_pending"), (403, "permission_required"), (500, "unavailable")],
)
def test_billing_failures_do_not_turn_into_zero_cost(status, state):
    session = Mock()
    response = requests.Response()
    response.status_code = status
    session.post.return_value.raise_for_status.side_effect = requests.HTTPError(
        response=response
    )
    result = query(session)
    assert result["state"] == state and "net_cost" not in result


def test_pending_billing_job_is_polled_and_empty_or_partial_results_are_not_zero():
    session = Mock()
    session.post.return_value.json.return_value = {
        "jobComplete": False,
        "jobReference": {"jobId": "job"},
    }
    session.get.return_value.json.return_value = billing_result()
    assert query(session)["state"] == "ok" and session.get.call_count == 1
    session.post.return_value.json.return_value = billing_result(rows=[])
    assert query(session)["state"] == "no_data"
    session.post.return_value.json.return_value = billing_result(pageToken="more")
    assert query(session)["state"] == "unavailable"


def test_different_currencies_are_not_added_together():
    session = Mock()
    row = billing_result()["rows"][0]
    other = {"f": [dict(cell) for cell in row["f"]]}
    other["f"][1]["v"] = "USD"
    session.post.return_value.json.return_value = billing_result() | {
        "rows": [row, other]
    }
    result = query(session)
    assert result["state"] == "mixed_currency" and "net_cost" not in result


def test_initial_report_snapshot_is_dated_project_scoped_and_never_overrides_export():
    timestamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
    snapshot = {
        "project": "fixture-project",
        "month": "2026-11",
        "gross_cost": 100,
        "credits": -100,
        "net_cost": 0,
        "observed_at": "2026-11-02T01:00:00Z",
    }
    pending = {
        "state": "export_pending",
        "report_url": "https://console.cloud.google.com/billing",
    }
    result = billing.with_snapshot(
        pending, snapshot, project="fixture-project", timestamp=timestamp
    )
    assert result["state"] == "snapshot" and result["source"] == "console_snapshot"
    assert result["automatic_state"] == "export_pending" and result["net_cost"] == 0
    assert billing.with_snapshot(
        {"state": "ok"}, snapshot, project="fixture-project", timestamp=timestamp
    ) == {"state": "ok"}
    for bad in [
        snapshot | {"month": "2026-10"},
        snapshot | {"project": "other-project"},
        snapshot | {"net_cost": 5},
    ]:
        assert (
            billing.with_snapshot(
                pending, bad, project="fixture-project", timestamp=timestamp
            )
            == pending
        )


def test_billing_table_configuration_rejects_sql_fragments(monkeypatch):
    monkeypatch.setenv(
        "GCP_BILLING_TABLE", "fixture-project.billing.export`; DROP TABLE x"
    )
    with pytest.raises(ValueError, match="fully qualified"):
        sources.CloudSource()
