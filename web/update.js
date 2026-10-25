// ===== 版本与更新（关于页底部）=====
// 不做弹窗：更新不是急事，弹出来打断人反而烦。
// 启动后静默查一次，发现新版只在侧栏「关于」点个小红点，
// 用户点进去才看到详情。

let updateInfo = null;
let updateBusy = false;

async function initUpdateCheck() {
  // 版本号 + 已保存的代理
  try {
    const v = await pywebview.api.app_version();
    if (v && v.ok) {
      const num = document.getElementById('about-version-num');
      const foot = document.getElementById('update-foot');
      const updNum = document.getElementById('update-version-num');
      if (num) num.textContent = v.version;
      if (foot) foot.textContent = '当前版本 v' + v.version;
      if (updNum) updNum.textContent = v.version;
    }
  } catch (e) { /* 拿不到版本号不影响使用 */ }

  try {
    const res = await pywebview.api.update_proxy_get();
    const proxyEl = document.getElementById('update-proxy');
    if (proxyEl && res && res.ok && res.proxy) proxyEl.value = res.proxy;
  } catch (e) { /* 没有代理配置也正常 */ }

  // 6 秒后静默检查：太早会和启动时的其他请求抢网络
  setTimeout(() => { checkUpdate(false); }, 6000);
}

async function checkUpdate(manual) {
  if (updateBusy) return;
  updateBusy = true;

  const btn = document.getElementById('about-check-update');
  const result = document.getElementById('update-result');
  const foot = document.getElementById('update-foot');
  const proxyEl = document.getElementById('update-proxy');
  const proxy = proxyEl ? proxyEl.value.trim() : '';

  // 不先占位显示「正在连接」：没有更新时结果区会隐藏，
  // 先显示再收起会闪一下。进度只体现在按钮文字上。
  if (btn) { btn.disabled = true; btn.textContent = '检查中…'; }

  let res = null;
  try {
    res = await pywebview.api.check_update(proxy);
  } catch (e) {
    res = { ok: false, msg: String(e) };
  }

  if (btn) { btn.disabled = false; btn.textContent = '检查更新'; }
  updateBusy = false;

  if (!res || !res.ok) {
    if (manual && result) {
      result.style.display = 'block';
      result.className = 'update-result bad';
      result.textContent = (res && res.msg) ? res.msg : '检查失败';
    }
    return;
  }

  updateInfo = res;

  if (!res.has_update) {
    // 参考 MaaEnd：一行居中的灰字，不占地方
    if (foot) foot.textContent = '当前已是最新版本 (v' + res.current + ')';
    if (result) result.style.display = 'none';
    updateClearBadge();
    return;
  }

  updateSetBadge();
  if (foot) foot.textContent = '当前 v' + res.current + '，可更新到 v' + res.latest;

  if (result) {
    result.style.display = 'block';
    result.className = 'update-result good';
    const size = res.size ? (res.size / 1048576).toFixed(1) + ' MB' : '';
    result.innerHTML =
      '<div class="update-ver">v' + escapeHtml(res.current) +
      ' → <b>v' + escapeHtml(res.latest) + '</b>' +
      (size ? ' <span class="update-size">' + size + '</span>' : '') + '</div>' +
      (res.notes
        ? '<div class="update-notes">' + escapeHtml(res.notes.slice(0, 300)) + '</div>'
        : '') +
      '<div class="update-actions">' +
        '<button class="update-btn primary" onclick="updateDownload()">下载并更新</button>' +
      '</div>';
  }
}

function updateSetBadge() {
  const nav = document.querySelector('.nav-item[data-page="update"]');
  if (nav) nav.classList.add('has-update');
}

function updateClearBadge() {
  const nav = document.querySelector('.nav-item[data-page="update"]');
  if (nav) nav.classList.remove('has-update');
}

function updateOpenGitHub() {
  const url = (updateInfo && updateInfo.page) ||
    'https://github.com/kongxiangyuTEST/worldapart-npc-editor/releases';
  try { window.open(url, '_blank'); } catch (e) { /* 忽略 */ }
}

async function updateDownload() {
  const result = document.getElementById('update-result');

  if (!updateInfo || !updateInfo.url) {
    if (result) {
      result.style.display = 'block';
      result.className = 'update-result bad';
      result.textContent = '这个版本没上传 zip 包，请点「GitHub」手动下载';
    }
    return;
  }

  if (result) {
    result.className = 'update-result';
    result.textContent = '正在下载，请稍候…（几十 MB，慢的话可能要一两分钟）';
  }

  let res = null;
  try {
    res = await pywebview.api.download_update(updateInfo.url);
  } catch (e) {
    res = { ok: false, msg: String(e) };
  }

  if (!res || !res.ok) {
    if (result) {
      result.className = 'update-result bad';
      result.textContent = '下载失败：' + ((res && res.msg) || '');
    }
    return;
  }

  window.__updateDir = res.dir;
  if (result) {
    result.className = 'update-result good';
    result.innerHTML =
      '<div class="update-ver">更新已就绪</div>' +
      '<div class="update-notes">点下面按钮后编辑器会关闭，覆盖程序文件再自动打开。' +
      '你的卡片、记忆、配置都不会动。</div>' +
      '<div class="update-actions">' +
        '<button class="update-btn primary" onclick="updateApply()">重启并更新</button>' +
      '</div>';
  }
}

async function updateApply() {
  if (!window.__updateDir) return;
  const result = document.getElementById('update-result');
  if (result) {
    result.className = 'update-result';
    result.textContent = '正在更新，编辑器即将关闭…';
  }
  try {
    await pywebview.api.apply_update(window.__updateDir);
  } catch (e) { /* 后端 1.5 秒后会自己退出 */ }
}
