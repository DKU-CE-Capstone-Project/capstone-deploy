const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(hash = '', fetchImpl) {
  class Element {
    constructor() { this.children = []; this.style = {setProperty(name, value) {this[name] = value;}}; this.attributes = {}; this.hidden = false; this.disabled = false; this.value = '24'; }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(name, fn) { this[name] = fn; }
    setAttribute(name, value) { this.attributes[name] = value; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
  }
  const nodes = new Map();
  const document = {activeElement: null, addEventListener() {}, getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }, createElement() {return new Element();}};
  for (const key of ['cpu', 'memory']) document.getElementById(key + '-card').append(document.getElementById(key + '-trigger'));
  const calls = [];
  const location = {hash};
  const window = {addEventListener(name, fn) {this[name] = fn;}, scrollTo() {}};
  const context = vm.createContext({document, Date, console, location, window, setInterval() {}, fetch: async (url) => {calls.push(url); return fetchImpl ? fetchImpl(url) : {ok:false};}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
  return {context, nodes, calls};
}
function overview(usage = {}) {
  return {project:'fixture-project',instance:'123',updated_at:new Date().toISOString(),connection:{state:'unknown'},metrics:{},usage:{state:'no_data',...usage}};
}

test('unknown DB and missing usage never render zero or disconnected', () => {
  const {context,nodes} = setup();
  context.data = overview();
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('db').textContent, '확인 불가');
  assert.equal(nodes.get('memory').textContent, '—');
  assert.equal(nodes.get('memory-capacity').textContent, '— GiB / — GiB');
  assert.equal(nodes.get('usage-empty').hidden, false);
  assert.match(nodes.get('notice').textContent, /이벤트가 아직 없습니다/);
});

test('report job lookup renders observed tokens and partial pricing, then clears a missing result', async () => {
  const jobId = '97a0bf73-21b5-4d89-85cb-1303a010d56a';
  const missingId = '2f34a75b-977e-4fc7-afc6-2b7b73b4bb55';
  const {context, nodes, calls} = setup('#cost', async (url) => {
    if (url.endsWith(missingId)) return {ok:true, json:async () => ({state:'no_data'})};
    if (!url.endsWith(jobId)) return {ok:true, json:async () => ({})};
    return {ok:true, json:async () => ({
      state:'ok', job_id:jobId, estimated_usd:0.000925, calls:2, priced_calls:1, unpriced_calls:1, logs_complete:true,
      rows:[{stage:'analysis', operation:'generate', provider:'gemini', model:'gemini-3.5-flash-lite',
        sdk_calls:2, http_attempts:0, cache_hits:0, estimated_usd:0.000925, unpriced_calls:1,
        token_observations:{input_tokens:2, output_tokens:1}, token_sums:{input_tokens:1200, output_tokens:200}}],
    })};
  });
  const submit = () => nodes.get('report-job-form').submit({preventDefault() {}});
  const input = context.document.getElementById('report-job-id');
  input.value = jobId;
  await submit();
  assert.equal(calls.at(-1), '/api/report-job-usage?job_id=' + jobId);
  assert.equal(nodes.get('report-job-results').hidden, false);
  assert.match(nodes.get('report-job-status').textContent, /1 \/ 2회 산정 · 미산정 1회/);
  assert.equal(nodes.get('report-job-totals').textContent, '관측 토큰 합계 · 입력 1,200 · 출력 200');
  const cells = nodes.get('report-job-rows').children[0].children;
  assert.equal(cells[0].textContent, 'analysis');
  assert.equal(cells[4].textContent, '200 (일부)');
  input.value = missingId;
  await submit();
  assert.equal(nodes.get('report-job-results').hidden, true);
  assert.equal(nodes.get('report-job-rows').children.length, 0);
  assert.match(nodes.get('report-job-status').textContent, /사용량이 없습니다/);
});

test('partial token coverage, HTTP versus SDK, and log cap stay visible', () => {
  const {context,nodes} = setup();
  context.data = overview({state:'ok',complete:false,providers:[{provider:'<script>private</script>',model:'fixture',operation:'generate',stage:'analysis',service_tier:'standard',http_attempts:0,sdk_calls:2,cache_hits:1,errors:0,cancelled:1,token_observations:{input_tokens:1,output_tokens:0},token_sums:{input_tokens:120,output_tokens:0},last_event_at:'2026-11-06T15:12:34.000000+00:00'}],
    routes:[{method:'GET',route:'/api/v2/maps/{map_id}',requests:1,errors:0}]});
  vm.runInContext('render(data)', context);
  const cells = nodes.get('usage').children[0].children;
  assert.equal(cells[0].textContent, '<script>private</script>');
  assert.match(cells[2].textContent, /2026\. 11\. 7\. .*12:12:34/);
  assert.equal(cells[3].textContent, '미관측');
  assert.equal(cells[4].textContent, '2');
  assert.equal(cells[7].textContent, '120 (일부)');
  assert.equal(cells[8].textContent, '미제공');
  assert.equal(nodes.get('routes').children[0].children[2].textContent, '미제공');
  assert.match(nodes.get('notice').textContent, /일부 이벤트/);
});

function diagnostics() {
  return [
    {stage:'bear', operation:'debate_analyst', kind:'agent_output_validation', error_type:'invalid_argument_schema', events:2, discarded_arguments:0,
      issues:[{path:'arguments.*.text', type:'string_too_long', count:2}, {path:'', type:'missing', count:1}]},
    {stage:'<b>bull</b>', operation:'debate_analyst', kind:'agent_output_normalized', error_type:'—', events:1200, discarded_arguments:3400, issues:[]},
  ];
}

test('report job lookup shows that job\'s agent diagnostics and hides the table without any', async () => {
  const jobId = '97a0bf73-21b5-4d89-85cb-1303a010d56a';
  const plainId = '2f34a75b-977e-4fc7-afc6-2b7b73b4bb55';
  const {context, nodes} = setup('#cost', async (url) => ({ok:true, json:async () => ({
    state:'ok', job_id:url.slice(-36), estimated_usd:0, calls:0, priced_calls:0, unpriced_calls:0, logs_complete:true, rows:[],
    ...(url.endsWith(jobId) ? {diagnostics:diagnostics()} : {}),
  })}));
  const submit = () => nodes.get('report-job-form').submit({preventDefault() {}});
  const input = context.document.getElementById('report-job-id');
  input.value = jobId;
  await submit();
  const rows = nodes.get('report-job-diagnostic-rows').children;
  assert.equal(nodes.get('report-job-diagnostics').hidden, false);
  assert.equal(rows.length, 2);
  const [rejected, trimmed] = rows.map(row => row.children);
  assert.equal(rejected.length, 5);
  assert.equal(rejected[0].textContent, 'bear');
  assert.equal(rejected[0].children[0].textContent, 'debate_analyst');
  assert.equal(rejected[1].textContent, '응답 형식 오류');
  assert.equal(rejected[1].children[0].textContent, 'invalid_argument_schema');
  assert.equal(rejected[2].textContent, '2');
  assert.equal(rejected[3].textContent, '—');
  assert.equal(rejected[4].textContent, 'arguments.*.text · string_too_long ×2\n(최상위) · missing ×1');
  assert.equal(rejected[4].className, 'issue-list');
  assert.equal(trimmed[0].textContent, '<b>bull</b>');
  assert.equal(trimmed[1].textContent, '논거 초과분 제외');
  assert.equal(trimmed[1].children.length, 0);
  assert.equal(trimmed[2].textContent, '1,200');
  assert.equal(trimmed[3].textContent, '3,400');
  assert.equal(trimmed[4].textContent, '—');
  input.value = plainId;
  await submit();
  assert.equal(nodes.get('report-job-results').hidden, false);
  assert.equal(nodes.get('report-job-diagnostics').hidden, true);
  assert.equal(nodes.get('report-job-diagnostic-rows').children.length, 0);
});

test('a job with diagnostics but no provider call shows them instead of reporting no usage', async () => {
  const onlyDiagnostics = '97a0bf73-21b5-4d89-85cb-1303a010d56a';
  const withCalls = '2f34a75b-977e-4fc7-afc6-2b7b73b4bb55';
  const empty = '0b0f6a52-6f0c-4c8e-9a51-6c1a3c1f7d10';
  // The collector reports "no_data" whenever the job has no provider call.
  const none = {state:'no_data', estimated_usd:null, calls:0, priced_calls:0, unpriced_calls:0, logs_complete:true, rows:[]};
  const {context, nodes} = setup('#cost', async (url) => {
    const job_id = url.slice(-36);
    if (job_id === onlyDiagnostics) return {ok:true, json:async () => ({...none, job_id, diagnostics:diagnostics()})};
    if (job_id === empty) return {ok:true, json:async () => ({...none, job_id, diagnostics:[]})};
    return {ok:true, json:async () => ({
      state:'ok', job_id, estimated_usd:0.000925, calls:1, priced_calls:1, unpriced_calls:0, logs_complete:true, diagnostics:[],
      rows:[{stage:'bear', operation:'generate', provider:'gemini', model:'gemini-3.5-flash-lite',
        sdk_calls:1, http_attempts:0, cache_hits:0, estimated_usd:0.000925, unpriced_calls:0,
        token_observations:{input_tokens:1, output_tokens:1}, token_sums:{input_tokens:1000, output_tokens:200}}],
    })};
  });
  const submit = () => nodes.get('report-job-form').submit({preventDefault() {}});
  const input = context.document.getElementById('report-job-id');
  input.value = onlyDiagnostics;
  await submit();
  assert.equal(nodes.get('report-job-results').hidden, false);
  assert.equal(nodes.get('report-job-usage').hidden, true);
  assert.equal(nodes.get('report-job-diagnostics').hidden, false);
  assert.equal(nodes.get('report-job-diagnostic-rows').children.length, 2);
  assert.equal(nodes.get('report-job-rows').children.length, 0);
  assert.equal(nodes.get('report-job-status').textContent, onlyDiagnostics + ' · 수집된 외부 API 호출 없음 · 에이전트 응답 진단 1,202건');
  assert.doesNotMatch(nodes.get('report-job-status').textContent, /사용량이 없습니다|예상 비용/);

  input.value = withCalls;
  await submit();
  assert.equal(nodes.get('report-job-usage').hidden, false);
  assert.equal(nodes.get('report-job-diagnostics').hidden, true);
  assert.equal(nodes.get('report-job-rows').children.length, 1);
  assert.match(nodes.get('report-job-status').textContent, /1 \/ 1회 산정/);

  input.value = empty;
  await submit();
  assert.equal(nodes.get('report-job-results').hidden, true);
  assert.match(nodes.get('report-job-status').textContent, /사용량이 없습니다/);
});

test('gauges show observed percentages including zero and keep missing data distinct', () => {
  const {context, nodes} = setup();
  context.data = overview();
  context.data.metrics = {
    cpu: {series: [{points: [{time: context.data.updated_at, value: 0}]}]},
    disk: {series: [{points: [{time: context.data.updated_at, value: 57.6}]}]},
  };
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('cpu').textContent, '0.0%');
  assert.equal(nodes.get('cpu-gauge').style['--usage'], 0);
  assert.equal(nodes.get('cpu-gauge').className, 'gauge');
  assert.equal(nodes.get('disk').textContent, '57.6%');
  assert.equal(nodes.get('disk-gauge').style['--usage'], 57.6);
  assert.equal(nodes.get('memory').textContent, '—');
  assert.equal(nodes.get('memory-gauge').className, 'gauge is-empty');
});

