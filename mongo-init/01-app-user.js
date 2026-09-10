// ─────────────────────────────────────────────────────────────────────────────
// 01 — 앱 전용 계정 생성
//
// 최초 기동 시 1회만 실행된다(/docker-entrypoint-initdb.d 규약).
// root 계정은 이미지가 MONGODB_INITDB_ROOT_USERNAME/PASSWORD 로 먼저 만들어 두고,
// 이 스크립트는 그 root 권한으로 실행된다.
//
// 앱 계정은 admin DB에 만들고 capstone_news 에만 readWrite 를 준다.
//   → 접속 URI: mongodb://<user>:<pw>@mongodb:27017/capstone_news?authSource=admin
//     (authSource=admin 이 되는 이유. app/config.py 의 MONGODB_URI 형식과 일치)
//
// readWrite 에는 createSearchIndexes / listSearchIndexes 가 포함되어 있어
// app/database.py 의 ensure_vector_index() 가 그대로 동작한다. root 를 앱에 주지 않는다.
// ─────────────────────────────────────────────────────────────────────────────
const APP_DB = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const APP_USER = process.env.MONGODB_APP_USERNAME;
const APP_PASS = process.env.MONGODB_APP_PASSWORD;

if (!APP_USER || !APP_PASS) {
  print('[init] MONGODB_APP_USERNAME/PASSWORD 미설정 → 앱 계정 생성 건너뜀 (root 만 사용됨)');
} else {
  const admin = db.getSiblingDB('admin');
  const exists = admin.getUser(APP_USER);
  if (exists) {
    print(`[init] app user '${APP_USER}' 이미 존재 → 생성 건너뜀`);
  } else {
    admin.createUser({
      user: APP_USER,
      pwd: APP_PASS,
      roles: [{ role: 'readWrite', db: APP_DB }],
    });
    print(`[init] app user '${APP_USER}' 생성 (readWrite@${APP_DB})`);
  }
}
