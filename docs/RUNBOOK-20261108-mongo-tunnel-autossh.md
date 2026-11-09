---
title: MongoDB 역방향 SSH 터널 자동 복구 런북 (autossh + systemd)
project: EconMind
owner: 김건
created: 2026-11-08
status: 준비 완료 · 홈 서버와 GCP VM에 미적용
related:
  - docs/RUNBOOK-20261018-db-maintenance.md 3절
  - ops/mongo-tunnel/econmind-mongo-tunnel.service
  - ops/mongo-tunnel/mongo-tunnel.env.example
  - scripts/tunnel-recovery-probe.py
  - econmind-docs/docs/19-server-tasks-kimgeon.md 9단계
tags: [runbook, mongodb, ssh-tunnel, autossh, systemd]
---

# MongoDB 역방향 SSH 터널 자동 복구 런북

> **이 문서는 손으로 실행하는 절차서다.** 작성할 때 홈 서버와 GCP VM에 접속하지 않았다. 일회용 로컬 컨테이너 시험 결과와 실제 환경에서 시험하지 못한 항목은 [8절](#8-시험-범위)에 있다.
>
> 터널 자동화는 이 문서를 따른다. DB 유지보수 런북 3.1의 상태 확인은 그대로 쓰고, 3.2의 unit은 쓰지 않는다. 3.2와 달라진 점은 다음과 같다.
> - `StartLimitIntervalSec`를 `[Service]`에서 systemd가 인식하는 `[Unit]`으로 옮겼다.
> - 값을 환경 파일로 분리하고 터널 전용 시스템 계정을 쓴다.
> - 정상 `stop`이 `failed`로 표시되지 않게 `SuccessExitStatus=1`을 넣었다.
>
> **비밀값 규칙.** 실제 호스트, 주소, 포트, 키, 수동 터널 명령을 Git, Notion, 채팅, PR에 적지 않는다. 기록표의 실제 값은 비밀번호 관리자나 홈 서버의 `0600` 파일에만 둔다. 서버의 `.env`는 이 작업에 필요 없으므로 열지 않는다.

## 1. 언제 하는가

전환과 복구 시험은 각각 DB 연결을 잠깐 끊는다. 한 시간대 안에 이어서 한다.

| 날짜 | 판단 | 이유 |
|---|---|---|
| 11-10 | 피한다 | 기능 배포일이라 장애 원인이 섞인다 |
| **11-11 ~ 11-12, 저부하 시간** | **권장** | 배포 뒤이고 실측 전이다 |
| 11-13 ~ 11-15 | **금지** | 조정된 실측 기간이다. 제안서의 "11-13 적용"은 이 기간과 겹치므로 따르지 않는다 |
| 11-15 실측 종료 뒤 ~ 11-16 동결 배포 전 | 11-12까지 못 했을 때의 예비 | 동결 배포와 시간을 분리한다 |
| 11-16 동결 배포 | 피한다 | 배포 검증과 섞인다 |
| 11-17 ~ 11-24 | 필요한 경우만 | 팀에 시간을 알린 뒤 한다 |
| 11-25 ~ 11-26 | **금지** | 리허설과 발표다. 이 기간에 적용되지 않았다면 수동 터널을 유지하고 당일 점검표로 대응한다 |

시작 전에 실측 일정이 바뀌지 않았는지 문주안에게 확인하고, 진행 중인 리포트 작업이 없는 시간을 고른다. 전환 시각은 김건이 팀에 직접 알린다.

## 2. 채워야 할 값

방향은 문서로 확인된다. 홈 서버가 GCP VM으로 접속해 `-R`로 포트를 연다. 나머지 실제 값은 저장소에 기록이 없으므로 DB 유지보수 런북 3.1의 읽기 전용 명령으로 확인한다.

| 항목 | 문서에서 확인된 내용 | 김건이 채울 값 | 환경 파일 변수 |
|---|---|---|---|
| 터널 방향 | 홈 서버 → GCP VM, `-R`. 런북 3.2, docs/19 9단계 | 실제 명령과 일치하는지 | |
| GCP SSH 계정 | 없음 | 현재 명령의 대상 계정 | `GCP_SSH_USER` |
| GCP 주소 | 런북에 과거 주소 기록 있음. 재확인 필요 | 현재 명령의 대상 주소 | `GCP_SSH_HOST` |
| GCP SSH 포트 | 없음 | 현재 명령의 `-p` 값. 없으면 SSH 기본 포트 | `GCP_SSH_PORT` |
| GCP 리스너 주소 | 없음. 후보만 있음(런북 3.1) | `ss -tlnp`로 본 값 | `TUNNEL_REMOTE_BIND` |
| GCP 리스너 포트 | 앱 URI 형태가 `host.docker.internal:27017`. 실제 `-R`은 기록 없음 | `ss -tlnp`로 본 값 | `TUNNEL_REMOTE_PORT` |
| 홈 쪽 MongoDB 주소와 포트 | 없음. `docker-compose.yaml`의 `mongodb`는 호스트에 포트를 게시하지 않음 | 현재 `-R` 인자의 뒷부분 | `TUNNEL_LOCAL_HOST`, `TUNNEL_LOCAL_PORT` |
| 수동 터널 PID와 전체 명령 | 없음 | 홈 서버 `ps` 결과. 되돌릴 때 그대로 쓴다 | 환경 파일에 넣지 않음 |
| 수동 터널을 띄우는 주체 | 없음 | tmux, nohup, cron, 재시작 루프 중 무엇인지 | 환경 파일에 넣지 않음 |

**중단 조건.** GCP에서 해당 포트의 리스너가 `sshd`가 아니면 이 절차를 멈춘다. 지금 구조가 문서와 다르다는 뜻이다.

컨테이너는 `host.docker.internal`(host-gateway)로 접속하므로 리스너가 `127.0.0.1`이면 컨테이너에서 닿지 않을 수 있다. 현재 값을 바꾸지 말고 그대로 옮긴다. 리스너 주소가 루프백이 아니면 `GatewayPorts clientspecified`가 필요하다.

GCP VM에서 다음 값을 읽어 기록한다. 바꾸지는 않는다.

```bash
sudo sshd -T | grep -iE '^(gatewayports|clientaliveinterval|clientalivecountmax|allowtcpforwarding) '
```

`clientaliveinterval 0`이면 홈 회선이 조용히 끊겼을 때 GCP 쪽 죽은 세션이 포트를 오래 잡을 수 있다. 이 동안 autossh는 `remote port forwarding failed`로 재시도만 반복한다. sshd 설정 변경은 VM 전체에 영향을 주므로 이번 절차에 넣지 않았다. 값을 팀에 공유하고 김건이 변경 여부를 결정한다.

## 3. 설치 (서비스 영향 없음)

수동 터널을 건드리지 않는 단계다. 저장소의 `ops/mongo-tunnel/` 파일 두 개를 홈 서버의 작업 폴더로 옮긴 뒤 진행한다.

**1) 패키지와 전용 계정, 키 (홈 서버)**

