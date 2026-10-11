// Canonical contract: econmind-docs/docs/10 + docs/11 (2026-10-04).
// No migration may infer provenance or turn an old prototype v2 into this v2.
(function () {
  const string = { bsonType: 'string' };
  const nonempty = { bsonType: 'string', minLength: 1 };
  const nullableString = { bsonType: ['string', 'null'] };
  const date = { bsonType: 'date' };
  const legacyDate = { bsonType: ['date', 'string'] };
  const nullableDate = { bsonType: ['date', 'null'] };
  const boolean = { bsonType: 'bool' };
  const integer = (minimum, maximum) => ({ bsonType: ['int', 'long'], minimum, ...(maximum === undefined ? {} : { maximum }) });
  const sha = { bsonType: 'string', pattern: '^[a-f0-9]{64}$' };
  const uuid = { bsonType: 'string', pattern: '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' };
  const strings = { bsonType: 'array', items: string };
  const ids = (min, max) => ({ bsonType: 'array', items: nonempty, minItems: min, maxItems: max, uniqueItems: true });
  const object = (properties, required = Object.keys(properties)) => ({ bsonType: 'object', required, properties });
  const array = (items, min, max) => ({ bsonType: 'array', items, minItems: min, maxItems: max });
  const model = object({ provider: nonempty, name: nonempty, config_hash: sha });
  const strategyItem = object({ ticker: nullableString, stock_name: string, action: { enum: ['buy', 'hold', 'sell', 'watch'] }, reason: string });
  const report = object({
    schema_version: { bsonType: 'int', enum: [2] }, report_id: uuid,
    title: string, event_summary: string, event_analysis: string, market_impact: string,
    stock_impacts: { bsonType: 'array', items: object({ ticker: nullableString, name: string, impact: nonempty, direction: { enum: ['up', 'down', 'mixed', 'unknown'] }, evidence_news_ids: ids(0, 10) }) },
    risk_factors: strings, requested_news_ids: ids(1, 10),
    used_evidence: array(object({ news_id: nonempty, title: string, source_name: string, source_url: nonempty, published_at: nullableDate, body_id: uuid, content_hash: sha, input_char_count: integer(1, 12000), truncated: boolean, input_excerpt: nonempty }), 1, 10),
    failed_evidence: array(object({ news_id: nonempty, code: { enum: ['BODY_EMPTY', 'EXTRACTION_FAILED', 'EXTRACTION_TIMEOUT', 'ARTICLE_UNAVAILABLE', 'ARTICLE_NOT_ALLOWED'] }, message: string, retryable: boolean }), 0, 9),
    coverage: object({ requested_count: integer(1, 10), used_count: integer(1, 10), failed_count: integer(0, 9) }),
    rag_evidence: array(object({ news_id: nonempty, title: string, source_url: nullableString, description: string, description_hash: sha }), 0, 3),
    verification: object({ status: { enum: ['passed', 'warning', 'unavailable'] }, notes: strings }),
    generation_key: sha, reuse_eligible: boolean,
    generation: object({ language: { enum: ['ko'] }, report_type: { enum: ['investment'] }, model, prompt_version: nonempty, preprocessing_version: nonempty, rag_version: nonempty, input_budget_version: nonempty }),
    created_at: date, updated_at: date,
  });
  const strategy = object({
    schema_version: { bsonType: 'int', enum: [2] }, strategy_id: uuid, report_id: uuid, report_generation_key: sha,
    risk_level: { enum: ['low', 'medium', 'high'] }, period: { enum: ['short', 'mid', 'long'] },
    strategy_summary: string, risk_warning: string, strategy_items: { bsonType: 'array', items: strategyItem },
    generation_key: sha, generation: object({ model, prompt_version: nonempty }), created_at: date, updated_at: date,
  });
  // Current v1 API writes have ISO dates and these exact required result fields.
  // Missing/1 versions only; no arbitrary unknown-version or old v2 escape hatch.
  const legacyReport = object({ report_id: nonempty, title: string, summary: string, event_analysis: string, market_impact: string, related_stocks: strings, evidence_news: { bsonType: 'array', items: object({ news_id: nonempty, title: string }) }, risk_factors: strings, created_at: legacyDate, schema_version: { bsonType: 'int', enum: [1] }, updated_at: legacyDate, owner_sid: nonempty, rag_sources: strings, verification: { bsonType: ['object', 'null'] } }, ['report_id', 'title', 'summary', 'event_analysis', 'market_impact', 'related_stocks', 'evidence_news', 'risk_factors', 'created_at']);
  const legacyStrategy = object({ strategy_id: nonempty, expected_return: { bsonType: ['double', 'int', 'long', 'decimal'] }, risk: string, period: { enum: ['short', 'mid', 'long'] }, strategy_summary: string, strategy_items: { bsonType: 'array', items: strategyItem }, created_at: legacyDate, updated_at: legacyDate, owner_sid: nonempty, report_id: nullableString, schema_version: { bsonType: 'int', enum: [1] } }, ['strategy_id', 'expected_return', 'risk', 'period', 'strategy_summary', 'strategy_items', 'created_at']);
  const compatibility = (schema, legacy) => ({ $or: [
    { $jsonSchema: schema },
    { $and: [{ $or: [{ schema_version: { $exists: false } }, { schema_version: 1 }] }, { $jsonSchema: legacy }] },
  ] });
  const bodies = object({ schema_version: { bsonType: 'int', enum: [2] }, body_id: uuid, news_id: nonempty, source_url: nonempty, content: nonempty, content_hash: sha, extractor_provider: nonempty, extractor_version: nonempty, extracted_at: date, created_at: date });
  const bodyRef = object({ body_id: uuid, content_hash: sha, source_url: nonempty, extractor_version: nonempty, extracted_at: date });
  const index = (collection, key, name, options = {}) => ({ collection, key, options: { name, ...options } });
  const indexes = [
    index('news', {news_id: 1}, 'uniq_news_news_id', {unique: true, sparse: true}),
    index('news', {url: 1}, 'uniq_news_url', {unique: true}),
    index('news_bodies', {body_id: 1}, 'uniq_news_bodies_body_id', {unique: true}),
    index('news_bodies', {news_id: 1, source_url: 1, extractor_version: 1, extracted_at: -1}, 'idx_news_bodies_latest'),
    index('reports', {report_id: 1}, 'uniq_reports_report_id', {unique: true, sparse: true}),
    index('reports', {generation_key: 1}, 'uniq_reports_v2_generation', {unique: true, partialFilterExpression: {schema_version: 2, reuse_eligible: true}}),
    index('reports', {requested_news_ids: 1, created_at: -1}, 'idx_reports_requested_created'),
    index('strategies', {strategy_id: 1}, 'uniq_strategies_strategy_id', {unique: true, sparse: true}),
    index('strategies', {generation_key: 1}, 'uniq_strategies_v2_generation', {unique: true, partialFilterExpression: {schema_version: 2}}),
    index('strategies', {report_id: 1, created_at: -1}, 'idx_strategies_report_created'),
  ];
  const schema = { validators: {news_bodies: {$jsonSchema: bodies}, reports: compatibility(report, legacyReport), strategies: compatibility(strategy, legacyStrategy)}, body_ref: bodyRef, indexes };
  globalThis.ECONMIND_V2_SCHEMA = schema;
  if (typeof module !== 'undefined') module.exports = schema;
})();
