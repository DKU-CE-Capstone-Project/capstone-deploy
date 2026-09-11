// 앱 전용 계정 생성 — capstone_news DB에만 readWrite 권한을 준다.
// root 계정은 운영/점검용으로만 쓰고, 백엔드는 이 계정으로 붙는다.
//
// 이 스크립트는 /docker-entrypoint-initdb.d/ 에서 최초 기동 시 1회 실행된다.
// 수동 재실행(멱등):
//   docker compose exec -T mongodb mongosh -u <root> -p <pw> --file /docker-entrypoint-initdb.d/00-app-user.js

const DB_NAME = process.env.MONGO_INITDB_DATABASE || "capstone_news";
const APP_USER = process.env.MONGO_APP_USERNAME || "econmind_app";
const APP_PASSWORD = process.env.MONGO_APP_PASSWORD;

if (!APP_PASSWORD) {
  // 비밀번호가 없으면 계정을 만들지 않는다. 빈 비밀번호 계정이 생기는 것보다 낫다.
  print("[init] MONGO_APP_PASSWORD 미설정 → 앱 계정 생성을 건너뜁니다. .env를 확인하세요.");
} else {
  const target = db.getSiblingDB(DB_NAME);
  const existing = target.getUser(APP_USER);

  if (existing) {
    // 이미 있으면 비밀번호만 .env 값으로 맞춘다 (멱등).
    target.updateUser(APP_USER, {
      pwd: APP_PASSWORD,
      roles: [{ role: "readWrite", db: DB_NAME }],
    });
    print(`[init] 앱 계정 '${APP_USER}' 갱신 완료 (db=${DB_NAME})`);
  } else {
    target.createUser({
      user: APP_USER,
      pwd: APP_PASSWORD,
      roles: [{ role: "readWrite", db: DB_NAME }],
    });
    print(`[init] 앱 계정 '${APP_USER}' 생성 완료 (db=${DB_NAME})`);
  }
}