```bash
sudo apt-get update && sudo apt-get install -y autossh
sudo useradd --system --create-home --home-dir /var/lib/econmind-tunnel --shell /usr/sbin/nologin econmind-tunnel
sudo install -d -m 700 -o econmind-tunnel -g econmind-tunnel /var/lib/econmind-tunnel/.ssh
sudo -u econmind-tunnel ssh-keygen -q -t ed25519 -N '' -C econmind-mongo-tunnel -f /var/lib/econmind-tunnel/.ssh/econmind_tunnel
sudo cat /var/lib/econmind-tunnel/.ssh/econmind_tunnel.pub
```

무인 재접속을 위해 키에 암호를 걸지 않는다. 아래 제한으로 포워딩 외의 용도를 막는다.

**2) GCP VM에 포워딩 전용으로 키 등록**

`<GCP_SSH_USER>`의 `~/.ssh/authorized_keys`에 한 줄을 추가한다. 기존 줄은 수정하지 않는다. 수동 터널의 기존 키도 되돌리기에 필요하므로 지우지 않는다.

```text
restrict,port-forwarding,permitlisten="<GCP_LISTEN_ADDRESS>:<GCP_LISTEN_PORT>" ssh-ed25519 AAAA...공개키... econmind-mongo-tunnel
```

