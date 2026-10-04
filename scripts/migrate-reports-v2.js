// ─────────────────────────────────────────────────────────────────────────────
// reports 기존 문서(앱 평탄 형태) → schema_version 2 변환 (econmind-docs docs/10 § 5.4)
//
// 백엔드는 2026-10-04부터 두 리포트 경로 모두 schema_version 2 로 저장한다.
// 그 전에 저장된 문서를 같은 형태로 바꿔야 scripts/strict-validation.js 로 'error' 승격할 수 있다.
//
//   docker compose exec -T -e HOME=/tmp mongodb sh -c \
//     'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
//        --quiet --file /scripts/migrate-reports-v2.js'
//
// MIGRATE_DRY_RUN=1 이면 변환 대상 수만 출력하고 쓰지 않는다. 여러 번 실행해도 결과가 같다
// (schema_version 2 문서는 건너뛴다). 변환 전에 mongodump 로 reports 를 백업해 둔다.
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const DRY_RUN = process.env.MIGRATE_DRY_RUN === '1';
const d = db.getSiblingDB(DB_NAME);
const FALLBACK_PREFIX = '(AI 분석 준비 중)';

function toDate(value) {
  if (value instanceof Date) return value;
  const parsed = value ? new Date(value) : null;
  return parsed && !isNaN(parsed.getTime()) ? parsed : new Date(0);
}

function strArray(value) {
  return Array.isArray(value) ? value.filter((v) => typeof v === 'string') : [];
}

const before = d.reports.countDocuments({});
const targets = d.reports.find({ schema_version: { $ne: 2 } });
let converted = 0;

targets.forEach((doc) => {
  const evidence = (Array.isArray(doc.evidence_news) ? doc.evidence_news : [])
    .filter((e) => e && e.news_id)
    .map((e) => ({ news_id: String(e.news_id), title: typeof e.title === 'string' ? e.title : '' }));
  const summary = typeof doc.summary === 'string' ? doc.summary : '';
  const created = toDate(doc.created_at);
  const set = {
    schema_version: 2,
    title: typeof doc.title === 'string' ? doc.title : '',
    report_type: doc.report_type || 'investment',
    source_news_ids: Array.isArray(doc.source_news_ids) ? doc.source_news_ids : evidence.map((e) => e.news_id),
    evidence,
    sections: {
      summary,
      event_analysis: typeof doc.event_analysis === 'string' ? doc.event_analysis : '',
      market_impact: typeof doc.market_impact === 'string' ? doc.market_impact : '',
      risk_factors: strArray(doc.risk_factors),
    },
    related_stocks: strArray(doc.related_stocks),
    rag_sources: strArray(doc.rag_sources),
    is_fallback: typeof doc.is_fallback === 'boolean' ? doc.is_fallback : summary.startsWith(FALLBACK_PREFIX),
    created_by: 'system',
    created_at: created,
    updated_at: doc.updated_at ? toDate(doc.updated_at) : created,
  };
  if (!DRY_RUN) {
    d.reports.updateOne(
      { _id: doc._id },
      { $set: set, $unset: { summary: '', event_analysis: '', market_impact: '', risk_factors: '', evidence_news: '' } },
      { bypassDocumentValidation: true },
    );
  }
  converted += 1;
});

const after = d.reports.countDocuments({});
const remaining = d.reports.countDocuments({ schema_version: { $ne: 2 } });
print(`[migrate-reports-v2] ${DRY_RUN ? '(dry run) ' : ''}대상 ${converted}건 · 문서 수 ${before} → ${after} · 미변환 ${DRY_RUN ? converted : remaining}건`);

if (!DRY_RUN) {
  const validator = d.getCollectionInfos({ name: 'reports' })[0]?.options?.validator;
  if (validator) {
    const bad = d.reports.countDocuments({ $nor: [validator] });
    print(`[migrate-reports-v2] validator 위반 ${bad}건${bad ? ' — strict 승격 전에 확인 필요' : ' — strict-validation.js 실행 가능'}`);
  }
}
