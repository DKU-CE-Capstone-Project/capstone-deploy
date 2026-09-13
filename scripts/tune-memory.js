// ─────────────────────────────────────────────────────────────────────────────
// WiredTiger 캐시 상한 적용 — 매 기동마다 실행된다 (mongodb-tuner 서비스)
//
// ── 왜 --wiredTigerCacheSizeGB 플래그를 안 쓰는가 ────────────────────────────
// mongodb/mongodb-atlas-local 은 CMD 가 ["/usr/local/bin/runner","server"] 이고,
// 그 runner 가 mongod + mongot 을 함께 띄우면서 mongod 의 인자를 스스로 조립한다.
// MongoDB 공식 문서가 명시한다:
//   "Remove any existing command from your docker-compose.yaml file. Because the
//    command in a Docker Compose definition overrides the ENTRYPOINT defined in
//    the mongodb-atlas-local image, you must remove any existing command for the
//    mongodb-atlas-local image to run as designed."
// 즉 compose 에서 command: 로 --wiredTigerCacheSizeGB 를 넘기면 mongot 이 안 뜨고
// $vectorSearch 가 죽는다. runner 는 mongod 플래그 패스스루도 제공하지 않는다.
// → 같은 효과를 런타임 파라미터로 낸다.
//
// ── 왜 초기화 스크립트가 아니라 별도 서비스인가 ──────────────────────────────
// /docker-entrypoint-initdb.d 는 '최초 기동 1회'만 실행된다. 재시작마다 확인해야
// 하므로 별도 서비스로 뺐다.
//
// ── 실측 결과 (2026-09-10) ───────────────────────────────────────────────────
// 이 환경에서는 mongod 가 cgroup 상한을 스스로 읽었다:
//   hostInfo.system.memSizeMB=7879 (호스트) / memLimitMB=3072 (컨테이너)
//   → (3072−1024)×50% = 1024MB 를 기동 시점에 자동 적용, down→up 후에도 유지.
// 즉 이 스크립트는 지금 '중복 안전장치'다. 그래도 남겨 두는 이유는 MongoDB 문서가
//   "WiredTiger may not account for the memory limits of the specific container
//    in certain cases"
// 라고 경고하기 때문 — 그 케이스에 걸리면 (호스트 RAM 7.7GB 기준) ~3.35GB 가 잡혀
// mem_limit 3g 를 넘기고 OOM-kill 루프에 빠진다. 비용은 컨테이너 하나 잠깐 도는 것뿐.
// ─────────────────────────────────────────────────────────────────────────────
const CACHE_MB = parseInt(process.env.MONGODB_WT_CACHE_MB || '1024', 10);
const admin = db.getSiblingDB('admin');

function mb(bytes) { return Math.round(bytes / 1048576); }

let host = {};
try { host = admin.runCommand({ hostInfo: 1 }); } catch (e) { /* 권한/버전 차이 무시 */ }
const memLimitMB = (host.system && host.system.memLimitMB) || 0;
const memTotalMB = (host.system && host.system.memSizeMB) || 0;

let before = 0;
try { before = db.serverStatus().wiredTiger.cache['maximum bytes configured']; } catch (e) { /* noop */ }

print(`[tune] host memSizeMB=${memTotalMB} memLimitMB=${memLimitMB} (memLimitMB 가 컨테이너 cgroup 상한)`);
print(`[tune] wiredTiger cache before = ${mb(before)} MB`);

try {
  admin.runCommand({ setParameter: 1, wiredTigerEngineRuntimeConfig: `cache_size=${CACHE_MB}M` });
  const after = db.serverStatus().wiredTiger.cache['maximum bytes configured'];
  print(`[tune] wiredTiger cache after  = ${mb(after)} MB (목표 ${CACHE_MB} MB)`);
  if (mb(after) > CACHE_MB * 1.1) {
    print('[tune] ⚠ 적용이 반영되지 않았다 — docker stats 로 mongodb 메모리를 반드시 확인할 것');
  }
} catch (e) {
  // 실패해도 스택 기동을 막지 않는다. mem_limit 이 하드 상한으로 남아 있다.
  print(`[tune] ⚠ setParameter 실패: ${e.codeName || e.message}`);
  print('[tune]   → mem_limit(3g) 만으로 버틴다. docker stats 로 실사용량 확인 필요.');
}
