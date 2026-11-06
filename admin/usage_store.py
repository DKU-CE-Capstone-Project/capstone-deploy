"""Ingest versioned application events and serve aggregates from local SQLite."""

import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from history import MAX_GROUPS
from periods import parse_start, window
from pricing import COST_FIELDS, estimate_external
from sources import TOKEN_FIELDS, UTC, iso, now, summarize

ENTRY_LIMIT = 10_000
MAX_BATCH_BYTES = 8 * 1024 * 1024
EVENT_ID = re.compile(r"[a-f0-9-]{16,64}", re.IGNORECASE)
# Retain only fields used for counts or estimates. Docker metadata, identifiers
# for requests/jobs, and arbitrary message fields never reach the ledger.
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


def time_key(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds")


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
            or (kind not in EXTERNAL_KINDS and kind != "api_request")
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
    for field in LABEL_FIELDS:
        value = event.get(field)
        if isinstance(value, str) and len(value) <= 160 and LABEL.fullmatch(value):
            payload[field] = value
    for field in TOKEN_FIELDS:
        value = event.get(field)
        if type(value) is int and 0 <= value < 2**63:
            payload[field] = value
    cost_key = None
    if kind in EXTERNAL_KINDS:
        cost_key = json.dumps({field: payload.get(field) for field in COST_FIELDS})
    return event_id, event_time, json.dumps(payload), cost_key


class UsageStore:
    def __init__(self, path):
        self.path = path
        self.diffbot_plan = os.getenv("DIFFBOT_COST_PLAN", "unknown")
        self.api_start = parse_start(os.getenv("API_COST_MEASUREMENT_START", ""))

    def connect(self):
        return closing(sqlite3.connect(self.path, timeout=5))

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS usage_events (
                    event_id TEXT PRIMARY KEY, event_time TEXT NOT NULL,
                    payload TEXT NOT NULL, cost_key TEXT
                );
                CREATE INDEX IF NOT EXISTS usage_event_time ON usage_events(event_time);
            """)

    def ingest(self, records):
        received_at = now()
        rows = [row for record in records if (row := event_row(record, received_at))]
        if not rows:
            return
        with self.connect() as db, db:
            db.executemany(
                "INSERT INTO usage_events VALUES (?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING",
                rows,
            )

    def usage(self, hours):
        end = now()
        start = end - timedelta(hours=hours)
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload FROM usage_events WHERE event_time >= ? AND event_time < ?
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
                {"jsonPayload": json.loads(row[0])} for row in rows[:ENTRY_LIMIT]
            ),
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


@app.get("/costs")
def costs(period: Literal["all", "day", "week", "month"] = "all"):
    return store.costs(period)
