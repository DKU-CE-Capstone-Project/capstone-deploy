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

## 1-A. 로컬 kind 배포 (✅ 검증 완료 — KEDA 자동확장 + Grafana 확인)

VM 없이 Mac의 Docker 위에서 실제 K8s를 띄워 동일하게 검증. 매니페스트는 VM k3s에 1:1 이전 가능.

```bash
brew install kind helm
kind create cluster --name econmind

# CNCF 컴포넌트
helm repo add nats https://nats-io.github.io/k8s/helm/charts/
helm repo add kedacore https://kedacore.github.io/charts
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install nats nats/nats --set config.jetstream.enabled=true --set config.monitor.enabled=true
helm install keda kedacore/keda -n keda --create-namespace --wait
helm install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
  --set alertmanager.enabled=false --set grafana.adminPassword=econmind --wait

# 이미지 빌드 → kind로 로드 (레지스트리 불필요)
docker build -t ghcr.io/dku-ce-capstone-project/econmind-backend:demo ../backend
docker build --build-arg VITE_API_BASE="" -t ghcr.io/dku-ce-capstone-project/econmind-frontend:demo ../frontend
kind load docker-image ghcr.io/dku-ce-capstone-project/econmind-backend:demo --name econmind
kind load docker-image ghcr.io/dku-ce-capstone-project/econmind-frontend:demo --name econmind

# 배포 (ingress는 kind에 Traefik 없으니 제외 → port-forward 사용)
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/redis.yaml -f k8s/backend-api.yaml -f k8s/backend-worker.yaml -f k8s/frontend.yaml -f k8s/keda-scaledobject.yaml

# 접근
kubectl -n econmind port-forward svc/econmind-api 8000:8000 &      # API/burst
kubectl -n monitoring port-forward svc/kps-grafana 3000:80 &       # Grafana (admin/econmind)
```

burst + KEDA 자동확장 관찰:
```bash
kubectl -n econmind get pods -w -l app=analysis-worker &   # worker 1→N 자동 생성 관찰
python loadtest/burst.py --base http://localhost:8000 --n 60
kubectl -n econmind get hpa     # KEDA가 NATS lag 측정: 0/5 (avg)
```
Grafana Explore PromQL: `kube_deployment_status_replicas{namespace="econmind",deployment="analysis-worker"}`

> **NATS 모니터링 포트(8222)는 `nats-headless` 서비스에 노출**됨 → ScaledObject의 `natsServerMonitoringEndpoint`는 `nats-headless.default.svc.cluster.local:8222`.

## 1-B. VM + k3s + Helm

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

## MongoDB Atlas (영속화 + 벡터검색 / RAG)

1. **Atlas Network Access**: 클러스터를 호출하는 머신/노드의 공인 IP를 IP Access List에 추가(데모는 `0.0.0.0/0`). 미등록 시 `TLSV1_ALERT_INTERNAL_ERROR`로 연결 거부됨.
2. **시크릿 주입**: `kubectl -n econmind create secret generic api-keys ... --from-literal=MONGODB_URI='mongodb+srv://<user>:<pw>@cluster0.h8e6cfn.mongodb.net/capstone_news?retryWrites=true&w=majority&appName=Cluster0'` (기존 GOOGLE_API_KEY/NEWSAPI_KEY와 함께). 비밀번호는 절대 매니페스트/깃에 넣지 말 것.
3. **활성화**: `econmind-api`에 `USE_MONGODB=true`(매니페스트 반영됨). `GET /health` → `{"mongodb":"on"}` 확인.
4. **벡터 인덱스**: 앱 startup이 `articles.embedding`(768d, cosine) `vectorSearch` 인덱스를 자동 생성 시도. 실패 시 **Atlas UI 수동 생성**:
   - Atlas → Cluster0 → Atlas Search → Create Search Index → JSON Editor → Vector Search
   - DB `capstone_news`, Collection `articles`, 이름 `vector_index`:
   ```json
   { "fields": [ { "type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine" } ] }
   ```
5. **확인**: `nvidia` 검색 → Atlas Browse Collections `capstone_news.articles`에 문서 + `embedding`(768) 존재. 리포트 생성 시 api 로그에 `[report] RAG grounded with N similar articles`, `GET /reports/{id}` 응답에 `verification`·`rag_sources`.

> worker는 burst 재현성 위해 `USE_MONGODB=false` 유지(임베딩/DB 호출이 burst 타이밍에 영향 X).

## 백업 트리거 (KEDA nats-jetstream 설정 난항 시)
`k8s/keda-scaledobject.yaml`의 trigger를 CPU 기반으로 교체:
```yaml
triggers:
  - type: cpu
    metricType: Utilization
    metadata: { value: "50" }
```
