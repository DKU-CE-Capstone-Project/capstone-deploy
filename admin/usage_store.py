"""Ingest versioned application events and serve aggregates from local SQLite."""

import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from history import MAX_GROUPS
from periods import KST, parse_start, window
from pricing import COST_FIELDS, estimate_external
from sources import (
    AGENT_KINDS,
    TOKEN_FIELDS,
    iso,
    now,
    provider_usage_key,
    summarize,
    time_key,
    validation_issues,
)

ENTRY_LIMIT = 10_000
DAILY_PAGE_SIZE = 100
MAX_BATCH_BYTES = 8 * 1024 * 1024
EVENT_ID = re.compile(r"[a-f0-9-]{16,64}", re.IGNORECASE)
# Retain only fields used for counts or estimates. Report job IDs are stored
# separately from the event payload for lookup.
LABEL_FIELDS = {
    "provider",
    "model",
    "operation",
    "stage",
    "service_tier",
    "outcome",
    "route",
    "method",
    "input_token_source",
    "token_count_status",
}
LABEL = re.compile(r"[-a-zA-Z0-9_./:{}]+")
EXTERNAL_KINDS = {"sdk_call", "http_attempt", "cache_hit"}
DAILY_FILTERS = {
    "external": "json_extract(payload, '$.kind') IN ('http_attempt', 'sdk_call')",
    "service": "json_extract(payload, '$.kind') = 'api_request'",
}
DAILY_FIELDS = {
    "external": (
        "kind",
        "provider",
        "model",
        "operation",
        "stage",
        "outcome",
        "input_tokens",
        "output_tokens",
        "counted_input_tokens",
        "token_count_status",
    ),
    "service": ("method", "route", "outcome"),
}


def event_row(record, received_at):
    if not isinstance(record, dict) or record.get("source") != "stdout":
        return None
    log = record.get("log")
    if not isinstance(log, str) or len(log) > 8192:
        return None
    try:
        event = json.loads(log)
        if not isinstance(event, dict):
            return None
        event_id, kind = event.get("event_id"), event.get("kind")
        if (
            event.get("event_type") != "econmind_usage"
            or type(event.get("schema_version")) is not int
            or event["schema_version"] != 1
            or not isinstance(event_id, str)
            or not EVENT_ID.fullmatch(event_id)
            or not isinstance(kind, str)
            or kind not in (*EXTERNAL_KINDS, *AGENT_KINDS, "api_request")
        ):
            return None
        timestamp = parse_start(event.get("timestamp"))
        if timestamp is None or timestamp > received_at:
            return None
        event_time = time_key(timestamp)
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        return None

    event_id = event_id.lower()
    payload = {
        "event_type": "econmind_usage",
        "schema_version": 1,
        "event_id": event_id,
        "kind": kind,
    }
    diagnostic = kind in AGENT_KINDS
    for field in (*LABEL_FIELDS, "error_type") if diagnostic else LABEL_FIELDS:
        value = event.get(field)
        if isinstance(value, str) and len(value) <= 160 and LABEL.fullmatch(value):
            payload[field] = value
    for field in ("discarded_arguments",) if diagnostic else TOKEN_FIELDS:
        value = event.get(field)
        if type(value) is int and 0 <= value < 2**63:
            payload[field] = value
    if diagnostic and (issues := validation_issues(event)):
        payload["validation_issues"] = issues
    cost_key = job_id = None
    if kind in EXTERNAL_KINDS:
        cost_key = json.dumps({field: payload.get(field) for field in COST_FIELDS})
    if kind in EXTERNAL_KINDS or diagnostic:
        value = event.get("job_id")
        if isinstance(value, str):
            try:
                job_id = str(UUID(value))
            except ValueError:
                pass
    return event_id, event_time, json.dumps(payload), cost_key, job_id


