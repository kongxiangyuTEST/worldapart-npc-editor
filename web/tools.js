// ===== 工具页 =====

let toolsCurrentTab = 'extract';

const TOOLS_SETTINGS_KEY = 'wa_tools_settings';

function loadToolsSettings() {
  try {
    return JSON.parse(localStorage.getItem(TOOLS_SETTINGS_KEY) || '{}');
  } catch (e) { return {}; }
}

function saveToolsSettings(s) {
  localStorage.setItem(TOOLS_SETTINGS_KEY, JSON.stringify(s));
}

function switchToolTab(tab) {
  toolsCurrentTab = tab;
  document.querySelectorAll('#page-tools .ai-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  renderToolTab();
}

function renderToolTab() {
  const box = document.getElementById('tools-tab-content');
  if (!box) return;

  if (toolsCurrentTab === 'extract') {
    box.innerHTML = renderExtractTab();
    bindExtractTab();
    if (extractMode === 'cards') loadEditorCardsCount();
  } else if (toolsCurrentTab === 'diff') {
    box.innerHTML = renderDiffTab();
    loadBackupsList();
  } else if (toolsCurrentTab === 'usage') {
    box.innerHTML = renderUsageTab();
    refreshUsage();
  } else if (toolsCurrentTab === 'health') {
    box.innerHTML = renderHealthTab();
    refreshHealth();
  } else if (toolsCurrentTab === 'batch') {

    box.innerHTML = `<div class="placeholder"><div class="big">📦</div><div>批量工具</div><div class="hint">之后再做</div></div>`;
  } else if (toolsCurrentTab === 'tavern') {
    box.innerHTML = renderTavernTab();
  } else if (toolsCurrentTab === 'io') {
    box.innerHTML = `<div class="placeholder"><div class="big">📥</div><div>导入 / 导出</div><div class="hint">之后再做</div></div>`;
  }
}

// ==================== Tab：用量统计 / 结构自检 ====================
// 这两块原先堆在「服务管理」页，页面越拉越长；挪到工具页按 Tab 分开放。
// 数据来源仍然是 core_proxy 的 /_status，元素 id 保持不变，
// 所以 services.js 里的 refreshUsage / refreshHealth 一行都不用改。

function renderUsageTab() {
  return `
    <div class="upstream-box" style="margin-top:0;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <h3 style="margin:0;font-size:14px;color:var(--cl-ink);">用量统计</h3>
        <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="refreshUsage()">刷新</button>
      </div>
      <div class="field-hint" style="margin-bottom:10px;">
        core_proxy 每轮记录上游返回的 token 用量。这里统计的是走云端 API 的部分；
        转给本地模型（Mac）的请求不产生费用，也不计入花费。
      </div>
      <div id="usage-summary" class="usage-summary">读取中…</div>
      <div id="usage-models" class="usage-models"></div>
      <div class="usage-price-row">
        <span class="usage-price-label">单价（每百万 token）</span>
        <input id="price-in" type="number" step="0.01" min="0" class="usage-price-input" placeholder="输入">
        <span class="usage-price-sep">/</span>
        <input id="price-out" type="number" step="0.01" min="0" class="usage-price-input" placeholder="输出">
        <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="savePriceSettings()">保存</button>
        <span class="inject-status" id="price-status"></span>
      </div>
      <div class="field-hint" style="margin-top:6px;">
        填 0 或留空表示不折算花费。单价按你自己充值的服务商为准，只用于估算。
      </div>
    </div>`;
}

function renderHealthTab() {
  return `
    <div class="upstream-box" style="margin-top:0;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <h3 style="margin:0;font-size:14px;color:var(--cl-ink);">游戏结构自检</h3>
        <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="refreshHealth()">刷新</button>
      </div>
      <div class="field-hint" style="margin-bottom:10px;">
        core_proxy 每轮转发前会比对「段落集合 + system 条数」这些结构特征。<br>
        段落集合变了 → 游戏改了 prompt 组装方式；NPC 名解析不出来 → 名片和长期记忆全部失效（最严重）。
      </div>
      <div id="health-summary" class="health-summary">读取中…</div>
      <div id="health-alerts" class="health-alerts"></div>
    </div>`;
}

// ==================== Tab 1：提取（头像 / 角色卡） ====================
// 两个功能用的是同一套输入（CLI 路径 + 游戏目录），合到一页用开关切换，
// 免得用户在两页之间反复填同样的路径。
let extractMode = 'portrait';

function switchExtractMode(mode) {
  extractMode = (mode === 'cards') ? 'cards' : 'portrait';
  renderToolTab();
}

function renderExtractTab() {
  const s = loadToolsSettings();
  const cli = s.assetstudio_cli || '';
  const gameDir = s.game_dir || '';
  const keepTmp = s.keep_tmp || false;
  const isCards = extractMode === 'cards';

  const intro = isCards
    ? `<p>从游戏本体直接解析 NPC 角色卡，<strong>不依赖 Mod</strong>。生成的卡片会写进项目内的
         <code>editor_cards\\</code>，跟 Mod 生成的卡片分开存放。已有的「用户名片」会保留。</p>`
    : `<p>从游戏资源里提取全部 NPC 头像。需要提前下载 <strong>AssetStudioModCLI</strong>，
         把它的 <code>.exe</code> 路径填在下面。</p>
       <p>下载地址：<code>https://github.com/aelurum/AssetStudio/releases</code><button class="copy-btn" onclick="copyAssetstudioUrl(this)">复制</button>（找文件名带 <code>CLI</code> 的压缩包）</p>`;

  return `
    <div class="extract-switch">
      <button class="extract-opt ${isCards ? '' : 'active'}" onclick="switchExtractMode('portrait')">
        提取头像
        <span class="extract-opt-sub">portraits\\</span>
      </button>
      <button class="extract-opt ${isCards ? 'active' : ''}" onclick="switchExtractMode('cards')">
        导出角色卡
        <span class="extract-opt-sub">editor_cards\\</span>
      </button>
    </div>

    <div class="ai-tab-note" style="margin-bottom:14px;">
      ${intro}
      <p style="color:var(--cl-muted);background:var(--cl-surface-soft);border:1px solid var(--cl-hairline);padding:8px 12px;border-radius:var(--cl-r-sm);margin-top:8px;">
        ⏱ <strong>耗时提示：</strong>提取要扫描游戏全部资源，一般需要 <strong>几分钟</strong>。
        进度可以切到左侧「日志」页查看，别关编辑器。
      </p>
    </div>

    <div class="field">
      <label>AssetStudioModCLI.exe 路径</label>
      <div style="display:flex;gap:8px;">
        <input id="tools-cli-path" value="${escapeHtml(cli)}" placeholder="选择 AssetStudioModCLI.exe 的完整路径"
               style="flex:1;padding:6px 10px;border:1px solid var(--cl-hairline);border-radius:var(--cl-r-sm);font-family:inherit;font-size:13px;">
        <button class="secondary" onclick="pickCli()">选择文件</button>
      </div>
    </div>

    <div class="field">
      <label>游戏根目录（WorldApart.exe 所在目录）</label>
      <div style="display:flex;gap:8px;">
        <input id="tools-game-dir" value="${escapeHtml(gameDir)}" placeholder="选择 WorldApart.exe 所在目录"
               style="flex:1;padding:6px 10px;border:1px solid var(--cl-hairline);border-radius:var(--cl-r-sm);font-family:inherit;font-size:13px;">
        <button class="secondary" onclick="pickGameDir()">选择文件夹</button>
      </div>
      <div class="field-hint" style="margin-top:6px;">选择框会记住上次打开的位置，不用每次都从根目录翻。</div>
    </div>

    ${isCards ? `
      <div class="editor-cards-info">
        <div class="editor-cards-info-left">
          <span class="editor-cards-label">当前 editor_cards\\</span>
          <span class="editor-cards-count"><strong id="cards-count-display">…</strong> 张卡片</span>
        </div>
        <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="openEditorCardsDir()">打开目录</button>
      </div>` : ''}

    <div class="inject-footer" style="margin-top:20px;">
      <div style="display:flex;align-items:center;gap:20px;">
        <label style="display:flex;align-items:center;gap:6px;margin:0;font-weight:normal;font-size:12px;color:var(--cl-body);cursor:pointer;">
          <input type="checkbox" id="tools-keep-tmp" ${keepTmp ? 'checked' : ''}>
          保留中间文件（调试用）
        </label>
        <button class="secondary" id="tools-abort-btn" onclick="abortExtract()" disabled>中止提取</button>
      </div>
      <div class="inject-footer-buttons">
        <span class="inject-status" id="tools-status" style="margin-right:14px;"></span>
        <button class="primary" id="tools-run-btn" onclick="runExtract()">${isCards ? '开始导出' : '开始提取'}</button>
      </div>
    </div>
  `;
}

function bindExtractTab() {
  const cliInput = document.getElementById('tools-cli-path');
  const gameInput = document.getElementById('tools-game-dir');
  const keepInput = document.getElementById('tools-keep-tmp');
  if (!cliInput || !gameInput || !keepInput) return;

  const persist = () => {
    saveToolsSettings({
      assetstudio_cli: cliInput.value.trim(),
      game_dir: gameInput.value.trim(),
      keep_tmp: keepInput.checked,
    });
  };

  cliInput.oninput = persist;
  gameInput.oninput = persist;
  keepInput.onchange = persist;
}

// 通用复制：WebView 有时不给剪贴板权限，退回 execCommand
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e2) {
      return false;
    }
  }
}

