// ===== 服务页 + 日志页 =====

let logNextIndex = 0;
let logTimer = null;

// ---------------- 服务页 ----------------

async function initServicesPage() {
  await refreshServicesStatus();
  setInterval(refreshServicesStatus, 3000);
  await loadUpstream();
  await loadMemorySettings();
  await refreshHealth();
  await refreshUsage();
}


// ---------------- 用量统计 ----------------

// core_proxy 每轮会把上游返回的 usage 记下来（以前直接丢了）。
// 单价没配就只显示 token 数，不折算花费——各家价格差很多，宁可让用户自己填。
async function refreshUsage() {
  const sum = document.getElementById('usage-summary');
  const models = document.getElementById('usage-models');
  const pin = document.getElementById('price-in');
  const pout = document.getElementById('price-out');
  if (!sum) return;

  let res;
  try {
    res = await pywebview.api.get_proxy_health();
  } catch (e) {
    sum.textContent = '读取失败：' + e;
    return;
  }

  if (!res || !res.ok) {
    sum.innerHTML = '<span class="health-muted">core_proxy 未运行，启动后开始累计</span>';
    if (models) models.innerHTML = '';
    return;
  }

  const u = res.usage || {};
  // 单价框在没被用户改过时才回填，避免刷新把正在输入的值冲掉
  if (pin && document.activeElement !== pin) pin.value = u.price_input_per_million || '';
  if (pout && document.activeElement !== pout) pout.value = u.price_output_per_million || '';

  const fmt = n => (n || 0).toLocaleString('zh-CN');
  const last = u.last;
  const rows = [
    ['累计轮数', fmt(u.rounds) + ' 轮'],
    ['输入 token', fmt(u.prompt_tokens)],
    ['输出 token', fmt(u.completion_tokens)],
    ['合计 token', fmt(u.total_tokens)],
  ];
  if (u.cost !== null && u.cost !== undefined) {
    rows.push(['估算花费', '¥ ' + Number(u.cost).toFixed(4)]);
  }
  if (last) {
    rows.push(['最近一轮', `输入 ${fmt(last.prompt_tokens)} / 输出 ${fmt(last.completion_tokens)}`]);
  }

  sum.innerHTML = rows.map(([k, v]) =>
    `<div class="usage-row"><span class="usage-k">${k}</span><span class="usage-v">${v}</span></div>`
  ).join('');

  // 按模型分组：换过上游的人能看出钱花在哪个模型上
  const byModel = u.by_model || {};
  const keys = Object.keys(byModel);
  if (models) {
    models.innerHTML = keys.length > 1
      ? '<div class="usage-models-head">按模型</div>' + keys.map(m => {
          const b = byModel[m];
          return `<div class="usage-row"><span class="usage-k">${escapeHtml(m)}</span>
                  <span class="usage-v">${b.rounds} 轮 · 入 ${fmt(b.prompt_tokens)} / 出 ${fmt(b.completion_tokens)}</span></div>`;
        }).join('')
      : '';
  }
}

async function savePriceSettings() {
  const pin = document.getElementById('price-in');
  const pout = document.getElementById('price-out');
  const st = document.getElementById('price-status');
  const setSt = (t, k) => { if (st) { st.textContent = t; st.className = 'inject-status ' + (k || ''); } };

  setSt('保存中…', '');
  let res;
  try {
    res = await pywebview.api.save_price_settings(
      pin ? pin.value : 0, pout ? pout.value : 0);
  } catch (e) {
    setSt('✗ ' + e, 'error');
    return;
  }
  if (!res || !res.ok) {
    setSt('✗ ' + ((res && res.msg) || '保存失败'), 'error');
    return;
  }
  setSt('✓ 已保存', 'success');
  // core_proxy 按 mtime 热加载，等它读到新配置再刷新
  setTimeout(refreshUsage, 800);
}


// ---------------- 游戏结构自检 ----------------

