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
cp .env.example .env                   # ← 최초 1회. MongoDB 계정을 채운다(아래 「MongoDB」절)
docker compose up --build -d           # nats + redis + mongodb + api + worker(1) + frontend
docker compose ps                      # mongodb 가 healthy 가 될 때까지 대기 (첫 기동 ~2-3분)
```
UI: http://localhost:8080  ·  API: http://localhost:8000/health

burst 부하 실험은 **반드시 burst 오버레이와 함께** 돌린다 — 기본 프로파일로 워커를 10개
띄우면 MongoDB 와 합쳐 8GB 서버에서 OOM 난다. 아래 「⚠ burst 데모와 동시 실행 금지」 참고.

```bash
# (A) 기준선 — 1 worker
python loadtest/burst.py --base http://localhost:8000 --n 100
# (B) 확장 — 10 worker (메모리 조인 오버레이 사용)
docker compose -f docker-compose.yaml -f docker-compose.burst.yaml up -d \
  --scale econmind-worker=10
python loadtest/burst.py --base http://localhost:8000 --n 100
```

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

## MongoDB (자체 호스팅 / Docker — 영속화 + 벡터검색 / RAG)

> **Atlas 는 더 이상 쓰지 않는다.** 미사용 기간이 길어져 무료 클러스터가 삭제됐고,
> 이제 자체 서버에 Docker 컨테이너로 올려 운영한다. 이관할 데이터는 없다(새로 시작).
> Notion「데이터베이스 정보」에 평문으로 남아 있는 옛 Atlas 자격증명은 **절대 재사용하지 않는다.**

### 이미지 선택 — `mongodb/mongodb-atlas-local`

`app/database.py` 는 `$vectorSearch` 와 `create_search_index()` 를 쓴다. 둘 다 **MongoDB
Community 서버에는 없는 Atlas 전용 기능**이라, `mongo:7` 을 띄우면
`app/agents/report_generator.py` 의 `retrieve_rag_articles`(RAG 그라운딩)가 통째로 죽는다.

`mongodb/mongodb-atlas-local` 은 `mongod` + `mongot`(Lucene 기반 검색 프로세스)을 함께
담고 있어 **애플리케이션 코드를 고치지 않고** 벡터검색이 그대로 동작한다. 단일 노드
replica set 으로 자동 구성된다. 태그는 `8.0.28` 로 고정했다(`latest` 금지).

### 준비

```bash
cd deploy
cp .env.example .env      # 값을 채운다. .env 는 .gitignore 에 있다 — 절대 커밋하지 말 것
```

`.env` 에 채울 값 (비밀번호는 **URL 안전 문자만** — `@ : / ? #` 가 들어가면 `MONGODB_URI` 가 깨진다):

```bash
# 예시 — 실제 값은 각자 생성해서 .env 에만 둔다
openssl rand -base64 24 | tr -d '/+=' | head -c 32   # 비밀번호 생성기
```

| 키 | 용도 |
|---|---|
| `MONGODB_ROOT_USERNAME` / `_PASSWORD` | root 계정. 운영·점검용이며 **앱에는 주지 않는다** |
| `MONGODB_APP_USERNAME` / `_PASSWORD` | 앱 계정. `capstone_news` 에만 `readWrite` |
| `MONGODB_DB_NAME` | `capstone_news` (고정) |
| `MONGODB_WT_CACHE_MB` | WiredTiger 캐시 상한. 8GB 서버 기준 `1024` |

`MONGODB_URI` 는 compose 가 조립한다 → `mongodb://<app>:<pw>@mongodb:27017/capstone_news?authSource=admin`
(앱 계정을 `admin` DB 에 만들기 때문에 `authSource=admin` 이다.)

### 기동

```bash
docker compose up -d          # nats + redis + mongodb + api + worker + frontend
docker compose ps             # mongodb 가 healthy 가 될 때까지 기다린다(첫 기동 ~2-3분: 이미지 1.2GB pull + replica set 구성 + 초기화 스크립트)
curl -s localhost:8000/health # {"status":"ok","mongodb":"on"}
```

`mongodb-tuner` 가 `Exited (0)` 로 보이는 건 **정상**이다 — 기동 때 한 번 실행되고 끝나는
서비스다(아래 「메모리」 참고). 단, `docker compose restart mongodb` 처럼 **MongoDB 만**
따로 재시작했다면 캡이 풀린 상태이므로 튜너를 다시 돌려야 한다:

```bash
docker compose up -d --force-recreate mongodb-tuner
```

### DB 초기화 — 8개 컬렉션 + JSON Schema Validation

`mongo-init/` 이 `/docker-entrypoint-initdb.d` 로 마운트돼 **최초 기동 시 1회** 알파벳 순으로 실행된다.

| 스크립트 | 하는 일 |
|---|---|
| `01-app-user.js` | 앱 전용 계정 생성 (`readWrite@capstone_news`) |
| `02-collections.js` | 8개 컬렉션 + `$jsonSchema` validator |
| `03-indexes.js` | 인덱스 (`uniq_news_url`, `idx_news_keywords`, … 명명 규칙 준수) |
| `04-vector-index.js` | `news.embedding` 벡터검색 인덱스 (768d, cosine) |

