// ===== AI 提示词页（4 Tab） =====

let aiConfig = null;          // 当前配置（对应 ai_config.json）
let promptFragments = null;   // prompt_fragments.json
let currentTab = 'user_card'; // 当前 Tab
let previewNpc = '';          // 预览用 NPC 名
let previewWithMemory = true; // 预览是否带长期记忆
let aiPresets = { builtin: [], presets: {}, active: '' };  // 注入方案

async function initPromptsPage() {
  // 加载 AI 配置
  try {
    const res = await pywebview.api.get_ai_config();
    aiConfig = res.ok ? res.data : { inject_user_card: true, inject: "", rules: [] };
  } catch (e) {
    aiConfig = { inject_user_card: true, inject: "", rules: [] };
  }
  // 加载固定段落
  try {
    const res = await pywebview.api.get_prompt_fragments();
    promptFragments = res.ok ? res.data : null;
  } catch (e) {
    promptFragments = null;
  }
  await loadPresets();
  renderPromptsPage();
}

// ---------------- 注入方案 ----------------

async function loadPresets() {
  try {
    const res = await pywebview.api.list_presets();
    aiPresets = (res && res.ok)
      ? { builtin: res.builtin || [], presets: res.presets || {}, active: res.active || '' }
      : { builtin: [], presets: {}, active: '' };
  } catch (e) {
    aiPresets = { builtin: [], presets: {}, active: '' };
  }
}

function presetOptionsHTML() {
  const builtin = aiPresets.builtin || [];
  const presets = aiPresets.presets || {};
  const active = aiPresets.active || '';
  const opt = (n, label) =>
    `<option value="${escapeHtml(n)}"${n === active ? ' selected' : ''}>${escapeHtml(label || n)}</option>`;
  const b = builtin.map(n => opt(n, n + '（内置）')).join('');
  const mine = Object.keys(presets).map(n => opt(n, n)).join('');
  return '<option value="">（不套用）</option>'
    + (b ? `<optgroup label="内置">${b}</optgroup>` : '')
    + (mine ? `<optgroup label="我的方案">${mine}</optgroup>` : '');
}

