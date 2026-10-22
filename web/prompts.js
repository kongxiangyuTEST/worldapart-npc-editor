// ===== AI 提示词页（4 Tab） =====

let aiConfig = null;          // 当前配置（对应 ai_config.json）
let promptFragments = null;   // prompt_fragments.json
let currentTab = 'user_card'; // 当前 Tab
let previewNpc = '';          // 预览用 NPC 名
let previewWithMemory = true; // 预览是否带长期记忆

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
  renderPromptsPage();
}

function renderPromptsPage() {
  const main = document.getElementById('prompts-main');
  main.innerHTML = `
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
        <button class="primary" onclick="saveAiConfig2()">保存配置</button>
        <button class="secondary" onclick="restartCoreProxy2()">重启 core_proxy</button>
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
    </div>
  `;
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
    <pre style="max-height:420px;overflow:auto;background:var(--cl-canvas);border:1px solid var(--cl-hairline);border-radius:var(--cl-r-md);padding:12px;font-family:var(--cl-mono);font-size:12px;line-height:1.7;color:var(--cl-body);white-space:pre-wrap;word-break:break-word;">${escapeHtml(res.system || '')}</pre>
  `;
}

function setPreviewStatus(text, cls) {
  const s = document.getElementById('preview-status');
  if (!s) return;
  s.textContent = text;
  s.className = 'inject-status ' + (cls || '');
}

// ============= Tab 4：固定段落 =============
function renderFragmentsTab() {
  if (!promptFragments) {
    return `<div class="ai-tab-note">没有 prompt_fragments.json，请先运行 extract_fragments.py 从样本提取。</div>`;
  }
  const fixed = promptFragments.fixed_sections || {};
  const titles = Object.keys(fixed);
  if (titles.length === 0) {
    return `<div class="ai-tab-note">没有固定段落。</div>`;
  }

  const listHTML = titles.map((t, i) => {
    const len = fixed[t].length;
    return `<div class="prompt-item${i === 0 ? ' active' : ''}" data-title="${escapeHtml(t)}" onclick="selectPromptFragment('${escapeHtml(t)}')">
      <div class="prompt-item-title">${escapeHtml(t)}</div>
      <div class="prompt-item-len">${len} 字符</div>
    </div>`;
  }).join('');

  return `
    <div class="field-hint">从游戏实际 prompt 中提取的固定段落（所有 NPC 共用），只读查看。</div>
    <div class="prompts-layout" style="height: calc(100% - 30px);">
      <div class="prompts-sidebar">
        <div class="prompts-sidebar-header">固定段落（${titles.length} 个）</div>
        ${listHTML}
        <div class="prompts-sidebar-footer">基于 ${promptFragments.total_samples || 0} 个游戏样本提取</div>
      </div>
      <div class="prompts-content" id="prompts-content"></div>
    </div>
  `;
}

function selectPromptFragment(title) {
  if (!promptFragments) return;
  const fixed = promptFragments.fixed_sections || {};
  const content = fixed[title] || '';

  document.querySelectorAll('.prompt-item').forEach(el => {
    el.classList.toggle('active', el.dataset.title === title);
  });

  const detail = document.getElementById('prompts-content');
  if (!detail) return;
  detail.innerHTML = `
    <div class="prompt-detail-header">
      <h3>${escapeHtml(title)}</h3>
      <div class="prompt-detail-info">${content.length} 字符 · 只读</div>
    </div>
    <pre class="prompt-detail-content">${escapeHtml(content)}</pre>
  `;
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

  const res = await pywebview.api.save_ai_config(aiConfig);
  if (res.ok) {
    setAiStatus('✓ 已保存，下次对话自动生效（不用重启）', 'success');
  } else {
    setAiStatus('✗ ' + (res.msg || '保存失败'), 'error');
  }
}

async function restartCoreProxy2() {
  if (!confirm('确定要重启 core_proxy 吗？')) return;
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