**3) GCP 호스트 키 등록 (홈 서버)**

지문을 GCP에서 직접 본 값과 비교한 뒤 등록한다.

```bash
ssh-keyscan -p <GCP_SSH_PORT> -t ed25519 <GCP_SSH_HOST> > /tmp/econmind-host-key
ssh-keygen -lf /tmp/econmind-host-key
# GCP VM에서: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub  -> 두 지문이 같아야 한다
sudo install -m 600 -o econmind-tunnel -g econmind-tunnel /tmp/econmind-host-key /var/lib/econmind-tunnel/.ssh/known_hosts
rm /tmp/econmind-host-key
```

**4) 환경 파일과 unit 설치 (홈 서버)**

```bash
sudo install -d -m 755 /etc/econmind
sudo install -m 600 -o root -g root mongo-tunnel.env.example /etc/econmind/mongo-tunnel.env
sudo -e /etc/econmind/mongo-tunnel.env          # 2절 값으로 CHANGEME를 모두 바꾼다
sudo grep -c CHANGEME /etc/econmind/mongo-tunnel.env   # 0이어야 한다
sudo install -m 644 -o root -g root econmind-mongo-tunnel.service /etc/systemd/system/econmind-mongo-tunnel.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/econmind-mongo-tunnel.service
```

**5) 끊지 않고 인증 확인 (홈 서버)**

수동 터널이 포트를 잡고 있는 상태에서 새 키로 접속만 시험한다.

```bash
sudo bash -c 'set -a; . /etc/econmind/mongo-tunnel.env; set +a; timeout 25 sudo -u econmind-tunnel ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=15 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$TUNNEL_KNOWN_HOSTS" -i "$TUNNEL_IDENTITY_FILE" -p "$GCP_SSH_PORT" -R "$TUNNEL_REMOTE_BIND:$TUNNEL_REMOTE_PORT:$TUNNEL_LOCAL_HOST:$TUNNEL_LOCAL_PORT" "$GCP_SSH_USER@$GCP_SSH_HOST"'
```

| 출력 | 뜻 | 조치 |
|---|---|---|
| `remote port forwarding failed for listen port` | **기대 결과.** 인증과 호스트 키가 통과했고 포트는 수동 터널이 쓰는 중이다 | 4절로 간다 |
| `Permission denied` | 키가 등록되지 않았거나 계정이 다르다 | 2)를 고친다 |
| `Host key verification failed` | known_hosts가 맞지 않다 | 3)을 고친다 |
| 출력 없이 25초 뒤 종료 | 포트가 비어 있어 포워딩이 열렸다. 2절 기록과 다르다 | 멈추고 기록을 다시 확인한다 |

이 시험은 `permitlisten` 제한이 전환 포트를 허용하는지까지는 보여주지 못한다. 허용하지 않으면 4절 전환에서 드러나므로 5절로 되돌린다.

## 4. 전환 (DB 연결이 끊긴다)

1절의 시간대에만 한다. 전환 시작 전에 2절 기록표의 수동 터널 명령을 다시 확인한다.

```bash
# 홈 서버. 수동 터널을 다시 띄우는 부모가 있으면 먼저 그것부터 멈춘다.
ps -o pid,ppid,user,lstart,args -p <OLD_TUNNEL_PID>
sudo systemctl enable econmind-mongo-tunnel.service
sudo kill <OLD_TUNNEL_PID> && sudo systemctl start econmind-mongo-tunnel.service
systemctl status econmind-mongo-tunnel.service --no-pager
journalctl -u econmind-mongo-tunnel.service -n 30 --no-pager
```

