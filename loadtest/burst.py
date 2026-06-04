"""
burst 부하 테스트 — POST /jobs N건 동시 발사 후 전부 완료될 때까지 폴링.
총 드레인 시간, p50/p95 완료 지연, 처리량을 출력한다.

발표 측정용:
  (A) 동기 1-worker:  KEDA min=max=1 또는 docker compose worker 1개
  (B) KEDA 1→N:       ScaledObject 활성 상태에서 동일 실행

사용:
  python burst.py --base http://localhost:8000 --n 100 --keyword semiconductor
  python burst.py --base http://<INGRESS_IP> --n 100
"""
from __future__ import annotations

import argparse
import asyncio
import time

import httpx


async def submit(client: httpx.AsyncClient, base: str, keyword: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    r = await client.post(f"{base}/jobs", json={"keyword": keyword})
    r.raise_for_status()
    return r.json()["job_id"], t0


async def wait_done(
    client: httpx.AsyncClient, base: str, job_id: str, t0: float,
    poll: float, timeout: float,
) -> float | None:
    while True:
        r = await client.get(f"{base}/jobs/{job_id}")
        if r.status_code == 200 and r.json().get("status") in ("done", "error"):
            return time.perf_counter() - t0
        if time.perf_counter() - t0 > timeout:
            return None
        await asyncio.sleep(poll)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--keyword", default="semiconductor")
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()

    print(f"▶ burst 시작: n={args.n}, base={args.base}, keyword={args.keyword}")
    async with httpx.AsyncClient(timeout=30) as client:
        start = time.perf_counter()
        submits = await asyncio.gather(
            *[submit(client, args.base, args.keyword) for _ in range(args.n)]
        )
        submit_done = time.perf_counter() - start
        print(f"  발행 완료: {len(submits)}건 ({submit_done:.1f}s) — 처리 대기 중...")

        latencies = await asyncio.gather(
            *[wait_done(client, args.base, jid, t0, args.poll, args.timeout) for jid, t0 in submits]
        )
        total = time.perf_counter() - start

    done = sorted(l for l in latencies if l is not None)
    p50 = done[len(done) // 2] if done else 0.0
    p95 = done[max(0, int(len(done) * 0.95) - 1)] if done else 0.0

    print("\n=== burst 결과 ===")
    print(f"완료:           {len(done)}/{args.n}")
    print(f"총 드레인 시간:  {total:.1f}s")
    print(f"p50 지연:       {p50:.1f}s")
    print(f"p95 지연:       {p95:.1f}s")
    print(f"처리량:         {len(done) / total * 60:.0f} jobs/min" if total else "처리량: n/a")


if __name__ == "__main__":
    asyncio.run(main())
