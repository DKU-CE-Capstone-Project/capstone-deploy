// ─────────────────────────────────────────────────────────────────────────────
// 데모용 시드 — capstone-backend/fixtures/news_mock.json 8건을
// 「구조크」news 스키마로 변환해 넣는다.
//
//   docker compose exec -T -e HOME=/tmp mongodb sh -c \
//     'mongosh "mongodb://$MONGODB_INITDB_ROOT_USERNAME:$MONGODB_INITDB_ROOT_PASSWORD@localhost:27017/?authSource=admin" \
//        --quiet --file /scripts/seed-news.js'
//
// 이 스크립트 자체가 validator 검증이다. 아래 문서들이 그대로 들어간다면
// 설계 스키마를 만족한다는 뜻이고, 하나라도 어긋나면 code 121 로 거부된다.
//
// 넣지 않는 것:
//   · embedding — Gemini 임베딩이 필요하다. 앱(econmind-api)이 수집 경로에서 채운다.
//     즉 시드 문서만으로는 RAG 벡터검색이 동작하지 않는다.
//   · keywords / categories / related_tickers — 추출기가 아직 없다(빈 배열).
//     TODO(문주안): 뉴스 API 단계 키워드·카테고리 / TODO(김성민): 종목 연관도
//
// 되돌리기: db.news.deleteMany({ _seeded: true })
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const d = db.getSiblingDB(DB_NAME);
const now = new Date();

const RAW = [
    {
      "news_id": "7730934d3514",
      "title": "Trump announces new tariff package, markets react",
      "summary": "President Trump announced a new package of tariffs targeting imports from several countries, sending equities lower in early trading.",
      "content": "President Trump announced a new package of tariffs targeting imports...",
      "url": "https://reuters.example.com/trump-tariff-2026",
      "source": {
        "name": "Reuters",
        "domain": "reuters.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-30T09:12:00Z"
    },
    {
      "news_id": "dd376d28dcad",
      "title": "Trump unveils tariff package; Wall Street slips",
      "summary": "Tariff announcement from the Trump administration triggered a selloff in global equities Wednesday morning.",
      "content": "A tariff announcement from the Trump administration triggered a selloff...",
      "url": "https://bloomberg.example.com/tariff-selloff",
      "source": {
        "name": "Bloomberg",
        "domain": "bloomberg.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-30T09:30:00Z"
    },
    {
      "news_id": "742815b1a6cd",
      "title": "Iran tensions rise after Trump comments",
      "summary": "Diplomatic tensions between Washington and Tehran escalated after recent remarks by President Trump on regional security.",
      "content": "Diplomatic tensions between Washington and Tehran escalated...",
      "url": "https://ft.example.com/iran-tensions",
      "source": {
        "name": "Financial Times",
        "domain": "ft.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-29T16:45:00Z"
    },
    {
      "news_id": "d986bca1fa9d",
      "title": "Tech stocks lead market decline on tariff news",
      "summary": "Mega-cap technology shares paced declines as investors reassessed earnings exposure to new trade barriers.",
      "content": "Mega-cap technology shares paced declines...",
      "url": "https://wsj.example.com/tech-decline-tariffs",
      "source": {
        "name": "Wall Street Journal",
        "domain": "wsj.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-30T11:02:00Z"
    },
    {
      "news_id": "ea033cc73529",
      "title": "Trump tariff plan: what investors should watch",
      "summary": "Strategists weigh in on sectors most exposed to the announced tariff package and possible portfolio adjustments.",
      "content": "Strategists weigh in on sectors most exposed...",
      "url": "https://cnbc.example.com/tariff-investor-guide",
      "source": {
        "name": "CNBC",
        "domain": "cnbc.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-30T13:20:00Z"
    },
    {
      "news_id": "7730934d3514",
      "title": "Trump announces new tariff package, markets react",
      "summary": "Duplicate-by-URL article that should be filtered out.",
      "content": "Duplicate.",
      "url": "https://reuters.example.com/trump-tariff-2026",
      "source": {
        "name": "Reuters",
        "domain": "reuters.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-30T09:12:00Z"
    },
    {
      "news_id": "b77226afe94c",
      "title": "트럼프, 새 관세 발표에 한국 증시 출렁",
      "summary": "트럼프 대통령의 신규 관세 발표 여파로 한국 코스피 지수가 장중 하락세를 보였다.",
      "content": "트럼프 대통령의 신규 관세 발표 여파로...",
      "url": "https://yonhap.example.com/kospi-tariff",
      "source": {
        "name": "Yonhap",
        "domain": "yonhap.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-30T10:00:00Z"
    },
    {
      "news_id": "c4ac9fb13f7c",
      "title": "Oil prices climb amid Iran-US tension",
      "summary": "Brent crude rose more than two percent as renewed friction between the United States and Iran raised supply concerns.",
      "content": "Brent crude rose more than two percent...",
      "url": "https://ap.example.com/oil-iran",
      "source": {
        "name": "Associated Press",
        "domain": "ap.example.com"
      },
      "thumbnail_url": null,
      "published_at": "2026-04-29T18:10:00Z"
    }
  ];

let upserted = 0;
let rejected = 0;
for (const r of RAW) {
  const doc = {
    news_id: r.news_id,                 // utils.make_news_id() 와 동일한 md5[:12]
    title: r.title,
    summary: r.summary,
    content: r.content,
    url: r.url,
    source: r.source,                   // {name, domain} — 설계대로 객체
    thumbnail_url: r.thumbnail_url,
    published_at: r.published_at ? new Date(r.published_at) : null,
    collected_at: now,
    keywords: [],
    categories: [],
    related_tickers: [],
    status: 'collected',
    language: 'en',                     // 목 데이터가 영문 기사다
    is_deleted: false,
    created_at: now,
    updated_at: now,
    _seeded: true,                      // 정리용 표식
  };
  try {
    d.news.updateOne({ url: doc.url }, { $set: doc }, { upsert: true });
    upserted += 1;
  } catch (e) {
    rejected += 1;
    print(`[seed] 거부됨 (${e.code}): ${r.url}`);
    if (e.errInfo) print(JSON.stringify(e.errInfo.details, null, 2));
  }
}

print(`[seed] upsert ${upserted}건, 거부 ${rejected}건`);
print(`[seed] news.countDocuments() = ${d.news.countDocuments()}`);
if (rejected > 0) {
  print('[seed] ⚠ validator 가 거부한 문서가 있다 — 위 details 를 보고 스키마를 맞출 것');
} else {
  print('[seed] ✅ 전 건 validator 통과');
}
