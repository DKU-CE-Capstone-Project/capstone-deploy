"""Read-only, bounded Cloud Logging/Monitoring queries and Mongo ping status."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession

UTC = timezone.utc
TOKEN_FIELDS = (
    "input_tokens",
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
            key = tuple(
                event.get(k) or "—"
                for k in ("provider", "model", "operation", "stage", "service_tier")
            )
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
                    value = event.get(field)
                    if type(value) is int and value >= 0:
                        row["token_sums"][field] += value
                        row["token_observations"][field] += 1
    return {
        "providers": sorted(providers.values(), key=lambda r: r["provider"]),
        "routes": sorted(routes.values(), key=lambda r: -r["requests"]),
        "observed_events": len(seen),
    }


class CloudSource:
    def __init__(self):
        self.project = os.getenv("GCP_PROJECT_ID", "")
        self.instance = os.getenv("GCP_VM_INSTANCE_ID", "")
        self.status_url = os.getenv("BACKEND_STATUS_URL", "")
        self.status_token = os.getenv("ADMIN_STATUS_TOKEN", "")
        if self.project and not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", self.project):
            raise ValueError("Invalid GCP_PROJECT_ID")
        if self.instance and not self.instance.isdecimal():
            raise ValueError("GCP_VM_INSTANCE_ID must be numeric")
        if self.status_url and not self.status_url.startswith("https://"):
            raise ValueError("BACKEND_STATUS_URL must use HTTPS")

    def connection(self):
        if not self.status_url or len(self.status_token) < 32:
            return {"state": "not_configured"}
        try:
            response = requests.get(
                self.status_url,
                headers={"Authorization": "Bearer " + self.status_token},
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

    def usage(self, session, hours):
        start, end = now() - timedelta(hours=hours), now()
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
        return {
            "state": "ok" if entries else "no_data",
            "complete": complete,
            "start": iso(start),
            "end": iso(end),
            "entry_limit": 10000,
            **summarize(entries),
        }

    def metrics(self, session):
        definitions = {
            "cpu": ("compute.googleapis.com/instance/cpu/utilization", "", 100),
            "memory": (
                "agent.googleapis.com/memory/percent_used",
                ' metric.labels.state="used"',
                1,
            ),
            "disk": (
                "agent.googleapis.com/disk/percent_used",
                ' metric.labels.state="used"',
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
                series = []
                for item in data.get("timeSeries", []):
                    device = item.get("metric", {}).get("labels", {}).get("device", "")
                    # Snap loop mounts are read-only images and normally report
                    # 100%; they do not represent writable VM disk capacity.
                    if key == "disk" and re.fullmatch(r"(?:/dev/)?loop[0-9]+", device):
                        continue
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
                        series.append(
                            {
                                "device": item.get("metric", {})
                                .get("labels", {})
                                .get("device", ""),
                                "points": points,
                            }
                        )
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
