// 인덱스 생성.
//
// 명명 규칙은 Notion "설계 › 데이터베이스 › 데이터베이스 정보"에 기록된 기존 규칙을 따른다.
//   https://app.notion.com/p/37314a4e8feb8003963aeda4b29e53ee
//   uniq_<collection>_<field> / idx_<collection>_<field>
//
// 멱등: 같은 이름+같은 스펙이면 no-op. 스펙이 달라 충돌하면 경고만 찍고 계속 진행한다
// (기존 데이터를 지우지 않기 위해 의도적으로 비치명 처리).

const DB_NAME = process.env.MONGO_INITDB_DATABASE || "capstone_news";
const target = db.getSiblingDB(DB_NAME);

const INDEXES = [
  // ── news ──────────────────────────────────────────────────────────────────
  // url이 빈 문자열인 문서(제목 해시로 id를 만든 예외 케이스)끼리 유니크 충돌하지
  // 않도록 partial 인덱스로 건다.
  ["news", { url: 1 }, { name: "uniq_news_url", unique: true, partialFilterExpression: { url: { $gt: "" } } }],
  ["news", { news_id: 1 }, { name: "uniq_news_news_id", unique: true }],
  ["news", { keywords: 1 }, { name: "idx_news_keywords" }],
  ["news", { categories: 1 }, { name: "idx_news_categories" }],
  ["news", { status: 1 }, { name: "idx_news_status" }],
  ["news", { published_at: -1 }, { name: "idx_news_published_at" }],
  // 앱이 연관뉴스를 "같은 검색어로 수집된 기사"에서 찾는다 (app/api/v1/news.py:_related_articles)
  ["news", { _search_keyword: 1 }, { name: "idx_news_search_keyword" }],

  // ── news_analysis ─────────────────────────────────────────────────────────
  ["news_analysis", { news_id: 1 }, { name: "uniq_news_analysis_news_id", unique: true }],
  ["news_analysis", { created_at: -1 }, { name: "idx_news_analysis_created_at" }],

  // ── news_relations ────────────────────────────────────────────────────────
  // (source, target) 쌍은 하나만 존재해야 한다 — 재계산 결과를 upsert로 덮어쓴다.
  [
    "news_relations",
    { source_news_id: 1, target_news_id: 1 },
    { name: "uniq_news_relations_pair", unique: true },
  ],
  [
    "news_relations",
    { source_news_id: 1, "relation.score": -1 },
    { name: "idx_news_relations_source_score" },
  ],

  // ── reports ───────────────────────────────────────────────────────────────
  ["reports", { report_id: 1 }, { name: "uniq_reports_report_id", unique: true }],
  ["reports", { created_at: -1 }, { name: "idx_reports_created_at" }],

  // ── strategies ────────────────────────────────────────────────────────────
  ["strategies", { strategy_id: 1 }, { name: "uniq_strategies_strategy_id", unique: true }],
  ["strategies", { report_id: 1 }, { name: "idx_strategies_report_id" }],

  // ── jobs / users (현재 미사용, 설계 대조용) ────────────────────────────────
  ["jobs", { status: 1 }, { name: "idx_jobs_status" }],
  ["jobs", { created_at: -1 }, { name: "idx_jobs_created_at" }],
  ["users", { email: 1 }, { name: "uniq_users_email", unique: true }],
];

for (const [coll, keys, opts] of INDEXES) {
  try {
    target.getCollection(coll).createIndex(keys, opts);
    print(`[init] index ${coll}.${opts.name}`);
  } catch (e) {
    print(`[init] index ${coll}.${opts.name} 건너뜀: ${e.codeName || e.message}`);
  }
}

// ── 벡터 검색 인덱스 (Atlas Search / mongot) ──────────────────────────────────
// 백엔드(app/database.py:ensure_vector_index)도 기동 시 같은 인덱스를 만들려고 시도한다.
// 여기서 먼저 만들어 두면 앱이 mongot 준비 전에 떠서 실패하는 경우를 줄일 수 있다.
// 최초 기동 시점엔 mongot이 아직 안 떠 있을 수 있으므로 실패해도 비치명 처리한다.
//
// 차원 768 = app/config.py의 embedding_model(gemini-embedding-001) 출력 차원과 일치해야 한다.
try {
  const names = target.news.getSearchIndexes().map((i) => i.name);
  if (names.includes("vector_index")) {
    print("[init] vector_index 이미 존재 — 건너뜀");
  } else {
    target.news.createSearchIndex("vector_index", "vectorSearch", {
      fields: [
        {
          type: "vector",
          path: "embedding",
          numDimensions: 768,
          similarity: "cosine",
        },
      ],
    });
    print("[init] vector_index 생성 요청 (빌드에 ~1분)");
  }
} catch (e) {
  print(`[init] vector_index 생성 건너뜀 (mongot 미준비 가능): ${e.codeName || e.message}`);
  print("[init] → 백엔드 기동 시 재시도하거나, README의 수동 생성 절차를 따르세요.");
}

print(`[init] 인덱스 준비 완료 (db=${DB_NAME})`);
