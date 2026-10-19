---
title: GCP 앱 업데이트 배포 및 검증 기록
updated: 2026-10-19
status: 앱 배포·검증 완료 · DB 유지보수 별도
---

# GCP 앱 업데이트 배포 및 검증 (2026-10-19)

기준일은 **2026-10-19 Asia/Seoul**이다. 사용자의 배포 요청에 따라 서버 `/home/econmind/capstone/{backend,frontend,deploy}`를 아래 main 커밋까지 fast-forward하고 API·두 워커·프론트엔드를 교체했다. 문서 작성 시 서버 Git SHA·실행 이미지·재시작 횟수와 `/ready`를 다시 확인했다. 프로젝트 `econmind`, 기존 `.env`, MongoDB 연결과 Redis·NATS·인증서 볼륨은 유지했다.

| 저장소 | 배포 전 | 서버 체크아웃·앱 빌드 기준 |
|---|---|---|
| 백엔드 | `59bc4b0` | `89dfb97` — API·v1/v2 워커 공용 이미지 |
| 프론트엔드 | `61adc29` | `b50e762` |
| 배포 | `e4ca7eb` | `96ff086` — 이번 pull의 차이는 DB 유지보수 문서 추가 |

후속 백엔드 main **`55b9dc1`은 MongoDB 테스트의 기대값만 수정**한 커밋이다. 통합 검사 때 사용한 수정과 동일하며 commit·push를 완료했다. 운영 체크아웃과 앱 이미지는 `89dfb97` 기준이고 `55b9dc1`로 다시 빌드하지 않았다. 이 기록을 추가하는 문서 PR도 앱/Compose 변경이나 추가 배포를 포함하지 않는다.

## 적용한 변경

- 프론트: 뉴스맵 생성·확장 대기의 경과 시간별 문구, 최초 생성 취소, 카드별 대기 표시, 429의 `Retry-After` 안내, 맵 revision이 바뀐 뒤 리포트 재시도 처리.
- 백엔드: 검색·맵 quota 동시 예약, 429 재시도 헤더/CORS 전달, 저장 기사 조회의 공급자 quota 미차감, MongoDB 일시 초기화 실패 재시도와 readiness 복구, v1 기사 ID 최대 128자, 삭제한 맵의 TTL 갱신 제외와 410 유지.
- 서버 설정: API·두 워커에서 `NEWS_MAP_SELECTOR=decision`, `PROTOTYPE_TIER=PAID`, `PAID_DEMO_ENABLED=true`, `USE_RAG=false`, `USE_CRITIC=true`를 확인했다. Redis 필수·Secure 쿠키·Caddy 팀 인증도 유지했다. `.env` 파일 해시는 배포 전후 같았다.

## 백업과 실행

복구 지점은 서버 **`/home/econmind/backups/20261019-main-release-20261019T130456Z`**(0700)이다. 기존 Git SHA·환경/Compose/Caddy/Nginx·미추적 `compose.server.mongo.yaml`을 보관하고, 백엔드·프론트 이미지를 각각 `econmind-backend:rollback-20261019T130456Z`, `econmind-frontend:rollback-20261019T130456Z`로 태그했다. private 설정과 백업을 Git에 넣지 않는다.

MongoDB는 운영 앱 연결의 `readWrite@capstone_news` 계정으로 `mongodump --archive --gzip` 백업을 만들었다. URI는 표준 입력으로 임시 도구 컨테이너 안의 설정 파일에만 전달했다. 압축 해제 전체 읽기(CRC 검사)로 무결성을 확인했고 복원 시험은 수행하지 않았다.

| 백업 항목 | 값 |
|---|---|
| 압축 크기 | 893,263 bytes |
| 압축 파일 SHA-256 | `f17008693bd7a9317e028d77804bbb0afbc071d5f3d6e27b16c22c62cfac311f` |
| 백업 전 문서 수 | news 190 / news_bodies 1 / reports 7 / strategies 12 |

실제 실행은 각 저장소에서 `git fetch origin main` 후 위 SHA에 `git merge --ff-only`했고, 아래 순서로 진행했다. 설정 JSON은 보안 검사에 파이프로 전달하고 전체 환경은 출력하지 않았다.

```bash
cd /home/econmind/capstone/deploy
sudo docker compose -f compose.server.yaml config -q
sudo docker compose -f compose.server.yaml config --format json | python3 scripts/check-server-security.py --paid-demo
sudo docker compose -f compose.server.yaml build econmind-api
sudo docker compose -f compose.server.yaml build frontend
sudo docker compose -f compose.server.yaml up -d --no-build --wait --wait-timeout 180
sudo docker compose -f compose.server.yaml ps
```

API·프론트 healthy, 두 워커 running, 네 앱 컨테이너의 재시작 횟수 0을 확인했다. 두 워커에는 별도 healthcheck가 없어 healthy로 표현하지 않는다. 마지막 디스크 확인은 48G 중 19G 사용·29G 여유였다. 복구가 필요하면 이 백업의 설정과 rollback 이미지 태그를 사용하며 DB 전체 덮어쓰기나 `down -v`를 자동 실행하지 않는다.

## 검증 결과와 범위