async function copyAssetstudioUrl(btn) {
  const url = 'https://github.com/aelurum/AssetStudio/releases';
  const ok = await copyText(url);
  if (!btn) return;
  const old = btn.textContent;
  btn.textContent = ok ? '✓ 已复制' : '✗ 失败';
  setTimeout(() => { btn.textContent = old; }, 1500);
}

async function pickCli() {
  const res = await pywebview.api.pick_assetstudio_cli();
  if (res.ok) {
    const input = document.getElementById('tools-cli-path');
    if (input) {
      input.value = res.path;
      input.dispatchEvent(new Event('input'));
    }
  } else if (res.msg && res.msg !== '未选择') {
    alert(res.msg);
  }
}

async function pickGameDir() {
  const res = await pywebview.api.pick_game_dir();
  if (res.ok) {
    const input = document.getElementById('tools-game-dir');
    if (input) {
      input.value = res.path;
      input.dispatchEvent(new Event('input'));
    }
  } else if (res.msg && res.msg !== '未选择') {
    alert(res.msg);
  }
}

async function loadEditorCardsCount() {
  try {
    const res = await pywebview.api.get_editor_cards_count();
    const el = document.getElementById('cards-count-display');
    if (el && res && res.ok) el.textContent = res.count;
  } catch (e) {}
}

