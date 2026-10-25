// ===== 版本与自动更新 =====
// 启动后延迟几秒静默查一次；发现新版才在右下角浮出卡片。
// 没有更新时完全不打扰（连提示都不给）。

let updateInfo = null;
let updateBusy = false;

async function initUpdateCheck() {
  try {
    const v = await pywebview.api.app_version();
    if (v && v.ok) {
      const el = document.getElementById('about-version-num');
      if (el) el.textContent = v.version;
    }
  } catch (e) { /* 版本号拿不到不影响使用 */ }

  // 延迟 6 秒：太早会和启动时的其他请求抢网络，也会显得急
  setTimeout(() => { checkUpdate(false); }, 6000);
}

async function checkUpdate(manual) {
  if (updateBusy) return;
  updateBusy = true;

  const btn = document.getElementById('about-check-update');
  if (btn && manual) { btn.disabled = true; btn.textContent = '检查中…'; }

  let res = null;
  try {
    res = await pywebview.api.check_update();
  } catch (e) {
    res = { ok: false, msg: String(e) };
  }

  if (btn && manual) { btn.disabled = false; btn.textContent = '检查更新'; }
  updateBusy = false;

  if (!res || !res.ok) {
    if (manual) updateToastShow('检查失败', res && res.msg, []);
    return;
  }

  updateInfo = res;

  if (!res.has_update) {
    if (manual) updateToastShow('已是最新版本', '当前 v' + res.current, []);
    return;
  }

  const size = res.size ? (res.size / 1048576).toFixed(1) + ' MB' : '';
  const notes = (res.notes || '').trim();
  updateToastShow(
    '发现新版本 v' + res.latest,
    '当前 v' + res.current + (size ? ' · ' + size : '') +
      (notes ? '\n' + notes.slice(0, 120) : ''),
    [
      { text: '立即更新', primary: true, onclick: 'updateDownload()' },
      { text: '稍后', onclick: 'updateToastHide()' },
    ]
  );
}

function updateToastShow(title, body, actions) {
  const box = document.getElementById('update-toast');
  const t = document.getElementById('update-toast-title');
  const b = document.getElementById('update-toast-body');
  const a = document.getElementById('update-toast-actions');
  if (!box) return;

  if (t) t.textContent = title || '';
  if (b) {
    b.textContent = body || '';
    b.style.whiteSpace = 'pre-line';
  }
  if (a) {
    a.innerHTML = (actions || []).map(x =>
      '<button class="' + (x.primary ? 'primary' : 'secondary') + ' tiny" ' +
      'onclick="' + x.onclick + '">' + escapeHtml(x.text) + '</button>'
    ).join('');
  }
  box.classList.add('show');
}

function updateToastHide() {
  const box = document.getElementById('update-toast');
  if (box) box.classList.remove('show');
}

async function updateDownload() {
  if (!updateInfo || !updateInfo.url) {
    updateToastShow('无法自动更新',
      '这个版本没上传 zip 包，请到 GitHub 页面手动下载', [
        { text: '打开下载页', primary: true, onclick: 'updateOpenPage()' },
        { text: '关闭', onclick: 'updateToastHide()' },
      ]);
    return;
  }

  updateToastShow('正在下载…', '下载完成后会让你确认重启', []);

  let res = null;
  try {
    res = await pywebview.api.download_update(updateInfo.url);
  } catch (e) {
    res = { ok: false, msg: String(e) };
  }

  if (!res || !res.ok) {
    updateToastShow('下载失败', res && res.msg, [
      { text: '重试', primary: true, onclick: 'updateDownload()' },
      { text: '关闭', onclick: 'updateToastHide()' },
    ]);
    return;
  }

  window.__updateDir = res.dir;
  updateToastShow('更新已就绪',
    '点「重启并更新」后编辑器会关闭，覆盖程序文件再自动打开。\n' +
    '你的卡片、记忆、配置都不会动。',
    [
      { text: '重启并更新', primary: true, onclick: 'updateApply()' },
      { text: '稍后', onclick: 'updateToastHide()' },
    ]);
}

function updateOpenPage() {
  const url = (updateInfo && updateInfo.page) ||
    'https://github.com/kongxiangyuTEST/worldapart-npc-editor/releases';
  try { window.open(url, '_blank'); } catch (e) { /* 忽略 */ }
}

async function updateApply() {
  if (!window.__updateDir) return;
  updateToastShow('正在更新…', '编辑器即将关闭，更新完会自动重新打开', []);
  try {
    await pywebview.api.apply_update(window.__updateDir);
  } catch (e) { /* 后端 1.5 秒后会自己退出 */ }
}