test('30-minute history preserves empty minutes, true zero and fixed percentage scale', () => {
  const {context, nodes} = setup();
  context.data = overview();
  context.data.updated_at = '2026-11-02T15:10:00Z';
  context.data.metrics.cpu = {series: [{points: [
    {time: '2026-11-02T14:39:59Z', value: 90},
    {time: '2026-11-02T14:40:00Z', value: 0},
    {time: '2026-11-02T14:42:00Z', value: 100},
    {time: '2026-11-02T14:42:30Z', value: 25},
    {time: '2026-11-02T15:08:00Z', value: 100},
  ]}]};
  vm.runInContext('render(data)', context);
  const slots = nodes.get('cpu-chart').children;
  assert.equal(slots.length, 30);
  assert.equal(slots[0].children[0].style.height, '0%');
  assert.equal(slots[1].children.length, 0);
  assert.match(slots[1].title, /수집값 없음/);
  assert.equal(slots[2].children[0].style.height, '25%');
  assert.equal(slots[28].children[0].style.height, '100%');
  assert.equal(slots[29].children.length, 0);
  assert.equal(nodes.get('cpu-start').textContent, vm.runInContext('clock("2026-11-02T14:40:00Z")', context) + ' · 30분 전');
  assert.equal(nodes.get('cpu-end').textContent, vm.runInContext('clock(data.updated_at)', context) + ' · 현재');
  assert.match(nodes.get('cpu-chart').attributes['aria-label'], /0에서 100%/);
  assert.match(slots[2].title, /25.0%/);
});

