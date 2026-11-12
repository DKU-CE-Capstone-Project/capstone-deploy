# EconMind 관리자 서비스

`capstone-deploy/admin`에서 독립 실행하는 FastAPI + 정적 UI다. Cloud Run IAM으로 보호하고,
런타임 서비스 계정에는 대상 프로젝트의 `roles/logging.viewer`, `roles/monitoring.viewer`를 부여한다.
비용 조회를 연결하면 아래에 설명한 BigQuery 작업 실행 권한과 전용 데이터셋 읽기 권한도 추가한다.
사용량 로그에 키·본문·프롬프트를 기록하지 않는다. DB 계정·URI는 관리자 서비스에 전달하지 않는다.

메뉴는 운영 현황(`#overview`), 외부 API 사용량(`#external-api`), 서비스 API 요청(`#service-api`),
비용(`#cost`)으로 나눈다. URL의 탭을 새로고침·뒤로 가기에서 유지하며 키보드 방향키로도 전환한다.
조회 기간은 두 API 사용량 탭에 표시한다. 비용 탭은 GCP와 외부 API 각각
`누적 / 하루 / 7일 / 한 달`을 선택하며 기본은 누적이다. 하루는 최근 24시간, 7일은 최근 7일,
한 달은 한국 시간 이번 달 1일부터 현재까지다. 누적은 각 측정 시작 시각부터이며,
짧은 기간도 측정 시작 이전으로 확장하지 않는다. 화면에 고정 시작 시각과 실제 조회 범위를 표시한다.
`/api/costs?gcp_period=all&external_period=all`은 조합별 5분 캐시한다.
GCP 요약과 서비스별 표에는 사용 크레딧만 표시한다. Billing의 음수 credits를 양수 사용액으로
표현하며, 양수 정정·복원은 음수 사용액으로 반영한다. 미수집과 실제 0원을 구분한다.

## GCP 없이 Docker Compose로 실행

