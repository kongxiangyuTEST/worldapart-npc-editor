// ===== 工具页 =====

let toolsCurrentTab = 'portrait';

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

  if (toolsCurrentTab === 'portrait') {
    box.innerHTML = renderPortraitTab();
    bindPortraitTab();
  } else if (toolsCurrentTab === 'cards') {
    box.innerHTML = renderCardsTab();
    bindCardsTab();
    loadEditorCardsCount();
  } else if (toolsCurrentTab === 'diff') {
    box.innerHTML = renderDiffTab();
    loadBackupsList();
  } else if (toolsCurrentTab === 'batch') {

    box.innerHTML = `<div class="placeholder"><div class="big">📦</div><div>批量工具</div><div class="hint">之后再做</div></div>`;
  } else if (toolsCurrentTab === 'io') {
    box.innerHTML = `<div class="placeholder"><div class="big">📥</div><div>导入 / 导出</div><div class="hint">之后再做</div></div>`;
  }
}

// ==================== Tab 1：头像提取 ====================
function renderPortraitTab() {
  const s = loadToolsSettings();
  const cli = s.assetstudio_cli || '';
  const gameDir = s.game_dir || '';
  const keepTmp = s.keep_tmp || false;

  return `
    <div class="ai-tab-note" style="margin-bottom:14px;">
      <p>从游戏资源里提取全部 NPC 头像。需要你提前下载 <strong>AssetStudioModCLI</strong>，并把它的 <code>.exe</code> 路径填在下面。</p>
      <p>下载地址：<code>https://github.com/aelurum/AssetStudio/releases</code>（找文件名带 <code>CLI</code> 的压缩包）</p>
      <p style="color:#886;background:#fffbea;border:1px solid #f0e0a0;padding:8px 12px;border-radius:4px;margin-top:8px;">
        ⏱ <strong>提取时间提示：</strong>提取过程会扫描游戏全部资源，耗时取决于你的电脑性能，一般需要 <strong>几分钟</strong>。请耐心等待，不要关闭编辑器。进度可以切到左侧"日志"页查看。
      </p>
    </div>

    <div class="field">
      <label>AssetStudioModCLI.exe 路径</label>
      <div style="display:flex;gap:8px;">
        <input id="tools-cli-path" value="${escapeHtml(cli)}" placeholder="例如 E:\\Tools\\AssetStudioModCLI.exe" style="flex:1;padding:6px 10px;border:1px solid #ccc;border-radius:3px;font-family:inherit;font-size:13px;">
        <button class="secondary" onclick="pickCli()">选择文件</button>
      </div>
    </div>

    <div class="field">
      <label>游戏根目录（WorldApart.exe 所在目录）</label>
      <div style="display:flex;gap:8px;">
        <input id="tools-game-dir" value="${escapeHtml(gameDir)}" placeholder="例如 H:\\SteamLibrary\\steamapps\\common\\A1" style="flex:1;padding:6px 10px;border:1px solid #ccc;border-radius:3px;font-family:inherit;font-size:13px;">
        <button class="secondary" onclick="pickGameDir()">选择文件夹</button>
      </div>
    </div>

    <div class="inject-footer" style="margin-top:20px;">
      <div style="display:flex;align-items:center;gap:20px;">
        <label style="display:flex;align-items:center;gap:6px;margin:0;font-weight:normal;font-size:12px;color:#555;cursor:pointer;">
          <input type="checkbox" id="tools-keep-tmp" ${keepTmp ? 'checked' : ''}>
          保留中间文件（调试用）
        </label>
        <button class="secondary" id="tools-abort-btn" onclick="abortExtract()" disabled>中止提取</button>
      </div>
      <div class="inject-footer-buttons">
        <span class="inject-status" id="tools-status" style="margin-right:14px;"></span>
        <button class="primary" id="tools-run-btn" onclick="runExtractPortraits()">开始提取</button>
      </div>
    </div>
  `;
}