| 구간 | 언제 | 서비스 영향 | 예상 길이 |
|---|---|---|---|
| 3절 설치 전체 | 전환 전 | 없음 | 끊김 없음 |
| 수동 터널 종료 ~ 새 터널 연결 | `kill` 직후 | MongoDB 접속 불가. `/ready` 503 | 수 초 ~ 약 20초 (실측 전 추정) |
| GCP에 남은 이전 세션이 포트를 잡는 동안 | 위와 겹칠 수 있음 | 위와 같음. 로그에 `remote port forwarding failed`가 반복된다 | 재시도 간격은 수 초 ~ 약 10초 |
| 전환 뒤 `/ready` 회복 | 터널 복구 직후 | 없음 | 코드 기준 대개 1초 이내, 최악 약 8.5초 + 조회 간격 |

전환 중 진행 중인 리포트와 분석 작업은 실패로 끝날 수 있고 자동으로 다시 실행되지 않는다(7절). 끊김이 90초를 넘으면 API 컨테이너가 `unhealthy`가 될 수 있으며, `restart: unless-stopped`는 이것만으로 재시작하지 않는다.

**판정.** 5분 안에 `/ready`가 `status=ok`, `mongodb=on`으로 돌아오지 않으면 원인 분석을 멈추고 5절로 되돌린다.

## 5. 되돌리기

```bash
# 홈 서버
sudo systemctl disable --now econmind-mongo-tunnel.service
# 2절에서 기록한 수동 터널 명령을 원래 방식(tmux 등)으로 그대로 다시 실행한다
```

`/ready`가 돌아온 것을 확인한 뒤, 완전히 제거할 때만 다음을 한다.

```bash
sudo rm /etc/systemd/system/econmind-mongo-tunnel.service /etc/econmind/mongo-tunnel.env
sudo systemctl daemon-reload && sudo systemctl reset-failed
sudo userdel --remove econmind-tunnel
```

GCP의 `authorized_keys`에서 `econmind-mongo-tunnel` 줄을 지운다.

## 6. 복구 시험 (1회 측정)

전환과 같은 시간대에 이어서 한다. DB가 다시 약 10~20초 끊긴다. 모든 시각은 GCP VM의 한 시계로 잰다.

**필요 권한.** GCP VM에서 컨테이너를 실행할 권한이 필요하다. 11-08 기준 확인된 작업 계정에는 docker 소켓 권한이 없었으므로, 권한 있는 계정을 미리 정한다.

**실행 순서**

1. 터미널 A(GCP VM)에서 측정을 시작한다. 포트는 2절의 `TUNNEL_REMOTE_PORT`다. 저장소에 있는 스크립트 경로로 실행한다.

   ```bash
   API=$(sudo docker ps --filter label=com.docker.compose.service=econmind-api --format '{{.Names}}'); echo "$API"
   sudo docker inspect --format '{{.Config.Image}}' "$API"       # 이미지 이름을 기록한다
   sudo docker exec -i "$API" python -u - --mongo-port <GCP_LISTEN_PORT> --duration 180 < scripts/tunnel-recovery-probe.py
   ```

2. 터미널 A에 `tcp UP`, `mongo UP`, `ready UP`이 모두 나온 뒤 30초를 기다린다. 하나라도 `DOWN`이면 시험을 시작하지 않는다.
3. 터미널 B(홈 서버)에서 강제로 끊는다.

   ```bash
   systemctl show -p NRestarts econmind-mongo-tunnel.service
   sudo systemctl kill -s KILL econmind-mongo-tunnel.service
   ```

4. 180초가 지나면 터미널 A에 요약이 출력된다. `docker exec`는 `Ctrl+C`를 컨테이너로 전달하지 않아 요약을 못 볼 수 있으니 끝날 때까지 기다린다. `미복구`가 나오면 4절의 판정 기준을 따른다.
5. 터미널 B에서 확인한다.

   ```bash
   systemctl is-active econmind-mongo-tunnel.service
   systemctl show -p NRestarts econmind-mongo-tunnel.service
   journalctl -u econmind-mongo-tunnel.service -n 20 --no-pager
   ```

**출력 읽기.** `tcp`는 GCP 리스너, `mongo`는 새 연결로 보낸 ping, `ready`는 앱이 가진 기존 연결의 상태다. `tcp UP`인데 `mongo DOWN`이면 포트는 열렸지만 홈 쪽 DB까지 닿지 않는다는 뜻이다. GCP에 죽은 세션이 남았거나 홈 서버 쪽 대상에 문제가 있는 경우다.

