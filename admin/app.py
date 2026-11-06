"""Unified admin service for Cloud Run or loopback Docker Compose."""

import asyncio
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from local_source import LocalSource
from sources import CloudSource

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
source_mode = os.getenv("ADMIN_SOURCE", "gcp")
source = {"gcp": CloudSource, "local": LocalSource}[source_mode]()
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")
cache = {}
lock = asyncio.Lock()
cost_cache = {}
cost_lock = asyncio.Lock()


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'",
        }
    )
    return response


@app.get("/")
async def index():
    return FileResponse(static / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/overview")
async def overview(hours: int = Query(default=24, ge=1, le=168)):
    async with lock:
        cached = cache.get(hours)
        if cached and time.monotonic() - cached[0] < 30:
            return JSONResponse(cached[1])
        try:
            data = await asyncio.to_thread(source.snapshot, hours)
        except Exception:  # noqa: BLE001 — return a sanitized dependency failure
            return JSONResponse(
                {
                    "error": "관측 데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."
                },
                status_code=503,
            )
        cache[hours] = time.monotonic(), data
        return JSONResponse(data)


@app.get("/api/costs")
async def costs(
    gcp_period: Literal["all", "day", "week", "month"] = "all",
    external_period: Literal["all", "day", "week", "month"] = "all",
):
    key = (gcp_period, external_period)
    async with cost_lock:
        cached = cost_cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return JSONResponse(cached[1])
        try:
            data = await asyncio.to_thread(
                source.cost_snapshot, gcp_period, external_period
            )
        except Exception:  # noqa: BLE001 — do not expose SQL or credentials
            return JSONResponse(
                {
                    "error": "비용 데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."
                },
                status_code=503,
            )
        cost_cache[key] = time.monotonic(), data
        return JSONResponse(data)