function bindPortraitTab() {
  const cliInput = document.getElementById('tools-cli-path');
  const gameInput = document.getElementById('tools-game-dir');
  const keepInput = document.getElementById('tools-keep-tmp');

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

async function pickCli() {
  const res = await pywebview.api.pick_assetstudio_cli();
  if (res.ok) {
    const input = document.getElementById('tools-cli-path');
    input.value = res.path;
    input.dispatchEvent(new Event('input'));
  } else if (res.msg && res.msg !== '未选择') {
    alert(res.msg);
  }
}

async function pickGameDir() {
  const res = await pywebview.api.pick_game_dir();
  if (res.ok) {
    const input = document.getElementById('tools-game-dir');
    input.value = res.path;
    input.dispatchEvent(new Event('input'));
  } else if (res.msg && res.msg !== '未选择') {
    alert(res.msg);
  }
}

async function runExtractPortraits() {
  const cli = document.getElementById('tools-cli-path').value.trim();
  const gameDir = document.getElementById('tools-game-dir').value.trim();
  const keepTmp = document.getElementById('tools-keep-tmp').checked;
  const status = document.getElementById('tools-status');
  const runBtn = document.getElementById('tools-run-btn');
  const abortBtn = document.getElementById('tools-abort-btn');

  if (!cli || !gameDir) {
    alert('请先填写 AssetStudioModCLI 路径和游戏目录');
    return;
  }

  runBtn.disabled = true;
  abortBtn.disabled = false;
  status.textContent = '正在提取...（看"日志"页有进度）';
  status.className = 'inject-status';

  try {
    const res = await pywebview.api.extract_portraits(cli, gameDir, keepTmp);
    if (res.ok) {
      status.textContent = `✓ 完成，共 ${res.done} 张`;
      status.className = 'inject-status success';
      if (typeof allCards !== 'undefined' && pywebview.api) {
        allCards = await pywebview.api.get_cards_list();
        if (typeof cardsRef !== 'undefined') cardsRef = allCards;
        if (typeof renderCardsPage === 'function') renderCardsPage();
      }
    } else if (res.aborted) {
      status.textContent = '⊘ 已中止';
      status.className = 'inject-status';
    } else {
      status.textContent = '✗ ' + (res.msg || '失败');
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
  abortBtn.disabled = true;
  status.textContent = '正在中止...';
  status.className = 'inject-status';
  await pywebview.api.abort_extract();
}

// ==================== Tab 2：导出角色卡 ====================
function renderCardsTab() {
  const s = loadToolsSettings();
  const cli = s.assetstudio_cli || '';
  const gameDir = s.game_dir || '';
  const keepTmp = s.keep_tmp || false;

  return `
    <div class="ai-tab-note" style="margin-bottom:14px;">
      <p>从游戏本体直接解析 NPC 角色卡，<strong>不依赖 Mod</strong>。生成的卡片会放到项目内的 <code>editor_cards\\</code> 目录，跟 Mod 生成的卡片分开存放，方便以后对比和合并。</p>
      <p style="color:#886;background:#fffbea;border:1px solid #f0e0a0;padding:8px 12px;border-radius:4px;margin-top:8px;">
        ⏱ <strong>提取时间提示：</strong>导出需要扫描游戏全部 TextAsset 资源，一般需要 <strong>几分钟</strong>。进度可以切到左侧"日志"页查看。
      </p>
    </div>

    <div class="field">
      <label>AssetStudioModCLI.exe 路径</label>
      <div style="display:flex;gap:8px;">
        <input id="cards-cli-path" value="${escapeHtml(cli)}" placeholder="例如 E:\\Tools\\AssetStudioModCLI.exe" style="flex:1;padding:6px 10px;border:1px solid #ccc;border-radius:3px;font-family:inherit;font-size:13px;">
        <button class="secondary" onclick="pickCardsCli()">选择文件</button>
      </div>
    </div>

    <div class="field">
      <label>游戏根目录（WorldApart.exe 所在目录）</label>
      <div style="display:flex;gap:8px;">
        <input id="cards-game-dir" value="${escapeHtml(gameDir)}" placeholder="例如 H:\\SteamLibrary\\steamapps\\common\\A1" style="flex:1;padding:6px 10px;border:1px solid #ccc;border-radius:3px;font-family:inherit;font-size:13px;">
        <button class="secondary" onclick="pickCardsGameDir()">选择文件夹</button>
      </div>
    </div>

    <div class="editor-cards-info">
      <div class="editor-cards-info-left">
        <span class="editor-cards-label">当前 editor_cards\</span>
        <span class="editor-cards-count"><strong id="cards-count-display">…</strong> 张卡片</span>
      </div>
      <button class="secondary" style="padding:4px 12px;font-size:12px;" onclick="openEditorCardsDir()">打开目录</button>
    </div>

    <div class="inject-footer" style="margin-top:20px;">
      <div style="display:flex;align-items:center;gap:20px;">
        <label style="display:flex;align-items:center;gap:6px;margin:0;font-weight:normal;font-size:12px;color:#555;cursor:pointer;">
          <input type="checkbox" id="cards-keep-tmp" ${keepTmp ? 'checked' : ''}>
          保留中间文件（调试用）
        </label>
        <button class="secondary" id="cards-abort-btn" onclick="abortCardsExtract()" disabled>中止导出</button>
      </div>
      <div class="inject-footer-buttons">
        <span class="inject-status" id="cards-status" style="margin-right:14px;"></span>
        <button class="primary" id="cards-run-btn" onclick="runExtractCards()">开始导出</button>
      </div>
    </div>
  `;
}

function bindCardsTab() {
  const cliInput = document.getElementById('cards-cli-path');
  const gameInput = document.getElementById('cards-game-dir');
  const keepInput = document.getElementById('cards-keep-tmp');

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

async function pickCardsCli() {
  const res = await pywebview.api.pick_assetstudio_cli();
  if (res.ok) {
    const input = document.getElementById('cards-cli-path');
    input.value = res.path;
    input.dispatchEvent(new Event('input'));
  } else if (res.msg && res.msg !== '未选择') {
    alert(res.msg);
  }
}

async function pickCardsGameDir() {
  const res = await pywebview.api.pick_game_dir();
  if (res.ok) {
    const input = document.getElementById('cards-game-dir');
    input.value = res.path;
    input.dispatchEvent(new Event('input'));
  } else if (res.msg && res.msg !== '未选择') {
    alert(res.msg);
  }
}

async function loadEditorCardsCount() {
  try {
    const res = await pywebview.api.get_editor_cards_count();
    const el = document.getElementById('cards-count-display');
    if (el && res.ok) el.textContent = res.count;
  } catch (e) {}
}

async function openEditorCardsDir() {
  await pywebview.api.open_editor_cards_dir();
}

async function runExtractCards() {
  const cli = document.getElementById('cards-cli-path').value.trim();
  const gameDir = document.getElementById('cards-game-dir').value.trim();
  const keepTmp = document.getElementById('cards-keep-tmp').checked;
  const status = document.getElementById('cards-status');
  const runBtn = document.getElementById('cards-run-btn');
  const abortBtn = document.getElementById('cards-abort-btn');

  if (!cli || !gameDir) {
    alert('请先填写 AssetStudioModCLI 路径和游戏目录');
    return;
  }

  runBtn.disabled = true;
  abortBtn.disabled = false;
  status.textContent = '正在导出...（看"日志"页有进度）';
  status.className = 'inject-status';

  try {
    const res = await pywebview.api.extract_npc_cards(cli, gameDir, keepTmp);
    if (res.ok) {
      status.textContent = `✓ 完成，共 ${res.done} 张`;
      status.className = 'inject-status success';
      loadEditorCardsCount();
    } else if (res.aborted) {
      status.textContent = '⊘ 已中止';
      status.className = 'inject-status';
    } else {
      status.textContent = '✗ ' + (res.msg || '失败');
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

async function abortCardsExtract() {
  const status = document.getElementById('cards-status');
  const abortBtn = document.getElementById('cards-abort-btn');
  abortBtn.disabled = true;
  status.textContent = '正在中止...';
  status.className = 'inject-status';
  await pywebview.api.abort_extract();
}

// ==================== Tab 3：卡片对比 ====================

function renderDiffTab() {
  return `
    <div class="diff-tab-wrapper">
      <div class="ai-tab-note" style="margin-bottom:10px;">
        <p style="margin:4px 0;">对比"上次备份"和"当前卡片"，看游戏更新后哪些 NPC 新增 / 改动 / 删除。</p>
        <p style="color:#666;font-size:12px;margin:4px 0;">每次点"导出角色卡"时，编辑器会<strong>自动备份</strong>上一次的卡片。这里选一份备份来对比。</p>
      </div>

      <div class="field" style="margin-bottom:10px;">
        <label style="display:block;margin-bottom:6px;color:#555;font-size:12px;font-weight:bold;">选择要对比的备份</label>
        <div style="display:flex;gap:10px;align-items:center;">
          <select id="diff-backup-select" style="flex:1;padding:6px 10px;border:1px solid #ccc;border-radius:3px;font-size:13px;">
            <option value="">加载中...</option>
          </select>
          <button class="primary" onclick="runDiff()" style="white-space:nowrap;">开始对比</button>
        </div>
      </div>

      <div id="diff-result" class="diff-result-box"></div>

      <div id="diff-actions" style="display:none;margin-top:12px;padding:12px 16px;background:#fffbea;border:1px solid #f0e0a0;border-radius:4px;flex-shrink:0;">
        <div style="font-size:12px;color:#886;margin-bottom:8px;">
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
      box.innerHTML = `<div class="diff-empty" style="color:#c33;">✗ ${escapeHtml(res.msg || '失败')}</div>`;
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
    box.innerHTML = `<div class="diff-empty" style="color:#c33;">✗ ${escapeHtml(String(e))}</div>`;
  }
}

async function applyDiff() {
  const sel = document.getElementById('diff-backup-select');
  if (!sel || !sel.value) return;
  if (!confirm('确定应用改动？当前卡片会先备份，然后合并。')) return;

  const box = document.getElementById('diff-result');
  box.innerHTML = '<div class="diff-empty">正在合并...</div>';

  try {
    const res = await pywebview.api.merge_cards(sel.value);
    if (res.ok) {
      box.innerHTML = `<div class="diff-empty" style="color:#388e3c;">✓ 合并完成：新增 ${res.added}，改动 ${res.changed}，删除保留 ${res.removed}，没变 ${res.unchanged}</div>`;
      if (typeof allCards !== 'undefined' && pywebview.api) {
        allCards = await pywebview.api.get_cards_list();
        if (typeof cardsRef !== 'undefined') cardsRef = allCards;
        if (typeof renderCardsPage === 'function') renderCardsPage();
        document.getElementById('header-status').textContent = `共 ${allCards.length} 张卡片`;
      }
    } else {
      box.innerHTML = `<div class="diff-empty" style="color:#c33;">✗ ${escapeHtml(res.msg || '失败')}</div>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="diff-empty" style="color:#c33;">✗ ${escapeHtml(String(e))}</div>`;
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

