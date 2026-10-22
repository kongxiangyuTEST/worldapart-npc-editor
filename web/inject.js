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