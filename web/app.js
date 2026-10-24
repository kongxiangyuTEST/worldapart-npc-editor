// ===== 入口 =====

let allCards = [];

// 各页面初始化函数的名字。
// 这里只存字符串、不直接引用函数：某个脚本加载失败时，
// 只是那一页不初始化，不会让整个 init() 抛异常中断。
const PAGE_INIT_STEPS = [
  'initCardsPage',
  'initEditPage',
  'initServicesPage',
  'initLogsPage',
  'initPromptsPage',
  'initMemoryPage',
  'initTrialPage',
];

let initStarted = false;
let initFailures = [];

function reportError(scope, err) {
  const msg = (err && (err.message || String(err))) || '未知错误';
  console.error('[' + scope + ']', err);

  const s = document.getElementById('header-status');
  if (s) {
    s.textContent = '⚠ ' + scope + ' 出错：' + String(msg).slice(0, 80);
    s.style.color = '#c0392b';
  }
  setFootLeft('⚠ ' + scope + ' 出错：' + msg);
}

async function init() {
  if (initStarted) return;
  initStarted = true;

  document.querySelectorAll('.nav-item').forEach(el => {
    el.onclick = () => switchPage(el.dataset.page);
  });

  // 先确保游戏目录可用。没有它，下面读卡片、头像、记忆全是空的。
  await ensureGameDir();

  try {
    allCards = await pywebview.api.get_cards_list();
    document.getElementById('header-status').textContent =
      `共 ${allCards.length} 张卡片`;
  } catch (e) {
    allCards = [];
    reportError('读取卡片列表', e);
  }

  // 加载游戏目录显示
  setTimeout(loadGameDirDisplay, 100);

  initFailures = [];
  for (const name of PAGE_INIT_STEPS) {
    const fn = window[name];
    if (typeof fn !== 'function') {
      initFailures.push(name);
      console.error(`初始化函数 ${name} 未定义，对应脚本可能加载失败`);
      continue;
    }
    try {
      await fn(allCards);
    } catch (e) {
      initFailures.push(name);
      reportError(name, e);
    }
  }

  if (initFailures.length) {
    setFootLeft('部分页面未初始化：' + initFailures.join('、') + '（详见控制台）');
  }
}