test('history opens on hover or focus and can be dismissed with Escape', () => {
  const {nodes} = setup();
  nodes.get('cpu-card').pointerenter();
  assert.equal(nodes.get('cpu-history').hidden, false);
  assert.equal(nodes.get('cpu-trigger').attributes['aria-expanded'], 'true');
  nodes.get('cpu-card').pointerleave();
  assert.equal(nodes.get('cpu-history').hidden, true);
  nodes.get('memory-card').focusin();
  assert.equal(nodes.get('memory-history').hidden, false);
  nodes.get('memory-card').keydown({key: 'Escape', preventDefault() {}});
  assert.equal(nodes.get('memory-history').hidden, true);
  assert.equal(nodes.get('memory-trigger').attributes['aria-expanded'], 'false');
});

test('GiB capacities follow the displayed device and preserve a real zero usage', () => {
  const {context, nodes} = setup();
  context.data = overview();
  const time = context.data.updated_at;
  const gib = 2 ** 30;
  context.data.metrics = {
    memory: {series: [{points: [{time, value: 0, used_bytes: 0, total_bytes: 8 * gib}]}]},
    disk: {series: [
      {device: '/dev/sda1', points: [{time, value: 50, used_bytes: 50 * gib, total_bytes: 100 * gib}]},
      {device: '/dev/sdb1', points: [{time, value: 80, used_bytes: 4 * gib, total_bytes: 5 * gib}]},
    ]},
  };
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('memory-capacity').textContent, '0.00 GiB / 8.00 GiB');
  assert.equal(nodes.get('disk').textContent, '80.0%');
  assert.equal(nodes.get('disk-capacity').textContent, '4.00 GiB / 5.00 GiB');
  assert.match(nodes.get('disk-note').textContent, /\/dev\/sdb1/);
  delete context.data.metrics.disk.series[1].points[0].total_bytes;
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('disk-capacity').textContent, '— GiB / — GiB');
});