컬렉션은 Notion「구조크」의 최종 목록 8개 그대로다:
`news` · `news_analysis` · `news_relations` · `mindmaps` · `reports` · `strategies` · `jobs` · `users`.

- `mindmaps` 는 12주차 회의 결정("DB 미사용, 쿠키 사용")에 따라 **만들되 비워 둔다.**
- `users` 는 로그인 미구현이라 마찬가지로 비어 있다.

**validator 엄격도** — 지금은 두 단계로 나눠 뒀다:

| 컬렉션 | `validationAction` | 이유 |
|---|---|---|
| `news` `news_analysis` `news_relations` `mindmaps` `jobs` `users` | `error` | 아무도 아직 쓰지 않는 신규 컬렉션이라 바로 강제해도 안전 |
| `reports` `strategies` | `warn` | 현재 백엔드가 설계와 다른 형태로 쓴다. `error` 로 두면 `save_report`/`save_strategy` 가 조용히 실패한다 |

백엔드 정합화가 끝나면 승격한다:

```bash
docker compose exec -T -e HOME=/tmp mongodb sh -c \
  'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
     --quiet --file /scripts/strict-validation.js'
```

> 명령이 컨테이너 **안의** 환경변수를 쓴다 — 호스트 셸에 비밀번호를 꺼내지 않는다.
> (`$MONGODB_INITDB_ROOT_*` 는 mongodb 컨테이너에 이미 들어 있다. 홑따옴표를 유지할 것)

### 검증 / 시드

```bash
# 8개 컬렉션·인덱스·validator·벡터인덱스·메모리 자가 점검
docker compose exec -T -e HOME=/tmp mongodb sh -c \
  'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
     --quiet --file /scripts/verify-db.js'

# 데모용 시드 8건 (fixtures/news_mock.json → news 스키마 변환)
docker compose exec -T -e HOME=/tmp mongodb sh -c \
  'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
     --quiet --file /scripts/seed-news.js'
```

`verify-db.js` 는 일부러 설계 위반 문서(`source` 를 문자열로)를 넣어 보고
`code 121`(DocumentValidationFailure)로 거부되는지까지 확인한다.

> 시드 문서에는 `embedding` 이 없다(Gemini 임베딩이 필요). RAG 벡터검색을 보려면
> `/api/v1/news/search?q=...` 로 앱이 수집·임베딩하게 해야 한다. 리포트 생성 시 api 로그에
> `[report] RAG grounded with N similar articles` 가 찍히면 동작한 것이다.

### 메모리 — 8GB 서버라 상한이 필수다

캡 없이 띄우면 안 된다. WiredTiger 캐시 기본값은 `(RAM − 1GB) × 50%` 라 **7.7GB 서버에서
mongod 혼자 ~3.3GB** 를 잡는다. 여기에 mongot JVM 까지 붙으면 나머지 서비스가 OOM-kill 된다.

적용한 것:

1. **`mem_limit`** — 모든 서비스에 걸었다(mongodb 3g / api 768m / worker 512m / nats·redis·frontend 128m).
2. **WiredTiger 캐시 1GB** — `mongodb-tuner` 서비스가 매 기동마다 런타임으로 건다.

> **왜 `--wiredTigerCacheSizeGB` 플래그가 아닌가**: 이 이미지는 CMD 가
> `/usr/local/bin/runner server` 이고 그 runner 가 mongod + mongot 을 함께 띄운다.
> MongoDB 공식 문서가 *"you must remove any existing `command` for the mongodb-atlas-local
> image to run as designed"* 라고 못 박는다 — compose 에서 `command:` 로 플래그를 넘기면
> mongot 이 안 뜨고 벡터검색이 죽는다. runner 는 mongod 플래그 패스스루도 제공하지 않는다.
> 그래서 같은 효과를 `setParameter: wiredTigerEngineRuntimeConfig` 로 낸다.
> 초기화 스크립트가 아니라 **별도 서비스**인 이유는, `/docker-entrypoint-initdb.d` 는 최초
> 기동 1회만 돌아서 **재시작하면 캡이 풀리기** 때문이다. 자세한 건 `scripts/tune-memory.js` 주석에.

**예산 vs 실측** — 2026-09-10, RAM 7.7GB 서버, 전체 스택 기동 후 측정:

| 컨테이너 | 상한 | 실측 (`docker stats`) | |
|---|---|---|---|
| **mongodb** (mongod + mongot) | 3.0 GB | **606 MiB** (19.7%) | ✅ |
| econmind-api | 768 MB | **66.8 MiB** (8.7%) | ✅ |
| econmind-worker × 1 | 512 MB | **37.1 MiB** (7.2%) | ✅ |
| nats | 128 MB | 18.5 MiB (14.4%) | ✅ |
| redis | 128 MB | 5.0 MiB (3.9%) | ✅ |
| frontend | 128 MB | 20.0 MiB (15.6%) | ✅ |
| **컨테이너 합계** | 4.6 GB | **~754 MiB** | |
| 호스트 전체 | 7.7 GB | 사용 2.6 GB / **가용 5.1 GB** | ✅ |