function setPresetStatus(text, kind) {
  const el = document.getElementById('preset-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'inject-status' + (kind ? ' ' + kind : '');
}

async function applyPreset() {
  const sel = document.getElementById('preset-select');
  const name = sel ? sel.value : '';
  if (!name) { setPresetStatus('先选一个方案', 'error'); return; }
  const ok = await uiConfirm(
    `应用方案「${name}」？\n当前的注入语、替换规则与记忆参数会被覆盖，其余配置不动。`,
    { okText: '应用' });
  if (!ok) return;

  let res;
  try {
    res = await pywebview.api.apply_preset(name);
  } catch (e) {
    setPresetStatus('✗ ' + e, 'error');
    return;
  }
  if (!res || !res.ok) {
    setPresetStatus('✗ ' + ((res && res.msg) || '失败'), 'error');
    return;
  }
  await initPromptsPage();
  setPresetStatus('✓ ' + res.msg, 'success');
}

async function saveAsPreset() {
  const name = await uiPrompt('把当前配置另存为方案，请输入方案名：',
                              { placeholder: '例如：高冷版 / 温柔版' });
  if (!name) return;
  let res;
  try {
    res = await pywebview.api.save_preset(name);
  } catch (e) {
    setPresetStatus('✗ ' + e, 'error');
    return;
  }
  if (!res || !res.ok) {
    setPresetStatus('✗ ' + ((res && res.msg) || '失败'), 'error');
    return;
  }
  await loadPresets();
  renderPromptsPage();
  setPresetStatus('✓ ' + res.msg, 'success');
}

async function deletePreset() {
  const sel = document.getElementById('preset-select');
  const name = sel ? sel.value : '';
  if (!name) { setPresetStatus('先选一个方案', 'error'); return; }
  const ok = await uiConfirm(`删除方案「${name}」？配置本身不会被改动。`,
                             { danger: true, okText: '删除' });
  if (!ok) return;
  let res;
  try {
    res = await pywebview.api.delete_preset(name);
  } catch (e) {
    setPresetStatus('✗ ' + e, 'error');
    return;
  }
  if (!res || !res.ok) {
    setPresetStatus('✗ ' + ((res && res.msg) || '失败'), 'error');
    return;
  }
  await loadPresets();
  renderPromptsPage();
  setPresetStatus('✓ ' + res.msg, 'success');
}

function renderPromptsPage() {
  const main = document.getElementById('prompts-main');
  main.innerHTML = `
    <div class="preset-bar">
      <span class="preset-bar-label">方案</span>
      <select id="preset-select" class="preset-select">${presetOptionsHTML()}</select>
      <button class="secondary" onclick="applyPreset()">应用</button>
      <button class="secondary" onclick="saveAsPreset()">另存为…</button>
      <button class="secondary" onclick="deletePreset()">删除</button>
      <span class="inject-status" id="preset-status"></span>
    </div>
    <div class="ai-tabs">
      <div class="ai-tab${currentTab === 'user_card' ? ' active' : ''}" data-tab="user_card" onclick="switchAiTab('user_card')">用户名片</div>
      <div class="ai-tab${currentTab === 'inject' ? ' active' : ''}" data-tab="inject" onclick="switchAiTab('inject')">注入规则</div>
      <div class="ai-tab${currentTab === 'replace' ? ' active' : ''}" data-tab="replace" onclick="switchAiTab('replace')">系统prompt替换</div>
      <div class="ai-tab${currentTab === 'preview' ? ' active' : ''}" data-tab="preview" onclick="switchAiTab('preview')">预览效果</div>
      <div class="ai-tab${currentTab === 'fragments' ? ' active' : ''}" data-tab="fragments" onclick="switchAiTab('fragments')">固定段落</div>
    </div>
    <div class="ai-tab-content" id="ai-tab-content"></div>
    <div class="ai-tab-footer">
      <span class="inject-status" id="ai-status"></span>
      <div class="inject-footer-buttons">
        <button class="secondary" onclick="reloadAiConfig()">放弃更改</button>
        <button class="primary" onclick="saveAiConfig2()">保存并生效</button>
      </div>
    </div>
  `;
  renderTabContent();
}

function switchAiTab(tab) {
  currentTab = tab;
  // 更新高亮
  document.querySelectorAll('.ai-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  renderTabContent();
}

function renderTabContent() {
  const box = document.getElementById('ai-tab-content');
  if (!box) return;

  if (currentTab === 'user_card') {
    box.innerHTML = renderUserCardTab();
  } else if (currentTab === 'inject') {
    box.innerHTML = renderInjectTab();
  } else if (currentTab === 'replace') {
    box.innerHTML = renderReplaceTab();
  } else if (currentTab === 'preview') {
    box.innerHTML = renderPreviewTab();
  } else if (currentTab === 'fragments') {
    box.innerHTML = renderFragmentsTab();
  }
}

// ============= Tab 1：用户名片 =============
function renderUserCardTab() {
  const enabled = aiConfig.inject_user_card !== false;
  return `
    <div class="inject-toggle">
      <label>
        <input type="checkbox" id="inject-user-card-toggle" ${enabled ? 'checked' : ''}>
        启用用户名片注入
      </label>
      <span class="inject-toggle-hint">关闭后，core_proxy 不再注入 .md 里的用户名片</span>
    </div>
    <div class="ai-tab-note">
      <p><strong>工作原理：</strong></p>
      <p>游戏每次对话时，core_proxy 会：</p>
      <ol>
        <li>从游戏发出的 prompt 里提取"当前对话 NPC 名字"</li>
        <li>去 <code>editor_cards/</code> 找对应的 .md 卡片</li>
        <li>读取卡片"用户名片"区块的内容</li>
        <li>把内容注入到 prompt 的 <code>## 用户补充</code> 段落</li>
      </ol>
      <p>AI 会看到这些内容，影响 NPC 的回复。游戏本身不知道。</p>
    </div>
  `;
}

// ============= Tab 2：INJECT =============
function renderInjectTab() {
  const inject = aiConfig.inject || '';
  return `
    <div class="field tall">
      <label>INJECT · 注入内容</label>
      <div class="field-hint">每次对话都会把这段内容注入到 system prompt 开头。</div>
      <textarea id="inject-text" spellcheck="false">${escapeHtml(inject)}</textarea>
    </div>
  `;
}

// ============= Tab 3：REPLACE =============
function renderReplaceTab() {
  const rules = aiConfig.rules || [];
  const rows = rules.map((r, i) => `
    <div class="rule-row">
      <textarea class="rule-old" data-index="${i}" placeholder="查找（OLD）">${escapeHtml(r.old || '')}</textarea>
      <div class="rule-arrow">→</div>
      <textarea class="rule-new" data-index="${i}" placeholder="替换为（NEW）">${escapeHtml(r.new || '')}</textarea>
      <button class="rule-del" onclick="removeRule(${i})">×</button>
    </div>
  `).join('');
  return `
    <div class="field-hint">对游戏<b>原始 system prompt</b> 做"查找 → 替换"（按顺序执行）。原文在游戏更新后可能变化，预览页会告诉你命中了几条。</div>
    <div id="rules-container">${rows}</div>
    <div class="inject-actions">
      <button class="secondary" onclick="addRule()">+ 添加规则</button>
      <button class="secondary" onclick="toggleRuleTest()">测试规则</button>
    </div>

    <!-- 规则测试器：贴一段原文逐条看能不能命中。以前只能改完去游戏里试，
         一条没命中根本看不出来（就是"规则静默失效"那个老问题）。 -->
    <div id="rule-test-panel" class="rule-test-panel" style="display:none;">
      <div class="field-hint">
        贴一段游戏原文（或点「载入最近样本」），逐条看规则能不能命中。
        <b>不调模型、不改配置</b>，可以反复试。
      </div>
      <textarea id="rule-test-input" rows="7" placeholder="把游戏的 system prompt 贴在这里…"></textarea>
      <div class="rule-test-actions">
        <button class="secondary" onclick="loadLatestSampleForTest()">载入最近样本</button>
        <button class="primary" onclick="runRuleTest()">运行</button>
        <span class="inject-status" id="rule-test-status"></span>
      </div>
      <div id="rule-test-result" class="rule-test-result"></div>
    </div>
  `;
}

function toggleRuleTest() {
  const p = document.getElementById('rule-test-panel');
  if (!p) return;
  p.style.display = (p.style.display === 'none' || !p.style.display) ? 'block' : 'none';
}

function setRuleTestStatus(text, kind) {
  const el = document.getElementById('rule-test-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'inject-status' + (kind ? ' ' + kind : '');
}

async function loadLatestSampleForTest() {
  const npcEl = document.getElementById('preview-npc');
  const npc = npcEl ? npcEl.value.trim() : '';
  setRuleTestStatus('读取样本…', '');
  try {
    const res = await pywebview.api.get_latest_sample(npc);
    if (!res || !res.ok) {
      setRuleTestStatus('✗ ' + ((res && res.msg) || '没有可用样本'), 'error');
      return;
    }
    let data = res.data && res.data.content;
    if (typeof data === 'string') {
      try { data = JSON.parse(data); } catch (e) { /* 保持字符串 */ }
    }
    const msgs = (data && data.messages) || (Array.isArray(data) ? data : []);
    const sys = msgs.filter(m => m.role === 'system')
                    .map(m => m.content || '').join('\n\n');
    const ta = document.getElementById('rule-test-input');
    if (ta) ta.value = sys || '';
    setRuleTestStatus(sys ? ('已载入 ' + (res.data.filename || '样本')) : '样本里没有 system 内容',
                      sys ? 'success' : 'error');
  } catch (e) {
    setRuleTestStatus('✗ ' + ((e && e.message) || e), 'error');
  }
}

async function runRuleTest() {
  const ta = document.getElementById('rule-test-input');
  const text = ta ? ta.value : '';
  if (!text.trim()) {
    setRuleTestStatus('✗ 请先输入或载入一段文本', 'error');
    return;
  }
  setRuleTestStatus('运行中…', '');
  let res;
  try {
    res = await pywebview.api.test_rules(text);
  } catch (e) {
    setRuleTestStatus('✗ ' + ((e && e.message) || e), 'error');
    return;
  }
  if (!res || !res.ok) {
    setRuleTestStatus('✗ ' + ((res && res.msg) || '运行失败'), 'error');
    return;
  }

  const rep = res.rule_report || [];
  const hit = rep.filter(r => r.hits > 0).length;
  const zero = rep.filter(r => r.hits === 0).length;
  const bad = rep.filter(r => r.hits < 0).length;
  const bits = [`命中 ${hit} 条`];
  if (zero) bits.push(`未命中 ${zero} 条`);
  if (bad) bits.push(`语法错误 ${bad} 条`);
  setRuleTestStatus((zero || bad ? '⚠ ' : '✓ ') + bits.join(' · '),
                    (zero || bad) ? 'error' : 'success');

  renderRuleTestResult(rep);
}

function renderRuleTestResult(rep) {
  const box = document.getElementById('rule-test-result');
  if (!box) return;
  if (!rep.length) {
    box.innerHTML = '<div class="rule-test-empty">还没有配置任何规则。</div>';
    return;
  }
  box.innerHTML = rep.map(r => {
    const cls = r.hits < 0 ? 'bad' : (r.hits === 0 ? 'zero' : 'hit');
    const tag = r.hits < 0 ? '语法错误' : (r.hits === 0 ? '未命中' : `命中 ${r.hits} 处`);
    const mode = r.mode === 'regex' ? ('正则' + (r.flags ? '/' + r.flags : '')) : '文本';
    return `
      <div class="rule-test-row ${cls}">
        <div class="rule-test-head">
          <span class="rule-test-mode">${escapeHtml(mode)}</span>
          <span class="rule-test-hits">${escapeHtml(tag)}</span>
        </div>
        <div class="rule-test-body">
          <div class="rule-test-old">${escapeHtml((r.old || '').slice(0, 160))}</div>
          <div class="rule-test-arrow">→</div>
          <div class="rule-test-new">${escapeHtml((r.new || '').slice(0, 160))}</div>
        </div>
      </div>`;
  }).join('');
}

// ============= Tab 4：预览效果 =============
function renderPreviewTab() {
  return `
    <div class="field-hint">
      用最近一次游戏对话的真实 prompt 跑一遍完整注入（替换 → 注入语 → 名片 → 记忆），
      <b>不调用模型、不写记忆</b>，可以反复试。
    </div>
    <div style="display:flex;gap:10px;align-items:center;margin:12px 0;flex-wrap:wrap;">
      <input id="preview-npc" type="text" placeholder="NPC 名字（留空 = 最新样本）"
             value="${escapeHtml(previewNpc)}"
             style="width:220px;padding:6px 10px;border:1px solid var(--cl-hairline);border-radius:3px;font-size:13px;">
      <label style="font-size:12px;color:var(--cl-body);display:flex;align-items:center;gap:4px;">
        <input type="checkbox" id="preview-memory" ${previewWithMemory ? 'checked' : ''}> 带长期记忆
      </label>
      <button class="primary" onclick="runPreview()">跑一次</button>
      <span class="inject-status" id="preview-status"></span>
    </div>
    <div id="preview-result"></div>
  `;
}

async function runPreview() {
  const npcEl = document.getElementById('preview-npc');
  const memEl = document.getElementById('preview-memory');
  previewNpc = npcEl ? npcEl.value.trim() : '';
  previewWithMemory = memEl ? memEl.checked : true;

  setPreviewStatus('正在组装...', '');
  const box = document.getElementById('preview-result');
  if (box) box.innerHTML = '';

  let res;
  try {
    res = await pywebview.api.preview_prompt(previewNpc, previewWithMemory);
  } catch (e) {
    setPreviewStatus('✗ ' + ((e && e.message) || e), 'error');
    return;
  }

  if (!res || !res.ok) {
    setPreviewStatus('✗ ' + ((res && res.msg) || '失败'), 'error');
    return;
  }

  setPreviewStatus('✓ 完成', 'success');
  renderPreviewResult(res);
}

// 哪些规则这次没命中（原文可能已随游戏更新变了）
function missedRules(stats) {
  const rules = (aiConfig && aiConfig.rules) || [];
  const hits = (stats && stats.rules_detail) || [];
  const out = [];
  rules.forEach((r, i) => {
    const key = String(r.old || '').trim().slice(0, 30).replace(/\n/g, ' ');
    if (!key) return;
    const matched = hits.some(h => String(h).trim() === key || String(h).trim().startsWith(key.slice(0, 12)));
    if (!matched) out.push(i + 1);
  });
  return out;
}

function renderPreviewResult(res) {
  const box = document.getElementById('preview-result');
  if (!box) return;
  const s = res.stats || {};
  const roleText = (res.roles || []).join(' → ');

  const missed = missedRules(s);
  let warn = '';
  if ((s.rules_hit || 0) === 0) {
    warn = '<div class="preview-warn">⚠ 没有任何替换规则命中。可能是游戏更新后原文变了，'
      + '打开"系统prompt替换"对照一下。</div>';
  } else if (missed.length) {
    warn = '<div class="preview-warn">第 ' + missed.join('、') + ' 条没命中：'
      + '游戏里的原文可能已经改了，这几条不会生效。</div>';
  }

  box.innerHTML = `
    ${warn}
    <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--cl-body);margin-bottom:8px;align-items:center;">
      <span>样本：<code>${escapeHtml(res.sample || '')}</code></span>
      <span>规则命中：<b>${s.rules_hit || 0}</b> / ${(aiConfig && aiConfig.rules || []).length} 条</span>
      <span>注入语：<b>${s.inject_chars || 0}</b> 字</span>
      <span>名片：<b>${s.card_chars || 0}</b> 字</span>
      <span>记忆：<b>${s.memory_chars || 0}</b> 字</span>
      <span>system 总长：<b>${s.system_chars || 0}</b> 字</span>
    </div>
    <div style="font-size:12px;color:var(--cl-muted);margin-bottom:6px;">角色顺序：${escapeHtml(roleText)}</div>
    ${renderMemoryDetail(res.memory_detail)}
    <pre style="max-height:420px;overflow:auto;background:var(--cl-canvas);border:1px solid var(--cl-hairline);border-radius:var(--cl-r-md);padding:12px;font-family:var(--cl-mono);font-size:12px;line-height:1.7;color:var(--cl-body);white-space:pre-wrap;word-break:break-word;">${escapeHtml(res.system || '')}</pre>
  `;
}

// 记忆注入明细：解释"这条为什么进了 / 为什么被裁"
// 分数 = 重要度×100 + 相关度×10 + 新近度：
//   重要度 500 级（刻骨铭心）永远压过 300 级（普通约定）
//   相关度是这条事实与本轮玩家发言的字符重合率，只在同重要度里起决定作用
function renderMemoryDetail(detail) {
  if (!detail || !detail.length) return '';
  const shown = detail.filter(d => d.included);
  const cut = detail.filter(d => !d.included);
  const row = d => `
    <div class="mem-detail-row ${d.included ? 'in' : 'out'}">
      <span class="mem-detail-score">${d.importance}★${d.relevance > 0 ? ' · 相关 ' + d.relevance : ''}</span>
      <span class="mem-detail-cat">${escapeHtml(d.cat)}</span>
      <span class="mem-detail-text">${escapeHtml((d.text || '').slice(0, 80))}</span>
    </div>`;
  return `
    <details class="mem-detail">
      <summary>记忆注入明细：注入 ${shown.length} 条${cut.length ? '，因预算未注入 ' + cut.length + ' 条' : ''}</summary>
      <div class="mem-detail-body">
        ${shown.map(row).join('')}
        ${cut.length ? `<div class="mem-detail-split">以下因预算不足被裁（重要度低或与当前话题无关）：</div>` + cut.map(row).join('') : ''}
      </div>
    </details>`;
}

function setPreviewStatus(text, cls) {
  const s = document.getElementById('preview-status');
  if (!s) return;
  s.textContent = text;
  s.className = 'inject-status ' + (cls || '');
}

// ============= Tab 4：固定段落 =============
// 「固定段落」这个说法已经不成立了：57 个样本里没有任何一段 100% 出现，
// 最高的是守住世界观/通用要求等基础段（49/57 = 86%）。
// 实际是按 talk_type 拼装：说服组、话题组、闲聊组各不相同。
const FRAG_GROUPS = [
  ['基础（大多数对话都有）', ['守住世界观', '通用要求', '角色属性', '角色设定', '角色背景', '当前场景', '输出格式']],
  ['任务（按对话类型选一个）', ['任务：闲聊', '任务：说服', '任务：话题闲聊']],
  ['口吻（有这个 NPC 的风格才出现）', ['说话习惯']],
  ['记忆', ['过往要事']],
  ['赠物（说服场景没有）', ['赠物通则']],
  ['状态（每轮都变）', ['本轮会话状态（只用于判断，不向玩家报数）']],
  ['话题相关（话题闲聊专属）', ['本次话题', '话题背景', '对话推进准则',
    '玩家想聊别的时（这条优先于话题）', '收尾判定（判定权在你）']],
];

function fragGroupOf(t) {
  for (const [g, arr] of FRAG_GROUPS) {
    if (arr.includes(t)) return g;
  }
  if (t.startsWith('任务')) return '任务（按对话类型选一个）';
  return '其他';
}

function renderFragmentsTab() {
  if (!promptFragments) {
    return `<div class="ai-tab-note">没有 prompt_fragments.json，请先运行 extract_fragments.py 从样本提取。</div>`;
  }
  const stats = promptFragments.stats || {};
  const total = promptFragments.total_samples || 0;
  const titles = Object.keys(stats);
  if (!titles.length) {
    return `<div class="ai-tab-note">还没有统计结果，先让游戏产生几个对话样本。</div>`;
  }

  // 按组归类，组内按出现率降序
  const groups = {};
  titles.forEach(t => {
    const g = fragGroupOf(t);
    (groups[g] = groups[g] || []).push(t);
  });

  const order = FRAG_GROUPS.map(x => x[0]).concat(['其他']);
  let listHTML = '';
  let idx = 0;
  order.filter(g => groups[g]).forEach(g => {
    listHTML += `<div class="frag-group-title">${escapeHtml(g)}</div>`;
    groups[g]
      .sort((a, b) => (stats[b].appear || 0) - (stats[a].appear || 0))
      .forEach(t => {
        const s = stats[t] || {};
        const pct = total ? Math.round((s.appear || 0) * 100 / total) : 0;
        listHTML += `<div class="prompt-item${idx === 0 ? ' active' : ''}"
          data-title="${escapeHtml(t)}" onclick="selectPromptFragment('${escapeHtml(t)}')">
          <div class="prompt-item-title">${escapeHtml(t)}</div>
          <div class="prompt-item-len">${pct}% · ${s.appear || 0}/${total}</div>
        </div>`;
        idx++;
      });
  });

  return `
    <div class="field-hint">
      游戏 prompt 由这些段落按<b>对话类型</b>拼装而成。
      <b>没有任何一段是所有对话都带的</b>（最高的基础段也只有 86%），
      所以不能当"固定段落"整段替换——说服场景连「赠物通则」都没有。
    </div>
    <div class="prompts-layout" style="height: calc(100% - 52px);">
      <div class="prompts-sidebar">
        <div class="prompts-sidebar-header">段落清单（${titles.length} 种）</div>
        ${listHTML}
        <div class="prompts-sidebar-footer">基于 ${total} 个游戏样本统计</div>
      </div>
      <div class="prompts-content" id="prompts-content"></div>
    </div>
  `;
}

function selectPromptFragment(title) {
  if (!promptFragments) return;
  const samples = promptFragments.section_samples || {};
  const stats = promptFragments.stats || {};
  const content = samples[title] || '';
  const s = stats[title] || {};
  const total = promptFragments.total_samples || 0;
  const pct = total ? Math.round((s.appear || 0) * 100 / total) : 0;

  document.querySelectorAll('.prompt-item').forEach(el => {
    el.classList.toggle('active', el.dataset.title === title);
  });

  const detail = document.getElementById('prompts-content');
  if (!detail) return;

  const hit = findFragmentRule(content);
  const volatile = isVolatileFragment(title);

  detail.innerHTML = `
    <div class="frag-head">
      <div>
        <h3 class="frag-title">${escapeHtml(title)}</h3>
        <div class="frag-meta">
          ${content.length} 字符 ·
          在 ${s.appear || 0}/${total} 个样本里出现（${pct}%）
          ${pct >= 100 ? '' : '· 条件段落，不是每轮都有'}
        </div>
      </div>
      <div class="frag-actions">
        ${volatile
          ? '<span class="frag-tag warn">每轮都变，不能做替换规则</span>'
          : `<button class="secondary" onclick="optimizeFragment('${escapeHtml(title)}')">让 AI 精简</button>`}
        ${hit ? `<button class="secondary" onclick="restoreFragmentRule('${escapeHtml(title)}')">恢复原设定</button>` : ''}
      </div>
    </div>

    ${hit ? `<div class="frag-inuse">
      当前已用精简版替换这一段（第 ${hit.index + 1} 条替换规则）·
      <a href="javascript:restoreFragmentRule('${escapeHtml(title)}')">恢复原设定</a>
    </div>` : ''}

    <div class="frag-hint">
      让 AI 合并同义句、删掉赘述，确认后再变成一条替换规则。JSON 结构、枚举值、
      「必须 / 不要」类约束都要求它不许动；随时可以恢复原设定。
      ${pct < 100 ? '<br>注意：这段不是每轮都出现，替换规则在没出现的对话里自然不会命中——属正常。' : ''}
    </div>

    <div id="frag-opt-status" class="inject-status"></div>
    <div id="frag-opt-result"></div>

    <div class="frag-section-label">原文（取信息量最完整的一个样本）</div>
    <pre class="prompt-detail-content">${escapeHtml(content)}</pre>
  `;
}

// 「本轮会话状态」「当前场景」这类每轮都会变的段落，做成整段替换规则会立刻失效
function isVolatileFragment(title) {
  return /本轮会话状态|当前场景|本次话题|话题背景/.test(String(title || ''));
}

// 这一段是不是已经被某条替换规则替换过
function findFragmentRule(content) {
  const rules = (aiConfig && aiConfig.rules) || [];
  const key = String(content || '').trim();
  for (let i = 0; i < rules.length; i++) {
    if (String(rules[i].old || '').trim() === key) {
      return { index: i, rule: rules[i] };
    }
  }
  return null;
}

async function restoreFragmentRule(title) {
  const fixed = (promptFragments && promptFragments.fixed_sections) || {};
  const content = fixed[title] || '';
  const hit = findFragmentRule(content);
  if (!hit) {
    alert('没有找到对应的替换规则，可能已经被删掉了。');
    return;
  }
  if (!(await uiConfirm('恢复原设定？\n\n会删掉这条替换规则（第 ' + (hit.index + 1) + ' 条），游戏原文重新生效。', { danger: true, okText: '删除规则' }))) {
    return;
  }
  aiConfig.rules.splice(hit.index, 1);
  let res;
  try {
    res = await pywebview.api.save_ai_config(aiConfig);
  } catch (e) {
    alert('恢复失败：' + ((e && e.message) || e));
    return;
  }
  if (!res || !res.ok) {
    alert('恢复失败：' + ((res && res.msg) || '未知错误'));
    return;
  }
  selectPromptFragment(title);
}

// ============= 固定段落：AI 精简 =============
let fragOptResult = null;

function setFragStatus(text, cls) {
  const el = document.getElementById('frag-opt-status');
  if (!el) return;
  el.textContent = text;
  el.className = 'inject-status ' + (cls || '');
}

async function optimizeFragment(title) {
  const fixed = (promptFragments && promptFragments.fixed_sections) || {};
  const text = fixed[title] || '';
  if (!text) return;

  setFragStatus('正在请求模型，长段落可能要几十秒...', '');
  const box = document.getElementById('frag-opt-result');
  if (box) box.innerHTML = '';

  let res;
  try {
    res = await pywebview.api.optimize_fragment(title, text);
  } catch (e) {
    setFragStatus('✗ ' + ((e && e.message) || e), 'error');
    return;
  }
  if (!res || !res.ok) {
    setFragStatus('✗ ' + ((res && res.msg) || '失败'), 'error');
    return;
  }

  fragOptResult = res;
  setFragStatus('✓ 完成', 'success');
  renderFragmentResult(res);
}

function renderFragmentResult(r) {
  const box = document.getElementById('frag-opt-result');
  if (!box) return;
  const riskMap = { low: ['低', 'ok'], mid: ['中', 'warn'], high: ['高', 'bad'] };
  const risk = riskMap[r.risk] || [r.risk, 'warn'];
  const dropped = (r.dropped || []).map(x => `<li>${escapeHtml(String(x))}</li>`).join('');

  box.innerHTML = `
    <div class="frag-result">
      <div class="frag-stats">
        <div class="frag-stat"><span class="frag-stat-num">${r.original_chars}</span><span class="frag-stat-label">原字数</span></div>
        <div class="frag-stat"><span class="frag-stat-num">${r.compressed_chars}</span><span class="frag-stat-label">精简后</span></div>
        <div class="frag-stat"><span class="frag-stat-num accent">-${r.saved_chars}</span><span class="frag-stat-label">省下 ${100 - r.ratio}%</span></div>
        <div class="frag-stat"><span class="frag-tag ${risk[1]}">风险 ${escapeHtml(risk[0])}</span>
          <span class="frag-stat-label">${escapeHtml(r.note || '')}</span></div>
      </div>

      ${dropped ? `<div class="frag-dropped"><b>改动点</b><ul>${dropped}</ul></div>` : ''}

      <div class="frag-actions" style="margin:12px 0;">
        <button class="primary" onclick="applyFragmentRule()">采用为替换规则</button>
        <button class="secondary" onclick="discardFragmentResult()">放弃</button>
      </div>

      <div class="frag-section-label">精简后的全文（确认没问题再采用）</div>
      <pre class="prompt-detail-content" style="max-height:280px;">${escapeHtml(r.compressed)}</pre>
    </div>
  `;
}

function discardFragmentResult() {
  fragOptResult = null;
  const box = document.getElementById('frag-opt-result');
  if (box) box.innerHTML = '';
  setFragStatus('已放弃', '');
}

async function applyFragmentRule() {
  if (!fragOptResult) return;
  if (!(await uiConfirm('把这段的压缩版加成一条替换规则？\n\n原来的内容会被整段替换，随时可以在「系统prompt替换」里删掉这条规则。'))) {
    return;
  }
  if (!aiConfig.rules) aiConfig.rules = [];
  aiConfig.rules.push({ old: fragOptResult.original, new: fragOptResult.compressed });

  let res;
  try {
    res = await pywebview.api.save_ai_config(aiConfig);
  } catch (e) {
    setFragStatus('✗ ' + ((e && e.message) || e), 'error');
    return;
  }
  if (res && res.ok) {
    const fixed = (promptFragments && promptFragments.fixed_sections) || {};
    const title = fragOptResult ? fragOptResult.title : '';
    fragOptResult = null;
    if (title) selectPromptFragment(title);
    setFragStatus('✓ 已保存为替换规则（共 ' + aiConfig.rules.length + ' 条），存盘即生效', 'success');
  } else {
    setFragStatus('✗ ' + ((res && res.msg) || '保存失败'), 'error');
  }
}

// ============= 保存/重载/重启 =============
async function reloadAiConfig() {
  const res = await pywebview.api.get_ai_config();
  if (res.ok) {
    aiConfig = res.data;
  }
  renderTabContent();
  setAiStatus('已放弃更改', '');
}

async function saveAiConfig2() {
  // 收集当前状态
  if (currentTab === 'user_card') {
    const toggle = document.getElementById('inject-user-card-toggle');
    aiConfig.inject_user_card = toggle ? toggle.checked : true;
  } else if (currentTab === 'inject') {
    const txt = document.getElementById('inject-text');
    aiConfig.inject = txt ? txt.value : '';
  } else if (currentTab === 'replace') {
    aiConfig.rules = collectRules();
  }

  setAiStatus('保存中...', '');
  const res = await pywebview.api.save_ai_config(aiConfig);
  if (!res || !res.ok) {
    setAiStatus('✗ ' + ((res && res.msg) || '保存失败'), 'error');
    return;
  }

  // 存盘后先问一次服务：配置是按 mtime 热加载的，正常情况这里就已经生效
  let chk = null;
  try {
    chk = await pywebview.api.apply_ai_config();
  } catch (e) {
    chk = null;
  }

  if (chk && chk.ok && chk.applied) {
    setAiStatus('✓ 已保存并生效 · ' + chk.msg, 'success');
  } else if (chk && chk.ok && chk.running === false) {
    setAiStatus('✓ 已保存 · ' + chk.msg, 'success');
  } else if (chk && chk.needs_restart) {
    // 服务还在用旧配置（极少见：进程读的是内存里的旧值）→ 自动重启一次
    setAiStatus('已保存，服务仍是旧配置，正在自动重启...', '');
    const rr = await pywebview.api.restart_core_proxy();
    if (!rr || !rr.ok) {
      setAiStatus('✗ 保存成功但重启失败：' + ((rr && rr.msg) || '未知错误'), 'error');
      return;
    }
    await new Promise(r => setTimeout(r, 1200));
    let chk2 = null;
    try {
      chk2 = await pywebview.api.apply_ai_config();
    } catch (e) { chk2 = null; }
    if (chk2 && chk2.applied) {
      setAiStatus('✓ 已保存并重启生效 · ' + chk2.msg, 'success');
    } else {
      setAiStatus('✓ 已保存，重启后仍未见新配置，请到「服务管理」看日志', 'error');
    }
  } else {
    setAiStatus('✓ 已保存（服务状态未知，下次对话会重读配置）', 'success');
  }

  // 在预览页保存后，顺手按新配置重跑一次，省得再点一次"预览"
  if (currentTab === 'preview') {
    const box = document.getElementById('preview-result');
    if (box && box.innerHTML.trim()) {
      await runPreview();
      setAiStatus('✓ 已保存并生效，预览已按新配置刷新', 'success');
    }
  }
}

async function restartCoreProxy2() {
  if (!(await uiConfirm('确定要重启 core_proxy 吗？'))) return;
  setAiStatus('正在重启...', '');
  const res = await pywebview.api.restart_core_proxy();
  if (res.ok) {
    setAiStatus('✓ core_proxy 已重启', 'success');
  } else {
    setAiStatus('✗ ' + (res.msg || '重启失败'), 'error');
  }
}

function setAiStatus(text, cls) {
  const s = document.getElementById('ai-status');
  if (!s) return;
  s.textContent = text;
  s.className = 'inject-status ' + (cls || '');
}

// ============= 规则增删 =============
function collectRules() {
  const box = document.getElementById('rules-container');
  if (!box) return aiConfig.rules || [];
  const rules = [];
  box.querySelectorAll('.rule-row').forEach(row => {
    const oldVal = row.querySelector('.rule-old').value;
    const newVal = row.querySelector('.rule-new').value;
    rules.push({ old: oldVal, new: newVal });
  });
  return rules;
}

function addRule() {
  // 先保存当前输入
  if (currentTab === 'user_card') {
    const toggle = document.getElementById('inject-user-card-toggle');
    aiConfig.inject_user_card = toggle ? toggle.checked : true;
  } else if (currentTab === 'inject') {
    const txt = document.getElementById('inject-text');
    aiConfig.inject = txt ? txt.value : '';
  } else if (currentTab === 'replace') {
    aiConfig.rules = collectRules();
  }

  aiConfig.rules = aiConfig.rules || [];
  aiConfig.rules.push({ old: '', new: '' });
  currentTab = 'replace';
  document.querySelectorAll('.ai-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === 'replace');
  });
  renderTabContent();
}

function removeRule(i) {
  aiConfig.rules = collectRules();
  aiConfig.rules.splice(i, 1);
  renderTabContent();
}