---
title: DB 유지보수 런북 (자격증명 회전 · 낡은 데이터 정리 · SSH 터널 · 운영 .env 형상)
project: EconMind (실시간 뉴스 기반 멀티 에이전트 투자 판단 지원 시스템)
owner: 김건 (kimgeon1104)
created: 2026-10-18
updated: 2026-10-19
status: 유지보수 절차 실행 전 · 2026-10-19 앱 배포와 별도
related:
  - README.md「GCP 서버 구성과 병합 후 배포」「MongoDB」절
  - econmind-docs/docs/11-newsmap-prototype-db-design.md
  - econmind-docs/docs/12-newsmap-v1-extension-implementation.md
  - econmind-docs/docs/14-newsmap-v2-implementation.md
  - econmind-docs/docs/99-verification.md (2026-10-11 GCP 배포 기록)
  - 2026-10-14 재분석 보고서 § 3.4, § 5-1, § 5-3, § 5-4, § 6
tags: [runbook, mongodb, security, credentials, ssh-tunnel, env]
---

# DB 유지보수 런북 (2026-10-18)

> **2026-10-19 보완:** [앱 업데이트 배포](RELEASE-20261019-gcp.md)는 완료했다. 운영 값은 Decision·PAID 최대 10건·RAG off·critic on이고, 백엔드 `89dfb97`에 MongoDB 일시 초기화 실패 복구가 적용돼 있다. 아래 비밀번호 회전·낡은 자료 정리·터널 자동화 절차는 별도이며 실행하지 않았다. 다음 작성 시점 설명은 2026-10-18 당시 기준이다.

> **이 문서는 손으로 실행하는 절차서다.** 작성 시점에 GCP 서버·홈 서버·MongoDB에 접속하지 않았다.
> 아래 명령은 저장소 파일(`compose.server.yaml`, `docker-compose.yaml`, `.env.example`, `mongo-init/`, `scripts/`)과
> 백엔드 코드(`capstone-backend` main `59bc4b0`)를 근거로 썼다. 서버의 실제 상태는 **미판정**이다.
> `<...>` 자리표시자는 서버에서 확인한 값으로 바꾼다.

> **비밀값 규칙 (모든 절 공통)**
> - 비밀번호·토큰·URI 전체를 명령줄 인자, 셸 이력, 터미널 출력, 채팅, Notion, Git, PR에 **절대** 남기지 않는다.
> - 비밀번호는 `mongosh`의 `passwordPrompt()`, `-u`만 주고 `-p`를 생략했을 때의 대화형 프롬프트, 또는 `read -s`로만 입력한다.
> - 출력을 공유할 때는 키 이름·`SET`/`EMPTY`·건수만 공유한다. `docker compose config`(전체), `docker inspect`의 `Env`, `printenv`(전체)는 값을 출력하므로 **붙여넣지 않는다.**
> - Notion 「서버 DB 관리자 계정 접근하기」 페이지의 기존 비밀번호는 **읽거나 복사하지 않는다.** 회전 후 폐기 대상일 뿐이다.

## 목차

