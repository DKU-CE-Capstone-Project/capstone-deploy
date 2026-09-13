# GCP 서버: 세 저장소 자동 통합 배포

대상: `35.216.13.110` / 서비스: https://econmind.duckdns.org

각 개발 저장소의 `main`이 변경되면 GitHub Actions의 `CI`가 실행됩니다.
서버의 systemd timer가 약 2분마다 네 저장소(개발 3개 + 배포 설정)의 `main`을 확인합니다.
정확한 커밋의 CI가 모두 성공해야 세 소스의 고정된 스냅샷을 함께 빌드합니다.
CI 대기·실패 상태는 GitHub API 제한을 고려해 5분 간격으로 재확인합니다.
GitHub에 서버 SSH 개인키나 PAT를 등록할 필요가 없습니다. 공개 저장소를 서버가 읽습니다.

| 저장소 | 배포 시 역할 |
| --- | --- |
| capstone-frontend | TypeScript 검사와 Vite 빌드를 거친 프론트 이미지 |
| capstone-backend | API·워커 공용 이미지 |
| capstone-news-logic | 해당 이미지에 설치하는 Python 패키지. GDELT·Diffbot 구현을 백엔드에서 직접 사용 |
| capstone-deploy | 통합 Dockerfile, 배포 제어 프로그램, 운영 설명서 |

## 개발 규칙

1. 각 저장소에서 기능 브랜치를 만들고 PR로 `main`에 반영합니다.
2. GitHub Actions의 `CI` 결과를 확인합니다. 실패한 커밋은 서버에 배포되지 않습니다.
3. 프론트만 변경해도 전체 조합의 테스트와 빌드를 확인합니다. 뉴스 로직 변경은 API·워커 이미지에 함께 반영됩니다.
4. 저장소 사이의 API를 바꿀 때는 먼저 호환성을 유지하는 변경을 배포한 뒤 호출부를 변경합니다.
   여러 저장소의 `main` 커밋은 하나의 원자적 커밋이 아니므로 호환되지 않는 중간 상태는 피합니다.

서버는 빌드 후에도 각 `main`이 그대로인지 다시 확인합니다. 빌드 중 새 커밋이 도착하면 다음 확인에서 다시 빌드합니다.
GitHub CI 외에 서버에서도 그 조합의 Python 테스트와 프론트 빌드를 실행합니다.
API 상태, 프론트 HTML, 데모 뉴스 검색, NATS→워커→Redis 작업 완료를 확인한 뒤 성공으로 기록합니다.
실서비스 모드에서는 유료 호출을 피하기 위해 데모 검색·작업 검사는 생략합니다.

## 최초 설치 / 배포 제어 프로그램 업데이트

기존 VM에서 `econmind` 사용자로 실행합니다. 기존 Compose 서비스가 정상 동작해야 합니다.

```bash
git clone https://github.com/DKU-CE-Capstone-Project/capstone-deploy.git ~/capstone-deploy
cd ~/capstone-deploy
bash gcp/install.sh
```

설치 스크립트는 `gcp/deploy.py`를 `/home/econmind/econmind-cd/controller/deploy.py`로 복사하고
systemd service/timer를 설치합니다. **제어 프로그램 자체를 수정한 경우 설치 스크립트를 다시 실행**합니다.
앱 코드와 통합 Dockerfile 변경은 자동으로 적용됩니다.

기존 `/home/econmind/econmind-gcp-deploy/compose.yaml`, `.env`, `nginx.conf`, `Caddyfile`을 계속 사용하고,
배포마다 이미지 태그만 별도 Compose override로 지정합니다. 서버의 환경변수와 HTTPS·볼륨 설정을 유지합니다.
최초 설정은 데모 모드이며 실제 뉴스·LLM 키 활성화는 별도 작업입니다.

## 확인과 운영

```bash
systemctl status econmind-deploy.timer
journalctl -u econmind-deploy.service -n 100 --no-pager
python3 ~/econmind-cd/controller/deploy.py --status
```

배포 중에는 서버 파일 잠금으로 다른 배포 실행을 막습니다. 성공한 커밋 조합과 이미지 태그는
`~/econmind-cd/state/current.json`, 직전 버전은 `previous.json`에 기록됩니다.
원본 소스 체크아웃은 `~/econmind-gcp-deploy/backend`, `frontend`에 남아 있고,
실제 새 배포 소스는 `~/econmind-cd/releases/<release-id>/`에 보관됩니다.
**기존 폴더에서 `docker compose up --build`를 실행하면 예전 코드로 되돌아갈 수 있습니다.**
현재 배포의 Compose 명령에는 `current.json`에 있는 `override` 파일도 함께 사용하세요.

상태 확인 실패 시 이전 이미지로 자동 복구합니다. 같은 실패 조합은 새 커밋이나 명시적 재시도 전까지 재배포하지 않습니다.
중단 중이던 배포는 `pending.json` 기록을 이용해 다음 실행에서 이전 이미지로 복구합니다.
현재·직전 버전과 최근 3개 릴리스를 보관하고 오래된 해당 배포 이미지·소스만 정리합니다.
Docker 빌드 캐시는 별도 보관되므로 디스크 사용량도 확인하세요. 빌드 시작 전 여유 공간이 3GiB 미만이면 중단합니다.

```bash
# 즉시 확인 / 실패 조합 재시도 (타이머를 기다리지 않음)
python3 ~/econmind-cd/controller/deploy.py --retry

# 일시 정지: 진행 중 배포는 완료되고 다음 자동배포부터 멈춤
touch ~/econmind-cd/state/paused

# 수동 복구 후 자동배포 일시 정지
python3 ~/econmind-cd/controller/deploy.py --rollback

# 재개
rm -f ~/econmind-cd/state/paused
sudo systemctl start econmind-deploy.service
```

자동배포 로그는 systemd journal에 남습니다. GitHub Actions의 성공은 CI 성공을 뜻하며,
서버 배포의 최종 성공 여부는 `current.json`과 journal에서 확인합니다.
이미지 교체 중 짧은 연결 중단이 있을 수 있으며 API 메모리의 뉴스·보고서 캐시는 초기화됩니다.
Redis·NATS·HTTPS 인증서의 기존 Docker 볼륨은 유지됩니다.

검증:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile gcp/deploy.py
```
