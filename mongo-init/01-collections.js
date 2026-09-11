// capstone_news 컬렉션 + JSON Schema Validator 생성.
//
// 기준 문서: Notion "설계 › 데이터베이스 › 구조크" (최종 컬렉션 목록 8개)
//   https://app.notion.com/p/37114a4e8feb8080bfaafc4459d016f9
//
// MongoDB는 스키마리스라 설계 문서만으로는 구조가 강제되지 않는다.
// 설계에서 벗어난 문서가 들어가면 DB가 거부하도록 $jsonSchema validator를 건다.
// 이게 설계-구현 정합화의 핵심 장치다.
//
// 멱등: 컬렉션이 있으면 collMod로 validator만 갱신한다. 언제든 재실행 가능.
//   docker compose exec -T mongodb mongosh -u <root> -p <pw> --file /docker-entrypoint-initdb.d/01-collections.js
//
// ── 설계와의 의도적 차이 (근거) ─────────────────────────────────────────────
// 1. 설계는 문서 참조를 `_id: ObjectId`로 두지만, 백엔드는 URL의 MD5 앞 12자인
//    `news_id`(string)를 API 응답·캐시 키로 이미 전 구간에서 쓴다(app/utils.py:make_news_id).
//    이를 ObjectId로 바꾸면 프론트 계약까지 깨지므로, 문자열 식별자를 유지하고
//    `news_id`/`report_id`/`strategy_id` 필드를 명시적으로 둔다.
// 2. `embedding`은 설계상 news_analysis 소속이지만 news에 둔다. 현재 임베딩 대상이
//    분석 결과가 아니라 뉴스 본문(title + summary)이고(app/utils.py:_persist_to_mongo),
//    벡터 인덱스를 한 컬렉션에 모아야 $vectorSearch가 단순해지기 때문이다.
// 3. `mindmaps`/`users`/`jobs`는 생성만 하고 쓰지 않는다. 사유는 각 항목 주석 참고.

const DB_NAME = process.env.MONGO_INITDB_DATABASE || "capstone_news";
const target = db.getSiblingDB(DB_NAME);

const STR = { bsonType: "string" };
const NUM = { bsonType: ["double", "int", "long", "decimal"] };
const BOOL = { bsonType: "bool" };
const DATE = { bsonType: ["date", "null"] };
const STR_ARR = { bsonType: "array", items: { bsonType: "string" } };
const DIRECTION = { enum: ["positive", "negative", "neutral", "mixed", null] };

const TICKER_ARR = {
  bsonType: "array",
  items: {
    bsonType: "object",
    properties: { symbol: STR, name: STR, market: STR },
  },
};

const IMPACT_ARR = (keyField) => ({
  bsonType: "array",
  items: {
    bsonType: "object",
    properties: {
      [keyField]: STR,
      name: STR,
      direction: DIRECTION,
      score: NUM,
      reason: STR,
    },
  },
});

// ── 컬렉션 정의 ───────────────────────────────────────────────────────────────

