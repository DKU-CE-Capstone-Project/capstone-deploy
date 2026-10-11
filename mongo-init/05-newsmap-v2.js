// Fresh database initialization only. Existing DBs use migrate-newsmap-v2.py.
// Never rewrite legacy documents or claim extraction provenance during init.
load('/scripts/lib/newsmap-v2-schema.js');
const target = db.getSiblingDB(process.env.MONGODB_INITDB_DATABASE || 'capstone_news');
const spec = globalThis.ECONMIND_V2_SCHEMA;
function commandWorked(result) {
  if (result.ok !== 1) throw new Error('MongoDB metadata command failed');
}
for (const [name, validator] of Object.entries(spec.validators)) {
  const info = target.getCollectionInfos({name})[0];
  if (info && target[name].countDocuments({$nor: [validator]}) !== 0) {
    throw new Error(`Incompatible ${name} data: use the migration dry-run first`);
  }
  const options = {validator, validationLevel: 'strict', validationAction: 'error'};
  if (info) commandWorked(target.runCommand({collMod: name, ...options}));
  else target.createCollection(name, options);
}
const news = target.getCollectionInfos({name: 'news'})[0];
const newsValidator = news.options.validator;
newsValidator.$jsonSchema.properties.body_ref = spec.body_ref;
commandWorked(target.runCommand({collMod: 'news', validator: newsValidator, validationLevel: 'strict', validationAction: 'error'}));
for (const entry of spec.indexes) target[entry.collection].createIndex(entry.key, entry.options);
print('[init] news-map v2 validators and indexes: strict/error');