- WiredTiger 캐시: **1024 MB** (`maximum bytes configured`)
- 전 컨테이너 `OOMKilled=false`, `RestartCount=0`
- 실사용이 상한보다 훨씬 낮다 — 상한은 폭주 방지용이지 예약이 아니다.
  특히 mongod 는 WiredTiger 캐시를 지연 할당하므로 데이터가 늘면 최대 ~1GB+ 까지 오른다.

> **실측으로 확인된 것**: mongod 가 cgroup 상한을 스스로 읽는다 —
> `hostInfo.system.memLimitMB=3072` (호스트 `memSizeMB=7879` 이 아니라)를 보고
> `(3072−1024)×50% = 1024MB` 를 기동 시점에 계산해 적용했다.
> `docker compose down` → `up` 후에도 **튜너 없이** 1024MB 가 유지됐다.
> 즉 `mongodb-tuner` 는 지금 환경에선 **중복 안전장치**다. MongoDB 문서가
> *"WiredTiger may not account for the memory limits of the specific container in
> certain cases"* 라고 경고하므로 남겨 두지만, 이 서버에선 `mem_limit` 만으로도 캡이 걸린다.

측정 방법 (스택 기동 후 10분 이상 두고 나서):

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"
free -h                                        # 여유 1GB 이상 남아야 한다
docker compose ps mongodb                      # 재시작 카운트 0 확인
docker inspect --format '{{.State.OOMKilled}} {{.RestartCount}}' $(docker compose ps -q mongodb)
```

### ⚠ burst 데모와 동시 실행 금지

`MEASUREMENTS.md` 의 부하 실험은 워커를 10개까지 띄운다. 기본 프로파일의 worker 상한
512MB × 10 = 5GB 에 MongoDB 3GB 를 더하면 8GB 서버에서 확실히 OOM 난다.
**반드시 burst 오버레이를 함께 쓸 것** (worker 1개당 200MB → 10개 총 2GB, mongodb 1GB):

```bash
docker compose -f docker-compose.yaml -f docker-compose.burst.yaml up -d \
  --scale econmind-worker=10
python loadtest/burst.py --base http://localhost:8000 --n 50
```

가장 깨끗한 측정을 원하면 MongoDB 를 아예 빼고 돌린다:

```bash
docker compose -f docker-compose.yaml -f docker-compose.burst.yaml up -d --no-deps \
  --scale econmind-worker=10 nats redis econmind-api econmind-worker
```

### 영속화 / 백업

named volume 3개를 쓴다. `docker compose down` 후 다시 `up` 해도 데이터가 남는다.

| 볼륨 | 경로 | 내용 |
|---|---|---|
| `mongodb-data` | `/data/db` | 문서 데이터 |
| `mongodb-config` | `/data/configdb` | replica set / mongot 설정 |
| `mongodb-mongot` | `/data/mongot` | 벡터·검색 인덱스 (빼면 재기동마다 리빌드) |

```bash
docker compose down            # 볼륨은 유지된다
docker compose down -v         # ⚠ 볼륨까지 삭제 — 데이터가 사라진다
```

백업:

```bash
docker compose exec -T mongodb sh -c \
  'mongodump --uri "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/capstone_news?authSource=admin" --archive' \
  | gzip > backup-$(date +%F).gz
```

### 접근 / 보안

- **27017 을 호스트에 노출하지 않는다.** 같은 compose 네트워크에서 `mongodb:27017` 로만 접근한다.
  포트를 열면 인증 하나만 믿고 DB 를 인터넷에 내놓는 셈이다.
- 밖에서 봐야 하면 SSH 터널을 쓴다:
  ```bash
  ssh -N -L 27017:localhost:27017 <user>@<server>   # 서버 쪽에서 포트를 열지 않아도 된다
  ```
- 자격증명은 `.env` 에만 둔다. Git·Notion 어디에도 적지 않는다.
- 앱 계정에는 `readWrite@capstone_news` 만 준다(root 아님).
  이 롤에 `createSearchIndexes` / `listSearchIndexes` 가 포함돼 있어
  `ensure_vector_index()` 는 그대로 동작한다.

> **k8s 매니페스트는 아직 Atlas 기준이며 미갱신이다.** `k8s/backend-api.yaml` 은 여전히
> `USE_MONGODB=true` + Secret 의 `MONGODB_URI`(Atlas `mongodb+srv://`)를 전제로 한다.
> 이번 작업 범위는 docker-compose 뿐이라 `k8s/` 는 손대지 않았다.

## 백업 트리거 (KEDA nats-jetstream 설정 난항 시)
`k8s/keda-scaledobject.yaml`의 trigger를 CPU 기반으로 교체:
```yaml
triggers:
  - type: cpu
    metricType: Utilization
    metadata: { value: "50" }
```
