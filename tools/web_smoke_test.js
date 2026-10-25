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
const ORDER = ['cards.js', 'edit.js', 'services.js', 'tools.js', 'prompts.js', 'app.js', 'memory.js', 'trial.js'];
const STEPS = ['initCardsPage', 'initEditPage', 'initServicesPage', 'initLogsPage', 'initPromptsPage', 'initMemoryPage', 'initTrialPage'];

let failed = 0;
const fail = (msg) => { failed++; console.log('  ✗ ' + msg); };
const ok = (msg) => console.log('  ✓ ' + msg);

// ---------- 假 DOM ----------
const els = new Map();
function makeEl(id) {
  const el = {
    id,
    className: '',
    _html: '',
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
  return el;
}
// 真浏览器里 innerHTML 一赋值就会清空子节点，假 DOM 也得这样，
// 否则"重绘"看起来像是把行一直往后追加
function makeElWithInnerHTML(id) {
  const el = makeEl(id);
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; },
    set(v) { el._html = String(v); el.children.length = 0; },
    configurable: true,
  });
  return el;
}
const getEl = (id) => { if (!els.has(id)) els.set(id, makeElWithInnerHTML(id)); return els.get(id); };

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
// 自动刷新测试要能改这份数据
const memoryStore = { items: sampleMemory.items.slice() };

