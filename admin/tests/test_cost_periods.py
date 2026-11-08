import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import billing
import history
import periods
import pricing
import sources


def test_windows_use_fixed_baselines_rolling_days_and_korean_calendar_month():
    start = periods.parse_start("2026-10-01T00:00:00+09:00")
    end = periods.parse_start("2026-11-01T01:00:00+09:00")
    assert periods.window("all", start, end)["period_start"] == start.isoformat()
    assert (
        periods.window("month", start, end)["period_start"]
        == "2026-11-01T00:00:00+09:00"
    )
    assert (
        periods.window("day", start, end)["period_start"] == "2026-10-31T01:00:00+09:00"
    )
    assert (
        periods.window("week", start, end)["period_start"]
        == "2026-10-25T01:00:00+09:00"
    )
    later = periods.parse_start("2026-11-01T00:30:00+09:00")
    assert periods.window("week", later, end)["period_start"] == later.isoformat()
    with pytest.raises(ValueError):
        periods.parse_start("2026-11-01")


def test_monthly_snapshot_cannot_be_reused_for_a_shorter_day():
    end = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    snapshot = {
        "project": "fixture",
        "month": "2026-11",
        "gross_cost": 10,
        "credits": -10,
        "net_cost": 0,
    }
    result = {
        "state": "no_data",
        "period_start": "2026-11-01T12:00:00+00:00",
        "report_url": "https://example.invalid",
    }
    assert (
        billing.with_snapshot(result, snapshot, project="fixture", timestamp=end)
        == result
    )


def test_group_weights_price_real_calls_once_without_charging_cache_hits():
    base = {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "provider": "gemini",
        "model": "gemini-embedding-001",
        "operation": "embed_batch",
        "kind": "sdk_call",
        "input_tokens": 1000,
    }
    data = [
        {"jsonPayload": base | {"event_id": "archive:0"}, "occurrences": 3},
        {
            "jsonPayload": base | {"event_id": "archive:1", "kind": "cache_hit"},
            "occurrences": 7,
        },
    ]
    result = pricing.estimate_external(data, grouped=True)
    assert result["calls"] == 3 and result["priced_calls"] == 3
    assert result["estimated_usd"] == pytest.approx(0.00045)
    assert result["providers"][0]["cache_hits"] == 7
    assert pricing.estimate_external(data)["calls"] == 1


def test_history_query_deduplicates_ids_excludes_live_tail_and_bounds_scanning():
    session = Mock()
    session.post.return_value.json.return_value = {
        "jobComplete": True,
        "schema": {"fields": [{"name": "signature"}, {"name": "occurrences"}]},
        "rows": [
            {
                "f": [
                    {
                        "v": json.dumps(
                            {
                                "provider": "naver",
                                "operation": "news_search",
                                "kind": "http_attempt",
                            }
                        )
                    },
                    {"v": "5"},
                ]
            }
        ],
    }
    result = history.query_history(
        session,
        project="fixture-project",
        table="fixture-project.history.events",
        location="US",
        start="2026-11-01T00:00:00Z",
        end="2026-11-03T00:00:00Z",
        exclude_ids=["live-id"],
    )
    assert result["complete"] and result["entries"][0]["occurrences"] == 5
    body = session.post.call_args.kwargs["json"]
    assert "PARTITION BY JSON_VALUE(payload, '$.event_id')" in body["query"]
    assert "NOT IN UNNEST(@exclude_ids)" in body["query"]
    assert body["maximumBytesBilled"] == str(128 * 2**20)
    assert body["queryParameters"][-1]["parameterValue"]["arrayValues"] == [
        {"value": "live-id"}
    ]


@pytest.mark.parametrize(
    "code,state",
    [(403, "permission_required"), (404, "archive_pending"), (500, "unavailable")],
)
def test_archive_failures_remain_incomplete(code, state):
    response = Mock(status_code=code)
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    session = Mock()
    session.post.return_value = response
    result = history.query_history(
        session,
        project="fixture-project",
        table="fixture-project.history.events",
        location="US",
        start="2026-11-01T00:00:00Z",
        end="2026-11-03T00:00:00Z",
        exclude_ids=[],
    )
    assert result["state"] == state and not result["complete"]