test('API tabs expose only their own panel and scope usage warnings and filters', () => {
  const {context, nodes} = setup();
  context.data = overview();
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('view-overview').hidden, false);
  assert.equal(nodes.get('view-external-api').hidden, true);
  assert.equal(nodes.get('notice').hidden, true);
  assert.equal(nodes.get('usage-period').hidden, true);
  nodes.get('tab-external-api').click();
  assert.equal(nodes.get('view-overview').hidden, true);
  assert.equal(nodes.get('view-external-api').hidden, false);
  assert.equal(nodes.get('view-service-api').hidden, true);
  assert.equal(nodes.get('notice').hidden, false);
  assert.equal(nodes.get('usage-period').hidden, false);
  assert.equal(nodes.get('tab-external-api').attributes['aria-selected'], 'true');
  assert.equal(context.location.hash, '#external-api');
  nodes.get('tab-service-api').click();
  assert.equal(nodes.get('view-external-api').hidden, true);
  assert.equal(nodes.get('view-service-api').hidden, false);
  assert.equal(nodes.get('page-title').textContent, '서비스 API 요청');
});

test('cost deep link fetches the cost endpoint and restores other tabs correctly', async () => {
  const {context, nodes, calls} = setup('#cost');
  await vm.runInContext('refresh()', context);
  assert.equal(calls[0], '/api/costs?gcp_period=all&external_period=all');
  assert.equal(nodes.get('view-cost').hidden, false);
  assert.equal(nodes.get('data-controls').hidden, false);
  assert.equal(nodes.get('data-meta').hidden, false);
  assert.equal(nodes.get('data-footer').hidden, false);
  assert.equal(nodes.get('page-title').textContent, '비용');
  context.location.hash = '#service-api';
  context.window.hashchange();
  assert.equal(nodes.get('view-cost').hidden, true);
  assert.equal(nodes.get('view-service-api').hidden, false);
  assert.equal(calls[1], '/api/overview?hours=24');
});