- [0. 실행 체크리스트 (김건)](#0-실행-체크리스트-김건)
- [공통 사전 확인 — 이름과 위치](#공통-사전-확인--이름과-위치)
- [1. DB 관리자 자격증명 회전](#1-db-관리자-자격증명-회전)
- [2. DB 낡은 정보 정리 (6주차 액션 아이템)](#2-db-낡은-정보-정리-6주차-액션-아이템)
- [3. MongoDB SSH 역방향 터널 단일 장애점](#3-mongodb-ssh-역방향-터널-단일-장애점)
- [4. 운영 `.env` 형상 기록 양식](#4-운영-env-형상-기록-양식)
- [부록. 근거 대조표](#부록-근거-대조표)

---

## 0. 실행 체크리스트 (김건)

권장 순서다. 읽기 전용 점검을 먼저 하고, 비밀번호 회전 → 백업 → 정리 → 기록 → 터널 자동화 순으로 간다.

| # | 할 일 | 어디서 | 예상 시간 | 절 |
|---|---|---|---|---|
| ☐ 0 | 홈 서버(DB 호스트)·GCP VM 접근 확인, 비밀번호 관리자 항목 준비, 작업 공지(쓰기 중지 아님) | 로컬 | 10분 | [공통](#공통-사전-확인--이름과-위치) |
| ☐ 1 | 터널·DB 상태 **읽기 전용 점검**과 현재 바인드 주소 기록 | GCP VM | 15분 | [3.1](#31-상태-확인-읽기-전용) |
| ☐ 2 | DBA 계정 식별 → 새 비밀번호 생성(관리자에 먼저 저장) → `changeUserPassword` | DB 호스트 또는 GCP | 20분 | [1.2–1.3](#12-계정-식별-읽기-전용) |
| ☐ 3 | `.env`·앱 연결 영향 확인, 이전 비밀번호 실패·새 비밀번호 성공·`/ready` 확인 | GCP VM + DB 호스트 | 15분 | [1.4–1.5](#14-env앱-연결-영향-확인) |
| ☐ 4 | Notion 페이지에서 비밀값 제거, 페이지 이력 정리(복제 후 원본 영구 삭제), 공유 범위 축소 | Notion | 20분 | [1.6](#16-notion-페이지-정리) |
| ☐ 5 | `mongodump --archive --gzip` 백업과 검증(`gzip -t`, `mongorestore --dryRun`) | DB 호스트 또는 GCP | 20분 | [2.2](#22-백업-먼저) |
| ☐ 6 | 확인 질의(읽기 전용)로 정리 대상 건수 기록 | 같은 mongosh | 15분 | [2.3](#23-확인-질의-읽기-전용) |
| ☐ 7 | 대상이 0이 아닐 때만 정리 질의 실행(건수 비교) | 같은 mongosh | 0–15분 | [2.4](#24-정리-질의-대상이-있을-때만) |
| ☐ 8 | 운영 `.env` 형상(키·상태만) 기록, 실행 중 컨테이너의 비밀 아닌 플래그 값 기록 | GCP VM | 20분 | [4](#4-운영-env-형상-기록-양식) |
| ☐ 9 | 홈 서버에 autossh + systemd 터널 적용, 재시작·재부팅 복구 시험 | 홈 서버 + GCP | 45–60분 | [3.2](#32-autossh--systemd-적용) |
| ☐ 10 | 결과(건수·상태·날짜)를 econmind-docs `99-verification.md` 또는 팀 채널에 **값 없이** 기록 | 로컬 | 10분 | 각 절 완료 조건 |

**합계 약 3~3.5시간.** 5~7번은 한 세션에서 이어서 한다. 9번은 홈 서버 관리자 권한이 필요하다.

---

## 공통 사전 확인 — 이름과 위치

저장소 기준으로 확인된 이름이다. 서버에서 다르면 서버 값을 따르고 이 표를 고친다.

| 항목 | 값 | 근거 |
|---|---|---|
| GCP 배포 디렉터리 | `/home/econmind/capstone/deploy` | README「GCP 서버 구성과 병합 후 배포」 |
| Compose 프로젝트명 | `econmind` | `compose.server.yaml:3` `name: econmind` |
| 서버 Compose 서비스 | `caddy`, `frontend`, `econmind-api`, `econmind-worker`, `econmind-v2-worker`, `nats`, `redis` | `compose.server.yaml` services |
| 서버 Compose의 MongoDB 서비스 | **없음** — VM 밖 홈 서버 DB에 SSH 역방향 터널로 연결 | `compose.server.yaml`에 mongo 이미지 0건, README:196, 재분석 § 3.4 |
| 앱이 쓰는 DB 연결 | `MONGODB_URI`(필수), `MONGODB_DB_NAME`(기본 `capstone_news`) | `compose.server.yaml:15-16`, `app/config.py` `mongodb_uri`·`mongodb_db_name` |
| 터널 경유 앱 URI 형태 | `mongodb://<app>:<pw>@host.docker.internal:27017/capstone_news?authSource=admin&directConnection=true` | `.env.example` 「실제 서버」절, README:196 |
| host-gateway 매핑 | `econmind-api`·`econmind-worker`·`econmind-v2-worker`에 `host.docker.internal:host-gateway` | `compose.server.yaml:141,181,195` |
| DB 이름 | `capstone_news` | `.env.example` `MONGODB_DB_NAME`, README「준비」 |
| 로컬/DB 호스트용 Compose의 Mongo 서비스 | `mongodb` (이미지 `mongodb/mongodb-atlas-local:8.0.28`), 튜너 `mongodb-tuner` | `docker-compose.yaml:81,126` |
| root 계정 env (DB 호스트 Compose) | `.env`의 `MONGODB_ROOT_USERNAME`/`MONGODB_ROOT_PASSWORD` → 컨테이너의 `MONGODB_INITDB_ROOT_USERNAME`/`MONGODB_INITDB_ROOT_PASSWORD` | `docker-compose.yaml:89-90` |
| 앱 계정 env (DB 호스트 Compose) | `.env`의 `MONGODB_APP_USERNAME`/`MONGODB_APP_PASSWORD` → `mongo-init/01-app-user.js`가 `admin` DB에 `readWrite@capstone_news`로 생성 | `docker-compose.yaml:93-94`, `mongo-init/01-app-user.js:22-30` |
| DBA 계정 | 재분석 보고서 § 5-1이 계정명을 `econmind_dba`로 기록. 이 문서에서는 `<DBA_USER>`로 쓴다 | 재분석 § 5-1, § 6-1 |
| 2026-10-11 백업 위치(선례) | `/home/econmind/backups/20261011-newsmap-v2` (0700) | README「2026-10-11 GCP v2 배포 기록」 |

> **주의 — 백엔드는 `MONGODB_APP_*`를 읽지 않는다.** `app/config.py`는 `mongodb_uri`, `mongodb_db_name`, `use_mongodb`, `mongodb_required`만 읽는다. `MONGODB_APP_*`·`MONGODB_ROOT_*`는 **DB 호스트에서 `docker-compose.yaml`을 쓸 때만** 의미가 있다(`docker-compose.yaml:161`이 로컬용 `MONGODB_URI`를 조립). GCP의 `compose.server.yaml`은 `.env`의 `MONGODB_URI`만 전달한다.

### 홈 서버(DB 호스트)의 컨테이너 이름 찾기

홈 서버가 이 저장소의 `docker-compose.yaml`로 떠 있다면 서비스명은 `mongodb`이고 컨테이너명은 Compose v2 규칙상 `<프로젝트명>-mongodb-1`이다. 프로젝트명은 디렉터리에 따라 다르므로 **반드시 실제 값을 확인한다.**

```bash
# 홈 서버에서 실행. 이미지·이름·상태만 출력한다(환경변수 출력 없음).
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -i mongo
MONGO_C='<위 출력의 컨테이너 이름>'      # 예: deploy-mongodb-1 (가정, 확인 필요)
docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}' "$MONGO_C"
```

홈 서버에 접근할 수 없으면 각 절의 **경로 B**(GCP에서 터널을 거쳐 일회용 `mongo:8.0` 컨테이너로 접속)를 쓴다. `mongo:8.0`은 `compose.local-test.yaml`과 같은 메이저 버전이며 `mongosh`·`mongodump`·`mongorestore`를 포함한다.

```bash
# 경로 B 공통 접두어 (GCP VM). 비밀번호는 -u만 주고 프롬프트로 입력한다.
MONGO_B='sudo docker run --rm -it --add-host=host.docker.internal:host-gateway -e HOME=/tmp mongo:8.0'
TUNNEL_URI='mongodb://host.docker.internal:27017/?directConnection=true&authSource=admin'
```

---

## 1. DB 관리자 자격증명 회전

**배경.** Notion 「서버 DB 관리자 계정 접근하기」(4. 개발·운영 하위, 2026-10-10 편집) 하단 callout에 DBA 계정명·비밀번호 평문과 내부 접속 주소 3종이 있다(재분석 § 4.1 #6, § 5-1, 확정 3/3표). 위키는 링크가 있는 사용자에게 편집을 허용하도록 공유돼 있었다. **문자열 삭제만으로는 부족하다 — 먼저 비밀번호를 바꿔 유출된 값을 무효화한다.**

### 1.1 준비

1. 팀 비밀번호 관리자(Bitwarden/1Password 등)에 항목 `EconMind MongoDB DBA`를 만들고, **관리자의 생성기로** 32자 이상·URL 안전 문자(영문·숫자만, `@ : / ? #` 금지 — README「준비」 규칙)를 생성해 **먼저 저장**한다. 터미널에 출력하지 않는다.
2. 비밀번호 관리자를 쓸 수 없으면 서버 전용 파일을 쓴다. 화면에 출력하지 않는다.

   ```bash
   # 대안: DB 호스트(또는 GCP)의 운영자 홈에만 둔다. 출력하지 않는다.
   install -d -m 700 ~/.econmind-secrets
   ( umask 077; openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32 > ~/.econmind-secrets/mongodb-dba.pw )
   stat -c '%a %U %n' ~/.econmind-secrets/mongodb-dba.pw   # 600 이어야 한다
   ```

3. 기존 세션 하나를 **열어 둔 채로** 작업한다(회전 직후 잠김 방지용 안전망).

### 1.2 계정 식별 (읽기 전용)

```bash
# 경로 A — 홈 서버, 실제 컨테이너 이름 사용. -p를 생략하면 비밀번호를 프롬프트로 묻는다.
docker exec -it -e HOME=/tmp "$MONGO_C" mongosh --quiet --authenticationDatabase admin -u '<DBA_USER>'
# 경로 B — GCP VM, 터널 경유
$MONGO_B mongosh --quiet "$TUNNEL_URI" -u '<DBA_USER>'
```

`<DBA_USER>`의 현재 비밀번호가 필요하다. **Notion에서 복사하지 말고** 이미 알고 있는 담당자(2026-10-11 배포 수행자 등)가 직접 입력하거나, 그 사람 대신 root 계정으로 접속한다.

```js
// mongosh 안. 비밀번호·해시는 출력하지 않는다.
var admin = db.getSiblingDB('admin');
admin.getUsers({ showCredentials: false }).users.map(u => ({ user: u.user, db: u.db, roles: u.roles.map(r => `${r.role}@${r.db}`) }));
db.runCommand({ connectionStatus: 1 }).authInfo.authenticatedUsers;
```

다음을 기록한다(이름·역할만).

| 계정 | 역할 | 분류 |
|---|---|---|
| `<DBA_USER>` | 예: `root@admin` 또는 `userAdminAnyDatabase`… | DBA (이번 회전 대상) |
| `<ROOT_USER>` | `root@admin` | DB 호스트 `.env`의 `MONGODB_ROOT_USERNAME`과 같은지 확인 |
| `<APP_USER>` | `readWrite@capstone_news` | 앱 계정 (`MONGODB_URI`에 들어 있음) |

> DBA 계정이 root(`MONGODB_ROOT_USERNAME`)와 **같은 계정인지 다른 계정인지**에 따라 1.4의 영향이 달라진다.

### 1.3 비밀번호 변경

```js
// mongosh 안. passwordPrompt()가 입력을 숨기며 이력에도 남지 않는다.
use admin
db.changeUserPassword('<DBA_USER>', passwordPrompt())
// 또는 동등하게:
// db.updateUser('<DBA_USER>', { pwd: passwordPrompt() })
```

- 비밀번호 관리자에서 붙여넣는다. 서버 전용 파일을 썼다면 다른 터미널에서 클립보드로 옮기되 `cat`으로 화면에 출력하지 않는다.
- 변경을 실행한 세션은 끊지 않는다. 1.5 검증이 끝날 때까지 유지한다.
- 이미 인증된 다른 연결은 즉시 끊기지 않는다. 앱 계정이 아니므로 서비스에는 영향이 없다(1.4 참조).

### 1.4 `.env`·앱 연결 영향 확인

| 확인 | 명령 (값 출력 없음) | 기대 |
|---|---|---|
| GCP 앱 URI가 DBA 계정을 쓰지 않는가 | `cd /home/econmind/capstone/deploy && sudo grep -c "^MONGODB_URI=mongodb://<DBA_USER>:" .env` | `0` → 앱 영향 없음. `1`이면 **앱이 DBA 계정을 쓰는 상태** — 아래 「앱 계정 회전」으로 즉시 분리 |
| DB 호스트 `.env`의 root가 DBA인가 | (홈 서버 Compose 디렉터리) `grep -c "^MONGODB_ROOT_USERNAME=<DBA_USER>$" .env` | `0` → DB 호스트 `.env` 변경 불필요 |
| 임시 자격증명 파일 잔존 여부 | `sudo find /home/econmind -xdev -type f \( -iname '*dba*' -o -iname '*cred*' -o -iname '*.pw' \) -printf '%m %u %p\n' 2>/dev/null` | 2026-10-11 배포는 DBA 자격증명을 임시 0600 파일로 쓰고 삭제했다(99-verification). 남은 파일이 있으면 내용 확인 없이 삭제 |

**DBA가 root(`MONGODB_ROOT_*`)와 같은 계정일 때 (DB 호스트에서 `docker-compose.yaml` 사용 시)**

- `MONGODB_INITDB_ROOT_*`는 **빈 데이터 디렉터리의 최초 초기화 때만** 적용된다(README「DB 초기화」: `/docker-entrypoint-initdb.d`는 최초 1회). 따라서 `.env`만 바꿔서는 비밀번호가 바뀌지 않고, 반대로 1.3에서 DB 안의 비밀번호를 바꾼 뒤 `.env`를 그대로 두면 다음 두 곳이 깨진다.
  1. `mongodb-tuner`가 기동마다 `MONGODB_ROOT_*`로 접속한다(`docker-compose.yaml:126-144`) → 다음 `up` 때 실패.
  2. README의 관리 명령(`strict-validation.js`, `verify-db.js`, `seed-news.js`, 백업)이 컨테이너 안의 `$MONGODB_INITDB_ROOT_*`를 쓴다 → 컨테이너를 재생성하기 전까지 이전 값이 남아 실패.
- 조치: DB 호스트 `.env`의 `MONGODB_ROOT_PASSWORD`를 편집기로 새 값으로 바꾸고(`chmod 600 .env` 유지), 튜너만 재생성한다.

  ```bash
  docker compose up -d --force-recreate mongodb-tuner      # README「기동」의 기존 명령
  ```

  `mongodb` 컨테이너 자체의 env 갱신은 컨테이너 재생성(=DB 재시작)이 필요하다. 터널 너머의 운영 API가 잠깐 503이 되므로 저부하 시간에 하고, 끝나면 [3.1](#31-상태-확인-읽기-전용)의 `/ready` 확인을 반복한다.

**선택 — 앱 계정 회전** (앱 비밀번호도 노출됐거나 위 grep이 `1`일 때만)

1. mongosh에서 `use admin; db.changeUserPassword('<APP_USER>', passwordPrompt())`
2. GCP `deploy/.env`의 `MONGODB_URI` 비밀번호 부분을 편집기로 교체(`sudo -e .env`). 명령줄 `sed`로 넣지 않는다.
3. 백엔드 3개 서비스만 재생성: `sudo docker compose -f compose.server.yaml up -d --no-build --no-deps --force-recreate econmind-api econmind-worker econmind-v2-worker`
4. DB 호스트 `.env`의 `MONGODB_APP_PASSWORD`도 같은 값으로 맞춘다(재초기화 대비 기록일 뿐 DB에는 영향 없음).

### 1.5 검증

```bash
# (1) 이전 비밀번호는 실패해야 한다 — 이전 값을 아는 담당자만 입력. 모르면 생략하고 (2)만 한다.
$MONGO_B mongosh --quiet "$TUNNEL_URI" -u '<DBA_USER>' --eval 'db.runCommand({connectionStatus:1}).ok'
#   기대: MongoServerError: Authentication failed.
# (2) 새 비밀번호는 성공해야 한다 (프롬프트에 비밀번호 관리자 값 붙여넣기)
$MONGO_B mongosh --quiet "$TUNNEL_URI" -u '<DBA_USER>' --eval 'db.runCommand({connectionStatus:1}).authInfo.authenticatedUsers'
#   기대: [ { user: '<DBA_USER>', db: 'admin' } ]
# (3) 앱 상태 — compose 내부 경로, 비밀값 출력 없음
cd /home/econmind/capstone/deploy
sudo docker compose -f compose.server.yaml ps
sudo docker compose -f compose.server.yaml exec -T econmind-api python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)))"
#   기대: {'status': 'ok', 'mongodb': 'on', 'redis': 'on'}
# (4) 공개 경로 — 팀 Basic 인증 비밀번호는 curl이 프롬프트로 묻는다(README「팀 전용 접속 인증」)
curl --user econmind-team -fsS https://econmind.duckdns.org/ready
```

경로 A(홈 서버)라면 `$MONGO_B mongosh "$TUNNEL_URI"` 대신 `docker exec -it -e HOME=/tmp "$MONGO_C" mongosh --quiet --authenticationDatabase admin`을 쓴다.

### 1.6 Notion 페이지 정리

비밀번호 회전이 끝난 **뒤에** 한다(회전 전 정리하면 유출 값이 여전히 유효하다).

1. **비밀값 제거.** 「서버 DB 관리자 계정 접근하기」 하단 gray callout 블록을 통째로 삭제한다. 내용을 열람·복사하지 않는다. 그 자리에 다음 한 줄만 둔다.
   > 자격증명은 팀 비밀번호 관리자 항목 `EconMind MongoDB DBA`에 있다. Notion·Git·채팅에 적지 않는다. (2026-10-18 회전)
   내부 접속 주소 3종(사설 IP·docker 브리지·`host.docker.internal`)도 정찰 정보이므로 「접속 경로는 배포 런북 참조」로 바꾸는 것을 권장한다.
2. **페이지 이력 정리.** Notion 페이지 기록(Page history)은 이전 버전을 보존하며 개별 버전을 지울 수 없다(보존 기간은 플랜별로 다르다 — Notion 도움말에서 현재 플랜 확인). 가장 확실한 방법:
   1. 정리된 페이지를 **복제**(Duplicate)한다 → 새 페이지는 이력이 없다.
   2. 복제본 제목을 원래 제목으로 바꾸고, 원래 위치(4. 개발·운영 하위)로 옮긴다.
   3. 원본 페이지를 삭제하고 **휴지통에서 영구 삭제**한다.
   4. 원본으로 가던 링크(허브 「📚 EconMind 캡스톤 위키」, 「4. 개발·운영」 등)를 복제본으로 고친다. 백링크 목록에서 확인한다.
   5. 페이지 댓글·인라인 토론에 같은 값이 있으면 함께 삭제한다.
   권한이 없으면 워크스페이스 소유자에게 위 3단계를 요청한다.
3. **공유 범위 축소.** 「2학기 2주차 회의」 기록상 위키가 "링크가 있는 모든 사용자 편집 허용"이다(재분석 § 5-1).
   - 공유 → 일반 액세스를 **초대된 사람만**(또는 워크스페이스 멤버만)으로 바꾼다.
   - **웹에 게시**(Publish)가 켜져 있으면 끈다. 공개 링크를 폐기한다.
   - 게스트 목록에서 팀원(김성민·김건·문주안)·지도교수 외 계정을 제거한다.
4. **같은 값이 남아 있을 수 있는 곳**: 팀 채팅, 개인 메모, `/home/econmind/backups/*` 안의 임시 파일. 비밀번호는 이미 회전됐으므로 이 항목들은 정리만 하면 된다.

**비밀번호를 둘 곳**: ① 팀 비밀번호 관리자(정본), ② 필요하면 DB 호스트·GCP 운영자 홈의 `0600` 파일(`~/.econmind-secrets/`, 디렉터리 `0700`). Notion·Git·`deploy/.env`(앱 전용)에는 두지 않는다 — `.env.example` 상단 주석과 README「접근 / 보안」의 기존 규칙과 같다.

### 1.7 완료 조건

- [ ] `<DBA_USER>` 새 비밀번호로 인증 성공, 이전 비밀번호로 인증 실패(또는 "이전 값 미보유로 생략" 기록).
- [ ] `/ready`가 `{"status":"ok","mongodb":"on","redis":"on"}`, `compose ps`에서 `econmind-api` healthy.
- [ ] GCP `MONGODB_URI`가 DBA 계정을 쓰지 않음(grep 결과 `0`) 확인 기록.
- [ ] DBA가 root와 같은 계정이면 DB 호스트 `.env` 갱신 + `mongodb-tuner` 재생성 성공.
- [ ] Notion 원본 페이지 영구 삭제, 정리된 복제본만 존재, 위키 일반 액세스가 "링크가 있는 모든 사용자"가 아님, 웹 게시 꺼짐.
- [ ] 새 비밀번호가 비밀번호 관리자에만 있고 어디에도 평문 사본이 없음.

### 1.8 되돌리기

- **이전(유출된) 비밀번호로 되돌리지 않는다.** 되돌리기는 "새 값으로 다시 회전"이다.
- 새 비밀번호를 잘못 입력해 잠긴 경우: 열어 둔 기존 세션이나 root 계정으로 `db.changeUserPassword('<DBA_USER>', passwordPrompt())`를 다시 실행한다.
- 앱 계정을 회전했다가 `/ready`가 실패하면: `.env` 백업(`sudo cp -p .env /home/econmind/backups/<날짜>/env.before`로 미리 보관)을 복원하고 백엔드 3서비스를 재생성한 뒤, DB 쪽 앱 비밀번호를 그 값에 맞게 다시 설정한다.
- Notion 원본을 영구 삭제하기 전이라면 휴지통에서 복구할 수 있다(그러나 비밀값 블록은 복구 후 다시 지운다).

---

## 2. DB 낡은 정보 정리 (6주차 액션 아이템)

### 2.1 무엇이 "현재 사용 중"인가 — 코드 근거

정리 대상을 고르기 전에 현재 main 코드가 실제로 읽고 쓰는 컬렉션을 확인했다(`capstone-backend` main `59bc4b0`).

| 컬렉션 | 현재 코드 사용 | 근거 | 분류 |
|---|---|---|---|
| `news` | 읽기·쓰기 | `app/database.py:23` 상수, `:316` upsert, `:368`·`:380`·`:417` find, `:483` 벡터 검색 aggregate | **사용 중** |
| `reports` | v1 읽기·쓰기 + v2 읽기·쓰기 | v1 `app/database.py:327` `save_report`·`:426` `get_report`, v2 `app/v2/persistence.py:62-70` 인덱스·`report()`/`reusable_report()`·`app/v2/reports.py:589` 문서 생성 | **사용 중** |
| `strategies` | v1 + v2 | `app/database.py:342`·`:440`, `app/v2/persistence.py` `strategy()`·`app/v2/reports.py:611` | **사용 중** |
| `news_bodies` | v2 쓰기·읽기 | `app/v2/persistence.py:62-63` 인덱스, `:93`, `:169`, `:196` | **사용 중** (2026-10-11 신설) |
| `news_analysis` | 상수 선언만 | `app/database.py:24`, 다른 참조 0건 | 선언만·미사용 |
| `news_relations` | 상수 선언만 | `app/database.py:25`, 다른 참조 0건 | 선언만·미사용 |
| `jobs` | 상수 선언만 (v1 작업은 Redis) | `app/database.py:28`, 다른 참조 0건 | 선언만·미사용 |
| `selections` | 상수 선언만 (선택은 프로세스 메모리) | `app/database.py:29`, 다른 참조 0건. `mongo-init/02-collections.js`도 만들지 않음 | 선언만·미생성 예상 |
| `mindmaps`, `users` | 백엔드 참조 0건 | `mongo-init/02-collections.js`가 생성, README「DB 초기화」: "만들되 비워 둔다" | 설계상 빈 컬렉션 |
| `articles` | 백엔드 참조 0건 (구 이름) | `app/database.py:20-22` 주석 "구 이름 articles 는 … news 로 교체" | **레거시 잔재 후보** |

> **선언만 있는 컬렉션은 이번에 삭제하지 않는다.** 정본 DB 설계 `econmind-docs/docs/11-newsmap-prototype-db-design.md:44`가 `mindmaps`·`jobs`·`users`·`news_analysis`·`news_relations`를 "강제하거나 삭제하지 않는다"고 정했고, `scripts/verify-db.js:10-11`의 `EXPECTED`가 이 컬렉션들의 존재를 검사한다(삭제 시 점검 스크립트가 실패). 이번 작업은 **비어 있는지 확인·기록**까지만 한다. 삭제는 팀 결정 후 `mongo-init/02-collections.js`·`scripts/verify-db.js`·`app/database.py` 상수를 함께 바꾸는 별도 PR로 한다(재분석 § 6-9).

**v2 리포트 두 종류 구분** — 같은 `schema_version: 2`가 서로 다른 형태를 가리킨다(econmind-docs 12번 문서 :439 "번호 충돌").

| | 현재 v2 (main, 2026-10-11~) | 과거 프로토타입 v2 (v1 확장, 2026-10-10 되돌림) |
|---|---|---|
| 정의 위치 | `app/v2/reports.py:589`, `scripts/lib/newsmap-v2-schema.js:20-31` | econmind-docs `12-…md` § 5.1(:294-305), 백엔드 `939c1d2`·배포 `30d201b`(main에 없음) |
| 식별 필드 | `generation_key`(sha256), `reuse_eligible`, `used_evidence`, `requested_news_ids`, `coverage`, `generation` | `sections{summary,event_analysis,market_impact,risk_factors}`, `reuse_key`, `is_fallback` |
| `sections`·`reuse_key` | **없음** (`ReportOutput` 필드: `title`·`event_summary`·`event_analysis`·`market_impact`·`stock_impacts`·`risk_factors`, `app/v2/reports.py:90-96`) | **필수** |
| 인덱스 | `uniq_reports_v2_generation`, `idx_reports_requested_created` | `idx_reports_reuse_key {reuse_key:1, is_fallback:1}` |

따라서 과거 프로토타입 문서 필터는 `{schema_version: 2, reuse_key: {$exists: true}, generation_key: {$exists: false}}`로 잡는다. 현재 v2 문서는 `generation_key`가 필수(validator)이므로 이 필터에 걸리지 않는다.

> **예상 결과는 0건이다.** 2026-10-11 배포 때 운영 `capstone_news`의 리포트 6건이 모두 무버전/1 호환 분기에 적합했고(99-verification "기존 기사 57, 리포트 6, 전략 11건은 새 validator에 모두 적합"), 그 뒤 `reports` validator가 strict/error라 프로토타입 형식은 새로 저장될 수 없다. 즉 이 항목은 **"없음을 확인하고 기록"**하는 작업일 가능성이 높다. `articles`도 2026-09-18 백업이 "8개 컬렉션"이었다는 기록(07-news-release)상 없을 가능성이 높다. 둘 다 서버에서 확인 전까지 **미판정**이다.

> **v1 리포트·전략(무버전/1)은 정리 대상이 아니다.** v1 API가 아직 mount돼 있고(`app/main.py:200-204`) `get_report`/`get_strategy`가 읽는다.

### 2.2 백업 먼저

쓰기 중지는 필요 없다(정리 대상이 앱이 쓰지 않는 문서뿐). 단 백업 시각을 기록한다.

```bash
# 공통: 백업 디렉터리 (선례와 같은 위치·권한)
BK=/home/econmind/backups/20261018-db-maintenance
sudo install -d -m 700 -o "$USER" "$BK"
```

**경로 A — 홈 서버, 실제 컨테이너 안에서 덤프 후 복사** (비밀번호는 프롬프트)

```bash
docker exec -it -e HOME=/tmp "$MONGO_C" mongodump \
  --authenticationDatabase admin -u '<DBA_USER>' \
  --db capstone_news --archive=/tmp/capstone_news-20261018.archive.gz --gzip
docker cp "$MONGO_C":/tmp/capstone_news-20261018.archive.gz "$BK"/
docker exec "$MONGO_C" rm -f /tmp/capstone_news-20261018.archive.gz
chmod 600 "$BK"/capstone_news-20261018.archive.gz
```

**경로 B — GCP VM, 터널 경유**

```bash
sudo docker run --rm -it --add-host=host.docker.internal:host-gateway -v "$BK":/backup mongo:8.0 \
  mongodump --uri "$TUNNEL_URI" -u '<DBA_USER>' \
  --db capstone_news --archive=/backup/capstone_news-20261018.archive.gz --gzip
sudo chown "$USER": "$BK"/capstone_news-20261018.archive.gz && chmod 600 "$BK"/capstone_news-20261018.archive.gz
```

**검증**

```bash
gzip -t "$BK"/capstone_news-20261018.archive.gz && echo GZIP_OK
ls -l "$BK"/capstone_news-20261018.archive.gz                     # 크기 기록 (10-11 선례: 416,311 bytes)
sha256sum "$BK"/capstone_news-20261018.archive.gz > "$BK"/SHA256SUMS && chmod 600 "$BK"/SHA256SUMS
# 복원 리허설 (실제로 쓰지 않음). 출력의 namespace 목록·문서 수를 2.3의 건수와 대조한다.
sudo docker run --rm -i --add-host=host.docker.internal:host-gateway -v "$BK":/backup:ro mongo:8.0 \
  mongorestore --uri "$TUNNEL_URI" -u '<DBA_USER>' --archive=/backup/capstone_news-20261018.archive.gz --gzip \
  --nsInclude 'capstone_news.*' --dryRun -v 2>&1 | grep -E 'found collection|restoring|dry run|document' | head -40
```

`-i`만 쓰면 비밀번호 프롬프트가 안 뜰 수 있으니 필요하면 `-it`로 바꾼다. 경로 A라면 같은 `mongorestore … --dryRun`을 덤프 파일을 지우기 전에 컨테이너 안에서 실행한다.

### 2.3 확인 질의 (읽기 전용)

mongosh에 DBA로 접속한 뒤(1.2와 같은 명령) 그대로 붙여넣는다. **쓰기 명령이 없다.** 같은 세션에서 2.4까지 이어 붙여도 되도록 `var`로 선언했다(mongosh에서 `const` 재선언은 오류).

```js
var d = db.getSiblingDB('capstone_news');

// (a) 서버의 DB 목록 — 다른 DB는 보고만 하고 이번 범위에서 손대지 않는다
db.adminCommand({ listDatabases: 1, nameOnly: true }).databases.map(x => x.name);

// (b) 컬렉션별 문서 수 (전후 비교 기준표)
var before = {};
d.getCollectionNames().sort().forEach(c => { before[c] = d[c].countDocuments({}); });
printjson(before);

// (c) 레거시 articles 잔재
var hasArticles = d.getCollectionNames().includes('articles');
print('articles exists:', hasArticles);
if (hasArticles) {
  print('articles count:', d.articles.countDocuments({}));
  printjson(d.articles.aggregate([{ $project: { k: { $objectToArray: '$$ROOT' } } }, { $unwind: '$k' },
    { $group: { _id: '$k.k', n: { $sum: 1 } } }, { $sort: { n: -1 } }, { $limit: 30 }]).toArray()); // 필드명만, 값 없음
  printjson(d.articles.getIndexes().map(i => i.name));
}

// (d) 과거 프로토타입 v2 리포트 (2026-10-10 되돌림)
var protoReportFilter = { schema_version: 2, reuse_key: { $exists: true }, generation_key: { $exists: false } };
print('proto v2 reports:', d.reports.countDocuments(protoReportFilter));
print('proto v2 reports with sections:', d.reports.countDocuments({ ...protoReportFilter, sections: { $exists: true } }));
// 안전 확인: 현재 v2와 겹치는 문서가 있으면 정리하지 말고 중단한다 (기대 0)
print('ambiguous v2 (reuse_key AND generation_key):', d.reports.countDocuments({ schema_version: 2, reuse_key: { $exists: true }, generation_key: { $exists: true } }));
print('current v2 reports:', d.reports.countDocuments({ schema_version: 2, generation_key: { $exists: true } }));
print('legacy v1/no-version reports:', d.reports.countDocuments({ $or: [{ schema_version: { $exists: false } }, { schema_version: 1 }] }));
print('unknown-version reports:', d.reports.countDocuments({ schema_version: { $exists: true, $nin: [1, 2] } }));
print('proto-like strategies (v2 without generation_key):', d.strategies.countDocuments({ schema_version: 2, generation_key: { $exists: false } }));

// (e) 프로토타입 배포가 남겼을 수 있는 인덱스
printjson(d.reports.getIndexes().map(i => i.name));
print('idx_reports_reuse_key present:', d.reports.getIndexes().some(i => i.name === 'idx_reports_reuse_key'));

// (f) 선언만 있는 컬렉션 — 비어 있어야 한다 (삭제하지 않음)
['news_analysis', 'news_relations', 'jobs', 'selections', 'mindmaps', 'users'].forEach(c =>
  print(c, d.getCollectionNames().includes(c) ? d[c].countDocuments({}) : 'absent'));
```

기록 양식 (값·본문 없이 건수만):

| 항목 | 기대 | 실제 | 조치 |
|---|---|---|---|
| `articles` 존재/건수 | 없음 | | 있으면 2.4-① |
| 프로토타입 v2 리포트 | 0 | | >0이면 2.4-② |
| 모호한 v2(둘 다 보유) | 0 | | >0이면 **중단**, 팀 공유 |
| 현재 v2 리포트 | ≥1 (10-11 기준 1) | | 유지 |
| 무버전/1 리포트 | 6 (10-11 기준) | | 유지 |
| 알 수 없는 버전 리포트 | 0 | | >0이면 중단, 팀 공유 |
| `idx_reports_reuse_key` | 없음 | | 있으면 2.4-③ |
| 선언만 있는 컬렉션 6종 | 0 또는 absent | | >0이면 중단(코드 밖 쓰기 경로 조사) |

### 2.4 정리 질의 (대상이 있을 때만)

**원칙: 같은 필터 변수로 `countDocuments` → `deleteMany` → `deletedCount` 비교.** 다르면 즉시 멈추고 백업에서 복원 여부를 판단한다.

```js
var d = db.getSiblingDB('capstone_news');

// ① 레거시 articles — 앱 참조 0건(app/database.py:20-22)일 때만
var fArticles = {};
var nArticles = d.articles.countDocuments(fArticles);
print('articles to delete:', nArticles);
var rArticles = d.articles.deleteMany(fArticles);
print('deleted:', rArticles.deletedCount, rArticles.deletedCount === nArticles ? 'MATCH' : 'MISMATCH — STOP');
if (rArticles.deletedCount === nArticles && d.articles.countDocuments({}) === 0) d.articles.drop();
print('articles exists after:', d.getCollectionNames().includes('articles'));

// ② 과거 프로토타입 v2 리포트 — 2.3에서 "모호한 v2"가 0일 때만
var fProto = { schema_version: 2, reuse_key: { $exists: true }, generation_key: { $exists: false } };
var nProto = d.reports.countDocuments(fProto);
print('proto v2 reports to delete:', nProto);
var rProto = d.reports.deleteMany(fProto);
print('deleted:', rProto.deletedCount, rProto.deletedCount === nProto ? 'MATCH' : 'MISMATCH — STOP');

// ③ 프로토타입 인덱스 — ②가 끝나 reuse_key 문서가 0일 때만
if (d.reports.countDocuments({ reuse_key: { $exists: true } }) === 0 &&
    d.reports.getIndexes().some(i => i.name === 'idx_reports_reuse_key')) {
  d.reports.dropIndex('idx_reports_reuse_key');
}

// 사후 확인 — 2.3 (b)의 before와 비교
var after = {};
d.getCollectionNames().sort().forEach(c => { after[c] = d[c].countDocuments({}); });
printjson(after);
```

- `reports`의 validator는 strict/error지만 삭제는 validator 검사를 받지 않는다.
- 프로토타입 리포트를 참조하는 전략이 있을 수 있다. 삭제 전 `d.strategies.countDocuments({ report_id: { $in: d.reports.find(fProto, { report_id: 1 }).toArray().map(x => x.report_id) } })`로 건수를 확인하고, >0이면 팀에 공유한 뒤 결정한다(전략은 이번에 지우지 않는다).
- 선언만 있는 컬렉션은 **정리하지 않는다**(2.1 참조).

### 2.5 완료 조건

- [ ] 백업 파일 `0600`, `gzip -t` 통과, SHA-256 기록, `mongorestore --dryRun` 출력의 namespace·건수가 2.3 (b)와 일치.
- [ ] 2.3 기록표가 채워짐(건수만). 0건 항목은 "확인 결과 없음"으로 기록.
- [ ] 정리를 실행한 항목마다 `count == deletedCount`(MATCH) 기록, 사후 `after`에서 `news`·`news_bodies`·`strategies`와 현재 v2/v1 리포트 건수 변화 없음.
- [ ] `/ready` 정상, 운영 화면에서 기존 리포트 1건 열람 확인.

### 2.6 되돌리기

삭제한 namespace만 복원한다. 같은 `_id`가 이미 있으면 `mongorestore`는 기본적으로 덮어쓰지 않고 건너뛴다(`--drop` 금지).

```bash
# 예: articles 전체 복원
sudo docker run --rm -it --add-host=host.docker.internal:host-gateway -v "$BK":/backup:ro mongo:8.0 \
  mongorestore --uri "$TUNNEL_URI" -u '<DBA_USER>' --archive=/backup/capstone_news-20261018.archive.gz --gzip \
  --nsInclude 'capstone_news.articles'
# 예: 프로토타입 리포트 복원 — reports 전체를 대상으로 하되 기존 _id는 건너뛴다.
#      단, 현재 reports validator(strict/error)가 프로토타입 형식을 거부하므로 복원 시 --bypassDocumentValidation이 필요하다.
sudo docker run --rm -it --add-host=host.docker.internal:host-gateway -v "$BK":/backup:ro mongo:8.0 \
  mongorestore --uri "$TUNNEL_URI" -u '<DBA_USER>' --archive=/backup/capstone_news-20261018.archive.gz --gzip \
  --nsInclude 'capstone_news.reports' --bypassDocumentValidation
```

인덱스를 지웠다면: `d.reports.createIndex({ reuse_key: 1, is_fallback: 1 }, { name: 'idx_reports_reuse_key' })` (econmind-docs 12번 문서 :413의 정의).

---

## 3. MongoDB SSH 역방향 터널 단일 장애점

**구조 (재분석 § 3.4, 확정·설정 기준).** `compose.server.yaml`에는 MongoDB 서비스가 없는데 `MONGODB_URI`가 필수(`:15`)이고 `MONGODB_REQUIRED: "true"`이며, `econmind-api` healthcheck가 `/ready`의 `mongodb=='on'`을 요구한다(`:163`). 연결 대상은 홈 서버 DB로 가는 SSH 역방향 터널(`host.docker.internal:27017`, `directConnection=true`)이고 이 터널은 **Compose 밖에서 사람이 유지**한다(§ 5-3).

**기동 순서와 복구 — 2026-10-19 정정.** 최초 작성 기준 `59bc4b0`은 시작 시 ping 실패 뒤 `_disabled=True`로 DB를 비활성화했다. GCP에 반영한 `89dfb97`은 이 동작을 제거했다. 클라이언트 생성 실패는 5초 cooldown 뒤 재시도하고, `/ready`가 연결 복구 후 미완료 인덱스 초기화를 수행한다. 복구 회귀는 격리 테스트로 확인했고 운영 터널 장애를 강제로 만들지는 않았다. Docker의 `restart: unless-stopped`는 unhealthy 자체를 재시작하지 않으므로 터널·DB·Redis 및 `/ready` 확인은 계속 필요하다.

터널 복구 후에도 `/ready` 503이 지속되면 로그·DB 권한·Redis·인덱스 상태를 먼저 확인한다. 재시작은 원인 확인 뒤 필요한 경우에 사용하며, 이전 버전의 영구 비활성화가 현재 코드에도 남아 있다고 단정하지 않는다.

### 3.1 상태 확인 (읽기 전용)

**GCP VM (터널의 수신 쪽)**

```bash
# 1) 27017 리스너와 바인드 주소 — 이 값을 기록한다 (127.0.0.1 / 172.17.0.1 / 0.0.0.0 중 무엇인가)
sudo ss -tlnp 'sport = :27017'
#    프로세스가 sshd 여야 역방향 터널이다.
# 2) host-gateway가 가리키는 주소 (보통 docker0 = 172.17.0.1)
ip -4 addr show docker0 | awk '/inet /{print $2}'
# 3) TCP 도달성 — 컨테이너가 보는 경로와 같은 주소로
nc -zv -w 3 172.17.0.1 27017
# 4) 앱 경로로 ping (인증 불필요 명령, 비밀값 없음)
cd /home/econmind/capstone/deploy
sudo docker compose -f compose.server.yaml exec -T econmind-api python -c "from pymongo import MongoClient; print(MongoClient('mongodb://host.docker.internal:27017/?directConnection=true', serverSelectionTimeoutMS=3000).admin.command('ping'))"
#    기대: {'ok': 1.0}
# 5) 앱 준비 상태
sudo docker compose -f compose.server.yaml exec -T econmind-api python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)))"
# 6) 외부 노출 여부 — 리스너가 0.0.0.0이면 반드시 확인. 로컬 PC에서 실행해 '실패'해야 정상
nc -zv -w 3 35.216.13.110 27017
gcloud compute firewall-rules list --format='table(name,direction,sourceRanges.list(),allowed[].map().firewall_rule().list())'
```

**홈 서버 (터널의 발신 쪽)**

```bash
ps -eo pid,user,lstart,args | grep -E '[s]sh .*-R|[a]utossh'      # 실행 중인 터널 프로세스와 시작 시각
systemctl list-units --type=service | grep -iE 'tunnel|autossh'     # 이미 서비스화돼 있는지
```

| 기록 항목 | 값 |
|---|---|
| GCP 리스너 바인드 주소:포트 | |
| 리스너 프로세스 (sshd 여부) | |
| 홈 서버 터널 프로세스 명령 형태(`-R` 인자, 사용자, 대상 호스트) | (비밀 없음) |
| 홈 서버 측 대상 주소:포트 (`<HOME_MONGO_TARGET>`) | |
| 서비스화 여부 (수동 / systemd / cron) | |
| 외부에서 27017 접속 | 실패해야 정상 |

> `docker-compose.yaml`의 `mongodb`는 호스트에 포트를 게시하지 않는다(`:107` 주석). 따라서 홈 서버 측 대상이 `127.0.0.1:27017`이라면 홈 서버에 별도 오버레이(README:198의 미추적 `compose.server.mongo.yaml` 류)나 다른 구성이 있다는 뜻이다. **현재 `-R` 인자를 그대로 옮겨 쓴다.**

### 3.2 autossh + systemd 적용

홈 서버에서 실행한다. 자리표시자는 3.1에서 기록한 실제 값으로 바꾼다.

| 자리표시자 | 의미 | 3.1에서 확인할 값 |
|---|---|---|
| `<TUNNEL_USER>` | 홈 서버에서 터널을 띄우는 로컬 사용자 | 현재 터널 프로세스의 사용자 |
| `<GCP_SSH_USER>` | GCP VM의 SSH 사용자 | 현재 `-R` 명령의 대상 사용자 |
| `<GCP_HOST>` | GCP VM 주소 | README 기록상 `35.216.13.110` |
| `<GCP_BIND>` | GCP 쪽 바인드 주소 | 현재 리스너 값(예: `172.17.0.1`) |
| `<HOME_MONGO_TARGET>` | 홈 서버 쪽 DB 주소:포트 | 현재 `-R` 인자의 뒷부분 |

**1) 전용 키와 GCP 측 제한**

```bash
# 홈 서버
sudo apt-get install -y autossh
sudo -u <TUNNEL_USER> ssh-keygen -t ed25519 -N '' -C econmind-mongo-tunnel -f /home/<TUNNEL_USER>/.ssh/econmind_tunnel
```

GCP VM의 `~<GCP_SSH_USER>/.ssh/authorized_keys`에 공개키를 **포워딩 전용**으로 등록한다.

```text
restrict,port-forwarding,permitlisten="<GCP_BIND>:27017" ssh-ed25519 AAAA...공개키... econmind-mongo-tunnel
```

`<GCP_BIND>`가 `127.0.0.1`이 아니면 GCP `sshd_config`에 `GatewayPorts clientspecified`가 필요하다(현재 터널이 이미 동작 중이면 설정돼 있을 것이다 — `sudo sshd -T | grep -i gatewayports`로 확인만 한다). 첫 접속 전에 GCP 호스트 키를 `known_hosts`에 등록한다(`StrictHostKeyChecking=yes`).

**2) systemd 유닛** — `/etc/systemd/system/econmind-mongo-tunnel.service`

```ini
[Unit]
Description=EconMind MongoDB reverse SSH tunnel to GCP (host.docker.internal:27017)
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=<TUNNEL_USER>
Environment=AUTOSSH_GATETIME=0
# -M 0: autossh 모니터 포트 대신 ServerAlive로 끊김 감지
ExecStart=/usr/bin/autossh -M 0 -N \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=yes \
  -o BatchMode=yes \
  -i /home/<TUNNEL_USER>/.ssh/econmind_tunnel \
  -R <GCP_BIND>:27017:<HOME_MONGO_TARGET> \
  <GCP_SSH_USER>@<GCP_HOST>
Restart=always
RestartSec=10
StartLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
```

- `ExitOnForwardFailure=yes`: GCP 쪽 27017을 못 잡으면(이전 세션이 남아 있음 등) 즉시 종료 → systemd가 재시도.
- 이전 세션이 GCP에 남아 포트를 점유하면 GCP `sshd_config`의 `ClientAliveInterval 30`·`ClientAliveCountMax 3`로 서버 쪽도 죽은 세션을 정리하게 한다(선택, sshd 재시작 필요 — 저부하 시간에).

**3) 전환** — 기존 수동 터널을 내리고 서비스를 올린다(짧은 DB 단절 발생).

```bash
sudo systemctl daemon-reload
sudo systemctl enable econmind-mongo-tunnel.service
# 기존 수동 터널 종료 (3.1에서 확인한 PID)
kill <OLD_TUNNEL_PID>
sudo systemctl start econmind-mongo-tunnel.service
systemctl status econmind-mongo-tunnel.service --no-pager
journalctl -u econmind-mongo-tunnel.service -n 30 --no-pager
```

**4) 복구 시험**

```bash
# 홈 서버: 강제로 끊고 자동 재연결 확인 (10~40초 내)
sudo systemctl kill -s KILL econmind-mongo-tunnel.service
sleep 30; systemctl is-active econmind-mongo-tunnel.service
# GCP: 3.1의 1)·4)·5) 재실행
```

**5) GCP 재부팅 대비 (선택)** — 터널이 붙은 뒤 `/ready`의 MongoDB·Redis 상태를 확인한다. `89dfb97`은 일시 초기화 실패를 재시도하므로 재시작을 필수 전제로 두지 않는다. 503이 지속되면 위 상태·로그 점검 후 대응한다. 운영 터널의 재부팅·자동 복구 시험과 타이머 추가는 아직 수행하지 않았다.

### 3.3 장기 대안 비교

| 항목 | A. DB를 GCP VM의 Compose로 이전 | B. MongoDB Atlas(관리형) | C. 터널 유지 + autossh/systemd (3.2) |
|---|---|---|---|
| 단일 장애점 | 홈 서버·터널 제거. VM 하나가 전부(이미 그렇다) | 홈 서버·터널 제거. Atlas 가용성에 의존 | 홈 서버 전원·가정 회선·IP 변동이 여전히 SPOF, 단 자동 재연결 |
| 기동 순서 문제 | `depends_on: condition: service_healthy`로 해결(`docker-compose.yaml:174`와 같은 방식) | 상시 가용이라 사실상 해소 | 남음 (3.2-5 수동 대응) |
| 벡터 검색(RAG) | `mongodb/mongodb-atlas-local:8.0.28` 사용 시 유지(README「이미지 선택」) | 기본 지원 | 현 상태 유지 |
| 자원·비용 | VM 메모리 +3GB 상한(mongodb `mem_limit: 3g`). 현재 서버 Compose 상한 합계 약 3.4GB → VM 메모리 확인 필요(`free -h`), 부족하면 머신 타입 상향 비용 | M0 무료(용량 512MB, 자동 백업 없음, 장기 미사용 시 일시중지·삭제 — README「MongoDB」: 이전 무료 클러스터가 미사용으로 삭제됨). 유료 Flex/전용 티어는 월 과금 — 공식 가격표 재확인 | 0원 |
| 백업 | VM 디스크 스냅샷 + `mongodump` 직접 | 유료 티어에 자동 백업 | 홈 서버에서 직접 |
| 보안 | 27017 미게시(compose 내부망만) — 현 README 원칙 그대로 | IP 접근 목록에 GCP 고정 IP(`35.216.13.110`)만 허용, TLS 기본 | 터널 바인드 주소·방화벽 확인 필요(3.1-6) |
| 작업량 | 중: 덤프/복원, 서버 Compose에 mongodb 서비스 추가(별도 PR), 초기화 스크립트·벡터 인덱스 재구축, `MONGODB_URI` 교체, 롤백 경로 | 중: 덤프/복원, 벡터 인덱스 재생성, `mongodb+srv://` URI로 교체(`directConnection` 제거), 접근 목록 설정 | 소: 1시간 |
| 데이터 위치 | 학교 과제용 GCP 프로젝트 | 외부 SaaS (리전 선택 필요) | 팀원 홈 서버 |
| 권장 | **11/26 발표 전 안정화 목표라면 1순위** | 비용·계정 관리 주체가 정해질 때 | **지금 바로(단기 완화)** |

권장: **C를 이번 주에 적용**하고, 발표·시연(11/26) 전에 A로 이전할지 팀이 결정한다. A·B 모두 코드/Compose 변경이 필요하므로 이 런북 범위 밖의 별도 PR로 한다.

### 3.4 완료 조건

- [ ] 3.1 기록표 작성(바인드 주소, 프로세스, 서비스화 여부, 외부 27017 차단 확인).
- [ ] `econmind-mongo-tunnel.service`가 `enabled`·`active`, 강제 종료 후 자동 복구, 복구 후 `/ready` ok.
- [ ] GCP `authorized_keys`의 터널 키가 `restrict,port-forwarding,permitlisten=…`으로 제한됨.
- [ ] 장기 대안 결정이 팀 회의 안건에 올라감.

### 3.5 되돌리기

```bash
sudo systemctl disable --now econmind-mongo-tunnel.service
# 3.1에서 기록한 기존 수동 터널 명령을 그대로 다시 실행
```

GCP `authorized_keys`에 추가한 줄을 지우고, `sshd_config`를 바꿨다면 백업본으로 되돌린 뒤 `sudo sshd -t && sudo systemctl reload ssh`.

---

## 4. 운영 `.env` 형상 기록 양식

**배경 (재분석 § 3.1, § 5-4).** 다음 5개 변수는 compose 기본값과 문서가 기록한 운영값이 반대다. **코드·compose만 읽으면 운영 설정을 반대로 추론한다.** 값이 아니라 "설정됨/비어 있음"과 비밀 아닌 플래그의 실효값만 기록한다.

| 변수 | compose 기본값 | 문서 기록 운영값 | 출처 |
|---|---|---|---|
| `NEWS_MAP_SELECTOR` | `embedding` (`compose.server.yaml:42`) | `decision` | README:149, 99-verification:52 |
| `PROTOTYPE_TIER` | `FREE` (`:30`) | `FREE` (10-11 배포) / 일부 문서 `PAID` | README:149 vs 재분석 § 3.1 표 — **불일치, 서버에서 확인** |
| `PAID_DEMO_ENABLED` | `false` (`:86`) | `false`(10-11 "PAID 데모 차단") / 일부 문서 `true` | 같음 — **불일치, 서버에서 확인** |
| `USE_RAG` | `true` (`:79`) | `false` | README:190, 99-verification:52 |
| `USE_CRITIC` | `true` (`:80`) | `true` | README:190 |

### 4.1 기록 양식

`deploy/.env` (GCP, `/home/econmind/capstone/deploy/.env`) — 기록일: `2026-10-__`, 기록자: 김건, 배포 SHA: `git -C /home/econmind/capstone/deploy rev-parse --short HEAD`

| 키 | 설정 여부 | 비밀 여부 | 비고/기대값 범주 |
|---|---|---|---|
| `MONGODB_URI` | | 비밀 | 필수(`:?`). 앱 계정, `host.docker.internal:27017`, `directConnection=true` 형태 |
| `MONGODB_DB_NAME` | | 아님 | `capstone_news` |
| `NAVER_CLIENT_ID` | | 비공개 | 필수 |
| `NAVER_CLIENT_SECRET` | | 비밀 | 필수 |
| `DIFFBOT_TOKEN` | | 비밀 | 필수 |
| `GOOGLE_API_KEY` | | 비밀 | 필수 |
| `OPENAI_API_KEY` | | 비밀 | `decision` 운영 시 필요 |
| `ECONMIND_TEAM_USER` | | 비공개 | 필수(Caddy만) |
| `ECONMIND_TEAM_PASSWORD_HASH` | | 비밀 | 필수, bcrypt, 작은따옴표로 감쌈 |
| `APP_ORIGIN` | | 아님 | `https://econmind.duckdns.org` |
| `NEWS_MAP_SELECTOR` | | 아님 | `embedding` \| `decision` |
| `PROTOTYPE_TIER` | | 아님 | `FREE` \| `PAID` |
| `PAID_DEMO_ENABLED` | | 아님 | `true` \| `false` (PAID와 짝) |
| `USE_RAG` | | 아님 | `true` \| `false` |
| `USE_CRITIC` | | 아님 | `true` \| `false` |
| `SESSION_ISSUE_CLIENT_HOURLY_LIMIT`, `SESSION_ISSUE_GLOBAL_HOURLY_LIMIT` | | 아님 | 정수 (기본 30/600) |
| `JOBS_`/`REPORTS_`/`STRATEGIES_`/`ANALYZE_CLIENT_HOURLY_LIMIT` | | 아님 | 정수 (기본 20/10/20/20) |
| `NEWS_MAP_CLIENT_HOURLY_LIMIT`, `NEWS_MAP_GLOBAL_HOURLY_LIMIT` | | 아님 | 정수 (기본 30/300) |
| `NEWS_MAP_DECISION_*` (5개) | | 아님 | 숫자 |
| `NEWS_MAP_EMBEDDING_*`, `NEWS_MAP_INITIAL_CANDIDATES`, `NEWS_MAP_MAX_CANDIDATES`, `NEWS_MAP_MIN_RELEVANCE`, `NEWS_MAP_KEYWORD_WEIGHT`, `NEWS_MAP_ENTITY_ONLY_PENALTY`, `NEWS_MAP_MMR_LAMBDA`, `NEWS_MAP_REPEAT_*`, `NEWS_MAP_SUPPLEMENT_*` | | 아님 | 숫자/모델명 |
| `V2_MAX_MAPS`, `V2_MAX_MAP_NODES`, `V2_MAX_MAP_DEPTH`, `V2_BRANCH_HISTORY_LIMIT`, `V2_BODY_*`, `V2_JOB_*` | | 아님 | 정수 |
| `V2_STATE_MEMORY` | | 아님 | **서버 Compose가 `"false"`로 고정**(`:29`) — `.env` 값은 무시됨 |
| `ANTHROPIC_API_KEY` | | 비밀 | **서버 Compose가 `""`로 고정**(`:74`) — `.env`에 있으면 지워도 됨 |
| `MONGODB_ROOT_*`, `MONGODB_APP_*`, `MONGODB_WT_CACHE_MB`, `USE_MONGODB`, `EMBEDDING_MODEL`, `NEWSAPI_KEY` | | 비밀/아님 | **서버 Compose가 읽지 않음**(`.env.example`에만 있음). GCP `.env`에 비밀이 남아 있으면 제거 검토 |

> 키 목록 근거: `compose.server.yaml`의 `${…}` 참조 58개 + `.env.example`. 같은 양식으로 DB 호스트 `.env`(`MONGODB_ROOT_*`, `MONGODB_APP_*`, `MONGODB_DB_NAME`, `MONGODB_WT_CACHE_MB`)도 따로 기록한다.

### 4.2 값을 출력하지 않는 점검 명령

```bash
cd /home/econmind/capstone/deploy

# (1) .env의 키와 SET/EMPTY만 — 값은 출력하지 않는다
env_state() { sudo awk -v sq="'" '
  /^[[:space:]]*(#|$)/ { next }
  {
    line = $0; sub(/^[[:space:]]*export[[:space:]]+/, "", line)
    eq = index(line, "="); if (eq == 0) next
    k = substr(line, 1, eq - 1); v = substr(line, eq + 1)
    gsub(/[[:space:]]/, "", k); gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)
    print k "\t" ((v == "" || v == "\"\"" || v == sq sq) ? "EMPTY" : "SET")
  }' .env | sort; }
env_state
env_state | cut -f1 | uniq -d    # 중복 키 — 출력이 있으면 정리한다(Compose는 마지막 값을 쓴다)

# (2) 서버 Compose가 필수(:?)로 요구하는 키가 .env에 비어 있지 않은지 ('' 같은 빈 따옴표도 EMPTY 처리)
for k in $(grep -oE '\$\{[A-Z0-9_]+:\?' compose.server.yaml | sed -E 's/^\$\{//; s/:\?$//' | sort -u); do
  if env_state | awk -v k="$k" '$1 == k && $2 == "SET" { f = 1 } END { exit !f }'; then echo "$k SET"; else echo "$k MISSING_OR_EMPTY"; fi
done

# (3) 비밀이 아닌 키만 허용 목록으로 값 출력 (.env 파일 기준)
ALLOW='^(NEWS_MAP_SELECTOR|PROTOTYPE_TIER|PAID_DEMO_ENABLED|USE_RAG|USE_CRITIC|MONGODB_DB_NAME|APP_ORIGIN)$'
sudo awk -v allow="$ALLOW" '
  /^[[:space:]]*(#|$)/ { next }
  { eq = index($0, "="); if (eq == 0) next
    k = substr($0, 1, eq - 1); gsub(/[[:space:]]/, "", k)
    if (k ~ allow) print k "=" substr($0, eq + 1) }' .env

# (4) 실행 중인 컨테이너의 "실효값" — .env에 없으면 compose 기본값이 들어간다. 같은 허용 목록만.
for s in econmind-api econmind-worker econmind-v2-worker; do
  echo "== $s"
  sudo docker compose -f compose.server.yaml exec -T "$s" sh -c \
    'for k in NEWS_MAP_SELECTOR PROTOTYPE_TIER PAID_DEMO_ENABLED USE_RAG USE_CRITIC MONGODB_DB_NAME; do printf "%s=%s\n" "$k" "$(printenv "$k")"; done'
done

# (5) 컨테이너 env의 키 이름만 (값 없음)
sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
  "$(sudo docker compose -f compose.server.yaml ps -q econmind-api)" | cut -d= -f1 | sort

# (6) 구조 점검 — 값이 출력되지 않는 것만
sudo docker compose -f compose.server.yaml config --services     # 서비스 이름만
sudo docker compose -f compose.server.yaml config -q && echo CONFIG_OK
```

> ⚠ **`docker compose -f compose.server.yaml config`(옵션 없이)나 `--format json`은 `.env`를 보간한 모든 값(API 키, `MONGODB_URI` 비밀번호 포함)을 출력한다. 실행 결과를 채팅·문서·PR에 붙이지 않는다.** CI가 이 출력을 쓰는 것은 더미 값(`.github/workflows/ci.yml` env)일 때뿐이다. `docker inspect`의 `Env`, 컨테이너 안 `printenv`(인자 없이), `env`도 같다.

(4)의 세 서비스 값이 서로 다르면 `.env` 변경 후 일부 서비스만 재생성된 것이다. 의도한 값으로 맞춘 뒤 세 서비스를 함께 재생성한다.

### 4.3 완료 조건

- [ ] 4.1 표가 SET/EMPTY로 채워지고, 필수 키 `MISSING_OR_EMPTY` 0건.
- [ ] 5개 핵심 플래그의 `.env` 값과 3개 백엔드 컨테이너 실효값이 기록되고 서로 일치(또는 차이 사유 기록).
- [ ] 기록에 비밀값·URI·해시가 한 글자도 없음. 날짜·배포 SHA 포함.
- [ ] 기록 위치: econmind-docs `docs/03-current-state.md` 또는 `99-verification.md`(값 없는 표), 팀 공유.

### 4.4 되돌리기

이 절은 읽기 전용이다. 되돌릴 변경이 없다. 기록 중 실수로 값이 출력·공유됐다면 해당 키를 즉시 회전하고(제공자 콘솔 또는 1.3 절차) 공유된 메시지를 삭제한다.

---

## 부록. 근거 대조표

이 런북이 명령에 쓴 이름과 그 출처. 서버에서 다르면 서버 값이 우선이다.

| 이름 | 이 런북의 사용 | 출처 |
|---|---|---|
| `econmind` (프로젝트) | `docker compose -f compose.server.yaml …` | `compose.server.yaml:3` |
| `econmind-api`, `econmind-worker`, `econmind-v2-worker` | `exec`/`restart`/`ps -q` | `compose.server.yaml` services |
| `mongodb`, `mongodb-tuner` (DB 호스트) | 컨테이너 탐색, 튜너 재생성 | `docker-compose.yaml:81,126` |
| `MONGODB_URI`, `MONGODB_DB_NAME` | 앱 연결 | `compose.server.yaml:15-16`, `app/config.py` |
| `MONGODB_ROOT_USERNAME`/`_PASSWORD` → `MONGODB_INITDB_ROOT_USERNAME`/`_PASSWORD` | root 계정·최초 초기화 | `docker-compose.yaml:89-90,133-134`, `.env.example` |
| `MONGODB_APP_USERNAME`/`_PASSWORD` | 앱 계정 생성(DB 호스트) | `docker-compose.yaml:93-94`, `mongo-init/01-app-user.js` |
| `capstone_news` | 대상 DB | `.env.example`, README「준비」 |
| `host.docker.internal:27017`, `directConnection=true` | 터널 경유 연결 | `.env.example`, README:196 |
| `/ready`의 `mongodb`·`redis` | 상태 확인 | `compose.server.yaml:163`, `app/main.py:94-112` |
| `NEWS_MAP_SELECTOR`, `PROTOTYPE_TIER`, `PAID_DEMO_ENABLED`, `USE_RAG`, `USE_CRITIC` | 형상 기록 | `compose.server.yaml:42,30,86,79,80`, `app/config.py` |
| 사용 중 컬렉션 `news`·`reports`·`strategies`·`news_bodies` | 정리 제외 | `app/database.py`, `app/v2/persistence.py` |
| 선언만 `news_analysis`·`news_relations`·`jobs`·`selections` | 확인만 | `app/database.py:24-29` |
| 현재 v2 리포트 필드 | 필터 구분 | `app/v2/reports.py:90-96,589`, `scripts/lib/newsmap-v2-schema.js:20-31` |
| 프로토타입 v2 리포트 필드 `sections`·`reuse_key`·`is_fallback`, 인덱스 `idx_reports_reuse_key` | 정리 필터 | econmind-docs `12-newsmap-v1-extension-implementation.md` § 5.1, :413, :439 |
