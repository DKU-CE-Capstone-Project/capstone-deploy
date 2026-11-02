const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  class Element {
    constructor() { this.children = []; this.style = {}; this.hidden = false; this.disabled = false; this.value = '24'; }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(name, fn) { this[name] = fn; }
  }
  const nodes = new Map();
  const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }, createElement() {return new Element();}};
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