test('keyboard navigation and hash history keep the selected tab in sync', () => {
  const {context, nodes} = setup('#external-api');
  assert.equal(nodes.get('view-external-api').hidden, false);
  nodes.get('tab-external-api').keydown({key: 'End', preventDefault() {}});
  assert.equal(context.location.hash, '#cost');
  assert.equal(nodes.get('tab-cost').attributes['tabindex'], '0');
  assert.equal(nodes.get('tab-external-api').attributes['tabindex'], '-1');
  nodes.get('tab-cost').keydown({key: 'ArrowRight', preventDefault() {}});
  assert.equal(context.location.hash, '#overview');
  context.location.hash = '#external-api';
  context.window.hashchange();
  assert.equal(nodes.get('view-external-api').hidden, false);
  context.location.hash = '#constructor';
  context.window.hashchange();
  assert.equal(nodes.get('view-overview').hidden, false);
});

function costData() {
  return {project:'fixture-project',updated_at:new Date().toISOString(),gcp_period:'all',external_period:'all',
    gcp:{state:'snapshot',automatic_state:'export_pending',currency:'KRW',month:'2026-11',net_cost:0,gross_cost:100,credits:-100,observed_at:'2026-11-02T01:00:00Z',services:[{service:'Compute Engine',currency:'KRW',gross_cost:100,credits:-100,net_cost:0}]},
    external:{state:'ok',complete:false,logs_complete:true,estimated_usd:.001,priced_calls:1,calls:3,unpriced_calls:2,pricing_date:'2026-11-02',assumptions:['fixture rates'],providers:[{provider:'gemini',models:['fixture-model'],calls:3,priced_calls:1,unpriced_calls:2,cache_hits:0,estimated_usd:.001,basis:['fixture'],source_url:'https://ai.google.dev/gemini-api/docs/pricing'}]}};
}

