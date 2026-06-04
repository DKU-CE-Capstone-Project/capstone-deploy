# EconMind — CNCF 배포 런북

발표 자료의 **"속보 burst → NATS 큐 버퍼 → KEDA worker 1→N 오토스케일"** 을 단일 클라우드 VM + k3s에 배포한다.

```
POST /jobs ──> econmind-api ──publish──> NATS JetStream(analysis.jobs)
                                              │
                              ┌───────────────┴─── KEDA(큐 적체 감지) ──> worker 1→N
                              ▼
                       analysis-worker ──run_analysis──> Redis(job:{id})
GET /jobs/{id} <── econmind-api <──read── Redis
```

## 0. 로컬 검증 (k3s 이관 전 — 안전 백업)

```bash
cd deploy
docker compose up --build              # nats + redis + api + worker(1) + frontend
# 다른 터미널 — burst (동기 1-worker)
python loadtest/burst.py --base http://localhost:8000 --n 100
# 워커 수동 스케일해서 효과 비교
docker compose up --scale econmind-worker=5
python loadtest/burst.py --base http://localhost:8000 --n 100
```
UI: http://localhost:8080  ·  API: http://localhost:8000/health

## 1. VM + k3s + Helm

```bash
# Ubuntu 22.04 VM (4 vCPU 권장), 80/443/22 방화벽 개방
curl -sfL https://get.k3s.io | sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

## 2. CNCF 컴포넌트 (Helm)

```bash
# NATS (JetStream) — 차트 버전별 키 차이 주의
helm repo add nats https://nats-io.github.io/k8s/helm/charts/ && helm repo update
helm install nats nats/nats --set config.jetstream.enabled=true --set natsBox.enabled=true

# KEDA
helm repo add kedacore https://kedacore.github.io/charts && helm repo update
helm install keda kedacore/keda -n keda --create-namespace

# Prometheus + Grafana (측정 그래프)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
helm install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
```

## 3. 이미지 빌드/푸시 (GHCR)

```bash
docker build -t ghcr.io/dku-ce-capstone-project/econmind-backend:demo ../backend
docker build --build-arg VITE_API_BASE="" -t ghcr.io/dku-ce-capstone-project/econmind-frontend:demo ../frontend
docker push ghcr.io/dku-ce-capstone-project/econmind-backend:demo
docker push ghcr.io/dku-ce-capstone-project/econmind-frontend:demo
```
> 레지스트리 없이: VM에서 `docker build` 후 `docker save img | sudo k3s ctr images import -`

## 4. 배포

```bash
kubectl apply -f k8s/namespace.yaml
# (선택) 실제 LLM/뉴스 키 — 없으면 demo_mode(mock)로 동작
kubectl -n econmind create secret generic api-keys \
  --from-literal=GOOGLE_API_KEY=*** --from-literal=NEWSAPI_KEY=*** || true
kubectl apply -f k8s/
```

확인:
```bash
kubectl -n econmind get pods
kubectl get scaledobject,hpa -n econmind
```

## 5. burst 데모 + 측정

```bash
# 터미널 A — worker 스케일 관찰 (녹화 대상)
kubectl -n econmind get pods -w -l app=analysis-worker

# 터미널 B — (A) 동기 1-worker: ScaledObject min=max=1로 잠시 고정
kubectl -n econmind patch scaledobject analysis-worker-scaler \
  --type merge -p '{"spec":{"maxReplicaCount":1}}'
python loadtest/burst.py --base http://<INGRESS_IP> --n 100   # T1, p95 기록

# (B) KEDA 1→N: 복원 후 동일 burst
kubectl -n econmind patch scaledobject analysis-worker-scaler \
  --type merge -p '{"spec":{"maxReplicaCount":10}}'
python loadtest/burst.py --base http://<INGRESS_IP> --n 100   # T2, p95 기록 → 개선율 계산
```

Grafana(이중축): NATS consumer lag vs `kube_deployment_status_replicas{deployment="analysis-worker"}`
→ "큐가 차니 KEDA가 늘렸다"를 한 화면에 시각화.

## 백업 트리거 (KEDA nats-jetstream 설정 난항 시)
`k8s/keda-scaledobject.yaml`의 trigger를 CPU 기반으로 교체:
```yaml
triggers:
  - type: cpu
    metricType: Utilization
    metadata: { value: "50" }
```