ctx.pywebview = {
  api: {
    get_cards_list: async () => [{ id: 1, name: '测试', filename: '1-测试.md', user_card: '', portrait_file: null }],
    get_services_status: async () => ({ core_proxy: 'running', caddy: 'running' }),
    get_logs: async () => ({ logs: [{ time: '00:00', name: 'core_proxy', line: 'test' }], next_index: 1 }),
    get_upstream: async () => ({ ok: true, url: 'http://127.0.0.1:8080/' }),
    get_upstream_status: async () => ({ ok: true, upstream: 'http://127.0.0.1:8080/' }),
    get_prompt_fragments: async () => ({
      ok: true,
      data: {
        total_samples: 57,
        // 2026-10-24 起统计口径改为「段落清单 + 出现率」：
        // 实测没有任何段落 100% 出现，fixed_sections 是空的
        fixed_sections: {},
        stats: {
          '守住世界观': { appear: 49, total: 57, status: 'partial' },
          '通用要求': { appear: 49, total: 57, status: 'partial' },
          '赠物通则': { appear: 45, total: 57, status: 'partial' },
          '任务：说服': { appear: 4, total: 57, status: 'partial' },
        },
        section_samples: {
          '守住世界观': '## 守住世界观\n本世界为东方修仙，凡人亦可修行。',
          '通用要求': '## 通用要求\n- 不机械复述属性信息。\n- 只对话，不发布任务。',
          '赠物通则': '## 赠物通则\n**随身之物**：玩家问起你有什么时照实说。',
          '任务：说服': '## 任务：说服\n玩家正试图说服你做一件事。',
        },
      },
    }),
    get_ai_config: async () => ({ ok: true, data: { inject: '', rules: [] } }),
    get_lorebook: async () => ({
      ok: true, budget: 800,
      entries: [
        { id: '1', title: '青云谷门规', keywords: ['青云谷', '门规'], content: '不得私斗。', enabled: true, priority: 80 },
        { id: '2', title: '常驻常识', keywords: [], content: '以灵石为货币。', enabled: true, priority: 30 },
      ],
    }),
    save_lorebook: async () => ({ ok: true, msg: '已保存 2 条' }),
    get_persona: async () => ({ ok: true, persona: '我是剑修。', inject: true }),
    save_persona: async () => ({ ok: true, msg: '已保存' }),
    list_presets: async () => ({ ok: true, builtin: ['精简'], presets: {}, active: '' }),
    get_memory_list: async () => ({ ok: true, debug_path: 'memory', items: memoryStore.items.slice() }),
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
    // ---- 试聊页（2026-10-24）----
    chat_trial_models: async () => ({
      ok: true,
      base: 'http://127.0.0.1:11434',
      models: [
        { name: 'wa-hauhau', size: 5620000000 },
        { name: 'qwen3:8b', size: 5200000000 },
      ],
    }),
    chat_trial_context: async () => ({
      ok: true,
      npc_name: '苏倾盏',
      base: '## 角色设定\n你是 苏倾盏。\n',
      user_card: '说话干脆，不爱绕弯子',
      memory: '玩家帮她修好了后院的水井',
      base_chars: 16, card_chars: 10, memory_chars: 12,
      has_card: true, has_memory: true,
    }),
    chat_trial_send: async () => ({ ok: true, reply: '客官里边请。', elapsed: 1.2 }),
    // ---- 版本与更新（2026-10-25）----
    app_version: async () => ({ ok: true, version: '0.5', repo: 'test/repo' }),
    update_proxy_get: async () => ({ ok: true, proxy: '' }),
    check_update: async () => ({ ok: true, current: '0.5', latest: '0.5', has_update: false }),
    download_update: async () => ({ ok: true, dir: 'update/pkg', size: 100, files: 5 }),
    apply_update: async () => ({ ok: true, backup: 'backup/update_x' }),
    chat_trial_fragments: async () => ({
      ok: true, total_samples: 57,
      fragments: [
        { title: '守住世界观', pct: 86, chars: 120, text: '世界观原文' },
        { title: '赠物通则', pct: 60, chars: 90, text: '赠物规则原文' },
      ],
    }),
    // ---- 模型来源（2026-10-24）----
    trial_source_get: async () => ({
      ok: true,
      base: 'http://192.168.0.116:11434',
      default_base: 'http://192.168.0.116:11434',
      model: 'wa-hauhau',
      has_key: false,
      key_masked: '',
    }),
    trial_source_save: async () => ({ ok: true }),
    model_sources_get: async () => ({
      ok: true,
      local_base: 'http://127.0.0.1:11434',
      lan_nodes: [{ id: 'mac-mini', name: 'Mac mini', base: 'http://192.168.0.116:11434' }],
      api_services: [{
        id: 'api-1', name: 'API', base: 'https://api.deepseek.com',
        model: 'deepseek-chat', key_masked: 'sk-12••••••3f2a', has_key: true,
      }],
    }),
    model_sources_save: async () => ({ ok: true }),
    models_scan: async () => ({
      ok: true, dir: 'models', local_available: false,
      files: [{ file: 'demo-Q4_K_M.gguf', size: 5000000000, model_name: 'demo-Q4_K_M', imported: false }],
    }),
    models_import: async () => ({ ok: true, model: 'demo-Q4_K_M' }),
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

  // ---------- 5. 列表自动刷新 ----------
  console.log('列表自动刷新：');
  try {
    const pageEl = getEl('page-memory');
    pageEl.classList = { _s: new Set(['active']), contains(c) { return this._s.has(c); },
                         add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); } };
    memoryStore.items.unshift({ id: 999999, name: '新角色', filename: '999999-新角色.json', mtime: 4 });
    await ctx.refreshMemoryListSilently();
    const n = getEl('memory-list').children.length;
    if (n === 4) ok('新增一条后自动变成 4 项');
    else fail('自动刷新没生效，实际 ' + n + ' 项');

    const snapshot = getEl('memory-list').children.map(c => c.textContent).join('|');
    await ctx.refreshMemoryListSilently();
    const again = getEl('memory-list').children.map(c => c.textContent).join('|');
    if (again === snapshot) ok('数据没变时不重绘（不会闪烁）');
    else fail('数据没变却重绘了');
  } catch (e) {
    fail('自动刷新测试抛错 -> ' + e.name + ': ' + e.message);
  }

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
  console.log('AI 提示词 · 段落清单：');
  try {
    await ctx.initPromptsPage();
    ctx.switchAiTab('fragments');
    // promptFragments 是 let 声明，不在 globalThis 上，从渲染出的侧栏里取标题
    const sideHtml = getEl('ai-tab-content').innerHTML;
    const m = sideHtml.match(/data-title="([^"]+)"/);
    if (!m) {
      fail('段落清单侧栏没渲染出条目');
    } else {
      const title = m[1];
      // 切进来就该自动选中第一段，右侧不能是空白（2026-10-24 修：以前一直是空的）
      const auto = getEl('prompts-content').innerHTML;
      if (auto && auto.indexOf(title) >= 0) {
        ok('自动选中第一段：' + title);
      } else {
        fail('切进 Tab 后右侧是空白（自动选中没生效）');
      }
      // 侧栏要有独立滚动容器，否则段落一多会把整个侧栏撑高、和右侧错位
      if (sideHtml.indexOf('prompts-sidebar-list') >= 0) {
        ok('侧栏列表区有独立滚动容器');
      } else {
        fail('侧栏缺 .prompts-sidebar-list 包裹层');
      }
      ctx.selectPromptFragment(title);
      const html = getEl('prompts-content').innerHTML;
      if (html && html.indexOf(title) >= 0) ok('手动切换详情也正常');
      else fail('详情为空（selectPromptFragment 可能抛错）');
    }
  } catch (e) {
    fail('段落清单渲染抛错 -> ' + e.name + ': ' + e.message);
  }

  console.log('AI 提示词 · 世界信息：');
  try {
    ctx.switchAiTab('lorebook');
    const html = getEl('ai-tab-content').innerHTML;
    if (html.indexOf('青云谷门规') >= 0 && html.indexOf('常驻') >= 0) {
      ok('条目列表渲染正常（含关键词条目与常驻条目）');
    } else {
      fail('世界信息列表没渲染出条目');
    }
    // 打开新增编辑表单，确认字段都在
    ctx.editLorebookEntry(-1);
    const ed = getEl('lb-editor').innerHTML;
    if (ed.indexOf('lb-title') >= 0 && ed.indexOf('lb-keywords') >= 0 && ed.indexOf('lb-content') >= 0) {
      ok('编辑表单字段完整');
    } else {
      fail('编辑表单缺字段');
    }
  } catch (e) {
    fail('世界信息页抛错: ' + (e && e.message));
  }

  console.log('AI 提示词 · 用户人设：');
  try {
    ctx.switchAiTab('persona');
    const html = getEl('ai-tab-content').innerHTML;
    if (html.indexOf('persona-text') >= 0 && html.indexOf('我是剑修') >= 0) {
      ok('人设文本与开关渲染正常');
    } else {
      fail('人设页没渲染出内容');
    }
  } catch (e) {
    fail('人设页抛错: ' + (e && e.message));
  }

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

  // ---------- 6. 工具页：用量统计 / 结构自检 ----------
  // 这两块 2026-10-24 从「服务管理」搬到了工具页，单独占 Tab
  console.log('工具页 · 用量统计 / 结构自检：');
  try {
    ctx.switchToolTab('usage');
    const u = getEl('tools-tab-content').innerHTML;
    if (u.indexOf('usage-summary') >= 0 && u.indexOf('price-in') >= 0) {
      ok('用量统计 Tab 渲染正常');
    } else {
      fail('用量统计 Tab 缺元素（usage-summary / price-in）');
    }

    ctx.switchToolTab('health');
    const h = getEl('tools-tab-content').innerHTML;
    if (h.indexOf('health-summary') >= 0 && h.indexOf('health-alerts') >= 0) {
      ok('结构自检 Tab 渲染正常');
    } else {
      fail('结构自检 Tab 缺元素（health-summary / health-alerts）');
    }
  } catch (e) {
    fail('工具页新 Tab 抛错 -> ' + e.name + ': ' + e.message);
  }

  // ---------- 7. 试聊页 ----------
  console.log('试聊页：');
  try {
    const listHtml = getEl('trial-npc').innerHTML;
    if (listHtml.indexOf('<option') >= 0) ok('NPC 下拉渲染正常');
    else fail('NPC 下拉没渲染出来');

    // 默认勾了「守住世界观 / 通用要求 / 赠物通则」，mock 里有前两个里的一个
    const sys = ctx.trialBuildSystem();
    if (sys.indexOf('世界观原文') >= 0) ok('默认勾选的游戏段落已拼进 system');
    else fail('system 里没有游戏段落内容');

    if (sys.indexOf('角色演绎要求') >= 0) ok('用户名片已拼进 system');
    else fail('system 里没有用户名片');
  } catch (e) {
    fail('试聊页抛错 -> ' + e.name + ': ' + e.message);
  }

  console.log('');
  if (failed) {
    console.log('结果：' + failed + ' 项失败');
    process.exit(1);
  }
  console.log('结果：全部通过');
})();