class UsageStore:
    def __init__(self, path):
        self.path = path
        self.diffbot_plan = os.getenv("DIFFBOT_COST_PLAN", "unknown")
        self.api_start = parse_start(os.getenv("API_COST_MEASUREMENT_START", ""))

    def connect(self):
        return closing(sqlite3.connect(self.path, timeout=5))

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
                    event_id TEXT PRIMARY KEY, event_time TEXT NOT NULL,
                    payload TEXT NOT NULL, cost_key TEXT, job_id TEXT
                )""")
                columns = db.execute("PRAGMA table_info(usage_events)").fetchall()
                if "job_id" not in {column[1] for column in columns}:
                    db.execute("ALTER TABLE usage_events ADD COLUMN job_id TEXT")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS usage_event_time ON usage_events(event_time)"
                )
                db.execute("""CREATE INDEX IF NOT EXISTS usage_event_job
                    ON usage_events(job_id, event_time DESC, event_id)
                    WHERE job_id IS NOT NULL""")
                for kind, predicate in DAILY_FILTERS.items():
                    db.execute(f"""CREATE INDEX IF NOT EXISTS usage_daily_{kind}
                        ON usage_events(event_time DESC, event_id DESC)
                        WHERE {predicate}""")

    def ingest(self, records):
        received_at = now()
        rows = [row for record in records if (row := event_row(record, received_at))]
        if not rows:
            return
        with self.connect() as db, db:
            db.executemany(
                """INSERT INTO usage_events (event_id, event_time, payload, cost_key, job_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET job_id = excluded.job_id
                   WHERE usage_events.job_id IS NULL AND excluded.job_id IS NOT NULL""",
                rows,
            )

    def usage(self, hours):
        end = now()
        start = end - timedelta(hours=hours)
        with self.connect() as db:
            rows = db.execute(
                """SELECT event_time, payload FROM usage_events WHERE event_time >= ? AND event_time < ?
                   ORDER BY event_time DESC, event_id LIMIT ?""",
                (time_key(start), time_key(end), ENTRY_LIMIT + 1),
            ).fetchall()
        return {
            "state": "ok" if rows else "no_data",
            "start": iso(start),
            "end": iso(end),
            "complete": len(rows) <= ENTRY_LIMIT,
            "entry_limit": ENTRY_LIMIT,
            **summarize(
                {"timestamp": event_time, "jsonPayload": json.loads(payload)}
                for event_time, payload in rows[:ENTRY_LIMIT]
            ),
        }

    def job_usage(self, job_id):
        with self.connect() as db:
            records = db.execute(
                """SELECT payload FROM usage_events WHERE job_id = ?
                   ORDER BY event_time DESC, event_id LIMIT ?""",
                (job_id, ENTRY_LIMIT + 1),
            ).fetchall()
        entries = [{"jsonPayload": json.loads(row[0])} for row in records[:ENTRY_LIMIT]]
        groups = {}
        for entry in entries:
            groups.setdefault(provider_usage_key(entry["jsonPayload"]), []).append(
                entry
            )

        summary = summarize(entries)
        rows = summary["providers"]
        for row in rows:
            cost = estimate_external(
                groups[provider_usage_key(row)], diffbot_plan=self.diffbot_plan
            )
            row.update(
                estimated_usd=cost["estimated_usd"],
                unpriced_calls=cost["unpriced_calls"],
            )
        cost = estimate_external(
            entries,
            complete=len(records) <= ENTRY_LIMIT,
            diffbot_plan=self.diffbot_plan,
        )
        return cost | {
            "job_id": job_id,
            "rows": rows,
            "diagnostics": summary["agent_diagnostics"],
        }

    def daily_usage(self, kind, day, cursor):
        predicate = DAILY_FILTERS[kind]
        with self.connect() as db:
            if day is None:
                latest = db.execute(
                    f"""SELECT event_time FROM usage_events WHERE {predicate}
                        ORDER BY event_time DESC, event_id DESC LIMIT 1"""
                ).fetchone()
                timestamp = parse_start(latest[0]) if latest else now()
                day = timestamp.astimezone(KST).date()
            start = datetime.combine(day, time.min, KST)
            end = time_key(start + timedelta(days=1))
            # Comparing both columns keeps the cursor stable when timestamps tie.
            before = cursor.split("|", 1) if cursor else (end, "")
            rows = db.execute(
                f"""SELECT event_time, event_id, payload FROM usage_events
                    WHERE {predicate} AND event_time >= ? AND event_time < ?
                      AND (event_time, event_id) < (?, ?)
                    ORDER BY event_time DESC, event_id DESC LIMIT ?""",
                (time_key(start), end, *before, DAILY_PAGE_SIZE + 1),
            ).fetchall()

        page = rows[:DAILY_PAGE_SIZE]
        events = []
        for event_time, _, payload in page:
            event = json.loads(payload)
            events.append(
                {"timestamp": event_time}
                | {key: event[key] for key in DAILY_FIELDS[kind] if key in event}
            )
        return {
            "state": "ok",
            "date": day.isoformat(),
            "rows": events,
            "next_cursor": "|".join(page[-1][:2])
            if len(rows) > DAILY_PAGE_SIZE
            else None,
        }

    def costs(self, period):
        end = now()
        with self.connect() as db:
            db.execute("BEGIN")
            start = self.api_start
            if start is None:
                (first,) = db.execute(
                    "SELECT min(event_time) FROM usage_events"
                ).fetchone()
                start = parse_start(first)
            if start is None:
                return {"state": "no_data", "archive_state": "archive_pending"}
            bounds = window(period, start, end)
            rows = db.execute(
                """SELECT cost_key, count(*) FROM usage_events
                   WHERE event_time >= ? AND event_time < ? AND cost_key IS NOT NULL
                   GROUP BY cost_key ORDER BY max(event_time) DESC, cost_key LIMIT ?""",
                (
                    time_key(parse_start(bounds["period_start"])),
                    time_key(end),
                    MAX_GROUPS + 1,
                ),
            ).fetchall()

        entries = [
            {
                "jsonPayload": {
                    "event_type": "econmind_usage",
                    "schema_version": 1,
                    "event_id": str(index),
                    **json.loads(key),
                },
                "occurrences": count,
            }
            for index, (key, count) in enumerate(rows[:MAX_GROUPS])
        ]
        estimate = estimate_external(
            entries,
            complete=len(rows) <= MAX_GROUPS,
            diffbot_plan=self.diffbot_plan,
            grouped=True,
        )
        return estimate | bounds | {"archive_state": "ok", "source": "SQLite"}


store = UsageStore(os.getenv("USAGE_DB_PATH", "/data/usage.sqlite3"))


@asynccontextmanager
async def lifespan(_app):
    store.initialize()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.post("/ingest", status_code=204)
async def ingest(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BATCH_BYTES:
            raise HTTPException(413, "Usage batch is too large")
        body.extend(chunk)
    try:
        records = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise HTTPException(400, "Invalid usage batch") from error
    if not isinstance(records, list):
        raise HTTPException(400, "Invalid usage batch")
    await run_in_threadpool(store.ingest, records)


@app.get("/usage")
def usage(hours: int = Query(default=24, ge=1, le=168)):
    return store.usage(hours)


@app.get("/usage/daily")
def daily_usage(
    kind: Literal["external", "service"],
    day: date | None = None,
    cursor: str | None = Query(default=None, max_length=100, pattern=r"^[^|]+\|[^|]+$"),
):
    if day in (date.min, date.max) or (cursor and day is None):
        raise HTTPException(422, "Invalid daily usage query")
    return store.daily_usage(kind, day, cursor)


@app.get("/report-job-usage")
def report_job_usage(job_id: UUID):
    return store.job_usage(str(job_id))


@app.get("/costs")
def costs(period: Literal["all", "day", "week", "month"] = "all"):
    return store.costs(period)