// core_proxy 每轮转发前会比对「段落集合 + system 条数」这些结构特征：
//   · 段落集合变了  → 游戏改了 prompt 组装方式
//   · NPC 名解析不出 → 名片和长期记忆全部失效（最严重）
// 这里只负责把告警显示出来。动态段（关系/库存/今日行为）不影响比对，
// 因为指纹里不含长度，内容变化不会误报。
async function refreshHealth() {
  const sum = document.getElementById('health-summary');
  const box = document.getElementById('health-alerts');
  if (!sum || !box) return;

  let res;
  try {
    res = await pywebview.api.get_proxy_health();
  } catch (e) {
    sum.textContent = '读取失败：' + e;
    return;
  }

  if (!res || !res.ok) {
    sum.innerHTML = `<span class="health-muted">${escapeHtml((res && res.msg) || 'core_proxy 未运行')}</span>`;
    box.innerHTML = '<div class="health-empty">服务启动后，这里会显示自检结果。</div>';
    return;
  }

  const h = res.health || {};
  const alerts = (h.alerts || []).slice().reverse();
  const errCount = alerts.filter(a => a.level === 'error').length;
  const warnCount = alerts.filter(a => a.level === 'warn').length;

  const chip = (label, val, bad) =>
    `<span class="health-chip${bad ? ' bad' : ''}">${label} <strong>${val || 0}</strong></span>`;

  sum.innerHTML = [
    chip('已处理', h.requests, false) + ' 轮',
    chip('NPC 名失败', h.npc_name_fail, h.npc_name_fail > 0),
    chip('结构变化', h.structure_changes, h.structure_changes > 0),
    chip('空响应', h.empty_response, h.empty_response > 0),
    chip('清洗污染', h.polluted_cleaned, h.polluted_cleaned > 0),
    chip('裁剪', h.trimmed_total, false) + ' 条',
  ].join(' ');

  if (!alerts.length) {
    box.innerHTML = '<div class="health-ok">✓ 未发现异常。游戏结构稳定，名片与记忆都正常注入。</div>';
    return;
  }

  const head = [];
  if (errCount) head.push(`<strong class="health-error-text">${errCount} 项严重</strong>`);
  if (warnCount) head.push(`${warnCount} 项提醒`);
  box.innerHTML =
    `<div class="health-head">${head.join(' · ')}（最近 ${alerts.length} 条）</div>` +
    alerts.map(a => `
      <div class="health-alert ${a.level}">
        <span class="health-msg">${escapeHtml(a.msg)}</span>
        <span class="health-ts">${escapeHtml(a.ts || '')}</span>
      </div>`).join('');
}

async function refreshServicesStatus() {
  try {
    const s = await pywebview.api.get_services_status();
    renderStatus('core_proxy', s.core_proxy || 'unknown');
    renderStatus('caddy', s.caddy || 'unknown');
  } catch (e) {
    console.error(e);
  }
}

function renderStatus(name, status) {
  const dot = document.getElementById('status-dot-' + name);
  const text = document.getElementById('status-text-' + name);
  if (!dot || !text) return;

  if (status === 'running') {
    dot.className = 'status-dot running';
    text.textContent = '运行中';
  } else if (status.startsWith('exited')) {
    dot.className = 'status-dot exited';
    text.textContent = '已退出 ' + status;
  } else {
    dot.className = 'status-dot stopped';
    text.textContent = '未运行';
  }
}

async function startService(name) {
  const res = await pywebview.api.start_service(name);
  if (!res.ok) {
    alert('启动失败: ' + (res.msg || '未知错误'));
  }
  await refreshServicesStatus();
}

async function stopService(name) {
  const res = await pywebview.api.stop_service(name);
  if (!res.ok) {
    alert('停止失败: ' + (res.msg || '未知错误'));
  }
  await refreshServicesStatus();
}

async function startAllServices() {
  await pywebview.api.start_all_services();
  await refreshServicesStatus();
}

async function stopAllServices() {
  if (!(await uiConfirm('确定要停止所有服务吗？游戏将无法正常对话。', { danger: true, okText: '全部停止' }))) return;
  await pywebview.api.stop_all_services();
  await refreshServicesStatus();
}

// ---------------- 日志页 ----------------

async function initLogsPage() {
  await refreshLogs();
  logTimer = setInterval(refreshLogs, 1000);
}

async function refreshLogs() {
  try {
    const res = await pywebview.api.get_logs(logNextIndex);
    const logs = res.logs || [];
    if (logs.length === 0) return;

    const box = document.getElementById('log-box');
    if (!box) return;

    const isAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;

    for (const entry of logs) {
      const line = document.createElement('div');
      line.className = 'log-line log-' + entry.name;
      line.innerHTML = `<span class="log-time">${escapeHtml(entry.time)}</span>` +
                       `<span class="log-name">[${escapeHtml(entry.name)}]</span>` +
                       `<span class="log-text">${escapeHtml(entry.line)}</span>`;
      box.appendChild(line);
    }

    while (box.children.length > 2000) {
      box.removeChild(box.firstChild);
    }

    if (isAtBottom) {
      box.scrollTop = box.scrollHeight;
    }

    logNextIndex = res.next_index;
  } catch (e) {
    console.error(e);
  }
}

async function clearLogs() {
  await pywebview.api.clear_logs();
  logNextIndex = 0;
  const box = document.getElementById('log-box');
  if (box) box.innerHTML = '';
}

// ==================== 上游地址 ====================

async function loadUpstream() {
  try {
    const res = await pywebview.api.get_upstream();
    const input = document.getElementById('upstream-url');
    if (input && res.ok) {
      input.value = res.url || '';
    }
  } catch (e) { console.error(e); }
  await refreshUpstreamLive();
}

async function refreshUpstreamLive() {
  const box = document.getElementById('upstream-live-status');
  if (!box) return;
  try {
    const res = await pywebview.api.get_upstream_status();
    if (!res.ok) {
      box.innerHTML = `⚠ <span style="color:var(--cl-error);">core_proxy 未运行</span>，无法读取实时上游`;
      return;
    }
    const url = res.upstream || '';
    const isLocal = url.includes('127.0.0.1') || url.includes('localhost');
    if (isLocal) {
      box.innerHTML = `✅ 当前 core_proxy 实际使用：<code>${escapeHtml(url)}</code>（本地）`;
    } else {
      box.innerHTML = `⚠ 当前 core_proxy 实际使用：<code style="color:var(--cl-warning);">${escapeHtml(url)}</code>（第三方）`;
    }
  } catch (e) {
    box.textContent = '状态读取失败';
  }
}

