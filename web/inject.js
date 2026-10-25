// ===== AI 注入配置页 =====

let currentConfig = null;

async function initInjectPage() {
  await loadAiConfig();
}

async function loadAiConfig() {
  const res = await pywebview.api.get_ai_config();
  if (!res.ok) {
    document.getElementById('inject-status').textContent = '读取失败: ' + (res.msg || '');
    return;
  }
  currentConfig = res.data;
  renderInjectPage();
}

function renderInjectPage() {
  const container = document.getElementById('inject-main');
  const inject = currentConfig.inject || '';
  const rules = currentConfig.rules || [];

  container.innerHTML = `
      <div class="inject-toggle">
      <label>
        <input type="checkbox" id="inject-user-card-toggle" ${currentConfig.inject_user_card !== false ? 'checked' : ''}>
        启用用户名片注入
      </label>
      <span class="inject-toggle-hint">关闭后，core_proxy 不再注入 .md 里的用户名片</span>
    </div>

    <div class="section-title">INJECT · 注入内容</div>
    <div class="field tall">
      <label>每次对话都会注入到 system prompt 开头的内容</label>
      <textarea id="inject-text" spellcheck="false">${escapeHtml(inject)}</textarea>
    </div>

    <div class="section-title">REPLACE · 替换规则</div>
    <div class="field">
      <label>对玩家输入做"查找 → 替换"（按顺序执行）</label>
    </div>
    <div id="rules-container"></div>
    <div class="inject-actions">
      <button class="secondary" onclick="addRule()">+ 添加规则</button>
      <button class="secondary" onclick="importAiConfig()" title="粘贴别人分享的配置 JSON，合并进来">导入配置</button>
    </div>

    <div class="inject-footer">
      <span class="inject-status" id="inject-status"></span>
      <div class="inject-footer-buttons">
        <button class="secondary" onclick="loadAiConfig()">放弃更改</button>
        <button class="primary" onclick="saveAiConfig()">保存配置</button>
        <button class="secondary" onclick="restartCoreProxy()">重启 core_proxy</button>
      </div>
    </div>
  `;

  renderRules(rules);
    // 绑定"用户名片注入"开关
  const toggle = document.getElementById('inject-user-card-toggle');
  if (toggle) {
    toggle.onchange = (e) => {
      currentConfig.inject_user_card = e.target.checked;
      // 标记有未保存修改（可选）
      const status = document.getElementById('inject-status');
      if (status) {
        status.textContent = '⚠ 有未保存修改，点"保存配置"生效';
        status.className = 'inject-status';
      }
    };
  }
}

function renderRules(rules) {
  const box = document.getElementById('rules-container');
  box.innerHTML = '';
  rules.forEach((r, i) => {
    const row = document.createElement('div');
    row.className = 'rule-row';
    row.innerHTML = `
      <textarea class="rule-old" data-index="${i}" placeholder="查找（OLD）">${escapeHtml(r.old || '')}</textarea>
      <div class="rule-arrow">→</div>
      <textarea class="rule-new" data-index="${i}" placeholder="替换为（NEW）">${escapeHtml(r.new || '')}</textarea>
      <button class="rule-del" onclick="removeRule(${i})">×</button>
    `;
    box.appendChild(row);
  });
}

function collectRules() {
  const box = document.getElementById('rules-container');
  const rows = box.querySelectorAll('.rule-row');
  const rules = [];
  rows.forEach((row, i) => {
    const oldVal = row.querySelector('.rule-old').value;
    const newVal = row.querySelector('.rule-new').value;
    rules.push({ old: oldVal, new: newVal });
  });
  return rules;
}

function addRule() {
  const rules = collectRules();
  rules.push({ old: '', new: '' });
  renderRules(rules);
}

function removeRule(i) {
  const rules = collectRules();
  rules.splice(i, 1);
  renderRules(rules);
}

async function saveAiConfig() {
  const inject = document.getElementById('inject-text').value;
  const rules = collectRules();

  const toggle = document.getElementById('inject-user-card-toggle');
  const injectUserCard = toggle ? toggle.checked : true;

  const data = {
    inject_user_card: injectUserCard,
    inject: inject,
    rules: rules
  };

  const res = await pywebview.api.save_ai_config(data);
  const status = document.getElementById('inject-status');
  if (res.ok) {
    status.textContent = '✓ 已保存（点"重启 core_proxy"后生效）';
    status.className = 'inject-status success';
    currentConfig = data;
  } else {
    status.textContent = '✗ ' + (res.msg || '保存失败');
    status.className = 'inject-status error';
  }
}

async function restartCoreProxy() {
  if (!(await uiConfirm('确定要重启 core_proxy 吗？'))) return;
  const status = document.getElementById('inject-status');
  status.textContent = '正在重启...';
  status.className = 'inject-status';

  const res = await pywebview.api.restart_core_proxy();
  if (res.ok) {
    status.textContent = '✓ core_proxy 已重启';
    status.className = 'inject-status success';
  } else {
    status.textContent = '✗ ' + (res.msg || '重启失败');
    status.className = 'inject-status error';
  }
}

// ===== 导入配置 =====
// 别人分享的通常是整个 ai_config.json，也可能只有 inject + replace_rules 片段。
// 只取认识的键，交给 save_ai_config（合并保存），其余字段一概不动。
const IMPORT_KEYS = [
  'inject', 'replace_rules', 'memory_merge_url', 'memory_slot',
  'memory_budget', 'save_guard', 'tts_style_inject', 'local_model_name',
];

async function importAiConfig() {
  const text = await uiPromptTextarea(
    '把配置 JSON 粘贴进来。\n支持整个 ai_config.json，也支持只含 inject / replace_rules 的片段。\n只导入认识的字段，其余配置保持不变。',
    {
      placeholder: '{"inject": "……", "replace_rules": [{"from": "旧", "to": "新"}]}',
      okText: '导入',
    });
  if (!text || !text.trim()) return;

  let data;
  try {
    data = JSON.parse(text.trim());
  } catch (e) {
    uiConfirm('不是合法的 JSON：\n' + e.message, { okText: '知道了' });
    return;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    uiConfirm('顶层要是一个对象（大括号包起来的），不是数组或纯文本。', { okText: '知道了' });
    return;
  }

  const patch = {};
  const hit = [];
  for (const k of IMPORT_KEYS) {
    if (k in data) { patch[k] = data[k]; hit.push(k); }
  }
  if (!hit.length) {
    uiConfirm('没找到认识的字段。\n认识的字段：' + IMPORT_KEYS.join('、'),
      { okText: '知道了' });
    return;
  }

  const res = await pywebview.api.save_ai_config(patch);
  if (!res || !res.ok) {
    uiConfirm('导入失败：' + ((res && res.msg) || '未知错误'), { okText: '知道了' });
    return;
  }

  await loadAiConfig();
  const st = document.getElementById('inject-status');
  if (st) st.textContent = '已导入 ' + hit.length + ' 项：' + hit.join('、');
  uiConfirm('已导入：' + hit.join('、') + '\n\n其他配置保持不变。', { okText: '好' });
}