async function openEditorCardsDir() {
  await pywebview.api.open_editor_cards_dir();
}

async function runExtract() {
  const cliEl = document.getElementById('tools-cli-path');
  const dirEl = document.getElementById('tools-game-dir');
  const tmpEl = document.getElementById('tools-keep-tmp');
  const status = document.getElementById('tools-status');
  const runBtn = document.getElementById('tools-run-btn');
  const abortBtn = document.getElementById('tools-abort-btn');

  const cli = cliEl ? cliEl.value.trim() : '';
  const gameDir = dirEl ? dirEl.value.trim() : '';
  const keepTmp = tmpEl ? tmpEl.checked : false;
  const isCards = extractMode === 'cards';

  if (!cli || !gameDir) {
    alert('请先填写 AssetStudioModCLI 路径和游戏目录');
    return;
  }

  runBtn.disabled = true;
  abortBtn.disabled = false;
  status.textContent = (isCards ? '正在导出...' : '正在提取...') + '（看「日志」页有进度）';
  status.className = 'inject-status';

  try {
    const res = isCards
      ? await pywebview.api.extract_npc_cards(cli, gameDir, keepTmp)
      : await pywebview.api.extract_portraits(cli, gameDir, keepTmp);

    if (res && res.ok) {
      status.textContent = `✓ 完成，共 ${res.done} 张`;
      status.className = 'inject-status success';

      if (isCards) {
        loadEditorCardsCount();
        if (typeof allCards !== 'undefined' && pywebview.api) {
          allCards = await pywebview.api.get_cards_list();
          if (typeof cardsRef !== 'undefined') cardsRef = allCards;
          if (typeof renderCardsPage === 'function') renderCardsPage();
        }
      } else if (typeof allCards !== 'undefined' && pywebview.api) {
        allCards = await pywebview.api.get_cards_list();
        if (typeof cardsRef !== 'undefined') cardsRef = allCards;
        if (typeof renderCardsPage === 'function') renderCardsPage();
      }
    } else if (res && res.aborted) {
      status.textContent = '⊘ 已中止';
      status.className = 'inject-status';
    } else {
      status.textContent = '✗ ' + ((res && res.msg) || '失败');
      status.className = 'inject-status error';
    }
  } catch (e) {
    status.textContent = '✗ ' + e;
    status.className = 'inject-status error';
  } finally {
    runBtn.disabled = false;
    abortBtn.disabled = true;
  }
}

