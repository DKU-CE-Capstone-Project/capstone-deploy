// Existing DB changes require a compatible validator and zero incompatible data.
// Prefer migrate-newsmap-v2.py dry-run/apply with rollback metadata for v2.
const DB_NAME = process.env.MONGODB_INITDB_DATABASE || 'capstone_news';
const ACTION = process.env.MONGODB_VALIDATION_ACTION || 'error';
if (!['error', 'warn'].includes(ACTION)) throw new Error('Invalid validation action');
const target = db.getSiblingDB(DB_NAME);
const plans = [];
for (const name of ['reports', 'strategies']) {
  const info = target.getCollectionInfos({name})[0];
  if (!info || !info.options.validator) throw new Error(`Missing ${name} validator`);
  const bad = target[name].countDocuments({$nor: [info.options.validator]});
  if (bad > 0) throw new Error(`${name}: ${bad} incompatible documents; no validator changed`);
  plans.push({name, validator: info.options.validator});
}
for (const {name, validator} of plans) {
  const result = target.runCommand({collMod: name, validator, validationLevel: 'strict', validationAction: ACTION});
  if (result.ok !== 1) throw new Error('MongoDB metadata command failed');
  print(`[strict] ${name} validationAction=${ACTION}`);
}
