"""Copy observed usage from existing app logs without changing their log driver."""

import argparse
import json
import subprocess
import time
from pathlib import Path

SERVICES = ("econmind-api", "econmind-worker", "econmind-v2-worker")
TAIL_LINES = 10_000


def docker(*args, timeout=10, payload=None):
    return subprocess.run(
        ("docker", *args),
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
        timeout=timeout,
        check=True,
    ).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-project", default="econmind")
    parser.add_argument("--store", default="econmind-monitoring-usage-store-1")
    parser.add_argument("--cursor", required=True, type=Path)
    args = parser.parse_args()

    end = int(time.time())
    try:
        start = int(args.cursor.read_text()) - 20
    except FileNotFoundError:
        start = end - 24 * 60 * 60

    containers = docker(
        "ps",
        "--filter",
        f"label=com.docker.compose.project={args.app_project}",
        "--format",
        '{{.ID}} {{.Label "com.docker.compose.service"}}',
    )
    selected = [line.split() for line in containers.splitlines()]
    if not any(service in SERVICES for _, service in selected):
        raise SystemExit("No application containers found")

    records = []
    for container, service in selected:
        if service not in SERVICES:
            continue
        lines = docker(
            "logs",
            "--since",
            str(start),
            "--until",
            str(end),
            "--tail",
            str(TAIL_LINES),
            container,
        )
        records.extend(
            {"source": "stdout", "log": line}
            for line in lines.splitlines()
            if line.startswith("{") and '"econmind_usage"' in line and len(line) <= 8192
        )

    for offset in range(0, len(records), 1000):
        docker(
            "exec",
            "-i",
            args.store,
            "python",
            "-c",
            "import json, sys; from usage_store import store; store.ingest(json.load(sys.stdin))",
            timeout=20,
            payload=json.dumps(records[offset : offset + 1000]),
        )
    temporary = args.cursor.with_suffix(".tmp")
    temporary.write_text(str(end))
    temporary.replace(args.cursor)
    print(f"Collected {len(records)} usage events before deduplication", flush=True)


if __name__ == "__main__":
    main()