function switchPage(pageName) {
  const name = pageName || '';

  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === name);
  });
  document.querySelectorAll('.page').forEach(el => {
    el.classList.toggle('active', el.id === 'page-' + name);
  });

  // 切到非"人物卡"页时，隐藏底部卡片按钮
  if (name !== 'cards') {
    showFooterButtons(false, false);
    setFootLeft('就绪');
  }

  if (name === 'cards' && typeof backToCards === 'function') {
    const editPage = document.getElementById('edit-page');
    if (editPage && editPage.style.display === 'flex') return;
    backToCards();
  }

  if (name === 'services') {
    if (typeof loadGameDirDisplay === 'function') loadGameDirDisplay();
    if (typeof refreshServicesStatus === 'function') refreshServicesStatus();
    if (typeof refreshUpstreamLive === 'function') refreshUpstreamLive();
  }

  if (name === 'memory' && typeof initMemoryPage === 'function') {
    try {
      initMemoryPage();
    } catch (e) {
      reportError('长期记忆', e);
    }
  }

  // 试聊页第一次进入时才连模型。启动时不连 —— 否则每次开编辑器都在
  // 后台干等一次网络往返（连不上还要等到超时），感觉像卡了一下。
  if (name === 'trial' && typeof trialOnEnter === 'function') {
    try {
      trialOnEnter();
    } catch (e) {
      reportError('试聊', e);
    }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

function showFooterButtons(showSave, showDelete) {
  document.getElementById('btn-save').style.display = showSave ? 'inline-block' : 'none';
  document.getElementById('btn-delete').style.display = showDelete ? 'inline-block' : 'none';
}

function setFootLeft(text) {
  const el = document.getElementById('foot-left');
  if (el) el.textContent = text;
}

// ===== 全局错误出口 =====
// 任何脚本里的未捕获错误都会显示到标题栏，避免"界面看着正常但功能全死"。
window.addEventListener('error', (e) => {
  if (!e || !e.message) return;
  const s = document.getElementById('header-status');
  if (s) {
    s.textContent = '⚠ 脚本错误：' + String(e.message).slice(0, 80);
    s.style.color = '#c0392b';
  }
  console.error('[window.error]', (e.filename || '') + ':' + (e.lineno || 0), e.message);
});

window.addEventListener('unhandledrejection', (e) => {
  const msg = (e && e.reason && e.reason.message) || String((e && e.reason) || '');
  if (!msg) return;
  const s = document.getElementById('header-status');
  if (s) {
    s.textContent = '⚠ 未处理的异常：' + msg.slice(0, 80);
    s.style.color = '#c0392b';
  }
  console.error('[unhandledrejection]', e.reason);
});

window.addEventListener('pywebviewready', init);
setTimeout(() => {
  const s = document.getElementById('header-status');
  if (s && s.textContent === '加载中...') init();
}, 800);

// ==================== 游戏目录管理 ====================

// 游戏目录按钮在顶栏，标签固定写「游戏目录」—— 路径太长会把顶栏撑开。
// 完整路径放在 title 里（悬停可见），以及点开的确认弹窗里。
let _lastGameDir = '';

async function loadGameDirDisplay() {
  const el = document.getElementById('game-dir-display');
  try {
    const res = await pywebview.api.check_game_dir();
    _lastGameDir = res.ok ? (res.game_dir || '') : '';
    if (el) {
      el.title = res.ok
        ? '当前：' + _lastGameDir + '\n点击修改'
        : '尚未设置游戏目录（' + (res.msg || '') + '）\n点击选择';
      el.classList.toggle('warn', !res.ok);
    }
    return res;
  } catch (e) {
    _lastGameDir = '';
    if (el) el.title = '读取失败：' + e;
    return { ok: false, msg: String(e) };
  }
}

async function onChangeGameDir() {
  // 只有在这里才显示完整路径（顶栏那颗按钮放不下）
  const msg = _lastGameDir
    ? `当前游戏目录：\n${_lastGameDir}\n\n修改会重启 core_proxy，确定继续？`
    : '还没有设置游戏目录。现在去选择？';
  if (!(await uiConfirm(msg))) return;

  const res = await pywebview.api.pick_and_save_game_dir();
  if (!res.ok) {
    if (res.msg !== '未选择') alert('✗ ' + (res.msg || '失败'));
    return;
  }
  await loadGameDirDisplay();
  // 刷新卡片列表（因为卡片来自新目录）
  setTimeout(async () => {
    try {
      const cards = await pywebview.api.get_cards_list();
      allCards = cards;
      if (typeof cardsRef !== 'undefined') cardsRef = cards;
      document.getElementById('header-status').textContent = `共 ${cards.length} 张卡片`;
    } catch (e) { }
  }, 1200);
  alert('✓ 已保存，core_proxy 已重启');
}

// ==================== 首次启动：强制选择游戏目录 ====================
// 没有游戏目录的话，卡片列表、头像、记忆读出来全是空的，后面每页都是废的，
// 所以干脆在最前面拦住：盖一层遮罩，只留「选择目录」一个出口。
// 点取消不会关掉遮罩 —— 必须选到含 WorldApart.exe 的目录才能进主界面。
async function ensureGameDir() {
  let res = null;
  try {
    res = await pywebview.api.check_game_dir();
  } catch (e) {
    res = { ok: false, msg: String(e) };
  }
  if (res && res.ok) return true;

  const mask = document.createElement('div');
  mask.className = 'gamedir-mask';
  mask.innerHTML = `
    <div class="gamedir-card">
      <h3>先选一下游戏目录</h3>
      <p>编辑器要从游戏目录里读取 NPC 卡片和头像。当前状态：
        <span class="gamedir-reason">${escapeHtml((res && res.msg) || '未设置')}</span>
      </p>
      <p class="gamedir-hint">请选到游戏根目录 —— 也就是里面有 <b>WorldApart.exe</b> 的那一层。</p>
      <button class="primary" id="gamedir-pick-btn">选择游戏目录</button>
      <div class="gamedir-status" id="gamedir-pick-status"></div>
    </div>`;
  document.body.appendChild(mask);

  const btn = document.getElementById('gamedir-pick-btn');
  const st = document.getElementById('gamedir-pick-status');

  await new Promise(resolve => {
    btn.onclick = async () => {
      btn.disabled = true;
      if (st) st.textContent = '正在打开文件夹选择框…';
      let r = null;
      try {
        r = await pywebview.api.pick_and_save_game_dir();
      } catch (e) {
        r = { ok: false, msg: String(e) };
      }
      if (r && r.ok) {
        if (st) st.textContent = '✓ 已保存：' + r.game_dir;
        await loadGameDirDisplay();
        setTimeout(() => { mask.remove(); resolve(true); }, 600);
        return;
      }
      // 取消选择不退出遮罩，继续等 —— 这就是「强制」
      if (st) {
        st.textContent = (r && r.msg === '未选择')
          ? '必须选一个游戏目录才能继续。'
          : '✗ ' + ((r && r.msg) || '选择失败');
      }
      btn.disabled = false;
    };
  });
  return true;
}

// ==================== 统一确认弹窗 ====================
// 原生 confirm() 在 WebView 里又丑又贴顶，换成主题一致的居中弹窗。
// 用法：const ok = await uiConfirm('消息', { danger: true, okText: '删除' });
function uiConfirm(message, opts) {
  return new Promise((resolve) => {
    const root = document.getElementById('ui-modal-root');
    if (!root) { resolve(window.confirm(message)); return; }

    const o = opts || {};
    const okText = o.okText || '确定';
    const cancelText = o.cancelText || '取消';

    const mask = document.createElement('div');
    mask.className = 'ui-mask';
    mask.innerHTML = `
      <div class="ui-dialog${o.danger ? ' danger' : ''}" role="dialog" aria-modal="true">
        <div class="ui-dialog-msg">${escapeHtml(message).replace(/\n/g, '<br>')}</div>
        <div class="ui-dialog-actions">
          <button class="ui-btn ui-btn-ghost" data-act="cancel">${escapeHtml(cancelText)}</button>
          <button class="ui-btn ui-btn-primary" data-act="ok">${escapeHtml(okText)}</button>
        </div>
      </div>`;

    let done = false;
    const close = (val) => {
      if (done) return;
      done = true;
      document.removeEventListener('keydown', onKey);
      root.removeChild(mask);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close(false);
      if (e.key === 'Enter') close(true);
    };
    document.addEventListener('keydown', onKey);

    mask.addEventListener('click', (e) => { if (e.target === mask) close(false); });
    mask.querySelector('[data-act="ok"]').onclick = () => close(true);
    mask.querySelector('[data-act="cancel"]').onclick = () => close(false);

    root.appendChild(mask);
    const btn = mask.querySelector('[data-act="ok"]');
    if (btn) btn.focus();
  });
}

// ==================== 统一输入弹窗 ====================
// 原生 prompt() 在 WebView 里也是贴顶的老样式，换成主题一致的居中弹窗。
// 用法：const val = await uiPrompt('请输入…', { placeholder: '…', value: '…' });
// 确定返回输入内容（去掉首尾空格），取消返回 null；输入为空时确定也返回 null。
function uiPrompt(message, opts) {
  return new Promise((resolve) => {
    const root = document.getElementById('ui-modal-root');
    if (!root) { resolve(window.prompt(message)); return; }

    const o = opts || {};
    const okText = o.okText || '确定';
    const cancelText = o.cancelText || '取消';

    const mask = document.createElement('div');
    mask.className = 'ui-mask';
    mask.innerHTML = `
      <div class="ui-dialog" role="dialog" aria-modal="true">
        <div class="ui-dialog-msg">${escapeHtml(message).replace(/\n/g, '<br>')}</div>
        <input class="form-input" type="text" style="width:100%;box-sizing:border-box;margin:0 0 14px;"
          ${o.placeholder ? `placeholder="${escapeHtml(o.placeholder)}"` : ''}>
        <div class="ui-dialog-actions">
          <button class="ui-btn ui-btn-ghost" data-act="cancel">${escapeHtml(cancelText)}</button>
          <button class="ui-btn ui-btn-primary" data-act="ok">${escapeHtml(okText)}</button>
        </div>
      </div>`;

    const input = mask.querySelector('input');
    if (o.value) input.value = o.value;

    let done = false;
    const close = (val) => {
      if (done) return;
      done = true;
      document.removeEventListener('keydown', onKey);
      root.removeChild(mask);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close(null);
      if (e.key === 'Enter') close(input.value.trim());
    };
    document.addEventListener('keydown', onKey);

    mask.addEventListener('click', (e) => { if (e.target === mask) close(null); });
    mask.querySelector('[data-act="ok"]').onclick = () => close(input.value.trim());
    mask.querySelector('[data-act="cancel"]').onclick = () => close(null);

    root.appendChild(mask);
    input.focus();
  });
}

