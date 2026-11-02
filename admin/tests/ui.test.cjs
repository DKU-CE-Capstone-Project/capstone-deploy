const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
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
  const context = vm.createContext({document, Date, console, setInterval() {}, fetch: async () => ({ok:false})});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
  return {context, nodes};
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
  assert.equal(nodes.get('usage-empty').hidden, false);
  assert.match(nodes.get('notice').textContent, /이벤트가 아직 없습니다/);
});

test('partial token coverage, HTTP versus SDK, and log cap stay visible', () => {
  const {context,nodes} = setup();
  context.data = overview({state:'ok',complete:false,providers:[{provider:'<script>private</script>',model:'fixture',operation:'generate',stage:'analysis',service_tier:'standard',http_attempts:0,sdk_calls:2,cache_hits:1,errors:0,cancelled:1,token_observations:{input_tokens:1,output_tokens:0},token_sums:{input_tokens:120,output_tokens:0}}]});
  vm.runInContext('render(data)', context);
  const cells = nodes.get('usage').children[0].children;
  assert.equal(cells[0].textContent, '<script>private</script>');
  assert.equal(cells[2].textContent, '미관측');
  assert.equal(cells[3].textContent, '2');
  assert.equal(cells[6].textContent, '120 (일부)');
  assert.equal(cells[7].textContent, '미제공');
  assert.match(nodes.get('notice').textContent, /일부 이벤트/);
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
