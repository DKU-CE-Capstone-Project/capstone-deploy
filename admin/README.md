# EconMind 관리자 서비스

`capstone-deploy/admin`에서 독립 실행하는 FastAPI + 정적 UI다. Cloud Run IAM으로 보호하고,
런타임 서비스 계정에는 대상 프로젝트의 `roles/logging.viewer`, `roles/monitoring.viewer`만 부여한다.
사용량 로그에 키·본문·프롬프트를 기록하지 않는다. DB 계정·URI는 관리자 서비스에 전달하지 않는다.

메뉴는 운영 현황(`#overview`), 외부 API 사용량(`#external-api`), 서비스 API 요청(`#service-api`),
비용(`#cost`)으로 나눈다. URL의 탭을 새로고침·뒤로 가기에서 유지하며 키보드 방향키로도 전환한다.
조회 기간은 두 API 탭에 표시한다. 비용은 `준비 중` 화면만 제공하고 해당 탭에서는 자동 조회를 멈춘다.

## 로컬 실행과 테스트

```sh
cd admin
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8090 --no-access-log
```

환경변수 미설정 시 실제 외부 서비스 호출 없이 설정 대기 화면을 제공한다.
실제 GCP 조회에는 `environment.example.yaml`의 세 환경변수와 ADC가 필요하다.
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