async function abortExtract() {
  const status = document.getElementById('tools-status');
  const abortBtn = document.getElementById('tools-abort-btn');
  if (abortBtn) abortBtn.disabled = true;
  if (status) {
    status.textContent = '正在中止...';
    status.className = 'inject-status';
  }
  await pywebview.api.abort_extract();
}

// ==================== Tab 3：卡片对比 ====================

function renderDiffTab() {
  return `
    <div class="diff-tab-wrapper">
      <div class="ai-tab-note" style="margin-bottom:10px;">
        <p style="margin:4px 0;">对比"上次备份"和"当前卡片"，看游戏更新后哪些 NPC 新增 / 改动 / 删除。</p>
        <p style="color:var(--cl-body);font-size:12px;margin:4px 0;">每次点"导出角色卡"时，编辑器会<strong>自动备份</strong>上一次的卡片。这里选一份备份来对比。</p>
      </div>

      <div class="field" style="margin-bottom:10px;">
        <label style="display:block;margin-bottom:6px;color:var(--cl-body);font-size:12px;font-weight:bold;">选择要对比的备份</label>
        <div style="display:flex;gap:10px;align-items:center;">
          <select id="diff-backup-select" style="flex:1;padding:6px 10px;border:1px solid var(--cl-hairline);border-radius:3px;font-size:13px;">
            <option value="">加载中...</option>
          </select>
          <button class="primary" onclick="runDiff()" style="white-space:nowrap;">开始对比</button>
        </div>
      </div>

      <div id="diff-result" class="diff-result-box"></div>

      <div id="diff-actions" style="display:none;margin-top:12px;padding:12px 16px;background:var(--cl-surface-soft);border:1px solid var(--cl-hairline);border-radius:4px;flex-shrink:0;">
        <div style="font-size:12px;color:var(--cl-muted);margin-bottom:8px;">
          ⚠ "应用改动"会把新数据合并进当前卡片（游戏字段用新版，用户名片保留）。
          合并前会自动备份当前卡片到 <code>editor_cards_backup\\</code>。
        </div>
        <div style="display:flex;gap:10px;">
          <button class="primary" id="diff-apply-btn" onclick="applyDiff()">应用改动（保留用户名片）</button>
        </div>
      </div>
    </div>
  `;
}

async function loadBackupsList() {
  const sel = document.getElementById('diff-backup-select');
  if (!sel) return;
  try {
    const res = await pywebview.api.list_card_backups();
    const backups = res.backups || [];
    if (backups.length === 0) {
      sel.innerHTML = '<option value="">（还没有备份）</option>';
      return;
    }
    sel.innerHTML = backups.map(b =>
      `<option value="${escapeHtml(b.name)}">${escapeHtml(b.name)}（${b.count} 张）</option>`
    ).join('');
  } catch (e) {
    sel.innerHTML = '<option value="">加载失败</option>';
  }
}