**기록표** (`econmind-docs` `99-verification.md`에 옮긴다. 호스트, 주소, 포트는 적지 않는다)

| 항목 | 값 |
|---|---|
| 일시 (KST), 수행자 | |
| API 이미지 | |
| 시나리오 | `systemctl kill -s KILL` 1회 |
| 측정 전 `NRestarts` / 측정 후 | |
| 첫 실패 관측 시각 (T_down, UTC) | |
| GCP 리스너 복구 (`tcp`, T_down 기준 초) | |
| MongoDB 재연결 (`mongo`, 초) | |
| `/ready` 복구 (`ready`, 초) | |
| 복구 뒤 `/ready` | `status`, `mongodb`, `redis` 값 |
| 앱 또는 컨테이너를 재시작했는가 | 아니오 / 예(이유) |
| 측정 중 실패한 작업 수 | |
| 판정 | 통과 / 실패 |

**판정 기준.** 다음을 모두 만족하면 통과다.

- 터널이 30초 안에 복구된다(docs/19 9단계 기준).
- `/ready`가 재시작 없이 복구된다.
- 복구 뒤 `NRestarts`가 1 늘고 서비스가 `active`다.

이 시험은 systemd 재시작 경로 한 가지만 잰다. 회선이 조용히 끊기는 경우와 재부팅 복구는 측정하지 않는다.

## 7. 애플리케이션이 스스로 복구하는가

`capstone-backend` `origin/main` `ff15651`을 읽어 확인했다. `app/database.py`와 `app/v2/persistence.py`는 `89dfb97`, 11-08 기록의 실행 이미지 `80e5dd0`과 내용이 같다. 실행 중 이미지는 6절에서 다시 기록한다.

| 대상 | 터널이 끊겼을 때 | 터널이 돌아온 뒤 | 근거 |
|---|---|---|---|
| API `/ready` | 3초 제한 ping 실패로 503 | **재시작 없이 200으로 복구** | `app/main.py:97-114`, `app/database.py:54` |
| API `/health` | `mongodb: on`으로 계속 응답 | 해당 없음. **터널 판단에 쓰지 않는다** | `app/database.py:240` |
| Mongo 클라이언트 | 프로세스 전체에 하나를 유지하고 비활성화 플래그가 없다 | 드라이버 모니터가 자동 재연결 | `app/database.py:207-236` |
| API 시작 시 DB 없음 | 경고만 남기고 떠 있다. `/ready`는 503 | 복구 뒤 `/ready`에서 인덱스를 마저 만든다 | `app/database.py:255-257` |
| v1 워커 진행 중 작업 | 예외가 나면 작업을 `error`로 저장하고 ack한다 | **자동 재시도 없음.** 사용자가 다시 요청해야 한다 | `app/worker.py:45-46,69` |
| v2 워커 진행 중 작업 | 저장 실패는 `failed`로 끝나고 ack된다 | **재전달 없음.** 다시 요청해야 한다 | `app/v2/reports.py:690` |
| v2 워커 시작 시 DB 없음 | 예외로 프로세스가 끝난다 | `restart: unless-stopped`로 다시 뜬다 | `app/v2/report_worker.py:137-138`, `compose.server.yaml` |

터널이 돌아온 뒤 `/ready` 회복 시간은 코드와 드라이버 동작에서 추정한 값이다. 대개 다음 조회에서 1초 안에 회복되고, 드라이버의 연결 시도가 걸려 있으면 약 8.5초에 조회 간격을 더한 만큼 걸릴 수 있다. `connectTimeoutMS=8000`이 근거다.

**주의할 예외.** 인덱스 초기화 도중 터널이 끊기면 개별 인덱스 실패를 건너뛰고도 완료로 표시한다(`app/database.py:273-277`). API가 막 시작한 직후에 복구 시험을 하지 않는다.