| 검사 | 결과 |
|---|---|
| 로컬 백엔드 | `.venv/bin/python -m pytest -q`: 450 passed, 18 skipped |
| 로컬 프론트 | `npm ci --no-audit`, `npm test`: 63 passed, `npm run build`: TypeScript·Vite 성공 |
| Python 3.11 릴리스 이미지 | 운영 자격증명 없는 격리 MongoDB·Redis·NATS 검사: 테스트 기대값 수정 후 **468 passed, skip 0** |
| readiness | 내부 `/ready` 200, `mongodb=on`, `redis=on`; 실제 브라우저는 공개 HTTPS와 기존 팀 인증으로 확인 |
| 실제 처리 | NAVER·Decision 반도체 검색 → Diffbot 본문 → Gemini 리포트·전략 완료, 요청 1/사용 1/실패 0, 경고 0 |
| 저장·접근 | 본문 SHA-256·리포트/전략 연결, 동일 Idempotency-Key의 같은 작업, 타 세션 맵/리포트/전략 404 |
| 입력·삭제 | 다른 Origin의 변경 403, v1 129자 기사 ID 422·상태 보존, 맵 삭제 후 세션 갱신에도 410 |
| 화면 | 기사 선택·리포트/전략 표시·새로고침 복원, 경과 시간 문구·취소 후 늦은 응답 무시 |
| 외부 접근 | 비인증 UI/health/ready/v2 session 401, 검사한 8000/8080/27017/6379/4222/8222 포트 직접 접속 실패 |

기존 테스트 하나가 파생 검색어·검색 순위 1을 기대해 최초 격리 검사는 **467 passed, 1 failed**였다. 기존 제품 계약인 원래 검색어 `국제유가`·다음 위치 21로 기대값을 바로잡고 전체 검사를 통과했으며, 그 수정이 `55b9dc1`이다.

로컬 Docker 엔진이 응답하지 않아 추가 격리 통합 검사는 GCP의 별도 테스트 서비스로 수행했다. 고정된 테스트 MongoDB 8.0 이미지는 호스트 커널 비호환으로 기동을 거부해 **테스트에만 MongoDB 7.0**을 사용했다([MongoDB 이슈](https://jira.mongodb.org/browse/SERVER-121912)). 실제 운영 MongoDB **8.0.28**의 저장 경로는 위 실제 생성으로 별도 확인했다. 임시 테스트 서비스와 테스트 이미지 태그는 정리했고 운영 DB의 장애를 강제로 만들지 않았다.

## 보안 후속 과제와 미검증 범위

`npm audit`와 실제 배포 이미지의 Python 의존성 80개에 대한 `pip-audit`는 알려진 취약점 0건이었다. 프론트 번들에서 검사한 운영 비밀값은 검출되지 않았다. 쿠키의 Secure/HttpOnly/SameSite=Lax, MongoDB 앱 계정의 readWrite 역할, SSH 비밀번호 인증 비활성화를 확인했다. 이것이 전체 서비스의 보안 완료를 뜻하지 않는다.

- 공유 Basic 인증의 계정별 접근 회수/MFA와 Caddy 로그인 시도 제한이 없다.
- API·워커가 root로 실행되고 writable rootfs·기본 capability를 쓴다. 비특권 사용자와 최소 권한화가 남아 있다.
- Python 버전 범위와 가변 기본 이미지 태그를 잠금 파일·검토한 digest로 고정할 필요가 있다.
- Caddy/Nginx에 HSTS·CSP·nosniff·클릭재킹 방지 헤더가 없다.
- DB 관리자 자격증명 회전·과거 평문 이력 정리·낡은 자료 삭제·autossh/systemd 적용은 이번 앱 배포에서 실행하지 않았다. [DB 유지보수 런북](RUNBOOK-20261018-db-maintenance.md)은 별도 작업이다.

실제 프로젝트의 GCP 방화벽 규칙은 `compute.firewalls.list` 권한 부족으로 미확인이다. OS/이미지 취약점 스캔·파괴적 침투 시험·운영 장애 주입·백업 복원·장기 부하·10건 동시 실제 리포트·Atlas RAG는 검증하지 않았다. 반도체 표본은 중심 1건·주변 **0/4**, `insufficient/search_limit`이었다. 가지 API도 200이지만 추가 기사는 0건이므로 선정 품질이나 4/3건 확보 완료로 보지 않는다. 홈 추천·키워드맵 mock과 선정 품질 개선 보류는 유지한다.

상세 정본과 비밀값을 제외한 근거는 [econmind-docs 검증 기록](https://github.com/DKU-CE-Capstone-Project/econmind-docs/blob/main/docs/99-verification.md#gcp-release-20261019), [증거 JSON](https://github.com/DKU-CE-Capstone-Project/econmind-docs/blob/main/docs/evidence/gcp-release-2026-10-19.json), [운영 화면](https://github.com/DKU-CE-Capstone-Project/econmind-docs/blob/main/docs/screenshots/gcp-release-2026-10-19-report.png)에 있다. 원본 로컬 증거는 프로젝트 루트 `artifacts/gcp-release-20261019/`, private 복구 자료는 위 GCP 백업 경로에 있다.