async function runDiff() {
  const sel = document.getElementById('diff-backup-select');
  const box = document.getElementById('diff-result');
  if (!sel || !sel.value) {
    alert('请先选择一个备份');
    return;
  }

  box.innerHTML = '<div class="diff-empty">正在对比...</div>';

  try {
    const res = await pywebview.api.diff_cards(sel.value);
    if (!res.ok) {
      box.innerHTML = `<div class="diff-empty" style="color:var(--cl-error);">✗ ${escapeHtml(res.msg || '失败')}</div>`;
      return;
    }

    let html = '';

    // 顶部汇总条
    html += `
      <div class="diff-summary">
        <span class="diff-sum-item diff-sum-added">新增 ${res.added.length}</span>
        <span class="diff-sum-sep">·</span>
        <span class="diff-sum-item diff-sum-changed">改动 ${res.changed.length}</span>
        <span class="diff-sum-sep">·</span>
        <span class="diff-sum-item diff-sum-removed">删除 ${res.removed.length}</span>
      </div>
    `;

    // 新增
    if (res.added.length > 0) {
      html += `<div class="diff-section">
        <div class="diff-section-title diff-title-added">🟡 新增（${res.added.length}）</div>
        <div class="diff-card-list">`;
      for (const name of res.added.slice(0, 100)) {
        html += `
          <div class="diff-card">
            <div class="diff-card-name">${escapeHtml(name)}</div>
            <div class="diff-card-fields">新增角色卡</div>
          </div>`;
      }
      if (res.added.length > 100) {
        html += `<div class="diff-more">… 还有 ${res.added.length - 100} 张</div>`;
      }
      html += `</div></div>`;
    }

    // 改动
    if (res.changed.length > 0) {
      html += `<div class="diff-section">
        <div class="diff-section-title diff-title-changed">🟡 改动（${res.changed.length}）</div>
        <div class="diff-card-list">`;
      for (const c of res.changed.slice(0, 100)) {
        const fieldsText = c.fields.map(f => `变更字段：${escapeHtml(f)}`).join(' · ');
        const keepBadge = c.user_card_kept
          ? `<span class="diff-card-badge">用户名片已保留</span>`
          : '';
        html += `
          <div class="diff-card">
            <div class="diff-card-name">${escapeHtml(c.filename)}</div>
            <div class="diff-card-fields">${fieldsText} ${keepBadge}</div>
          </div>`;
      }
      if (res.changed.length > 100) {
        html += `<div class="diff-more">… 还有 ${res.changed.length - 100} 张</div>`;
      }
      html += `</div></div>`;
    }

    // 删除
    if (res.removed.length > 0) {
      html += `<div class="diff-section">
        <div class="diff-section-title diff-title-removed">🔴 删除（${res.removed.length}）</div>
        <div class="diff-card-list">`;
      for (const name of res.removed.slice(0, 100)) {
        html += `
          <div class="diff-card">
            <div class="diff-card-name">${escapeHtml(name)}</div>
            <div class="diff-card-fields">游戏里已删除，本卡片会保留</div>
          </div>`;
      }
      if (res.removed.length > 100) {
        html += `<div class="diff-more">… 还有 ${res.removed.length - 100} 张</div>`;
      }
      html += `</div></div>`;
    }

    // 无差异
    if (res.added.length === 0 && res.changed.length === 0 && res.removed.length === 0) {
      html = '<div class="diff-empty">✓ 两次导出的数据完全一致，没有差异</div>';
    }

    box.innerHTML = html;

    // 有差异时才显示"应用改动"
    const actions = document.getElementById('diff-actions');
    if (actions) {
      const hasDiff = res.added.length > 0 || res.changed.length > 0;
      actions.style.display = hasDiff ? 'block' : 'none';
    }
  } catch (e) {
    box.innerHTML = `<div class="diff-empty" style="color:var(--cl-error);">✗ ${escapeHtml(String(e))}</div>`;
  }
}