배포 저장소 루트에서 실행한다. [Docker Engine 28 이상](https://docs.docker.com/engine/network/port-publishing/)과
[Docker Compose 2.24.4 이상](https://docs.docker.com/reference/compose-file/merge/#replace-value)을 사용한다.

```sh
docker compose --env-file /dev/null -p econmind-monitor-local -f compose.monitoring.yaml up -d --build
```

관리자 페이지는 <http://127.0.0.1:8090>에서 연다. 서버에서 실행하는 경우 SSH 포트 포워딩으로
접속한다. 로컬 모드는 IAP 인증이 없으므로 관리자·로그 수신 포트를 루프백에 바인딩한다.
`MONITOR_ADMIN_PORT`, `MONITOR_FORWARD_PORT`(기본 `24224`), `MONITOR_HOST_NAME`으로 설정을 조정한다.

- `host-metrics`는 Docker Engine이 실행되는 Linux의 CPU 전체 코어와 메모리를 `/proc`에서 읽고,
  Docker 데이터 볼륨의 파일시스템 용량을 수집한다. Docker Desktop에서는 Linux VM 기준이다.
  메모리 사용량은 `MemTotal − MemAvailable`, 디스크 사용량은 전체 블록에서 자유 블록을 뺀 값이다.
  10초마다 읽으며 최근 30분만 메모리에 보관한다. 수집기 재시작 후 지표 이력이 다시 쌓인다.
- `usage-forwarder`는 Docker의 Fluentd 드라이버로 들어온 `econmind_usage` 로그를 전달한다.
  `usage-store`는 집계에 쓰이는 필드만 `monitor_usage` SQLite 볼륨에 저장하고 `event_id`로 중복을 제거한다.
  누적 시작 시각은 가장 이른 보관 이벤트 기준이다. 이전 시각의 이벤트가 늦게 도착하면 함께 집계한다.
  `docker compose down` 후에도 볼륨을 보존하면 누적 기록이 유지된다. 로그 전달은 비동기이며,
  수집기 중단이나 버퍼 포화 시 미저장 로그가 유실될 수 있다. 원격 백업·이전은 별도로 관리한다.
- `admin`은 수집기 두 곳을 따로 조회한다. 한쪽 조회가 실패해도 다른 쪽 데이터를 표시한다.
  로컬 비용 탭은 수집된 외부 API의 예상액과 기간별 합계를 표시한다. GCP 청구 내역은 표시하지 않는다.

앱 사용량과 DB 상태를 연결하려면 **새 서버 또는 로컬 앱**의 기존 Compose에 두 모니터링 파일을
덧붙이고 앱을 기동한다. 아래 예는 로컬 `docker-compose.yaml`과 함께 실행하는 경우다.
앱에 필요한 로컬 `.env`를 준비한다. 아래 빌드 경로는 현재 프로젝트 체크아웃 기준이다.
같은 포트를 쓰는 독립 모니터링 스택이 있으면 먼저 내리거나 포트를 변경한다.

```sh
BACKEND_BUILD_CONTEXT=../capstone-backend FRONTEND_BUILD_CONTEXT=../capstone-frontend \
  docker compose -f docker-compose.yaml -f compose.monitoring.yaml -f compose.monitoring-app.yaml up -d --build
```

오버레이는 `econmind-api`, `econmind-worker`, `econmind-v2-worker`의 로그 드라이버를 설정한다.
로그 드라이버 변경에는 해당 앱 컨테이너의 재생성이 필요하다. 다른 앱을 구성할 때는 동일한 서비스
이름 또는 그에 맞춘 오버레이를 사용한다. 앱 없이 기동하면 호스트 지표부터 표시된다.
MongoDB 카드는 앱과 관리자에 같은 32자 이상의 `ADMIN_STATUS_TOKEN`을 주입했을 때 활성화된다.
관리자는 사설 Compose 네트워크에서 백엔드 상태 엔드포인트의 연결 결과만 조회한다.

## 비용 데이터 연결

- `GCP_COST_MEASUREMENT_START`와 `API_COST_MEASUREMENT_START`는 시간대가 있는 고정 ISO 시각이다.
  현재 운영 기준은 GCP `2026-11-01T00:00:00+09:00`, API `2026-11-02T18:00:00+09:00`이다.
  시작 시각을 바꾸면 누적 정의가 바뀌므로 월이 바뀌거나 재배포한다고 갱신하지 않는다.
- API 누적 기록은 US의 `econmind_cost_history` 데이터셋에 보관한다. `econmind-cost-history`
  Logging sink는 대상 VM의 `econmind_usage`만 partitioned table `econmind_docker`로 자동 저장한다.
  Logging writer identity에 **이 데이터셋만 WRITER**, 관리자 런타임에 **이 데이터셋만 READER**를 부여한다.
  원문·프롬프트·키를 보관하지 않으며 기존 Logging 보관 기간이 지나도 누적 카운터를 유지한다.
- sink 생성 이전 관측 기록을 `usage_backfill`(event_time TIMESTAMP, received_at TIMESTAMP,
  payload JSON, event_time 일별 partition)에 넣고 `usage-history-view.sql`의 대상 프로젝트·VM을
  치환해 `usage_events` view를 만든다. `GCP_USAGE_HISTORY_VIEW`에 이 전체 view ID를 설정한다.
  원본과 backfill의 중복은 event_id로 제거하고, 집계는 가격 결정 필드별 그룹 수를 곱해 계산한다.
  최근 24시간 로그를 함께 조회해 sink 지연을 보완하고 해당 ID는 archive 집계에서 제외한다.
  조회당 최대 128 MiB와 10,000그룹/10,000최근 로그로 제한하며 상한·권한 오류는 일부 산정으로 표시한다.
  신규 저장·조회 사용료가 발생할 수 있다. 전체 기록과 달리 계측 이전 사용량은 복원하지 않는다.

- Cloud Billing **표준 사용량 비용**을 전용 BigQuery 데이터셋에 내보낸다. 신규 US/EU 멀티 리전
  내보내기는 이전 달까지 소급하며 초기 데이터에 최대 며칠이 걸릴 수 있다. 런타임에는
  `GCP_BILLING_TABLE`(전체 테이블 ID), `GCP_BILLING_LOCATION`을 설정한다.
- 관리자 서비스 계정에는 프로젝트의 `roles/bigquery.jobUser`와 **해당 데이터셋만** `READER`
  권한을 부여한다. Billing 계정 관리자 권한이나 서비스 계정 키는 필요하지 않다.
  쿼리는 대상 `project.id`와 선택한 기간으로 제한하고, 요청당 최대 128 MiB를 스캔한다.
  크레딧은 행마다 합산한 뒤 사용료에 더해 비용이 중복 집계되지 않도록 한다.
- 첫 내보내기 전에는 검증한 보고서 CSV의 집계를 `GCP_BILLING_SNAPSHOT_JSON`으로 주입할 수 있다.
  이 값은 Git에 넣지 않는다. 화면에 보고서 기준·확인 시각·원 단위 반올림을 표시하고,
  다른 프로젝트·다른 달의 스냅샷은 사용하지 않는다. BigQuery 내역이 도착하면 자동 전환한다.
  월간 스냅샷을 하루 등 더 짧은 기간의 값으로 재사용하지 않는다. 해당 기간 내보내기가
  아직 없으면 금액 대신 수집 대기를 표시한다.
  미수집·권한 오류는 0원이 아니다. 계정 공통 요금·세금은 제외되며 확정 청구서와 다를 수 있다.

공개 단가 확인일은 **2026-11-02**다. `pricing.py`는 이벤트별 모델·작업·처리 티어·토큰으로
USD 예상액을 계산하며, 관측되지 않은 토큰이나 등록되지 않은 모델은 미산정으로 남긴다.

- [Gemini 요금](https://ai.google.dev/gemini-api/docs/pricing): `gemini-3.5-flash-lite` Standard
  입력/출력·사고/캐시 $0.30/$2.50/$0.03, Flex $0.15/$1.25/$0.02 (1M 토큰당).
  입력의 캐시 부분은 분리하고 출력·사고 토큰은 한 번만 더한다. `gemini-embedding-001`은
  입력 $0.15/1M이며 동기 `embed_batch`를 Batch API 할인으로 처리하지 않는다.
  임베딩 응답 사용량이 없으면 동일 모델의 무료 `countTokens`로 실제 전송 텍스트를 계수한다.
  응답 토큰과 계수 토큰을 구분해 표시하며, 계수 기반 비용은 내부 토큰 등 청구량과 다를 수 있다.
  입력별 2,040토큰 초과로 잘림 가능성이 있거나 계수 오류·시간 초과가 있으면 미산정이다.
  기존 미계측 호출의 토큰은 복원하지 않으며, 무료 계수 요청은 추론 호출 수에 더하지 않는다.
- [OpenAI Decisions 요금](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability):
  `gpt-6-luna` 입력 $0.10/1M, 출력 무료. 캐시 읽기 $0.01/1M, 기본 쓰기 $0.125/1M이며
  272K 초과 입력은 장문 배율을 적용한다. 캐시 세부정보가 없으면 일반 입력 단가를 사용한다.
- [Claude 요금](https://platform.claude.com/docs/en/about-claude/pricing): `claude-opus-4-8`
  입력 $5/출력 $25, 캐시 읽기 $0.50/현재 코드의 기본 5분 쓰기 $6.25 (1M 토큰당).
- [Diffbot 요금](https://www.diffbot.com/pricing/): 사용자가 확인한 무료·학생 요금제는
  `DIFFBOT_COST_PLAN=free_academic`으로 적용한다. 계정별 한도·다른 프로젝트 사용량은 제외한다.
  요금제가 바뀌면 `unknown`으로 설정하고 새 계약 기준을 반영한다.
- [NAVER 검색 API 정책](https://developers.naver.com/notice/article/32530): 현재 사용하는
  기존 검색 API의 기본 무료 정책을 적용한다.

앱 캐시 재사용에는 외부 호출 비용을 더하지 않는다. LLM 무료량·계약 할인·세금·환율과
SDK 내부 재시도·계측 이전/누락 사용량은 반영하지 않는다. 단가 확인일과 미산정 건수를 표시하며,
GCP 청구에 Gemini 비용이 포함될 수 있으므로 GCP 금액과 API 예상액을 합산하지 않는다.

## 로컬 실행과 테스트

```sh
cd admin
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8090 --no-access-log
```

환경변수 미설정 시 실제 외부 서비스 호출 없이 설정 대기 화면을 제공한다.
실제 GCP 조회에는 `environment.example.yaml`의 해당 기능 환경변수와 ADC가 필요하다.
개발 시에도 운영 DB 대신 별도 로컬 stub으로 연결 상태를 검증한다. `BACKEND_STATUS_URL`은
고정한 HTTPS 주소만 허용하고 리다이렉트하지 않는다. 브라우저가 URL이나 조회 쿼리를 지정할 수 없다.

## 배포 준비 (자동 실행하지 않음)

1. 백엔드 브랜치의 API·v1/v2 워커를 같은 버전으로 빌드한다. 배포 `.env`에
   `ADMIN_STATUS_TOKEN`을 32자 이상의 무작위 값으로 넣고 같은 값을 Secret Manager에 저장한다.
   이 값은 DB 비밀번호와 별개이며 로그·Git에 넣지 않는다. API는 미설정·짧은 토큰·인증 실패에 403을 반환한다.
2. 변경된 Caddyfile을 반영한다. `/internal/admin/status` 하나만 별도 bearer 인증으로 API에 전달한다.
   다른 UI/API의 팀 Basic 인증은 유지된다. 이 경로는 Mongo ping 결과만 반환한다.
3. `monitoring/ops-agent-config.yaml`을 기존 사용자 설정과 비교해 VM에 적용한다. 공식 엔진의
   `-in /etc/google-cloud-ops-agent/config.yaml`로 검증 후 에이전트를 시작한다.
   **현재 VM 서비스 계정 연결과 홈 서버 역방향 SSH 터널 복구 준비가 선행돼야 한다.** 이 저장소 변경은
   VM 정지·시작이나 기존 터널 변경을 실행하지 않는다. writer 권한과 관리자 reader 권한을 분리한다.
4. Cloud Run·Artifact Registry·Secret Manager API, 이미지 저장소와 전용 런타임 계정을 준비한다.
   Secret Manager 접근자는 해당 토큰 secret에만 `roles/secretmanager.secretAccessor`를 받는다.
   VM의 숫자 ID를 `gcloud compute instances describe instance-econmind --project
   project-cdd9163e-eb08-4280-824 --zone asia-northeast3-c --format='value(id)'`로 확인한다.
5. Docker 이미지 빌드: `docker build --platform linux/amd64 -t "$ADMIN_IMAGE" admin` (배포 저장소 루트).
   이미지를 push한 후 환경 YAML을 검토하고 `admin/deploy.sh`를 명시적으로 실행한다.
   이 스크립트는 공개 접근을 허용하지 않는다. `roles/run.invoker`를 지정 사용자에게만 부여하고
   `gcloud run services proxy econmind-admin --project "$GCP_PROJECT_ID" --region asia-northeast3`로 접속한다.
   브라우저 직접 로그인은 별도 IAP 설정이 필요하다. 서비스의 공개 IAM 바인딩이 없어야 한다.

## 집계 의미와 한계

- 서비스 요청 / HTTP 요청 / SDK 호출 / 캐시 재사용은 별도 값이다. SDK 내부 재시도는 미관측이다.
  캐시 재사용 횟수는 캐시 계층별 이벤트 수이며 절감된 HTTP 요청 수로 환산하지 않는다.
- 에이전트 응답 진단(`agent_output_validation` 응답 형식 오류, `agent_output_normalized` 논거 초과분 제외)은
  외부 호출이 아니므로 호출 수·토큰·비용에 더하지 않는다. 단계·작업·오류 유형별 건수, 제외한 논거 수,
  오류 필드 경로와 검증 유형 코드만 보관하고 응답 본문은 보관하지 않는다. 외부 API 사용량 탭과
  리포트 작업별 조회에 표시한다. SQLite 수집기는 이 버전을 반영한 뒤 수신한 진단부터 보관한다.
- Gemini 입력에는 캐시 입력이 포함되므로 다시 더하지 않는다. 사고 토큰·cache 생성/읽기도 별도 필드다.
  Anthropic 입력과 cache 읽기/생성의 의미는 Gemini와 다르다. 누락 토큰은 `미제공`, 일부만 있으면 `일부`다.
- Cloud Logging의 `event_id`로 전송 중복을 제거한다. 워커 재실행에서 실제 발생한 호출은 별도 이벤트로 센다.
  request/job/run ID는 이벤트 상관관계용이며 지표 라벨로 쓰지 않는다. 세션 ID·원문 URL은 기록하지 않는다.
- 조회는 대상 프로젝트·VM·기간(1~168시간)으로 제한하고 최대 10페이지/10,000개다. 한도에 도달하면
  전체 합계로 표시하지 않는다. 30초 캐시, UI 60초 갱신. VM 지표는 최근 30분과 실제 관측 시각을 보여 준다.
- VM CPU·메모리·디스크의 최신 사용률은 원형 안에 `%`로 표시한다. CPU·메모리는 카드 호버,
  키보드 포커스 또는 원형 클릭으로 최근 30분 막대그래프를 연다. 세로축은 0~100%로 고정하고,
  조회 시각 기준 30분 전·현재 시각을 표시한다. 1분 구간의 마지막 관측값을 사용하며 미수집 구간은 공백이다.
- 메모리·디스크에는 `사용 GiB / 전체 GiB`를 함께 표시한다(`1 GiB = 2^30 bytes`). Ops Agent
  `memory/bytes_used`, `disk/bytes_used`의 같은 시각·장치에 대한 모든 상태를 합산해 전체 용량을 구하고,
  `used` 상태를 사용량으로 삼아 원형 사용률도 계산한다. 디스크 전체에는 예약 영역을 포함한다.
  메모리 전체는 OS 수집 기준이며, 디스크는 사용률이 가장 높은 장치의 이름과 용량을 표시한다.
  일부 상태가 누락되면 해당 시각의 용량과 사용률을 만들지 않는다.
- 로그 보존 기간·수집 지연·프로세스 종료·stdout 전송 실패로 누락될 수 있다. 청구 정산 장부가 아니며
  실제 공급자 계정 사용량/비용과 다를 수 있다. 계측 배포 이전 내역은 복원되지 않는다.
- DB `연결 불가`는 백엔드 ping 실패, `확인 불가`는 상태 API 조회 실패다. DB 전원 상태·원인은 추정하지 않는다.

정본 문서 반영 대상은 `econmind-docs/docs/02-implementation.md`, `docs/03-current-state.md`,
`docs/99-verification.md`이며 이 코드 작업에서 정본 문서를 변경하지 않는다.
