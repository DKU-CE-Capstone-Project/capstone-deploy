"""Read-only, bounded Cloud Logging/Monitoring queries and Mongo ping status."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

import google.auth
import requests
from billing import query_billing, with_snapshot
from google.auth.transport.requests import AuthorizedSession
from history import query_history
from periods import parse_start, window
from pricing import estimate_external

UTC = timezone.utc
TOKEN_FIELDS = (
    "input_tokens",
    "counted_input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "thought_tokens",
    "total_tokens",
    "cache_creation_tokens",
)


def now():
    return datetime.now(UTC)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def time_key(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def connection_status(url, token, get=None):
    if not url or len(token) < 32:
        return {"state": "not_configured"}
    try:
        response = (get or requests.get)(
            url,
            headers={"Authorization": "Bearer " + token},
            timeout=(3, 8),
            allow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        state = body.get("mongodb") if isinstance(body, dict) else None
        return {
            "state": state
            if state in ("connected", "unreachable", "disabled")
            else "unknown",
            "checked_at": iso(now()),
        }
    except (requests.RequestException, ValueError):
        # An unreachable status endpoint is not evidence that MongoDB is down.
        return {"state": "unknown", "checked_at": iso(now())}


def capacity_series(data, key):
    """Use complete, simultaneous Linux host states for capacity and utilization."""
    states = (
        {"buffered", "cached", "free", "slab", "used"}
        if key == "memory"
        else {"free", "reserved", "used"}
    )
    observations = {}
    for item in data.get("timeSeries", []):
        labels = item.get("metric", {}).get("labels", {})
        device = labels.get("device", "")
        state = labels.get("state")
        if state not in states:
            continue
        if key == "disk" and (
            not device or re.fullmatch(r"(?:/dev/)?loop[0-9]+", device)
        ):
            continue
        for point in item.get("points", []):
            value = point.get("value", {})
            raw = value.get("doubleValue", value.get("int64Value"))
            if raw is not None:
                timestamp = point["interval"]["endTime"]
                observations.setdefault((device, timestamp), {})[state] = float(raw)
    devices = {}
    for (device, timestamp), values in observations.items():
        if set(values) != states or any(
            not math.isfinite(value) or value < 0 for value in values.values()
        ):
            continue
        total = math.fsum(values.values())
        if total <= 0 or not math.isfinite(total):
            continue
        used = values["used"]
        devices.setdefault(device, []).append(
            {
                "time": timestamp,
                "value": used / total * 100,
                "used_bytes": used,
                "total_bytes": total,
            }
        )
    return [
        {"device": device, "points": sorted(points, key=lambda point: point["time"])}
        for device, points in devices.items()
    ]


def provider_usage_key(event):
    return tuple(
        event.get(key) or "—"
        for key in ("provider", "model", "operation", "stage", "service_tier")
    )


def summarize(entries):
    providers, routes, seen = {}, {}, set()
    for entry in entries:
        event = entry.get("jsonPayload", {}) if isinstance(entry, dict) else {}
        if not isinstance(event, dict):
            continue
        if (
            event.get("event_type") != "econmind_usage"
            or event.get("schema_version") != 1
        ):
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or event_id in seen:
            continue
        seen.add(event_id)
        kind = event.get("kind")
        if kind == "api_request":
            key = (event.get("method", ""), event.get("route", "unmatched"))
            row = routes.setdefault(
                key, {"method": key[0], "route": key[1], "requests": 0, "errors": 0}
            )
            row["requests"] += 1
            row["errors"] += event.get("outcome") == "error"
        elif kind in ("sdk_call", "http_attempt", "cache_hit"):
            key = provider_usage_key(event)
            row = providers.setdefault(
                key,
                dict(
                    zip(
                        ("provider", "model", "operation", "stage", "service_tier"), key
                    )
                )
                | {
                    "sdk_calls": 0,
                    "http_attempts": 0,
                    "cache_hits": 0,
                    "errors": 0,
                    "cancelled": 0,
                    "token_sums": {k: 0 for k in TOKEN_FIELDS},
                    "token_observations": {k: 0 for k in TOKEN_FIELDS},
                },
            )
            row[
                {
                    "sdk_call": "sdk_calls",
                    "http_attempt": "http_attempts",
                    "cache_hit": "cache_hits",
                }[kind]
            ] += 1
            if kind != "cache_hit":
                row["errors"] += event.get("outcome") == "error"
                row["cancelled"] += event.get("outcome") == "cancelled"
                for field in TOKEN_FIELDS:
                    if field == "counted_input_tokens" and (
                        event.get("token_count_status") != "ok"
                        or event.get("input_tokens") is not None
                    ):
                        continue
                    value = event.get(field)
                    if type(value) is int and value >= 0:
                        row["token_sums"][field] += value
                        row["token_observations"][field] += 1
        else:
            continue

        raw_time = event.get("timestamp") or entry.get("timestamp")
        if not isinstance(raw_time, str) or not raw_time:
            continue
        try:
            event_time = time_key(parse_start(raw_time))
        except (ValueError, OverflowError):
            continue
        if event_time > row.get("last_event_at", ""):
            row["last_event_at"] = event_time
    return {
        "providers": sorted(providers.values(), key=lambda r: r["provider"]),
        "routes": sorted(routes.values(), key=lambda r: -r["requests"]),
        "observed_events": len(seen),
    }


class CloudSource:
    def job_usage(self, _job_id):
        return {"state": "not_configured"}

    def daily_usage(self, _params):
        return {"state": "not_configured"}

    def __init__(self):
        self.project = os.getenv("GCP_PROJECT_ID", "")
        self.instance = os.getenv("GCP_VM_INSTANCE_ID", "")
        self.status_url = os.getenv("BACKEND_STATUS_URL", "")
        self.status_token = os.getenv("ADMIN_STATUS_TOKEN", "")
        self.billing_table = os.getenv("GCP_BILLING_TABLE", "")
        self.billing_location = os.getenv("GCP_BILLING_LOCATION", "US")
        self.billing_snapshot = json.loads(
            os.getenv("GCP_BILLING_SNAPSHOT_JSON", "null")
        )
        self.diffbot_plan = os.getenv("DIFFBOT_COST_PLAN", "unknown")
        self.history_table = os.getenv("GCP_USAGE_HISTORY_VIEW", "")
        self.gcp_start = parse_start(os.getenv("GCP_COST_MEASUREMENT_START", ""))
        self.api_start = parse_start(os.getenv("API_COST_MEASUREMENT_START", ""))
        if self.project and not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", self.project):
            raise ValueError("Invalid GCP_PROJECT_ID")
        if self.instance and not self.instance.isdecimal():
            raise ValueError("GCP_VM_INSTANCE_ID must be numeric")
        if self.status_url and not self.status_url.startswith("https://"):
            raise ValueError("BACKEND_STATUS_URL must use HTTPS")
        for table in (self.billing_table, self.history_table):
            if table and not re.fullmatch(
                r"[a-z][a-z0-9-]{4,62}\.[A-Za-z_][A-Za-z0-9_]{0,1023}\.[A-Za-z_][A-Za-z0-9_]{0,1023}",
                table,
            ):
                raise ValueError("Expected a fully qualified BigQuery table")

    def connection(self):
        return connection_status(self.status_url, self.status_token)

    def usage(self, session, hours):
        start, end = now() - timedelta(hours=hours), now()
        entries, complete = self.log_entries(session, start, end)
        return {
            "state": "ok" if entries else "no_data",
            "complete": complete,
            "start": iso(start),
            "end": iso(end),
            "entry_limit": 10000,
            **summarize(entries),
            "estimated_costs": estimate_external(
                entries, complete=complete, diffbot_plan=self.diffbot_plan
            ),
        }

    def log_entries(self, session, start, end):
        query = (
            f'resource.type="gce_instance" resource.labels.instance_id="{self.instance}" '
            f'jsonPayload.event_type="econmind_usage" timestamp>="{iso(start)}" timestamp<="{iso(end)}"'
        )
        entries, page_token, complete = [], None, False
        for _ in range(10):
            body = {
                "resourceNames": [f"projects/{self.project}"],
                "filter": query,
                "orderBy": "timestamp desc",
                "pageSize": 1000,
            }
            if page_token:
                body["pageToken"] = page_token
            response = session.post(
                "https://logging.googleapis.com/v2/entries:list",
                json=body,
                timeout=(3, 10),
            )
            response.raise_for_status()
            payload = response.json()
            entries.extend(payload.get("entries", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                complete = True
                break
        return entries, complete

    def external_costs(self, session, period):
        end, start = (
            parse_start(period["period_end"]),
            parse_start(period["period_start"]),
        )
        try:
            tail, tail_complete = self.log_entries(
                session, max(start, end - timedelta(days=1)), end
            )
        except (requests.RequestException, ValueError):
            tail, tail_complete = [], False
        if not self.history_table:
            return {"state": "not_configured"} | period
        history = query_history(
            session,
            project=self.project,
            table=self.history_table,
            location=self.billing_location,
            start=period["period_start"],
            end=period["period_end"],
            exclude_ids=sorted(
                {
                    e.get("jsonPayload", {}).get("event_id")
                    for e in tail
                    if isinstance(e.get("jsonPayload", {}).get("event_id"), str)
                }
            ),
        )
        estimate = estimate_external(
            history["entries"] + tail,
            grouped=True,
            complete=history["complete"] and tail_complete,
            diffbot_plan=self.diffbot_plan,
        )
        if (
            history["state"] == "ok"
            and estimate["complete"]
            and not estimate["providers"]
        ):
            estimate.update(state="ok", estimated_usd=0)
        return (
            estimate
            | period
            | {"archive_state": history["state"], "source": "BigQuery + Cloud Logging"}
        )

    def cost_snapshot(self, gcp_period="all", external_period="all"):
        timestamp = now()
        result = {
            "project": self.project,
            "updated_at": iso(timestamp),
            "gcp_period": gcp_period,
            "external_period": external_period,
            "gcp": {"state": "not_configured"},
            "external": {"state": "not_configured"},
        }
        if not self.project:
            return result
        try:
            credentials, _ = google.auth.default(
                scopes=[
                    "https://www.googleapis.com/auth/logging.read",
                    "https://www.googleapis.com/auth/bigquery",
                ]
            )
            with AuthorizedSession(credentials) as session:
                result["gcp"] = query_billing(
                    session,
                    project=self.project,
                    table=self.billing_table,
                    location=self.billing_location,
                    timestamp=timestamp,
                    period_start=parse_start(
                        window(gcp_period, self.gcp_start, timestamp)["period_start"]
                    )
                    if self.gcp_start
                    else None,
                )
                if self.instance and self.api_start:
                    try:
                        result["external"] = self.external_costs(
                            session, window(external_period, self.api_start, timestamp)
                        )
                    except (requests.RequestException, ValueError, ArithmeticError):
                        result["external"] = {"state": "unavailable"}
        except google.auth.exceptions.GoogleAuthError:
            result.update(
                gcp={
                    "state": "authentication_required",
                    "report_url": f"https://console.cloud.google.com/billing?project={self.project}",
                },
                external={"state": "authentication_required"},
            )
        result["gcp"] = with_snapshot(
            result["gcp"],
            self.billing_snapshot,
            project=self.project,
            timestamp=timestamp,
        )
        if self.gcp_start:
            result["gcp"].update(window(gcp_period, self.gcp_start, timestamp))
        if self.api_start:
            result["external"].update(
                window(external_period, self.api_start, timestamp)
            )
        return result

    def metrics(self, session):
        definitions = {
            "cpu": ("compute.googleapis.com/instance/cpu/utilization", "", 100),
            "memory": (
                "agent.googleapis.com/memory/bytes_used",
                "",
                1,
            ),
            "disk": (
                "agent.googleapis.com/disk/bytes_used",
                "",
                1,
            ),
        }
        result = {}
        for key, (metric, extra, scale) in definitions.items():
            try:
                response = session.get(
                    f"https://monitoring.googleapis.com/v3/projects/{self.project}/timeSeries",
                    params={
                        "filter": f'metric.type="{metric}" resource.type="gce_instance" resource.labels.instance_id="{self.instance}"'
                        + extra,
                        "interval.startTime": iso(now() - timedelta(minutes=30)),
                        "interval.endTime": iso(now()),
                        "view": "FULL",
                        "pageSize": 1000,
                    },
                    timeout=(3, 8),
                )
                response.raise_for_status()
                data = response.json()
                if key in {"memory", "disk"}:
                    series = capacity_series(data, key)
                else:
                    series = []
                    for item in data.get("timeSeries", []):
                        points = []
                        for point in item.get("points", []):
                            value = point.get("value", {})
                            raw = value.get("doubleValue", value.get("int64Value"))
                            if raw is not None:
                                points.append(
                                    {
                                        "time": point["interval"]["endTime"],
                                        "value": float(raw) * scale,
                                    }
                                )
                        points.sort(key=lambda p: p["time"])
                        if points:
                            series.append({"device": "", "points": points})
                result[key] = {
                    "state": "ok" if series else "no_data",
                    "series": series,
                    "complete": not bool(data.get("nextPageToken")),
                }
            except (requests.RequestException, ValueError, KeyError):
                result[key] = {"state": "unavailable", "series": []}
        return result

    def snapshot(self, hours):
        result = {
            "project": self.project,
            "instance": self.instance,
            "updated_at": iso(now()),
            "hours": hours,
            "connection": self.connection(),
        }
        if not self.project or not self.instance:
            return result | {"usage": {"state": "not_configured"}, "metrics": {}}
        try:
            credentials, _ = google.auth.default(
                scopes=[
                    "https://www.googleapis.com/auth/monitoring.read",
                    "https://www.googleapis.com/auth/logging.read",
                ]
            )
            with AuthorizedSession(credentials) as session:
                result["metrics"] = self.metrics(session)
                try:
                    result["usage"] = self.usage(session, hours)
                except (requests.RequestException, ValueError):
                    result["usage"] = {"state": "unavailable"}
        except google.auth.exceptions.GoogleAuthError:
            result.update(metrics={}, usage={"state": "authentication_required"})
        return result
