// ===== 服务页 + 日志页 =====

let logNextIndex = 0;
let logTimer = null;

// ---------------- 服务页 ----------------

async function initServicesPage() {
  await refreshServicesStatus();
  setInterval(refreshServicesStatus, 3000);
  await loadUpstream();
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
  if (!confirm('确定要停止所有服务吗？游戏将无法正常对话。')) return;
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
      box.innerHTML = `⚠ <span style="color:#c33;">core_proxy 未运行</span>，无法读取实时上游`;
      return;
    }
    const url = res.upstream || '';
    const isLocal = url.includes('127.0.0.1') || url.includes('localhost');
    if (isLocal) {
      box.innerHTML = `✅ 当前 core_proxy 实际使用：<code>${escapeHtml(url)}</code>（本地）`;
    } else {
      box.innerHTML = `⚠ 当前 core_proxy 实际使用：<code style="color:#e67e22;">${escapeHtml(url)}</code>（第三方）`;
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
  if (!confirm('保存并重启 core_proxy？')) return;
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

// 服务页初始化时，顺便加载上游地址
const _origInitServicesPage = initServicesPage;
initServicesPage = async function() {
  await _origInitServicesPage();
  await loadUpstream();
};

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