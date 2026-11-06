"""Sample the Docker host's Linux CPU, memory and data volume."""

import asyncio
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI

SAMPLE_SECONDS = 10
HISTORY = timedelta(minutes=30)
PROC = Path("/host/proc")
DISK = "/host/disk"
points = {name: deque() for name in ("cpu", "memory", "disk")}
failed = set()


def cpu_ticks():
    with (PROC / "stat").open() as stat:
        # Aggregate line, all logical CPUs. Guest ticks are already included
        # in user/nice, so only add the first eight counters.
        ticks = [int(value) for value in stat.readline().split()[1:9]]
    return sum(ticks), ticks[3] + ticks[4]


def capacity(used, total):
    if not 0 <= used <= total or total <= 0:
        raise ValueError("Invalid host capacity")
    return {"value": 100 * used / total, "used_bytes": used, "total_bytes": total}


def memory():
    values = {}
    for line in (PROC / "meminfo").read_text().splitlines():
        name, _, value = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            values[name] = int(value.split()[0]) * 1024
    total = values["MemTotal"]
    return capacity(total - values["MemAvailable"], total)


def disk():
    fs = os.statvfs(DISK)
    return capacity((fs.f_blocks - fs.f_bfree) * fs.f_frsize, fs.f_blocks * fs.f_frsize)


def prune(series, timestamp):
    while series and series[0][0] < timestamp - HISTORY:
        series.popleft()


async def collect():
    previous = None
    while True:
        timestamp = datetime.now(timezone.utc)
        for name, sample in (("cpu", cpu_ticks), ("memory", memory), ("disk", disk)):
            series = points[name]
            prune(series, timestamp)
            try:
                value = sample()
                if name == "cpu":
                    total, idle = value
                    baseline, previous = previous, value
                    if baseline is None or total <= baseline[0]:
                        continue
                    idle_ratio = (idle - baseline[1]) / (total - baseline[0])
                    value = {"value": max(0.0, min(100.0, 100 * (1 - idle_ratio)))}
                series.append((timestamp, {"time": timestamp.isoformat(), **value}))
                failed.discard(name)
            except (OSError, ValueError, KeyError, IndexError):
                failed.add(name)
                if name == "cpu":
                    previous = None
        await asyncio.sleep(SAMPLE_SECONDS)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(collect())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/metrics")
async def metrics():
    timestamp = datetime.now(timezone.utc)
    result = {}
    for name, series in points.items():
        prune(series, timestamp)
        result[name] = {
            "state": "ok" if series else "unavailable" if name in failed else "no_data",
            "series": [
                {
                    "device": "Docker 데이터 볼륨" if name == "disk" else "",
                    "points": [point for _, point in series],
                }
            ]
            if series
            else [],
        }
    return result