const COLLECTIONS = {
  // 1. news — 뉴스 원문과 기본 메타데이터
  news: {
    required: [
      "news_id", "url", "title", "source", "status",
      "language", "is_deleted", "collected_at", "created_at", "updated_at",
    ],
    properties: {
      news_id: STR,               // 앱 식별자 (위 차이 1)
      title: STR,
      summary: STR,
      content: STR,
      url: STR,
      source: {
        bsonType: "object",
        required: ["name", "domain"],
        properties: { name: STR, domain: STR },
      },
      thumbnail_url: STR,
      published_at: DATE,         // 파싱 실패 시 null 허용 (외부 API 날짜 형식이 제각각)
      collected_at: DATE,
      keywords: STR_ARR,          // TODO(문주안): 뉴스 API 단계에서 키워드 추출
      categories: STR_ARR,        // TODO(문주안): 카테고리 분류 미구현
      related_tickers: TICKER_ARR, // TODO(김성민): 종목 추출 에이전트 미구현
      status: { enum: ["collected", "analyzed", "failed"] },
      language: STR,
      is_deleted: BOOL,
      created_at: DATE,
      updated_at: DATE,
      embedding: { bsonType: "array", items: NUM }, // 위 차이 2
      _search_keyword: STR,       // 앱 내부: 연관뉴스 조회용 원검색어
    },
  },

  // 2. news_analysis — AI 분석 결과
  // TODO(김성민): 현재 백엔드는 리포트만 만들고 뉴스 단위 분석을 저장하지 않는다.
  //               event/industry_impact/stock_impact 파이프라인이 생기면 여기에 쓴다.
  news_analysis: {
    required: ["news_id", "created_at", "updated_at"],
    properties: {
      news_id: STR,
      event: {
        bsonType: "object",
        properties: {
          main_event: STR,
          event_type: STR,
          importance: NUM,
          sentiment: DIRECTION,
        },
      },
      analysis: {
        bsonType: "object",
        properties: {
          short_summary: STR,
          cause: STR,
          effect: STR,
          market_impact: STR,
          risk_factors: STR_ARR,
        },
      },
      industry_impact: IMPACT_ARR("industry"),
      stock_impact: IMPACT_ARR("symbol"),
      embedding: { bsonType: "array", items: NUM },
      model_info: {
        bsonType: "object",
        properties: { provider: STR, model: STR, analyzed_at: DATE },
      },
      created_at: DATE,
      updated_at: DATE,
    },
  },

  // 3. news_relations — 뉴스 간 연관 관계 (매 요청 재계산 대신 캐시)
  news_relations: {
    required: ["source_news_id", "target_news_id", "relation", "created_at", "updated_at"],
    properties: {
      source_news_id: STR,
      target_news_id: STR,
      relation: {
        bsonType: "object",
        required: ["type", "score"],
        properties: {
          type: {
            enum: [
              "same_topic", "cause_effect", "same_company",
              "same_industry", "opposite_view", "follow_up",
            ],
          },
          score: NUM,
          reason: STR,
        },
      },
      shared_keywords: STR_ARR,
      created_at: DATE,
      updated_at: DATE,
    },
  },

  // 4. mindmaps — 12주차 회의 결정에 따라 DB에 저장하지 않는다.
  //    "마인드맵은 세션이 지워지면 데이터를 지우고 세션 아이디가 필요" → 쿠키/세션으로 처리.
  //    설계 목록과의 대조를 위해 컬렉션만 만들어 두고 비워 둔다.
  //    TODO(김건): 쿠키 기반 세션 식별이 아직 미구현이라 사용자별 마인드맵 분리가 안 된다.
  mindmaps: {
    required: ["created_at"],
    properties: {
      center: {
        bsonType: "object",
        properties: { type: STR, value: STR, ref_id: { bsonType: ["string", "objectId", "null"] } },
      },
      nodes: { bsonType: "array" },
      edges: { bsonType: "array" },
      depth: NUM,
      created_at: DATE,
      updated_at: DATE,
    },
  },

  // 5. reports — AI 리포트
  reports: {
    required: ["report_id", "title", "created_at"],
    properties: {
      report_id: STR,
      title: STR,
      topic: STR,
      report_type: { enum: ["investment_report", "issue_report", "market_report", null] },
      source_news_ids: STR_ARR,
      related_tickers: TICKER_ARR,
      sections: {
        bsonType: "object",
        properties: {
          summary: STR,
          key_events: STR_ARR,          // TODO(김성민): 프롬프트에 미포함
          scenario_analysis: STR,       // TODO(김성민): 프롬프트에 미포함
          industry_analysis: STR,       // TODO(김성민): 프롬프트에 미포함
          company_analysis: STR,        // TODO(김성민): 프롬프트에 미포함
          risk_analysis: STR,
          conclusion: STR,              // TODO(김성민): 프롬프트에 미포함
        },
      },
      scores: {
        bsonType: "object",
        properties: { importance: NUM, market_impact: NUM, confidence: NUM },
      },
      model_info: {
        bsonType: "object",
        properties: { provider: STR, model: STR, prompt_version: STR },
      },
      reuse: {
        bsonType: "object",
        properties: { view_count: NUM, used_count: NUM },
      },
      // 앱 확장 필드 (설계 외): RAG 근거와 critic 검증 결과
      rag_sources: STR_ARR,
      verification: { bsonType: "object" },
      created_by: { enum: ["system", "user", null] },
      created_at: DATE,
      updated_at: DATE,
    },
  },

  // 6. strategies — 투자 전략
  // 설계는 백테스트(진입/청산 조건 + 수익률) 중심인데, 현재 구현은 종목 추천 목록이다.
  // 두 구조를 모두 선언하되, 지금 쓰는 필드만 required로 둔다.
  // TODO: 백테스트(logic/parameters/backtest) 전체 미구현 — Notion "기술 › 백테스트" 참고.
  strategies: {
    required: ["strategy_id", "created_at"],
    properties: {
      strategy_id: STR,
      title: STR,
      report_id: STR,
      source_news_ids: STR_ARR,
      target: {
        bsonType: "object",
        properties: { symbol: STR, name: STR, market: STR },
      },
      strategy_type: {
        enum: ["event_momentum", "sentiment_based", "technical", "mixed", null],
      },
      logic: { bsonType: "object" },
      parameters: { bsonType: "object" },
      backtest: { bsonType: "object" },
      result_summary: STR,
      status: { enum: ["created", "tested", "completed", "failed", null] },
      // 현재 구현이 실제로 쓰는 필드
      expected_return: NUM,
      risk: STR,
      period: STR,
      strategy_summary: STR,
      strategy_items: {
        bsonType: "array",
        items: {
          bsonType: "object",
          properties: {
            ticker: STR,
            stock_name: STR,
            action: { enum: ["buy", "hold", "sell", "watch"] },
            reason: STR,
          },
        },
      },
      created_at: DATE,
      updated_at: DATE,
    },
  },

  // 7. jobs — 설계엔 있으나 실제로는 Redis(`job:{id}`, TTL 1h)로 구현되어 있다.
  //    app/job_store.py 참고. burst 처리 경로의 지연을 줄이려는 선택.
  //    설계 목록 대조용으로만 생성하고 비워 둔다.
  jobs: {
    required: ["job_type", "status", "created_at"],
    properties: {
      job_type: {
        enum: [
          "news_collection", "news_analysis", "mindmap_generation",
          "report_generation", "strategy_generation", "backtest",
        ],
      },
      status: { enum: ["queued", "running", "completed", "failed", "cancelled"] },
      input: { bsonType: "object" },
      output: { bsonType: "object" },
      progress: { bsonType: "object" },
      created_at: DATE,
      started_at: DATE,
      finished_at: DATE,
    },
  },

  // 8. users — 설계 주석 그대로 "로그인 기능을 넣을 경우에만 사용".
  //    현재 로그인 미구현이므로 비워 둔다. tier는 app/utils.py:tier_ok가 쿼리 파라미터로 받는다.
  users: {
    required: ["email", "created_at"],
    properties: {
      email: STR,
      name: STR,
      plan: { enum: ["FREE", "BASIC", "PAID"] },
      usage: { bsonType: "object" },
      preferences: { bsonType: "object" },
      created_at: DATE,
      updated_at: DATE,
    },
  },
};

// ── 적용 ─────────────────────────────────────────────────────────────────────

const existing = new Set(target.getCollectionNames());

for (const [name, spec] of Object.entries(COLLECTIONS)) {
  const validator = {
    $jsonSchema: {
      bsonType: "object",
      required: spec.required,
      properties: spec.properties,
    },
  };

  if (existing.has(name)) {
    target.runCommand({
      collMod: name,
      validator: validator,
      validationLevel: "moderate", // 기존 문서는 건드리지 않고, 신규/갱신만 검사
      validationAction: "error",
    });
    print(`[init] collMod '${name}' — validator 갱신`);
  } else {
    target.createCollection(name, {
      validator: validator,
      validationLevel: "moderate",
      validationAction: "error",
    });
    print(`[init] createCollection '${name}'`);
  }
}

print(`[init] 컬렉션 ${Object.keys(COLLECTIONS).length}개 준비 완료 (db=${DB_NAME})`);
print(`[init] 목록: ${target.getCollectionNames().sort().join(", ")}`);
