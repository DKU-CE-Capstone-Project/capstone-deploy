# 성능 측정 결과 — burst 부하 테스트 (실측)

로컬 docker-compose 환경(NATS JetStream + Redis + worker N), demo_mode(고정 2s/job)로
**동일 burst를 worker 수만 바꿔** 측정. worker 1팟 = 동시성 1 → replica 수가 곧 병렬도.

> 측정 환경: docker-compose, demo_mode=true(통제된 부하 시뮬레이션), keyword=semiconductor.
> k8s에서는 이 worker 스케일링을 **KEDA가 NATS 큐 적체 기준으로 자동** 수행한다.

## 헤드라인 — 50건 동시 burst

| 지표 | (A) 1 worker | (B) 10 workers | **개선** |
|---|---|---|---|
| **총 드레인 시간** | **100.9s** | **10.4s** | **9.7× 단축 (−90%)** |
| **p95 응답 지연** | 94.8s | 10.4s | **−89%** |
| **p50 응답 지연** | 52.4s | 6.3s | −88% |
| **처리량** | 30 jobs/min | 290 jobs/min | **9.7× 향상** |

## ⭐ k8s(kind) — KEDA **자동** 확장 실측 (사람 개입 없음)

로컬 kind 클러스터 + NATS JetStream + KEDA + Prometheus/Grafana 실배포.
KEDA `nats-jetstream` 트리거가 큐 적체(consumer lag)를 감지해 worker를 **자동** 확장.

- **KEDA HPA가 NATS 큐 lag 실시간 측정**: idle 시 `0/5 (avg)`, 적체 시 임계 초과 → 확장
- **자동 스케일 타임라인** (n=60 burst):
  | 시각 | desired replica |
  |---|---|
  | 0s (발사) | 1 |
  | ~10s | **4** (KEDA가 큐 적체 감지) |
  | ~25s | **8** |
  | ~40s | 10 (max 도달) |
  | drain 후 +30s | 1 (자동 축소) |
- n=60 드레인 30.6s / n=80 드레인 38.8s, 124 jobs/min → 처리 후 **worker 자동 1로 축소**
- **Prometheus 수집 곡선**(`kube_deployment_status_replicas`): `1→10→1→5→1` → Grafana 대시보드로 시각화

> 핵심: compose는 사람이 `--scale`을 눌렀지만, **k8s에서는 KEDA가 큐 적체만 보고 스스로** worker를 1→10으로 늘리고 idle엔 1로 줄였다. "속보 burst 자동 대응"이 실제로 동작함을 증명.

## 보조 — 10건 동시 burst (재현 확인)

| 지표 | 1 worker | 5 workers | 개선 |
|---|---|---|---|
| 드레인 시간 | 20.4s | 4.2s | 4.9× (−79%) |
| p95 지연 | 18.3s | 4.2s | −77% |
| 처리량 | 29 jobs/min | 144 jobs/min | ~5× |

## 해석 (보고서 §7 / 슬라이드 7 화법)
- 직렬(1 worker): 50건 × 2s ≈ 100s — 측정값 100.9s로 이론과 일치(파이프라인 정상).
- 병렬(10 worker): (50/10) × 2s + 램프 ≈ 10s — 측정값 10.4s.
- **"동일 burst 처리에 100초 → 10초, 약 10배 빨라졌다. NATS 버퍼 + worker 확장이 실제로 burst 실패모드를 해소했다."**
- k8s에서는 사람이 `--scale`을 누르지 않아도 **KEDA가 큐 적체를 감지해 자동으로** worker를 1→10으로 늘린다(ScaledObject).

## 재현 방법
```bash
cd deploy
docker compose up -d --build nats redis econmind-api econmind-worker
# (A) 기준선
docker compose up -d --scale econmind-worker=1 econmind-worker
python loadtest/burst.py --base http://localhost:8000 --n 50
# (B) 확장
docker compose up -d --scale econmind-worker=10 econmind-worker
python loadtest/burst.py --base http://localhost:8000 --n 50
```
