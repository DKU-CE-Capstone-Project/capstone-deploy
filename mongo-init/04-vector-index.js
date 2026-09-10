// ─────────────────────────────────────────────────────────────────────────────
// 04 — 벡터검색 인덱스 (RAG 그라운딩의 근거 계층)
//
// 이 이미지(mongodb/mongodb-atlas-local)는 mongod + mongot 을 함께 띄우므로
// Atlas 전용인 $vectorSearch / createSearchIndex 가 그대로 동작한다.
// (일반 mongo:7 이었다면 app/agents/report_generator.py 의 retrieve_rag_articles 가
//  통째로 죽는다 — 이미지 선택 근거)
//
// 대상: news.embedding — 768차원, cosine.
//   768 은 app/agents/llm.py 의 embed() 가 EmbedContentConfig(output_dimensionality=768)
//   로 요청하는 값이다. 모델은 app/config.py 기준 gemini-embedding-001.
//   (app/database.py 의 "EMBED_DIM = 768  # text-embedding-004" 주석은 실제와
//    다르므로 작업범위 3에서 정정할 것)
//
// ⚠ 앱은 아직 컬렉션 이름이 articles 라 startup 에서 articles 쪽에 인덱스를 만든다.
//   작업범위 3에서 ARTICLES→NEWS 로 바꾸면 앱이 만들려는 인덱스와 여기서 만든 것이
//   일치한다(app/database.py 의 ensure_vector_index 는 이미 멱등하다).
//   embedding 을 news_analysis 로 옮기기로 결정하면 아래 COLL 도 함께 바꿀 것.
// ─────────────────────────────────────────────────────────────────────────────
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const COLL = 'news';
const INDEX_NAME = 'vector_index';   // app/database.py 의 VECTOR_INDEX 와 같은 이름
const DIM = 768;

const d = db.getSiblingDB(DB_NAME);

function already() {
  try {
    return d[COLL].getSearchIndexes(INDEX_NAME).length > 0;
  } catch (e) {
    return false;   // mongot 미기동 → 아직 모름
  }
}

if (already()) {
  print(`[init] search index '${INDEX_NAME}' 이미 존재`);
} else {
  // mongot 이 아직 안 떴을 수 있다 — 몇 번 재시도하고, 끝내 실패해도 기동은 막지 않는다.
  // (실패해도 앱 startup 의 ensure_vector_index() 가 다시 시도한다)
  let ok = false;
  for (let i = 1; i <= 6 && !ok; i++) {
    try {
      d[COLL].createSearchIndex({
        name: INDEX_NAME,
        type: 'vectorSearch',
        definition: {
          fields: [
            { type: 'vector', path: 'embedding', numDimensions: DIM, similarity: 'cosine' },
          ],
        },
      });
      ok = true;
      print(`[init] search index '${INDEX_NAME}' 생성 요청 (${COLL}.embedding, ${DIM}d cosine)`);
    } catch (e) {
      print(`[init] search index 시도 ${i}/6 실패: ${e.codeName || e.message}`);
      sleep(5000);
    }
  }
  if (!ok) {
    print(`[init] search index '${INDEX_NAME}' 생성 실패 → 앱 startup 의 ensure_vector_index() 에 맡김`);
  }
}
