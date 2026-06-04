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
