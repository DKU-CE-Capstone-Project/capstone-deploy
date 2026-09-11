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

## 0. 로컬/자체서버 실행 (docker compose)

```bash
cd deploy
cp .env.example .env        # MongoDB 계정·비밀번호를 채운다 (openssl rand -base64 24)
docker compose up --build   # nats + redis + mongodb + api + worker(1) + frontend
```
UI: http://localhost:8080  ·  API: http://localhost:8000/health

`GET /health`가 `{"status":"ok","mongodb":"on"}`이면 DB까지 정상이다.

### burst 측정

⚠️ **워커를 스케일하기 전에 `mongodb`를 내린다.** 서버 RAM 8GB 기준으로 worker 10개(≈5GB)와
MongoDB(3GB)를 동시에 띄우면 OOM 난다. worker는 `USE_MONGODB=false`라 DB가 없어도 동작한다.

```bash
docker compose stop mongodb
# (A) 기준선 — worker 1개
docker compose up -d --scale econmind-worker=1 econmind-worker
python loadtest/burst.py --base http://localhost:8000 --n 100
# (B) 스케일 — worker 10개
docker compose up -d --scale econmind-worker=10 econmind-worker
python loadtest/burst.py --base http://localhost:8000 --n 100
# 측정이 끝나면 되돌린다
docker compose up -d --scale econmind-worker=1 mongodb econmind-worker
```

측정 중 `docker stats`로 실사용량을 확인하고 `MEASUREMENTS.md`에 기록할 것.

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

## MongoDB (자체 호스팅 — 영속화 + 벡터검색 / RAG)

> 기존 MongoDB Atlas 무료 클러스터는 미사용 기간이 길어 **삭제**되었다. 이제 `docker compose`의
> `mongodb` 서비스로 직접 운영한다. Atlas 연결 문자열·IP Access List 절차는 더 이상 쓰지 않는다.

### 왜 `mongodb/mongodb-atlas-local` 이미지인가

백엔드(`app/database.py`)가 `$vectorSearch`와 `createSearchIndex`를 쓰는데, **이 둘은 일반
MongoDB Community 서버에 없다.** `mongo:7`을 띄우면 RAG 그라운딩이 통째로 죽는다.
Atlas Local 이미지는 `mongod` + `mongot`(Lucene 검색 프로세스)을 함께 띄워 두 기능을
그대로 제공하므로, 애플리케이션 코드를 고치지 않아도 된다.

### 1. 계정 준비

```bash
cp .env.example .env
# MONGO_ROOT_PASSWORD / MONGO_APP_PASSWORD 를 새로 생성해 채운다
openssl rand -base64 24
```

백엔드는 root가 아니라 **`capstone_news`에만 readWrite 권한을 가진 앱 계정**으로 붙는다
(`mongo-init/00-app-user.js`가 최초 기동 시 생성).

### 2. 스키마 적용

컨테이너 최초 기동 시 `mongo-init/*.js`가 자동 실행되어 **8개 컬렉션 + 인덱스 + JSON Schema
Validator**를 만든다. 기준은 Notion `설계 › 데이터베이스 › 구조크`다.

```
capstone_news
├─ news              ├─ mindmaps (미사용: 쿠키/세션으로 처리)
├─ news_analysis     ├─ reports
├─ news_relations    ├─ strategies
                     ├─ jobs  (미사용: Redis로 구현)
                     └─ users (미사용: 로그인 미구현)
```

MongoDB는 스키마리스라 설계 문서만으로는 구조가 강제되지 않는다. 그래서 validator를 걸어
**설계에서 벗어난 문서는 DB가 거부**하게 했다. 이게 설계-구현 정합화의 핵심 장치다.

스키마를 수정한 뒤 재적용 (스크립트는 멱등이라 언제든 재실행 가능):

```bash
docker compose exec -T mongodb mongosh \
  -u "$MONGO_ROOT_USERNAME" -p "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin \
  --file /docker-entrypoint-initdb.d/01-collections.js
```

완전 초기화가 필요하면 `docker compose down -v` (볼륨까지 삭제 → 다음 기동에 자동 재생성).

### 3. 확인

```bash
curl -s localhost:8000/health                       # {"status":"ok","mongodb":"on"}
docker compose exec -T mongodb mongosh -u ... --quiet \
  --eval 'db.getSiblingDB("capstone_news").getCollectionNames()'
```

검색 후 데이터가 쌓이는지:

```bash
curl -s "localhost:8000/api/v1/news/search?q=엔비디아&size=5" > /dev/null
docker compose exec -T mongodb mongosh -u ... --quiet \
  --eval 'db.getSiblingDB("capstone_news").news.countDocuments()'
```

리포트 생성 시 api 로그에 `[report] RAG grounded with N similar articles`가 찍히고,
`GET /api/v1/reports/{id}` 응답에 `verification`·`rag_sources`가 포함되면 RAG까지 정상이다.

### 4. 벡터 인덱스가 안 만들어질 때

`mongot` 기동이 늦으면 최초 인덱스 생성이 실패할 수 있다. 백엔드가 기동 시 재시도하지만,
그래도 없으면 수동 생성한다:

```bash
docker compose exec -T mongodb mongosh -u ... --quiet --eval '
  db.getSiblingDB("capstone_news").news.createSearchIndex(
    "vector_index", "vectorSearch",
    { fields: [{ type: "vector", path: "embedding", numDimensions: 768, similarity: "cosine" }] }
  )'
```

`numDimensions`는 `app/config.py`의 `embedding_model`(기본 `gemini-embedding-001`) 출력
차원과 반드시 일치해야 한다.

### 5. 메모리 (서버 RAM 8GB 기준)

`mongodb` 서비스에 `mem_limit: 3g`를 걸어 두었다. mongod는 컨테이너 cgroup 한도를 읽어
WiredTiger 캐시를 `(limit − 1GB) × 50%` ≈ 1GB로 잡는다. **캡을 지우면 호스트 RAM 기준으로
3.5GB를 잡아가 나머지 서비스를 밀어낸다.**

`docker stats`로 실사용량을 확인하고, 여유가 1GB 미만으로 떨어지면 `mem_limit`을 조정할 것.

> worker는 burst 재현성 위해 `USE_MONGODB=false`를 유지한다(임베딩/DB 호출이 burst 타이밍에 섞이지 않도록).

> **k8s 매니페스트(`k8s/`)는 아직 Atlas 기준이며 이번 전환에 포함되지 않았다.** `k8s/backend-api.yaml`의
> `MONGODB_URI`는 삭제된 Atlas 클러스터를 가리키므로, k8s로 배포하려면 먼저 갱신해야 한다.

## 백업 트리거 (KEDA nats-jetstream 설정 난항 시)
`k8s/keda-scaledobject.yaml`의 trigger를 CPU 기반으로 교체:
```yaml
triggers:
  - type: cpu
    metricType: Utilization
    metadata: { value: "50" }
```
