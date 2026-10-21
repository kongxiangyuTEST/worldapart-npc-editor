/**
 * tools/web_smoke_test.js
 *
 * 前端冒烟测试：在 node 里用假 DOM 加载 web/*.js，检查：
 *   1. 每个脚本都能被解析（语法错误会让整个页面功能静默失效）
 *   2. 每个页面初始化函数都真实存在
 *   3. init() 能把 6 个页面全部初始化完，且不抛异常
 *   4. 长期记忆页能用假数据渲染出列表
 *
 * 用法： node tools/web_smoke_test.js
 * 退出码 0 = 全通过，1 = 有问题
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.join(__dirname, '..', 'web');
const ORDER = ['cards.js', 'edit.js', 'services.js', 'tools.js', 'prompts.js', 'app.js', 'memory.js'];
const STEPS = ['initCardsPage', 'initEditPage', 'initServicesPage', 'initLogsPage', 'initPromptsPage', 'initMemoryPage'];

let failed = 0;
const fail = (msg) => { failed++; console.log('  ✗ ' + msg); };
const ok = (msg) => console.log('  ✓ ' + msg);

// ---------- 假 DOM ----------
const els = new Map();
function makeEl(id) {
  return {
    id,
    className: '',
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    style: {},
    dataset: {},
    children: [],
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {},
    removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    select() {},
    setSelectionRange() {},
    get firstChild() { return this.children[0] || null; },
    scrollTop: 0,
    clientHeight: 0,
    scrollHeight: 0,
  };
}
const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); };

const timers = [];
const ctx = {
  console,
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  setInterval: () => 0,
  clearTimeout: () => {},
  clearInterval: () => {},
  document: {
    getElementById: getEl,
    createElement: () => makeEl(''),
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener() {},
    body: makeEl('body'),
  },
  confirm: () => false,
  alert: () => {},
  prompt: () => null,
  navigator: {},
  addEventListener: () => {},
  removeEventListener: () => {},
  Number, String, Object, Array, Math, JSON, Date, RegExp, Error, Promise,
};
ctx.window = ctx;
vm.createContext(ctx);

// ---------- 1. 逐个脚本解析 ----------
console.log('脚本解析：');
for (const f of ORDER) {
  const code = fs.readFileSync(path.join(WEB, f), 'utf8');
  try {
    vm.runInContext(code, ctx, { filename: f });
    ok(f);
  } catch (e) {
    fail(f + ' -> ' + e.name + ': ' + e.message);
  }
}

// ---------- 2. 初始化函数是否存在 ----------
console.log('初始化函数：');
for (const name of STEPS) {
  if (typeof ctx[name] === 'function') ok(name);
  else fail(name + ' 未定义（对应脚本可能加载失败）');
}

// ---------- 3. init() 跑完整流程 ----------
console.log('init() 流程：');
const sampleMemory = {
  ok: true,
  debug_path: 'memory',
  items: [
    { id: 100121, name: '纪小蝶', filename: '100121-纪小蝶.json', mtime: 3 },
    { id: 140041, name: '索雅', filename: '140041-索雅.json', mtime: 2 },
    { id: 100111, name: '纪澜之', filename: '100111-纪澜之.json', mtime: 1 },
  ],
};

ctx.pywebview = {
  api: {
    get_cards_list: async () => [{ id: 1, name: '测试', filename: '1-测试.md', user_card: '', portrait_file: null }],
    get_services_status: async () => ({ core_proxy: 'running', caddy: 'running' }),
    get_logs: async () => ({ logs: [{ time: '00:00', name: 'core_proxy', line: 'test' }], next_index: 1 }),
    get_upstream: async () => ({ ok: true, url: 'http://127.0.0.1:8080/' }),
    get_upstream_status: async () => ({ ok: true, upstream: 'http://127.0.0.1:8080/' }),
    get_prompt_fragments: async () => ({ ok: true, data: { fixed_sections: {} } }),
    get_ai_config: async () => ({ ok: true, data: { inject: '', rules: [] } }),
    get_memory_list: async () => sampleMemory,
    get_memory: async () => ({
      ok: true,
      data: {
        npc_id: 100121, npc_name: '纪小蝶', time_location: '大梁城',
        early_gist: '梗概', long_summary: '剧情',
        facts: { 赠物: [{ text: '玉佩', importance: 4, age_count: 2 }], 承诺: [], 关系里程碑: [], 重要事件: [] },
      },
    }),
    check_game_dir: async () => ({ ok: true, game_dir: '<test>/game' }),
    preview_prompt: async () => ({
      ok: true,
      sample: '20261022-000000-测试.json',
      roles: ['system', 'user'],
      system: '最终 system 文本',
      stats: { npc_name: '测试', rules_hit: 7, inject_chars: 108, card_chars: 0, memory_chars: 424, system_chars: 9132 },
    }),
  },
};

const called = new Set();
for (const name of STEPS) {
  if (typeof ctx[name] !== 'function') continue;
  const orig = ctx[name];
  ctx[name] = function (...args) {
    called.add(name);
    return orig.apply(this, args);
  };
}

(async () => {
  try {
    await ctx.init();
  } catch (e) {
    fail('init() 抛出异常 -> ' + e.name + ': ' + e.message);
  }

  for (const name of STEPS) {
    if (typeof ctx[name] !== 'function') continue;
    if (called.has(name)) ok(name + ' 已执行');
    else fail(name + ' 没有被 init() 调用到');
  }

  // 头部状态不该是错误提示
  const header = getEl('header-status').textContent;
  if (/⚠/.test(header)) fail('标题栏出现错误提示：' + header);
  else ok('标题栏状态正常：' + header);

  // ---------- 4. 记忆页渲染 ----------
  console.log('长期记忆页渲染：');
  const listHtml = getEl('memory-list').innerHTML + getEl('memory-list').children.map(c => c.textContent).join('');
  const rows = getEl('memory-list').children.length;
  if (rows === 3) ok('列表渲染 3 项');
  else fail('列表渲染异常，实际 ' + rows + ' 项；innerHTML=' + String(listHtml).slice(0, 120));

  const dirText = getEl('memory-dir-path').textContent;
  if (dirText.indexOf('memory') >= 0) ok('记忆目录已显示：' + dirText);
  else fail('记忆目录未显示，实际：' + dirText);

  try {
    await ctx.loadMemoryDetail(100121, '纪小蝶');
    const detail = getEl('memory-detail').innerHTML;
    const facts = getEl('m-facts').innerHTML;
    if (detail.indexOf('纪小蝶') >= 0 && facts.indexOf('玉佩') >= 0) ok('详情渲染正常');
    else fail('详情渲染缺少内容（姓名命中 ' + detail.indexOf('纪小蝶') +
      '，事实命中 ' + facts.indexOf('玉佩') + '）');
  } catch (e) {
    fail('loadMemoryDetail 抛错 -> ' + e.name + ': ' + e.message);
  }

  // ---------- 5. AI 提示词预览页 ----------
  console.log('AI 提示词 · 预览页：');
  try {
    ctx.switchAiTab('preview');
    const tabHtml = getEl('ai-tab-content').innerHTML;
    if (tabHtml.indexOf('跑一次') >= 0) ok('预览 Tab 渲染正常');
    else fail('预览 Tab 渲染异常');

    await ctx.runPreview();
    const result = getEl('preview-result').innerHTML;
    if (result.indexOf('规则命中') >= 0 && result.indexOf('9132') >= 0) ok('预览结果渲染正常');
    else fail('预览结果渲染异常');
  } catch (e) {
    fail('预览页抛错 -> ' + e.name + ': ' + e.message);
  }

  console.log('');
  if (failed) {
    console.log('结果：' + failed + ' 项失败');
    process.exit(1);
  }
  console.log('结果：全部通过');
})();