function setUpstream(url) {
  const input = document.getElementById('upstream-url');
  if (input) input.value = url;
}

function setUpsStatus(text, cls) {
  const s = document.getElementById('upstream-status');
  if (!s) return;
  s.textContent = text;
  s.className = 'inject-status ' + (cls || '');
}

async function saveUpstreamOnly() {
  const url = document.getElementById('upstream-url').value.trim();
  if (!url) { alert('请填写地址'); return; }
  setUpsStatus('保存中...', '');
  const res = await pywebview.api.save_upstream(url);
  if (res.ok) {
    setUpsStatus('✓ 已保存（重启 core_proxy 后生效）', 'success');
    await refreshUpstreamLive();
  } else {
    setUpsStatus('✗ ' + (res.msg || '失败'), 'error');
  }
}

async function saveAndRestartUpstream() {
  const url = document.getElementById('upstream-url').value.trim();
  if (!url) { alert('请填写地址'); return; }
  if (!(await uiConfirm('保存并重启 core_proxy？\n\n上游地址改动要重启才会生效。'))) return;
  setUpsStatus('保存并重启中...', '');
  const res = await pywebview.api.save_and_restart_upstream(url);
  if (res.ok) {
    setUpsStatus('✓ 已保存，core_proxy 已重启', 'success');
    // 等 1 秒让 core_proxy 启动完成
    setTimeout(refreshUpstreamLive, 1000);
  } else {
    setUpsStatus('✗ ' + (res.msg || '失败'), 'error');
  }
}

// 注：上游地址的加载已并入 initServicesPage() 里，不再二次包装。

// ==================== 游戏接口地址 ====================

function copyGameApiUrl() {
  const input = document.getElementById('game-api-url');
  if (!input) return;
  input.select();
  input.setSelectionRange(0, 99999);
  try {
    navigator.clipboard.writeText(input.value);
  } catch (e) {
    document.execCommand('copy');
  }
  // 顺便给个视觉反馈
  const btn = event.target;
  const oldText = btn.textContent;
  btn.textContent = '已复制';
  setTimeout(() => { btn.textContent = oldText; }, 1500);
}
// ==================== 记忆总结设置 ====================
// 接第三方 API 时，总结建议走本地模型：对话走 API、记忆留本地。
function setMergeUrl(url) {
  const el = document.getElementById('merge-url');
  if (el) el.value = url || '';
}

async function loadMemorySettings() {
  try {
    const res = await pywebview.api.get_ai_config();
    if (!res || !res.ok) return;
    const d = res.data || {};
    const urlEl = document.getElementById('merge-url');
    const everyEl = document.getElementById('merge-every');
    const tokEl = document.getElementById('merge-tokens');
    if (urlEl) urlEl.value = d.memory_merge_url || '';
    if (everyEl) everyEl.value = d.memory_merge_every || 12;
    if (tokEl) tokEl.value = d.memory_merge_max_tokens || 4000;
    const slotEl = document.getElementById('memory-slot');
    if (slotEl) slotEl.value = d.memory_slot || 'past_events';
    const pastEl = document.getElementById('past-events-max');
    if (pastEl) pastEl.value = (d.past_events_max_chars === undefined ? 800 : d.past_events_max_chars);
  } catch (e) {
    console.error(e);
  }

  // 填完忘点保存 → 静默失效（用户踩过：总结地址没存，结果打到上游 401）。
  // 改成失焦即存，core_proxy 按 mtime 热加载，不需要重启。
  ['merge-url', 'merge-every', 'merge-tokens', 'past-events-max', 'memory-slot'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => saveMemorySettings(true));
  });
}

async function saveMemorySettings(silent) {
  const status = document.getElementById('merge-status');
  const setMsg = (t, cls) => {
    if (!status) return;
    status.textContent = t;
    status.className = 'inject-status ' + (cls || '');
  };

  const url = (document.getElementById('merge-url') || {}).value || '';
  const every = (document.getElementById('merge-every') || {}).value || 12;
  const tokens = (document.getElementById('merge-tokens') || {}).value || 4000;
  const slot = (document.getElementById('memory-slot') || {}).value || 'past_events';
  const pastMax = (document.getElementById('past-events-max') || {}).value;

  if (!silent) setMsg('保存中...', '');
  let res;
  try {
    res = await pywebview.api.save_memory_settings(url.trim(), every, tokens, slot, pastMax);
  } catch (e) {
    setMsg('✗ ' + ((e && e.message) || e), 'error');
    return;
  }
  if (res && res.ok) {
    setMsg(silent ? '✓ 已自动保存' : '✓ 已保存，core_proxy 会自动重读', 'success');
  } else {
    setMsg('✗ ' + ((res && res.msg) || '保存失败'), 'error');
  }
}
