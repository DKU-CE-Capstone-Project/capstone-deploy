#!/usr/bin/env python3
"""터널 복구 시간을 GCP VM의 한 시계로 잰다.

econmind-api 컨테이너 안에서 실행한다. 절차서:
docs/RUNBOOK-20261108-mongo-tunnel-autossh.md

  sudo docker exec -i "$API" python -u - --mongo-port <포트> < scripts/tunnel-recovery-probe.py

환경 변수와 URI를 읽지 않으며 인증 없이 동작한다. 상태가 바뀔 때만 한 줄을 출력한다.
"""
import argparse
import datetime as dt
import json
import socket
import threading
import time
import urllib.error
import urllib.request


def stamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"


def tcp_check(args):
    with socket.create_connection((args.mongo_host, args.mongo_port), timeout=args.timeout):
        return True, "listener"


def ping_check(args):
    from pymongo import MongoClient

    client = MongoClient(
        host=args.mongo_host,
        port=args.mongo_port,
        directConnection=True,
        serverSelectionTimeoutMS=int(args.timeout * 1000),
        connectTimeoutMS=int(args.timeout * 1000),
    )
    try:
        client.admin.command("ping")
        return True, "ping-ok"
    finally:
        client.close()


def ready_check(args):
    try:
        with urllib.request.urlopen(args.ready_url, timeout=args.ready_timeout) as response:
            status = response.status
            body = json.load(response)
    except urllib.error.HTTPError as error:
        status = error.code
        body = json.load(error)
    state = {key: body.get(key) for key in ("status", "mongodb", "redis")}
    healthy = status == 200 and state == {"status": "ok", "mongodb": "on", "redis": "on"}
    return healthy, f"http={status} {json.dumps(state, sort_keys=True)}"


class Probe:
    def __init__(self, name, check):
        self.name = name
        self.check = check
        self.healthy = None
        self.down_at = None
        self.up_at = None

    def run(self, args, stop):
        while not stop.is_set():
            try:
                healthy, detail = self.check(args)
            except Exception as error:
                healthy, detail = False, type(error).__name__
            now = time.time()
            if healthy != self.healthy:
                print(f"{stamp(now)} {self.name} {'UP' if healthy else 'DOWN'} {detail}", flush=True)
                if healthy is False and self.down_at is None:
                    self.down_at = now
                if healthy and self.down_at is not None and self.up_at is None:
                    self.up_at = now
                self.healthy = healthy
            stop.wait(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mongo-host", default="host.docker.internal")
    parser.add_argument("--mongo-port", type=int, required=True)
    parser.add_argument("--ready-url", default="http://127.0.0.1:8000/ready")
    parser.add_argument("--duration", type=float, default=300.0, help="전체 측정 초")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=1.0, help="TCP/ping 제한 초")
    parser.add_argument("--ready-timeout", type=float, default=15.0)
    args = parser.parse_args()

    stop = threading.Event()
    probes = [Probe("tcp", tcp_check), Probe("mongo", ping_check), Probe("ready", ready_check)]
    workers = [threading.Thread(target=probe.run, args=(args, stop), daemon=True) for probe in probes]
    for worker in workers:
        worker.start()
    print(f"{stamp(time.time())} probe started for {args.duration:.0f}s", flush=True)
    try:
        stop.wait(args.duration)
    except KeyboardInterrupt:
        pass
    stop.set()
    for worker in workers:
        worker.join(timeout=args.ready_timeout + 1)

    downs = [probe.down_at for probe in probes if probe.down_at is not None]
    if not downs:
        print("no outage observed")
        return
    start = min(downs)
    print(f"T_down {stamp(start)} (첫 실패 관측, UTC)")
    for probe in probes:
        recovered = "미복구" if probe.up_at is None else f"{probe.up_at - start:.1f}s"
        print(f"{probe.name}: 복구까지 {recovered}")


if __name__ == "__main__":
    main()