test('cost view shows credit consumption, dated snapshots and unpriced usage', () => {
  const {context,nodes} = setup('#cost');
  context.data = costData();
  vm.runInContext('renderCosts(data)',context);
  assert.equal(nodes.get('gcp-credits-used').textContent, vm.runInContext('money(100,"KRW")',context));
  assert.equal(nodes.get('gcp-cost-rows').children[0].children.length, 2);
  assert.equal(nodes.get('gcp-cost-rows').children[0].children[1].textContent, nodes.get('gcp-credits-used').textContent);
  assert.match(nodes.get('gcp-cost-note').textContent, /보고서 CSV/);
  assert.match(nodes.get('gcp-cost-note').textContent, /첫 내보내기 대기/);
  assert.equal(nodes.get('external-cost-state').textContent, '일부 산정');
  assert.match(nodes.get('external-cost-coverage').textContent, /미산정 2회/);
  assert.equal(nodes.get('gcp-cost-rows').children.length, 1);
  context.data.gcp = {state:'export_pending'};
  context.data.external.estimated_usd = null;
  vm.runInContext('renderCosts(data)',context);
  assert.equal(nodes.get('gcp-credits-used').textContent, '—');
  assert.equal(nodes.get('external-cost-total').textContent, '—');
  assert.equal(vm.runInContext('money(.00000001)',context), '< US$0.000001');
  assert.equal(vm.runInContext('creditsUsed(0,"KRW")',context), vm.runInContext('money(0,"KRW")',context));
  assert.equal(vm.runInContext('creditsUsed(20,"KRW")',context), vm.runInContext('money(-20,"KRW")',context));
});

test('application cache hits are summarized separately from both provider tables', () => {
  const {context, nodes} = setup('#cost');
  const costs = costData();
  const cache = {provider: 'application', cache_hits: 4};
  const cacheCostRow = {...cache, models: ['—'], calls: 0, priced_calls: 0, unpriced_calls: 0, estimated_usd: 0, basis: []};
  costs.external.providers.unshift(cacheCostRow);
  context.data = costs;
  vm.runInContext('renderCosts(data)', context);
  assert.deepEqual(nodes.get('external-cost-rows').children.map(row => row.children[0].textContent), ['gemini']);
  assert.equal(nodes.get('external-cost-empty').hidden, true);

  Object.assign(costs.external, {providers: [cacheCostRow], complete: true, calls: 0, priced_calls: 0, unpriced_calls: 0, estimated_usd: 0});
  vm.runInContext('renderCosts(data)', context);
  assert.equal(nodes.get('external-cost-rows').children.length, 0);
  assert.equal(nodes.get('external-cost-empty').hidden, false);
  assert.match(nodes.get('external-cost-empty').textContent, /관측된 외부 API 사용량이 없습니다/);

  const cacheUsageRow = {
    ...cache, operation: 'keyword_map', http_attempts: 0, sdk_calls: 0, errors: 0, cancelled: 0,
    token_observations: {}, token_sums: {},
  };
  context.data = overview({state: 'ok', providers: [
    cacheUsageRow,
    {...cacheUsageRow, operation: 'report_v2', cache_hits: 2},
    {...cacheUsageRow, provider: 'gemini', operation: 'embed_vector', cache_hits: 1},
  ]});
  vm.runInContext('render(data)', context);
  const rows = nodes.get('usage').children;
  assert.deepEqual(rows.map(row => row.children[0].textContent), ['gemini']);
  assert.equal(rows[0].children[5].textContent, '1');
  assert.equal(nodes.get('internal-cache-hits').textContent, '6회');
  assert.equal(nodes.get('usage-empty').hidden, true);

  context.data.usage.providers = [cacheUsageRow];
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('usage').children.length, 0);
  assert.equal(nodes.get('usage-empty').hidden, false);
  assert.equal(nodes.get('internal-cache-hits').textContent, '4회');

  context.data = overview({state: 'unavailable'});
  vm.runInContext('render(data)', context);
  assert.equal(nodes.get('internal-cache-hits').textContent, '—');
});

test('counted embedding inputs and cost coverage remain distinct from response usage', () => {
  const {context,nodes} = setup('#cost');
  context.data = costData();
  context.data.external.counted_calls = 1;
  context.data.external.providers[0].counted_calls = 1;
  vm.runInContext('renderCosts(data)',context);
  assert.match(nodes.get('external-cost-coverage').textContent, /입력 계수 1회/);
  assert.match(nodes.get('external-cost-rows').children[0].children[3].children[0].textContent, /입력 계수 1회.*미산정 2회/);
  context.row = {sdk_calls:4,http_attempts:0,token_observations:{input_tokens:1,counted_input_tokens:1},token_sums:{input_tokens:20,counted_input_tokens:30}};
  assert.equal(vm.runInContext('tokens(row,"input_tokens")',context), '20 (응답) + 30 (계수) (일부)');
});

