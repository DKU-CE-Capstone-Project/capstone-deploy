// ─────────────────────────────────────────────────────────────────────────────
// DB 검증 — 완료 기준 자가 점검
//   docker compose exec -T -e HOME=/tmp mongodb sh -c \
//     'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
//        --quiet --file /scripts/verify-db.js'
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const d = db.getSiblingDB(DB_NAME);

const EXPECTED = ['news', 'news_analysis', 'news_relations', 'mindmaps',
                  'reports', 'strategies', 'jobs', 'users'];

let failures = 0;
function check(label, ok, detail) {
  print(`${ok ? '  ok  ' : ' FAIL '} ${label}${detail ? ' — ' + detail : ''}`);
  if (!ok) failures += 1;
}

print(`\n=== ${DB_NAME} 검증 ===\n`);

// 1) 컬렉션 8개
const names = d.getCollectionNames();
const missing = EXPECTED.filter((n) => !names.includes(n));
check('컬렉션 8개', missing.length === 0, missing.length ? `누락: ${missing.join(', ')}` : names.filter(n => EXPECTED.includes(n)).sort().join(', '));

// 2) 인덱스 + validator
for (const name of EXPECTED) {
  if (!names.includes(name)) continue;
  const idx = d[name].getIndexes().map((i) => i.name).filter((n) => n !== '_id_');
  const info = d.getCollectionInfos({ name })[0] || {};
  const opts = info.options || {};
  const action = opts.validationAction || '(none)';
  const hasValidator = !!(opts.validator && opts.validator.$jsonSchema);
  check(`${name}: validator=${action}, 인덱스 ${idx.length}개`,
        hasValidator && idx.length > 0, idx.join(', '));
}

// 3) validator 가 실제로 거부하는지 — 설계 위반 문서를 일부러 넣어 본다
//    (source 를 문자열로: 현재 백엔드가 저지르는 바로 그 실수)
let rejected = false;
try {
  d.news.insertOne({ title: '검증용', url: 'https://example.invalid/validator-probe', source: 'Reuters' });
} catch (e) {
  rejected = e.code === 121;   // DocumentValidationFailure
}
check('news validator 가 설계 위반 문서를 거부', rejected,
      rejected ? 'source 문자열 → code 121' : '거부되지 않았다(!)');
d.news.deleteOne({ url: 'https://example.invalid/validator-probe' });

// 4) 벡터검색 인덱스
let vec = [];
try { vec = d.news.getSearchIndexes('vector_index'); } catch (e) { /* mongot 미기동 */ }
check('news.vector_index (768d cosine)', vec.length > 0,
      vec.length ? `status=${vec[0].status}` : 'mongot 미기동이거나 아직 생성 전 — 앱 startup 이 재시도한다');

// 5) 메모리 — 여유 1GB 이상 남는지 판단할 근거값
try {
  const h = db.getSiblingDB('admin').runCommand({ hostInfo: 1 });
  const cacheMB = Math.round(db.serverStatus().wiredTiger.cache['maximum bytes configured'] / 1048576);
  print(`\n  memLimitMB=${h.system.memLimitMB}  wiredTigerCache=${cacheMB} MB  문서수(news)=${d.news.countDocuments()}`);
  check('wiredTiger 캐시 ≤ 1.2GB', cacheMB <= 1229, `${cacheMB} MB`);
} catch (e) {
  print(`  (메모리 정보 조회 실패: ${e.message})`);
}

print(`\n=== ${failures === 0 ? '전부 통과' : failures + '건 실패'} ===\n`);
