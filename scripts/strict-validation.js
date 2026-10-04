// ─────────────────────────────────────────────────────────────────────────────
// reports / strategies 의 validationAction 을 warn → error 로 승격.
//
// 언제 실행하나: reports 는 백엔드가 schema_version 2 로 저장하기 시작하고
// scripts/migrate-reports-v2.js 로 기존 문서를 변환한 뒤. 위반 문서가 0건인지 먼저 확인한다.
// strategies 는 MONGODB_STRICT_COLLECTIONS=reports,strategies 로 명시할 때만 승격한다.
//
//   docker compose exec -T -e HOME=/tmp mongodb sh -c \
//     'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
//        --quiet --file /scripts/strict-validation.js'
//
// 되돌리기: MONGODB_VALIDATION_ACTION=warn 를 주고 다시 실행.
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const ACTION = process.env.MONGODB_VALIDATION_ACTION || 'error';
// 기본은 reports 만 승격한다. strategies 는 설계(백테스트)와 앱 형태가 달라 M6 전까지 warn 유지.
const TARGETS = (process.env.MONGODB_STRICT_COLLECTIONS || 'reports').split(',').map((s) => s.trim()).filter(Boolean);
const d = db.getSiblingDB(DB_NAME);

for (const name of TARGETS) {
  const info = d.getCollectionInfos({ name })[0];
  if (!info) { print(`[strict] ${name} 없음 — 건너뜀`); continue; }

  // 기존 문서 중 위반이 남아 있으면 먼저 알려 준다 (validationLevel:strict 는
  // 기존 문서의 update 도 막으므로, 승격 전에 정리 대상을 알아야 한다)
  const validator = info.options.validator;
  const bad = d[name].countDocuments({ $nor: [validator] });
  if (bad > 0) {
    print(`[strict] ⚠ ${name}: 설계와 안 맞는 기존 문서 ${bad}건 — 승격 후 이 문서들의 update 가 막힌다`);
  }

  d.runCommand({ collMod: name, validator: validator, validationLevel: 'strict', validationAction: ACTION });
  print(`[strict] ${name} validationAction=${ACTION}`);
}
