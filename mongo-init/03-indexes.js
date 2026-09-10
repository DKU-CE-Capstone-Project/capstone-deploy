// ─────────────────────────────────────────────────────────────────────────────
// 03 — 인덱스
//
// 명명 규칙은 Notion「데이터베이스 정보」를 따른다:
//   uniq_<collection>_<field>  — 유니크
//   idx_<collection>_<field>   — 일반
// 그 페이지가 명시한 news 5종(uniq_news_url / idx_news_keywords /
// idx_news_categories / idx_news_status / idx_news_published_at)을 그대로 만들고,
// 나머지 컬렉션도 같은 규칙으로 이름을 붙였다.
//
// 앱 고유 id(news_id/report_id/strategy_id)는 upsert 키로 쓰이므로 유니크로 잡되,
// 설계상 필수 필드가 아니라서 sparse 로 둔다(값이 없는 문서끼리 충돌 방지).
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const d = db.getSiblingDB(DB_NAME);

const INDEXES = {
  news: [
    [{ url: 1 }, { name: 'uniq_news_url', unique: true }],
    [{ news_id: 1 }, { name: 'uniq_news_news_id', unique: true, sparse: true }],
    [{ keywords: 1 }, { name: 'idx_news_keywords' }],
    [{ categories: 1 }, { name: 'idx_news_categories' }],
    [{ status: 1 }, { name: 'idx_news_status' }],
    [{ published_at: -1 }, { name: 'idx_news_published_at' }],
    [{ collected_at: -1 }, { name: 'idx_news_collected_at' }],
    // 검색어별 캐시 조회 (utils.cache_articles 의 _search_keyword)
    [{ _search_keyword: 1, published_at: -1 }, { name: 'idx_news_search_keyword' }],
  ],

  news_analysis: [
    [{ news_id: 1 }, { name: 'uniq_news_analysis_news_id', unique: true }],
    [{ 'event.sentiment': 1 }, { name: 'idx_news_analysis_sentiment' }],
    [{ 'event.importance': -1 }, { name: 'idx_news_analysis_importance' }],
    [{ created_at: -1 }, { name: 'idx_news_analysis_created_at' }],
  ],

  news_relations: [
    // 같은 (source, target, type) 조합은 하나만 — 재계산 결과 중복 저장 방지
    [{ source_news_id: 1, target_news_id: 1, 'relation.type': 1 },
     { name: 'uniq_news_relations_pair', unique: true }],
    [{ source_news_id: 1 }, { name: 'idx_news_relations_source' }],
    [{ target_news_id: 1 }, { name: 'idx_news_relations_target' }],
    [{ 'relation.score': -1 }, { name: 'idx_news_relations_score' }],
    [{ shared_keywords: 1 }, { name: 'idx_news_relations_shared_keywords' }],
  ],

  // 쿠키 사용 결정으로 비어 있음. 나중에 서버 저장으로 전환할 때를 위해 최소 인덱스만.
  mindmaps: [
    [{ 'center.value': 1 }, { name: 'idx_mindmaps_center_value' }],
    [{ created_at: -1 }, { name: 'idx_mindmaps_created_at' }],
  ],

  reports: [
    [{ report_id: 1 }, { name: 'uniq_reports_report_id', unique: true, sparse: true }],
    [{ topic: 1 }, { name: 'idx_reports_topic' }],
    [{ report_type: 1 }, { name: 'idx_reports_report_type' }],
    [{ source_news_ids: 1 }, { name: 'idx_reports_source_news_ids' }],
    [{ created_at: -1 }, { name: 'idx_reports_created_at' }],
  ],

  strategies: [
    [{ strategy_id: 1 }, { name: 'uniq_strategies_strategy_id', unique: true, sparse: true }],
    [{ report_id: 1 }, { name: 'idx_strategies_report_id' }],
    [{ status: 1 }, { name: 'idx_strategies_status' }],
    [{ created_at: -1 }, { name: 'idx_strategies_created_at' }],
  ],

  jobs: [
    [{ job_id: 1 }, { name: 'uniq_jobs_job_id', unique: true, sparse: true }],
    [{ status: 1 }, { name: 'idx_jobs_status' }],
    [{ job_type: 1 }, { name: 'idx_jobs_job_type' }],
    [{ created_at: -1 }, { name: 'idx_jobs_created_at' }],
  ],

  users: [
    [{ email: 1 }, { name: 'uniq_users_email', unique: true }],
    [{ plan: 1 }, { name: 'idx_users_plan' }],
  ],
};

let created = 0;
for (const [coll, specs] of Object.entries(INDEXES)) {
  for (const [keys, opts] of specs) {
    try {
      d[coll].createIndex(keys, opts);   // 이미 있으면 no-op
      created += 1;
    } catch (e) {
      // 인덱스 실패는 비치명 — 나머지를 계속 만든다
      print(`[init] index ${coll}.${opts.name} skip: ${e.codeName || e.message}`);
    }
  }
}
print(`[init] indexes ensured: ${created}`);
