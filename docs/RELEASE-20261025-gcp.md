---
title: GCP 초기 키워드 맵·마인드맵 정렬 반영 및 검증 기록
updated: 2026-10-25
status: 앱 배포·검증 완료 · DB 유지보수 별도
---

# GCP 초기 키워드 맵·마인드맵 정렬 반영 및 검증 (2026-10-25)

기준일은 **2026-10-25 Asia/Seoul**이다. 사용자의 요청에 따라 `feat/keyword-map-gemini` 변경을 세 코드 저장소 `main`에 병합한 뒤, 서버 `/home/econmind/capstone/{backend,frontend,deploy}`를 해당 `main` 커밋으로 fast-forward하고 API·두 워커·프론트엔드 컨테이너를 교체했다. 프로젝트 `econmind`, 기존 `.env`, MongoDB 연결과 Redis·NATS·인증서 볼륨은 그대로 유지했다.

| 저장소 | 배포 전 | main 병합 PR · 서버 앱 빌드 기준 |
|---|---|---|
| 백엔드 | `89dfb97` | [PR #24](https://github.com/DKU-CE-Capstone-Project/capstone-backend/pull/24) → `9884005` — API·v1/v2 워커 공용 이미지 |
| 프론트엔드 | `b50e762` | [PR #20](https://github.com/DKU-CE-Capstone-Project/capstone-frontend/pull/20) → `68e8c06` — 정적 빌드·Nginx 프록시 |
| 배포 | `96ff086` | [PR #21](https://github.com/DKU-CE-Capstone-Project/capstone-deploy/pull/21) → `77d0786` — `KEYWORD_MAP_*` 8개 설정 기본값·override 전달 |

이 기록 문서를 추가하는 후속 배포 문서 PR은 앱/Compose 설정 변경이나 컨테이너 재빌드를 포함하지 않는다.

## 적용한 변경

- 백엔드 (`9884005`): `GET /api/v1/keywords/map?q=...` 실데이터 API 추가. NAVER 검색 최대 50건을 `gemini-embedding-001` 단일 배치 호출(`768d`, `SEMANTIC_SIMILARITY`)로 임베딩하고, 코사인 유사도 분포 기반 동적 임계값(`KEYWORD_MAP_SIM_THRESHOLD=0.42`, `MAX=0.51`, `ANISOTROPY_GAIN=0.25`) + Greedy 1-pass 클러스터링 + `kiwipiepy` 형태소 분석으로 중심어·이슈(최대 4개)·개체(최대 4개)를 Zero-LLM으로 추출한다. `'규모'` 단독 시작 방지와 대소문자 무관 중복 제거, 180초 인메모리 캐시, 미캐시 공급자 호출에만 `news_search` 시간당 한도 차감 처리를 반영했다.
- 프론트 (`68e8c06`): `KeywordMap`을 `fetchKeywordMap(query)` 실데이터 응답(`center`·`issue`·`entity`, `node.search_query`)에 연결하고, 상단 검색 바·하단 상태칩과 겹치지 않도록 캔버스 가용 중심 `(W/2, topInset + availH/2)`·반경 클램프·좌우 라벨 텍스트 예산으로 잘림을 수정했다. `NewsMapExplorer`는 초기 캔버스 카메라 기준점을 `(W/2, H/2)` 화면 정중앙으로 정렬하고 우측 상세(`detailCx`)·리포트(`reportCx`) 시프트는 유지했다.
- 배포·서버 설정 (`77d0786`): `.env.example`, `compose.server.yaml`, `docker-compose.yaml`에 `KEYWORD_MAP_*` 8개 설정 기본값과 override 전달을 추가했다. 운영 설정은 `NEWS_MAP_SELECTOR=decision`, `PROTOTYPE_TIER=PAID`, `PAID_DEMO_ENABLED=true`, `USE_RAG=false`, `USE_CRITIC=true`, `SESSION_COOKIE_SECURE=true`, `SESSION_STORE_REQUIRED=true`를 유지했고 `.env` SHA-256 해시는 배포 전후 동일했다.

## 백업과 실행

복구 지점은 서버 **`/home/econmind/backups/20261025-keyword-map-gemini-20261025T100033Z`**(0700)이다. 배포 전 Git SHA·환경/Compose/Caddy/Nginx·미추적 `compose.server.mongo.yaml`을 권한 0600으로 보관하고, 백엔드·프론트 이미지를 각각 `econmind-backend:rollback-20261025T100033Z`, `econmind-frontend:rollback-20261025T100033Z`로 태그했다. private 설정과 백업을 Git에 넣지 않는다.

MongoDB는 운영 앱 연결의 `readWrite@capstone_news` 계정으로 `mongodump --archive --gzip` 백업을 만들었다. URI는 임시 도구 컨테이너 내부 임시 설정(0600)으로만 전달했고 `gzip -t` 무결성 검사를 통과했다. 복원 시험은 수행하지 않았다.

| 백업 항목 | 값 |
|---|---|
| 압축 크기 | 1,464,919 bytes |
| 압축 파일 SHA-256 | `621b1c415ac2b9ddc1355cd2629526c80b39bec4ca36401ecbbf9a6d98b18fb3` |
| 백업 전 문서 수 | news 332 / news_bodies 8 / reports 10 / strategies 15 |

실제 배포 명령은 각 저장소에서 `git fetch origin main` 후 위 SHA에 `git merge --ff-only`했고, 아래 순서로 실행했다.

```bash
cd /home/econmind/capstone/deploy
sudo docker compose -f compose.server.yaml config -q
sudo docker compose -f compose.server.yaml config --format json | python3 scripts/check-server-security.py --paid-demo
sudo docker compose -f compose.server.yaml build econmind-api
sudo docker compose -f compose.server.yaml build frontend
sudo docker compose -f compose.server.yaml up -d --no-build --wait --wait-timeout 180
sudo docker compose -f compose.server.yaml ps
```

`econmind-api`·`frontend` healthy, `econmind-worker`·`econmind-v2-worker` running, 전체 앱 컨테이너 재시작 횟수 0을 확인했다. Kiwi 형태소 분석기는 `econmind-api` 프로세스에서만 지연 초기화되어 `docker stats` 기준 `econmind-api` 449.1 MiB / 1 GiB(43.85%), `econmind-v2-worker` 57.41 MiB / 768 MiB(7.48%), `econmind-worker` 39.5 MiB / 768 MiB(5.14%), `frontend` 3.58 MiB / 128 MiB(2.80%)였다. 디스크 여유 공간은 48G 중 28G 여유(41% 사용)였다.

## 검증 결과와 범위

| 검사 | 결과 |
|---|---|
| 로컬 백엔드 | `ruff check` 통과, `.venv/bin/python -m pytest -q`: **456 passed, 18 skipped** (GitHub Actions Python 3.11 CI 통과) |
| 로컬 프론트 | `npm test`: **67 passed, 0 failed**, `npm run build`: TypeScript·Vite 성공 (GitHub Actions CI 통과) |
| 로컬 배포 설정 | 뉴스맵 29개·v2 11개·`KEYWORD_MAP_*` 8개 설정 8가지 Compose 검증 및 `check-server-security.py` 통과 (GitHub Actions CI 통과) |
| readiness | 내부 `/health`·`/ready` 200 (`mongodb=on`, `redis=on`), 쿠키 `Secure; HttpOnly; SameSite=Lax` 확인 |
| 실데이터 키워드 맵 | `GET /api/v1/keywords/map` 공백 입력 `422`; `배터리` 미캐시 **3,318.1 ms** → 캐시 재호출 **3.7 ms**; `코스피`·`반도체`·`금리`·`환율`·`인공지능`·`해운` 모두 `200`, 이슈·개체 분리 및 `'규모'` 단독 시작 0건 확인 |
| 키워드맵 → v2 탐색 연동 | 추출 이슈 검색어(`코스피 10대 그룹 시총`)로 `POST /api/v2/maps` `201` (노드 2개), `GET` 복원 `200`, 검증용 맵·세션 `DELETE` `204` 정리 완료 |
| 정적 번들·외부 접근 | 프론트 정적 번들(`index-DW6W-RD4.js`, `index-B1b2vnRD.css`) 내 운영 비밀값 검출 0건; 공개 HTTPS 인증서 유효, 비인증 `/`, `/health`, `/ready`, `/api/v1/keywords/map?q=반도체`, `/api/v2/session` 모두 `401`, `8000/8080/27017/6379/4222/8222` 직접 접속 차단 |

## 남은 과제와 미검증 범위

- 초기 검색어 키워드 맵(`KeywordMap`)은 실데이터 API로 전환 완료했으나, **홈 화면 추천 키워드칩은 여전히 고정 목록**이다.
- 뉴스맵 주변 기사 선정(Decision API)의 부족 현상(`insufficient/search_limit`, `코스피 10대 그룹 시총` 표본도 주변 1/4건)은 이번 변경 대상이 아니며 기존 보류 상태를 유지한다.
- 이번 배포 검증에서는 리포트·전략 신규 LLM 생성이나 PAID 10건 동시 생성, Atlas RAG를 재실행하지 않았다(`news_bodies` 8 / `reports` 10 / `strategies` 15 보존, 검색 캐시 적재로 `news` 332 → 375).
- 공유 Basic 인증 시도 제한·MFA, root 컨테이너 최소 권한화, 의존성/이미지 digest 고정, 보안 헤더 보강, [DB 유지보수 런북](RUNBOOK-20261018-db-maintenance.md)(관리자 자격증명 회전·낡은 자료 정리·터널 자동화)은 별도 후속 과제로 남아 있다.

상세 정본과 비밀값을 제외한 근거는 [econmind-docs 검증 기록](https://github.com/DKU-CE-Capstone-Project/econmind-docs/blob/main/docs/99-verification.md#gcp-release-20261025)과 [증거 JSON](https://github.com/DKU-CE-Capstone-Project/econmind-docs/blob/main/docs/evidence/gcp-release-2026-10-25.json)에 있다.