`/ready`가 503에 머물면 DB 권한, Redis, NATS, 로그를 먼저 본다. 재시작은 원인을 확인한 뒤에 한다.

## 8. 시험 범위

### 로컬에서 확인한 것

Docker의 일회용 컨테이너 세 개로 시험했다. systemd가 도는 Debian 12(홈 서버 역할), sshd(VM 역할), MongoDB다. 실제 unit과 환경 파일 형식, `restrict,port-forwarding,permitlisten` 키 제한을 그대로 썼다. `/ready`는 앱과 같은 motor 설정(`serverSelectionTimeoutMS=8000`, `connectTimeoutMS=8000`, 긴 수명의 클라이언트 하나, 3초 ping)을 쓰는 대역 스크립트로 대신했다. 시간은 1초 간격 측정값이다.

| 시나리오 | 결과 |
|---|---|
| `systemd-analyze verify`, 부팅 등록, 실행 인자 | 통과. `-M 0`과 모든 `-o` 옵션 적용 확인 |
| `systemctl kill -s KILL` | 리스너 11.0초, `mongo` 10.9초, `ready` 12.1초. 대역 앱 재시작 없음. `NRestarts` 1 증가 |
| VM 쪽에서 SSH 세션 강제 종료 | autossh가 같은 초에 재접속. 1초 간격 측정에서 끊김 미관측 |
| 100초 무응답 단절 | 재접속 없이 같은 세션이 살아났다. `ready`는 첫 실패 뒤 112.5초에 복구 |
| 150초 무응답 단절 | autossh가 재접속. 회선 복구 뒤 29초에 `ready` 복구. 그동안 이전 세션 때문에 `remote port forwarding failed` 반복 |
| 정전 후 부팅 (같은 IP) | 부팅 자동 시작 확인. 첫 실패 뒤 약 19초에 복구. 이전 세션 때문에 포워딩 실패가 일시적으로 반복 |
| VM 재시작 | autossh가 재접속. 대역 앱을 띄운 뒤 `ready` 정상 |
| `systemctl stop` | `inactive`로 끝나고 다시 뜨지 않음 |
| autossh만 종료(SIGTERM) | systemd가 다시 띄움 |

정전 시나리오에서 이전 세션이 빨리 풀린 것은 홈 쪽 IP가 같아 포워딩 시도에 RST로 응답했기 때문으로 보인다. `ClientAliveInterval 10`을 설정한 경우와 결과 차이가 없어 이 설정의 효과는 입증하지 못했다.

### 실제 환경에서 시험하지 못한 것

| 항목 | 이유 또는 확인 시점 |
|---|---|
| 실제 `-R` 값, 리스너 주소, 방화벽 | 2절에서 확인 |
| 홈 서버 OS와 autossh, systemd 버전. `ProtectSystem=strict`와의 호환 | 설치 때 확인 |
| GCP 쪽 `permitlisten`이 실제 포트와 주소를 허용하는지. 루프백이 아닌 주소에서의 동작 | 전환 때 확인 |
| 실제 `econmind-api` 컨테이너의 `/ready` 복구 시간과 NATS, Redis 영향 | 6절 측정 |
| 진행 중 작업이 있을 때의 실패 건수 | 측정하지 않음 |
| 홈 공인 IP가 바뀌거나 공유기가 재시작된 경우. 이때 GCP 쪽 세션이 남는 시간 | 재현하지 못함. `ClientAlive` 값에 따라 달라짐 |
| 재부팅 후 홈 서버 MongoDB가 뜨는 순서와 터널의 관계 | 실제 재부팅을 하지 않음 |
| GCP VM 재부팅 뒤 앱 전체 복구 | 실제 재부팅을 하지 않음 |

## 9. 완료 조건

- [ ] 2절 기록표가 안전한 곳에 있다.
- [ ] 서비스가 `enabled`이고 `active`다.
- [ ] 6절 기록표가 `99-verification.md`에 값 없이 기록됐다.
- [ ] 터널 키가 `authorized_keys`에서 포워딩 전용으로 제한돼 있다.
- [ ] 수동 터널이 남아 있지 않고 다시 뜨지 않는다.