async function applyDiff() {
  const sel = document.getElementById('diff-backup-select');
  if (!sel || !sel.value) return;
  if (!(await uiConfirm('确定应用改动？当前卡片会先备份，然后合并。'))) return;

  const box = document.getElementById('diff-result');
  box.innerHTML = '<div class="diff-empty">正在合并...</div>';

  try {
    const res = await pywebview.api.merge_cards(sel.value);
    if (res.ok) {
      box.innerHTML = `<div class="diff-empty" style="color:var(--cl-success);">✓ 合并完成：新增 ${res.added}，改动 ${res.changed}，删除保留 ${res.removed}，没变 ${res.unchanged}</div>`;
      if (typeof allCards !== 'undefined' && pywebview.api) {
        allCards = await pywebview.api.get_cards_list();
        if (typeof cardsRef !== 'undefined') cardsRef = allCards;
        if (typeof renderCardsPage === 'function') renderCardsPage();
        document.getElementById('header-status').textContent = `共 ${allCards.length} 张卡片`;
      }
    } else {
      box.innerHTML = `<div class="diff-empty" style="color:var(--cl-error);">✗ ${escapeHtml(res.msg || '失败')}</div>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="diff-empty" style="color:var(--cl-error);">✗ ${escapeHtml(String(e))}</div>`;
  }
}


// 首次加载时渲染
document.addEventListener('DOMContentLoaded', () => {
  // 等 pywebview 和 DOM 都就绪
  setTimeout(() => {
    if (document.getElementById('tools-tab-content')) {
      renderToolTab();
    }
  }, 500);
});



// ==================== Tab：导出到酒馆（SillyTavern） ====================
//
// 真正干活的在 tools/tavern_card.py（角色卡 PNG+JSON）与
// tools/tavern_export.py（预设 / 世界书）；编辑器只管「选范围 → 调接口 → 显示结果」。
// 范围默认留空=全部，但会提示先收窄：1592 张全导既慢又没人用得上。
let tavernOutDir = '';

function renderTavernTab() {
  return `
    <div class="upstream-box" style="margin-top:0;">
      <h3 style="margin:0 0 10px;font-size:14px;color:var(--cl-ink);">导出到酒馆（SillyTavern）</h3>
      <div class="field-hint" style="margin-bottom:12px;">
        导出的角色卡是 PNG，卡数据嵌在图片里，酒馆里直接「导入角色」；预设 / 世界书分别用
        各自的导入入口。注入语含敏感内容，默认只导出结构、不写正文。
      </div>

      <div class="tv-row">
        <span class="tv-label">NPC ID</span>
        <input id="tv-lo" class="tv-input" placeholder="起始" inputmode="numeric">
        <span class="tv-sep">~</span>
        <input id="tv-hi" class="tv-input" placeholder="结束" inputmode="numeric">
        <span class="tv-label" style="margin-left:14px;">角色名含</span>
        <input id="tv-kw" class="tv-input" style="flex:1;min-width:90px;" placeholder="留空 = 全部">
        <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="tavernScan()">预览范围</button>
      </div>
      <div id="tv-scan" class="field-hint" style="margin-top:6px;">
        还没预览。建议先收窄范围（例如 100000 ~ 100050），别一上来导全部。
      </div>

      <div class="tv-row" style="margin-top:12px;">
        <label class="tv-check"><input type="checkbox" id="tv-cards" checked> 角色卡</label>
        <label class="tv-check"><input type="checkbox" id="tv-preset" checked> 预设</label>
        <label class="tv-check"><input type="checkbox" id="tv-wb"> 世界书（注入语常驻条目）</label>
      </div>

      <div class="tv-row" style="margin-top:10px;">
        <span class="tv-label">输出目录</span>
        <span id="tv-dir" class="field-hint" style="flex:1;word-break:break-all;">未选择</span>
        <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="tavernPickDir()">选择…</button>
      </div>

      <div style="margin-top:14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <button class="primary" style="padding:6px 16px;font-size:13px;" onclick="tavernDoExport()">开始导出</button>
        <span id="tv-msg" class="field-hint"></span>
      </div>
    </div>`;
}

function _tvVal(id) {
  const el = document.getElementById(id);
  return el ? el.value.trim() : '';
}

async function tavernScan() {
  const box = document.getElementById('tv-scan');
  if (!box) return;
  box.textContent = '统计中…';
  try {
    const res = await pywebview.api.tavern_scan(_tvVal('tv-lo'), _tvVal('tv-hi'), _tvVal('tv-kw'));
    if (!res || !res.ok) { box.textContent = (res && res.msg) || '统计失败'; return; }
    if (!res.total) { box.textContent = '这个范围里没有卡片'; return; }
    let t = `命中 ${res.total} 张：` + res.names.join('、') + (res.total > res.names.length ? ' …' : '');
    if (res.blank_bg) t += `　｜　其中 ${res.blank_bg} 张背景为空（已在检查的 ${res.checked} 张内）`;
    if (res.total > 200) t += '　｜　数量较多，导出会比较慢';
    box.textContent = t;
  } catch (e) {
    box.textContent = '统计失败：' + e;
  }
}

async function tavernPickDir() {
  try {
    const res = await pywebview.api.tavern_pick_dir();
    if (res && res.ok) {
      tavernOutDir = res.path;
      const el = document.getElementById('tv-dir');
      if (el) el.textContent = tavernOutDir;
    }
  } catch (e) { /* 取消选择不是错误 */ }
}

async function tavernDoExport() {
  const msg = document.getElementById('tv-msg');
  const btn = document.querySelector('#tools-tab-content .primary');
  if (!tavernOutDir) { if (msg) msg.textContent = '先选输出目录'; return; }
  const withCards = document.getElementById('tv-cards').checked;
  const withPreset = document.getElementById('tv-preset').checked;
  const withWb = document.getElementById('tv-wb').checked;
  if (!withCards && !withPreset && !withWb) { if (msg) msg.textContent = '至少勾一项'; return; }
  if (btn) btn.disabled = true;
  if (msg) msg.textContent = '导出中，卡片多时会慢…';
  try {
    const res = await pywebview.api.tavern_export(
      tavernOutDir, _tvVal('tv-lo'), _tvVal('tv-hi'), _tvVal('tv-kw'),
      withCards, withPreset, withWb);
    if (msg) {
      msg.textContent = res && res.ok
        ? `完成：${res.msg}` + (res.preset ? '，预设已生成' : '') + (res.worldbook ? '，世界书已生成' : '')
        : ((res && res.msg) || '导出失败');
    }
  } catch (e) {
    if (msg) msg.textContent = '导出失败：' + e;
  } finally {
    if (btn) btn.disabled = false;
  }
}