def test_archive_and_live_tail_have_no_overlap_and_empty_known_window_is_zero(
    monkeypatch,
):
    source = sources.CloudSource()
    source.history_table = "fixture-project.history.events"
    event = {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "event_id": "live",
        "provider": "gemini",
        "model": "gemini-embedding-001",
        "operation": "embed",
        "kind": "sdk_call",
        "input_tokens": 1000,
    }
    source.log_entries = Mock(return_value=([{"jsonPayload": event}], True))
    archive = Mock(
        return_value={
            "state": "ok",
            "complete": True,
            "entries": [
                {"jsonPayload": event | {"event_id": "archive:0"}, "occurrences": 2}
            ],
        }
    )
    monkeypatch.setattr(sources, "query_history", archive)
    window = periods.window(
        "all",
        periods.parse_start("2026-11-01T00:00:00Z"),
        periods.parse_start("2026-11-02T20:00:00Z"),
    )
    result = source.external_costs(Mock(), window)
    assert archive.call_args.kwargs["exclude_ids"] == ["live"]
    assert result["calls"] == 3 and result["estimated_usd"] == pytest.approx(0.00045)
    archive.return_value = {"state": "ok", "complete": True, "entries": []}
    source.log_entries.return_value = ([], True)
    result = source.external_costs(Mock(), window)
    assert result["state"] == "ok" and result["estimated_usd"] == 0
    archive.return_value = {
        "state": "archive_pending",
        "complete": False,
        "entries": [],
    }
    result = source.external_costs(Mock(), window)
    assert not result["complete"] and result["estimated_usd"] is None
    archive.return_value = {
        "state": "ok",
        "complete": True,
        "entries": [
            {"jsonPayload": event | {"event_id": "archive:0"}, "occurrences": 2}
        ],
    }
    source.log_entries.side_effect = requests.Timeout()
    result = source.external_costs(Mock(), window)
    assert result["calls"] == 2 and not result["complete"]
    assert result["estimated_usd"] == pytest.approx(0.0003)


def test_history_polling_paging_and_group_limit(monkeypatch):
    fields = [{"name": "signature"}, {"name": "occurrences"}]
    row = {
        "f": [
            {
                "v": json.dumps(
                    {
                        "provider": "naver",
                        "kind": "http_attempt",
                        "operation": "news_search",
                    }
                )
            },
            {"v": "2"},
        ]
    }
    job = {"jobId": "job-id"}
    session = Mock()
    session.post.return_value.json.return_value = {
        "jobComplete": False,
        "jobReference": job,
    }
    session.get.side_effect = [
        Mock(
            json=Mock(
                return_value={
                    "jobComplete": True,
                    "jobReference": job,
                    "schema": {"fields": fields},
                    "rows": [row],
                    "pageToken": "next",
                }
            )
        ),
        Mock(
            json=Mock(
                return_value={
                    "jobComplete": True,
                    "jobReference": job,
                    "schema": {"fields": fields},
                    "rows": [row],
                }
            )
        ),
    ]
    kwargs = {
        "project": "fixture-project",
        "table": "fixture-project.history.events",
        "location": "US",
        "start": "2026-11-01T00:00:00Z",
        "end": "2026-11-03T00:00:00Z",
        "exclude_ids": [],
    }
    result = history.query_history(session, **kwargs)
    assert result["complete"] and len(result["entries"]) == 2
    assert session.get.call_args.kwargs["params"]["pageToken"] == "next"
    monkeypatch.setattr(history, "MAX_GROUPS", 1)
    session = Mock()
    session.post.return_value.json.return_value = {
        "jobComplete": True,
        "schema": {"fields": fields},
        "rows": [row, row],
    }
    result = history.query_history(session, **kwargs)
    assert not result["complete"] and len(result["entries"]) == 1