test('leaving a pending cost request immediately refreshes the infrastructure view afterward', async () => {
  let finishCost;
  const {context,nodes,calls} = setup('#cost',url => url.startsWith('/api/costs')
    ? new Promise(resolve => {finishCost = resolve;})
    : Promise.resolve({ok:true,json:async () => overview()}));
  nodes.get('tab-overview').click();
  assert.equal(calls.length,1);
  finishCost({ok:true,json:async () => costData()});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls[1],'/api/overview?hours=24');
  assert.equal(nodes.get('view-overview').hidden,false);
  assert.equal(nodes.get('view-cost').hidden,true);
  assert.equal(nodes.get('db').textContent,'확인 불가');
});

test('GCP and API periods are independent and measurement baselines stay visible', async () => {
  const {context,nodes,calls} = setup('#cost', url => {
    const data=costData();
    const params=new URLSearchParams(url.split('?')[1]);
    data.gcp_period=params.get('gcp_period');data.external_period=params.get('external_period');
    data.gcp.measurement_start='2026-10-31T15:00:00Z';
    data.external.measurement_start='2026-11-02T09:00:00Z';
    return {ok:true,json:async()=>data};
  });
  await new Promise(setImmediate);
  nodes.get('external-period-day').click();
  await new Promise(setImmediate);
  assert.equal(calls.at(-1),'/api/costs?gcp_period=all&external_period=day');
  assert.equal(nodes.get('external-cost-period').textContent,'하루');
  assert.equal(nodes.get('external-period-day').attributes['aria-pressed'],'true');
  assert.match(nodes.get('gcp-measurement-start').textContent,/측정 시작.*2026.*KST/);
  assert.match(nodes.get('external-measurement-start').textContent,/측정 시작.*2026.*KST/);
  assert.equal(nodes.get('usage-period').hidden,true);
  nodes.get('gcp-period-month').click();
  await new Promise(setImmediate);
  assert.equal(calls.at(-1),'/api/costs?gcp_period=month&external_period=day');
  assert.match(nodes.get('gcp-selected-period').textContent,/한 달/);
});

test('changing a period while fetching skips the stale result and fetches the selected one', async () => {
  let resolveFirst;
  let count=0;
  const {context,nodes,calls}=setup('#cost',()=> ++count === 1
    ? new Promise(resolve=>{resolveFirst=resolve;})
    : {ok:true,json:async()=>({...costData(),external_period:'week'})});
  nodes.get('external-period-week').click();
  resolveFirst({ok:true,json:async()=>costData()});
  await new Promise(setImmediate);
  assert.equal(calls.length,2);
  assert.equal(calls[1],'/api/costs?gcp_period=all&external_period=week');
  assert.equal(vm.runInContext('lastCosts.external_period',context),'week');
  assert.equal(nodes.get('external-cost-period').textContent,'7일');
});

test('Compose mode labels host data and hides GCP billing without hiding API costs', () => {
  const {context, nodes} = setup();
  context.data = {...overview(), mode: 'local', instance: 'Docker Linux'};
  vm.runInContext('render(data)', context);
  assert.match(nodes.get('project').textContent, /Docker Compose.*Docker Linux/);
  assert.equal(nodes.get('cpu-label').textContent, '호스트 CPU');
  assert.match(nodes.get('notice').textContent, /앱 로그 전달 설정/);
  nodes.get('tab-cost').click();
  context.cost = {mode: 'local', instance: 'Docker Linux', updated_at: context.data.updated_at};
  vm.runInContext('renderCosts(cost)', context);
  assert.equal(nodes.get('gcp-summary').hidden, true);
  assert.equal(nodes.get('gcp-details').hidden, true);
  assert.match(nodes.get('external-cost-source').textContent, /로컬 수집/);
});
