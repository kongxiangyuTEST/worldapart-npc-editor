// ===== 编辑页 =====

let currentFilename = null;
let currentCard = null;
let originalCard = null;       // 从 originals\ 读的
let lastSavedCard = null;      // 从 last_saved\ 读的
let originalCardJson = null;   // 用于检测"是否修改过"
let editListCards = [];
let editRef = null;

function initEditPage(cards) {
  editRef = cards;
  document.getElementById('edit-search').addEventListener('input', renderEditList);

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      if (document.getElementById('edit-page').style.display === 'flex') {
        e.preventDefault();
        onSaveCard();
      }
    }
  });

  window.addEventListener('beforeunload', (e) => {
    if (currentFilename && hasUnsavedChanges()) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}

function hasUnsavedChanges() {
  if (!currentCard || !originalCardJson) return false;
  return JSON.stringify(currentCard) !== originalCardJson;
}

function snapshotCurrentCard() {
  originalCardJson = JSON.stringify(currentCard);
}

async function showCardEdit(filename) {
  if (currentFilename && hasUnsavedChanges()) {
    const ok = confirm(`卡片 ${currentFilename} 有未保存的修改，是否放弃？`);
    if (!ok) return;
  }

  currentFilename = filename;
  const card = await pywebview.api.get_card(filename);
  if (!card) {
    alert('读取失败：' + filename);
    return;
  }
  currentCard = card;
  snapshotCurrentCard();

  // 读原版备份 + 上次保存备份
  try {
    const origRes = await pywebview.api.get_original_card(filename);
    originalCard = origRes.ok ? origRes.data : null;
  } catch (e) {
    originalCard = null;
  }
  try {
    const lsRes = await pywebview.api.get_last_saved_card(filename);
    lastSavedCard = lsRes.ok ? lsRes.data : null;
  } catch (e) {
    lastSavedCard = null;
  }

  document.getElementById('cards-page').style.display = 'none';
  document.getElementById('edit-page').style.display = 'flex';
  showFooterButtons(true, true);

  editListCards = (editRef || []).slice();
  renderEditList();
  renderEditMain();
}

function renderEditList() {
  const kw = document.getElementById('edit-search').value.trim();
  let list = editListCards;
  if (kw) {
    list = list.filter(c => c.name.includes(kw) || String(c.id).includes(kw));
  }

  document.getElementById('edit-list-count').textContent =
    `${list.length} / ${editListCards.length} 卡片`;

  const container = document.getElementById('edit-list');
  container.innerHTML = '';

  for (const card of list) {
    const initial = (card.name || '?')[0];

    // 头像：有则显示图片，无则显示字
    let miniPortraitHTML;
    if (card.portrait_file) {
      miniPortraitHTML = `
        <img src="/portraits/${card.portrait_file}" alt="${escapeHtml(card.name)}"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
        <span style="display:none;">${escapeHtml(initial)}</span>
      `;
    } else {
      miniPortraitHTML = `<span>${escapeHtml(initial)}</span>`;
    }

    const item = document.createElement('div');
    item.className = 'edit-list-item' +
      (card.filename === currentFilename ? ' active' : '');
    item.onclick = () => showCardEdit(card.filename);
    item.innerHTML = `
      <div class="mini-portrait">${miniPortraitHTML}</div>
      <div class="mini-info">
        <div class="mini-name">
          <span>${escapeHtml(card.name)}</span>
        </div>
        <div class="mini-title">ID ${card.id}</div>
      </div>
    `;
    container.appendChild(item);
  }
}

function fieldActionsHTML(fieldKey) {
  return `
    <span class="field-actions">
      <span class="link" onclick="revertField('${fieldKey}')">撤销此项修改</span>
      <span class="link" onclick="restoreFieldOriginal('${fieldKey}')">恢复此字段原版</span>
    </span>
  `;
}

function renderEditMain() {
  const card = currentCard;
  const main = document.getElementById('edit-main');

  main.innerHTML = `
    <h1>${escapeHtml(card.name)}</h1>
    <div class="npc-sub">NPC ID ${card.id} / 文件 ${card.filename}</div>
    <div class="npc-note">修改后点底部"保存卡片"写回 .md 文件（或按 Ctrl+S）。游戏下一次对话即可读取。</div>

    <div class="section-title">基础信息</div>
    <div class="field">
      <label>卡片名字（同时用于文件名）</label>
      <input id="f-name" value="${escapeHtml(card.name)}">
    </div>

    <div class="section-title">预设资料</div>
    <div class="preset-grid">
      ${presetFieldHTML('姓名', card.preset['姓名'] || '')}
      ${presetFieldHTML('性别', card.preset['性别'] || '')}
      ${presetFieldHTML('年龄', card.preset['年龄'] || '')}
      ${presetFieldHTML('境界', card.preset['境界'] || '')}
      ${presetFieldHTML('个性', card.preset['个性'] || '')}
      ${presetFieldHTML('兴趣', card.preset['兴趣'] || '')}
    </div>

    <div class="section-title">用户名片（优先读取）</div>
    <div class="field tall">
      <label>
        <span>在此填写说话习惯、身世、目标、与玩家的约定等</span>
        <span class="field-actions">
          <span class="link" onclick="revertField('user_card')">撤销此项修改</span>
          <span class="link" onclick="restoreFieldOriginal('user_card')">恢复此字段原版</span>
          <span class="link" onclick="showGamePrompt('${escapeHtml(card.name)}')">查看游戏 prompt</span>
        </span>
      </label>
      <textarea id="f-user_card">${escapeHtml(card.user_card)}</textarea>
    </div>

    <div class="section-title">原游戏背景</div>
    <div class="field tall">
      <label>
        <span>游戏自动导出，可修改</span>
        ${fieldActionsHTML('background')}
      </label>
      <textarea id="f-background">${escapeHtml(card.background)}</textarea>
    </div>
  `;

  const markDirty = () => {
    setFootLeft(hasUnsavedChanges() ? '⚠ 有未保存的修改' : '就绪');
  };

  document.getElementById('f-name').oninput = e => {
    currentCard.name = e.target.value;
    markDirty();
  };
  for (const key of ['姓名', '性别', '年龄', '个性', '兴趣']) {
    // 跳过"境界"——它被锁住了，不需要绑定
    const el = document.getElementById('f-preset-' + key);
    if (!el) continue;
    el.oninput = e => {
      currentCard.preset[key] = e.target.value;
      markDirty();
    };
  }
  document.getElementById('f-user_card').oninput = e => {
    currentCard.user_card = e.target.value;
    markDirty();
  };
  document.getElementById('f-background').oninput = e => {
    currentCard.background = e.target.value;
    markDirty();
  };

  setFootLeft('就绪');
}

function presetFieldHTML(key, value) {
  // 境界：暂时锁住（游戏运行时数据，配置表拿不到）
  const locked = key === '境界';
  return `
    <div class="field">
      <label>
        <span>${key}${locked ? ' <span style="color:var(--cl-muted-soft);font-weight:normal;font-size:11px;">（暂未支持）</span>' : ''}</span>
        ${locked ? '' : fieldActionsHTML('preset.' + key)}
      </label>
      <input id="f-preset-${key}" value="${escapeHtml(value)}"
             ${locked ? 'readonly style="background:var(--cl-surface-soft);color:var(--cl-muted-soft);cursor:not-allowed;" placeholder="（游戏运行时数据，暂无法读取）"' : ''}>
    </div>
  `;
}

// ==================== 撤销/恢复 ====================

function getFieldValue(cardObj, fieldKey) {
  if (!cardObj) return "";
  if (fieldKey === "user_card") return cardObj.user_card || "";
  if (fieldKey === "background") return cardObj.background || "";
  if (fieldKey.startsWith("preset.")) {
    const k = fieldKey.slice(7);
    return (cardObj.preset && cardObj.preset[k]) || "";
  }
  if (fieldKey === "name") return cardObj.name || "";
  return "";
}

function setFieldValue(fieldKey, value) {
  if (fieldKey === "user_card") {
    currentCard.user_card = value;
    const el = document.getElementById("f-user_card");
    if (el) el.value = value;
  } else if (fieldKey === "background") {
    currentCard.background = value;
    const el = document.getElementById("f-background");
    if (el) el.value = value;
  } else if (fieldKey.startsWith("preset.")) {
    const k = fieldKey.slice(7);
    currentCard.preset[k] = value;
    const el = document.getElementById("f-preset-" + k);
    if (el) el.value = value;
  } else if (fieldKey === "name") {
    currentCard.name = value;
    const el = document.getElementById("f-name");
    if (el) el.value = value;
  }
}

// 撤销此项修改：恢复到"上次保存"状态
function revertField(fieldKey) {
  if (!lastSavedCard) {
    alert("没有上次保存的备份，无法撤销");
    return;
  }
  const v = getFieldValue(lastSavedCard, fieldKey);
  setFieldValue(fieldKey, v);
  setFootLeft(hasUnsavedChanges() ? '⚠ 有未保存的修改' : '就绪');
}

// 恢复此字段原版：从 originals\ 恢复
function restoreFieldOriginal(fieldKey) {
  if (!originalCard) {
    alert("没有原版备份，无法恢复");
    return;
  }
  if (!confirm("确定要恢复此字段到原版吗？")) return;
  const v = getFieldValue(originalCard, fieldKey);
  setFieldValue(fieldKey, v);
  setFootLeft(hasUnsavedChanges() ? '⚠ 有未保存的修改' : '就绪');
}

async function onSaveCard() {
  if (!currentCard) return;
  const res = await pywebview.api.save_card(currentFilename, currentCard);
  if (!res.ok) {
    alert('保存失败');
    return;
  }
  currentFilename = res.filename;
  snapshotCurrentCard();

  try {
    const lsRes = await pywebview.api.get_last_saved_card(currentFilename);
    lastSavedCard = lsRes.ok ? lsRes.data : null;
  } catch (e) {
    lastSavedCard = null;
  }

  allCards = await pywebview.api.get_cards_list();
  cardsRef = allCards;
  editRef = allCards;
  document.getElementById('header-status').textContent = `共 ${allCards.length} 张卡片`;

  setFootLeft('✓ 已保存');
  renderEditList();
  alert('已保存：' + currentFilename);
}

async function onDeleteCard() {
  if (!currentFilename) return;
  if (!confirm(`确定要删除卡片 ${currentFilename}？`)) return;
  const res = await pywebview.api.delete_card(currentFilename);
  if (!res.ok) {
    alert('删除失败');
    return;
  }
  currentFilename = null;
  currentCard = null;
  originalCardJson = null;
  lastSavedCard = null;

  allCards = await pywebview.api.get_cards_list();
  cardsRef = allCards;
  editRef = allCards;
  document.getElementById('header-status').textContent = `共 ${allCards.length} 张卡片`;
  backToCards();
}

// ==================== 查看游戏 prompt ====================

async function showGamePrompt(npcName) {
  // 显示遮罩
  let overlay = document.getElementById('prompt-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'prompt-overlay';
    overlay.className = 'prompt-overlay';
    overlay.innerHTML = `
      <div class="prompt-modal" onclick="event.stopPropagation()">
        <div class="prompt-modal-header">
          <span id="prompt-modal-title">加载中...</span>
          <span class="prompt-modal-close" onclick="closePromptModal()">×</span>
        </div>
        <div class="prompt-modal-body" id="prompt-modal-body">
          <div style="padding:30px;text-align:center;color:var(--cl-muted-soft);">正在读取样本...</div>
        </div>
      </div>
    `;
    overlay.onclick = closePromptModal;
    document.body.appendChild(overlay);
  } else {
    overlay.style.display = 'flex';
    document.getElementById('prompt-modal-title').textContent = '加载中...';
    document.getElementById('prompt-modal-body').innerHTML = '<div style="padding:30px;text-align:center;color:var(--cl-muted-soft);">正在读取样本...</div>';
  }

  // 调 API
  const res = await pywebview.api.get_latest_sample(npcName);
  if (!res.ok) {
    document.getElementById('prompt-modal-title').textContent = npcName + ' 的游戏 prompt';
    document.getElementById('prompt-modal-body').innerHTML =
      `<div style="padding:30px;text-align:center;color:var(--cl-error);">${escapeHtml(res.msg || '读取失败')}</div>`;
    return;
  }

  const sample = res.data;
  document.getElementById('prompt-modal-title').textContent =
    npcName + ' 的游戏 prompt · ' + (sample.filename || '') + ' · ' + (sample.mtime || '');

  const messages = (sample.content && sample.content.messages) || [];

  // 渲染每条消息
  let html = '';
  messages.forEach((m, i) => {
    const role = m.role || '?';
    const content = m.content || '';
    const preview = (typeof content === 'string') ? content : JSON.stringify(content, null, 2);

    html += `
      <div class="prompt-msg">
        <div class="prompt-msg-header">
          <span class="prompt-msg-role prompt-role-${role}">${role}</span>
          <span class="prompt-msg-len">${preview.length} 字符</span>
        </div>
        <pre class="prompt-msg-content">${escapeHtml(preview)}</pre>
      </div>
    `;
  });

  if (messages.length === 0) {
    html = '<div style="padding:30px;text-align:center;color:var(--cl-muted-soft);">样本里没有 messages</div>';
  }

  document.getElementById('prompt-modal-body').innerHTML = html;
}

function closePromptModal() {
  const overlay = document.getElementById('prompt-overlay');
  if (overlay) overlay.style.display = 'none';
}