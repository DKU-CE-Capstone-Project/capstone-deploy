// ─────────────────────────────────────────────────────────────────────────────
// 02 — 8개 컬렉션 + JSON Schema Validation
//
// 기준 문서: Notion「구조크」— 최종 확정 스키마 (8 컬렉션).
// MongoDB는 스키마리스라 설계가 저절로 지켜지지 않는다. 여기서 $jsonSchema 로
// 설계를 DB에 못 박아, 설계에서 벗어난 문서는 서버가 거부하게 만든다.
//
// ── DB 이름 ──────────────────────────────────────────────────────────────────
// capstone_news 로 통일한다. 「구조크」상단 `database: capstone` 은 오기이며,
// 같은 문서의 사용방법 절·app/config.py(mongodb_db_name)·기존 Atlas URI 모두
// capstone_news 를 쓴다.
//
// ── 엄격도 정책 (중요) ────────────────────────────────────────────────────────
//  · validationLevel: 'strict'  — 모든 insert/update 에 적용
//  · validationAction:
//      'error' → news, news_analysis, news_relations, mindmaps, jobs, users
//                (지금 아무도 쓰지 않는 신규 컬렉션이므로 바로 강제해도 안전)
//      'warn'  → reports, strategies
//                현재 백엔드가 설계와 다른 형태로 쓰고 있다.
//                  reports    : {report_id, summary, event_analysis, market_impact, ...}
//                  strategies : {strategy_id, expected_return, risk, period, ...}
//                  설계        : {topic, report_type, sections{7개}, scores, ...}
//                                {logic, parameters, backtest, status, ...}
//                여기에 지금 'error' 를 걸면 app/database.py 의 save_report/
//                save_strategy 가 조용히 실패한다(예외를 잡아 skip 로그만 남김).
//                → 백엔드 정합화(작업범위 3)가 끝난 뒤 scripts/strict-validation.js
//                  를 실행해 'error' 로 승격한다 (실행법은 README「MongoDB」절).
//
// ── 날짜 타입 ────────────────────────────────────────────────────────────────
// 설계의 "datetime" 은 BSON date 로 강제한다(문자열 허용 안 함).
// 지금 백엔드는 datetime.now(...).isoformat() 로 '문자열'을 넣으므로,
// 작업범위 3에서 isoformat() 를 벗기고 datetime 객체를 그대로 넘겨야 한다
// (motor/pymongo 가 BSON date 로 변환한다).
//
// ── 교차 참조 id 타입 ────────────────────────────────────────────────────────
// 설계는 news_id/report_id 등을 ObjectId 로 적었지만, 실제 앱은
// utils.make_news_id() 가 만드는 12자 md5 문자열을 쓰고 프론트엔드
// (apiAdapter.ts)도 그 문자열을 파싱한다. 응답 스키마를 깨지 않기 위해
// 참조 필드는 ['objectId','string'] 둘 다 허용한다.
//
// ── additionalProperties ────────────────────────────────────────────────────
// 최상위는 열어 둔다(앱 고유 필드 news_id/_search_keyword/embedding 등이 필요).
// 대신 설계가 완결된 작은 중첩 객체(source, relation, ticker, period)만
// additionalProperties:false 로 잠근다 — source 를 문자열로 넣던 현재 버그가
// 정확히 여기서 걸린다.
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const d = db.getSiblingDB(DB_NAME);

const DATE = { bsonType: 'date' };
const DATE_N = { bsonType: ['date', 'null'] };
const STR = { bsonType: 'string' };
const STR_N = { bsonType: ['string', 'null'] };
const NUM = { bsonType: ['double', 'int', 'long', 'decimal'] };
const STR_ARR = { bsonType: 'array', items: { bsonType: 'string' } };
const REF = { bsonType: ['objectId', 'string'] };            // 위 '교차 참조 id 타입' 참고
const REF_N = { bsonType: ['objectId', 'string', 'null'] };
const REF_ARR = { bsonType: 'array', items: REF };
const SENTIMENT = { enum: ['positive', 'negative', 'neutral', 'mixed'] };
const EMBEDDING = { bsonType: 'array', items: NUM };          // 768차원 (gemini-embedding-001, output_dimensionality=768)

const TICKER = {
  bsonType: 'object',
  required: ['symbol'],
  additionalProperties: false,
  properties: { symbol: STR, name: STR, market: STR },
};
const TICKER_ARR = { bsonType: 'array', items: TICKER };

const IMPACT_ITEM = (keyField) => ({
  bsonType: 'object',
  required: [keyField],
  properties: {
    [keyField]: STR,
    name: STR,
    direction: SENTIMENT,
    score: NUM,
    reason: STR,
  },
});

const CONDITION = {
  bsonType: 'object',
  properties: {
    indicator: STR,
    operator: STR,
    value: { bsonType: ['string', 'double', 'int', 'long', 'decimal'] },
  },
};

// ── 스키마 정의 ──────────────────────────────────────────────────────────────
const SCHEMAS = {
  // 1. news — 뉴스 원문 + 기본 메타데이터
  news: {
    action: 'error',
    schema: {
      bsonType: 'object',
      required: ['title', 'url', 'source', 'status', 'language', 'is_deleted',
                 'collected_at', 'created_at', 'updated_at'],
      properties: {
        title: STR,
        summary: STR,
        content: STR,
        url: STR,
        // 현재 코드가 source 를 문자열로 넣는 버그가 여기서 거부된다
        source: {
          bsonType: 'object',
          required: ['name'],
          additionalProperties: false,
          properties: { name: STR, domain: STR },
        },
        thumbnail_url: STR_N,
        published_at: DATE_N,
        collected_at: DATE,
        keywords: STR_ARR,          // TODO(문주안): 뉴스 API 단계에서 추출 → 지금은 []
        categories: STR_ARR,        // TODO(문주안): 동상
        related_tickers: TICKER_ARR, // TODO(김성민): 종목 연관 추출 → 지금은 []
        status: { enum: ['collected', 'analyzed', 'failed'] },
        language: { bsonType: 'string', pattern: '^[a-z]{2}$' },
        is_deleted: { bsonType: 'bool' },
        created_at: DATE,
        updated_at: DATE,

        // ── 앱 고유 필드(설계 외, 의도적 허용) ──
        news_id: STR,               // utils.make_news_id() 12자 md5. 프론트가 이 값을 쓴다
        _search_keyword: STR,       // 검색어별 캐시 조회용
        embedding: EMBEDDING,       // 벡터검색 대상. 아래 'embedding 위치' 주석 참고
      },
    },
  },

  // 2. news_analysis — AI 분석 결과
  //    embedding 은 설계상 여기 소속이다. 다만 현재 앱은 수집 시점(cache_articles)에
  //    임베딩을 만들고 분석 파이프라인은 아직 없어서 news 에 얹혀 있다.
  //    두 컬렉션 모두 embedding 을 허용해 두었으니, 작업범위 3에서 어느 쪽을
  //    벡터 인덱스 대상으로 삼든 DB 는 받아준다(04-vector-index.js 도 함께 조정할 것).
  news_analysis: {
    action: 'error',
    schema: {
      bsonType: 'object',
      required: ['news_id', 'created_at', 'updated_at'],
      properties: {
        news_id: REF,
        event: {
          bsonType: 'object',
          properties: {
            main_event: STR,
            event_type: STR,
            importance: NUM,
            sentiment: SENTIMENT,
          },
        },
        analysis: {
          bsonType: 'object',
          properties: {
            short_summary: STR,
            cause: STR,
            effect: STR,
            market_impact: STR,
            risk_factors: STR_ARR,
          },
        },
        industry_impact: { bsonType: 'array', items: IMPACT_ITEM('industry') },
        stock_impact: { bsonType: 'array', items: IMPACT_ITEM('symbol') },
        embedding: EMBEDDING,
        model_info: {
          bsonType: 'object',
          properties: { provider: STR, model: STR, analyzed_at: DATE },
        },
        created_at: DATE,
        updated_at: DATE,
      },
    },
  },

  // 3. news_relations — 뉴스 간 연관 관계
  //    graph_builder._relevance_score() 결과를 저장해 재계산을 피하는 자리.
  //    relation.type 은 설계의 6종만 허용한다 → 현재 하드코딩된 "related" 는 거부된다.
  //    TODO(김성민): 최소 same_topic 판별 구현 (작업범위 3)
  news_relations: {
    action: 'error',
    schema: {
      bsonType: 'object',
      required: ['source_news_id', 'target_news_id', 'relation', 'created_at', 'updated_at'],
      properties: {
        source_news_id: REF,
        target_news_id: REF,
        relation: {
          bsonType: 'object',
          required: ['type', 'score'],
          additionalProperties: false,
          properties: {
            type: {
              enum: ['same_topic', 'cause_effect', 'same_company',
                     'same_industry', 'opposite_view', 'follow_up'],
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
  },

  // 4. mindmaps — 12주차 회의 결정: "DB 미사용, 쿠키 사용".
  //    설계 목록에 있으므로 컬렉션과 validator 는 만들어 두되 아무도 쓰지 않는다.
  //    (프론트가 쿠키에 저장한다. 나중에 서버 저장으로 바꿀 때 이 스키마를 그대로 쓴다)
  mindmaps: {
    action: 'error',
    schema: {
      bsonType: 'object',
      required: ['center', 'nodes', 'edges', 'created_at', 'updated_at'],
      properties: {
        center: {
          bsonType: 'object',
          required: ['type', 'value'],
          properties: {
            type: { enum: ['keyword', 'news', 'company', 'stock', 'industry', 'event'] },
            value: STR,
            ref_id: REF_N,
          },
        },
        nodes: {
          bsonType: 'array',
          items: {
            bsonType: 'object',
            required: ['id', 'type', 'label'],
            properties: {
              id: STR,
              type: { enum: ['keyword', 'news', 'company', 'stock', 'industry',
                             'event', 'risk', 'report'] },
              label: STR,
              ref_id: REF_N,
              level: NUM,
              weight: NUM,
            },
          },
        },
        edges: {
          bsonType: 'array',
          items: {
            bsonType: 'object',
            required: ['id', 'source', 'target'],
            properties: {
              id: STR,
              source: STR,
              target: STR,
              type: { enum: ['related_news', 'same_topic', 'cause_effect',
                             'industry_impact', 'stock_impact', 'risk_factor'] },
              score: NUM,
              label: STR,
              reason: STR,
            },
          },
        },
        depth: NUM,
        created_at: DATE,
        updated_at: DATE,
      },
    },
  },

  // 5. reports — AI 리포트
  //    ⚠ validationAction: 'warn' (파일 상단 '엄격도 정책' 참고)
  reports: {
    action: 'warn',
    schema: {
      bsonType: 'object',
      required: ['title', 'report_type', 'sections', 'created_by', 'created_at', 'updated_at'],
      properties: {
        title: STR,
        topic: STR,
        report_type: { enum: ['investment_report', 'issue_report', 'market_report'] },
        source_news_ids: REF_ARR,
        related_tickers: TICKER_ARR,
        sections: {
          bsonType: 'object',
          properties: {
            summary: STR,
            key_events: STR_ARR,
            scenario_analysis: STR,
            industry_analysis: STR,
            company_analysis: STR,
            risk_analysis: STR,
            conclusion: STR,
          },
        },
        scores: {
          bsonType: 'object',
          properties: { importance: NUM, market_impact: NUM, confidence: NUM },
        },
        model_info: {
          bsonType: 'object',
          properties: { provider: STR, model: STR, prompt_version: STR },
        },
        reuse: {
          bsonType: 'object',
          properties: { view_count: NUM, used_count: NUM },
        },
        created_by: { enum: ['system', 'user'] },
        created_at: DATE,
        updated_at: DATE,

        // 앱 고유: 응답 스키마(schemas.py)·프론트가 쓰는 값들
        report_id: STR,
        verification: { bsonType: 'object' },
        rag_sources: STR_ARR,
      },
    },
  },

  // 6. strategies — 전략 + 백테스트
  //    ⚠ validationAction: 'warn' (파일 상단 '엄격도 정책' 참고)
  strategies: {
    action: 'warn',
    schema: {
      bsonType: 'object',
      required: ['title', 'status', 'created_at', 'updated_at'],
      properties: {
        title: STR,
        report_id: REF_N,
        source_news_ids: REF_ARR,
        target: TICKER,
        strategy_type: { enum: ['event_momentum', 'sentiment_based', 'technical', 'mixed'] },
        logic: {
          bsonType: 'object',
          properties: {
            description: STR,
            entry_conditions: { bsonType: 'array', items: CONDITION },
            exit_conditions: { bsonType: 'array', items: CONDITION },
          },
        },
        parameters: {
          bsonType: 'object',
          properties: {
            holding_days: NUM, take_profit: NUM, stop_loss: NUM, position_size: NUM,
          },
        },
        backtest: {
          bsonType: 'object',
          properties: {
            period: {
              bsonType: 'object',
              additionalProperties: false,
              properties: { start: DATE, end: DATE },
            },
            initial_cash: NUM,
            final_cash: NUM,
            return_rate: NUM,
            max_drawdown: NUM,
            win_rate: NUM,
            trade_count: NUM,
          },
        },
        result_summary: STR,
        status: { enum: ['created', 'tested', 'completed', 'failed'] },
        created_at: DATE,
        updated_at: DATE,

        // 앱 고유: 응답 스키마(schemas.py)·프론트가 쓰는 값들
        strategy_id: STR,
      },
    },
  },

  // 7. jobs — 비동기 작업 상태
  //    현재는 Redis(job:{id})가 이 역할을 한다. 설계에는 Mongo 소속이므로
  //    컬렉션은 만들어 둔다. Redis 를 계속 쓸지, 여기로 옮길지는 작업범위 3의 결정.
  jobs: {
    action: 'error',
    schema: {
      bsonType: 'object',
      required: ['job_type', 'status', 'created_at'],
      properties: {
        job_type: {
          enum: ['news_collection', 'news_analysis', 'mindmap_generation',
                 'report_generation', 'strategy_generation', 'backtest'],
        },
        status: { enum: ['queued', 'running', 'completed', 'failed', 'cancelled'] },
        input: {
          bsonType: 'object',
          properties: {
            keyword: STR,
            news_ids: REF_ARR,
            report_id: REF_N,
            ticker_symbols: STR_ARR,
          },
        },
        output: {
          bsonType: 'object',
          properties: {
            news_ids: REF_ARR,
            analysis_ids: REF_ARR,
            mindmap_id: REF_N,
            report_id: REF_N,
            strategy_id: REF_N,
            error_message: STR_N,
          },
        },
        progress: {
          bsonType: 'object',
          additionalProperties: false,
          properties: { current_step: STR, percent: NUM },
        },
        created_at: DATE,
        started_at: DATE_N,
        finished_at: DATE_N,

        // 앱 고유: Redis 와 같은 job id 를 쓰면 추적이 쉬움
        job_id: STR,
      },
    },
  },

  // 8. users — 로그인 기능을 넣을 경우에만 사용.
  //    현재 로그인 미구현이므로 컬렉션만 만들고 비워 둔다.
  users: {
    action: 'error',
    schema: {
      bsonType: 'object',
      required: ['email', 'plan', 'created_at', 'updated_at'],
      properties: {
        email: { bsonType: 'string', pattern: '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$' },
        name: STR,
        plan: { enum: ['FREE', 'BASIC', 'PAID'] },
        usage: {
          bsonType: 'object',
          properties: {
            report_count_today: NUM,
            strategy_count_today: NUM,
            last_used_at: DATE,
          },
        },
        preferences: {
          bsonType: 'object',
          properties: {
            language: { bsonType: 'string', pattern: '^[a-z]{2}$' },
            interested_tickers: STR_ARR,
            interested_keywords: STR_ARR,
          },
        },
        created_at: DATE,
        updated_at: DATE,
      },
    },
  },
};

// ── 적용 (멱등) ──────────────────────────────────────────────────────────────
const existing = new Set(d.getCollectionNames());
for (const [name, spec] of Object.entries(SCHEMAS)) {
  const opts = {
    validator: { $jsonSchema: spec.schema },
    validationLevel: 'strict',
    validationAction: spec.action,
  };
  if (existing.has(name)) {
    d.runCommand(Object.assign({ collMod: name }, opts));
    print(`[init] collMod ${name} (validationAction=${spec.action})`);
  } else {
    d.createCollection(name, opts);
    print(`[init] createCollection ${name} (validationAction=${spec.action})`);
  }
}

print(`[init] ${DB_NAME} collections: ${d.getCollectionNames().sort().join(', ')}`);
