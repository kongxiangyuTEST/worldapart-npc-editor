// ===== 试聊页 =====
// 拿一个 NPC 的真实卡片 + 长期记忆 + 可选的游戏段落，直接跟本地模型对话。
// 用处：不启动游戏也能验证「改完名片 / 记忆之后，这个 NPC 说话像不像样」，
// 也能勾上「赠物通则」这类段落，对比游戏里实际会怎么说话。
//
// 为什么不走 core_proxy：那边要完整游戏 prompt 才注入；试聊手里只有卡片，
// 直连更干净，也不会污染游戏的记忆文件。

let trialCtx = null;          // chat_trial_context 返回
let trialMessages = [];       // 对话历史（不含 system）
let trialBusy = false;
let trialBase = '';
let trialModels = [];
let trialAllCards = [];       // 全部卡片（搜索用）
let trialSelected = '';       // 当前选中的 filename
let trialFragments = [];      // 游戏段落
let trialFragOpen = false;

async function initTrialPage() {
  const search = document.getElementById('trial-npc-search');
  if (search) search.oninput = trialRenderNpcList;

  await trialLoadModels();
  await trialLoadFragments();

  const temp = document.getElementById('trial-temp');
  if (temp) {
    temp.oninput = () => {
      const v = document.getElementById('trial-temp-val');
      if (v) v.textContent = temp.value;
    };
  }

  ['trial-use-card', 'trial-use-memory'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.onchange = () => { trialUpdateStat(); trialReset(); };
  });

  const ta = document.getElementById('trial-text');
  if (ta) {
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); trialSend(); }
    });
  }

  trialAllCards = (typeof allCards !== 'undefined' && allCards) ? allCards : [];
  trialRenderNpcList();
  if (trialAllCards.length) await trialSelectNpc(trialAllCards[0].filename);
}

// ---------- NPC 列表（搜索 + 头像）----------

function trialRenderNpcList() {
  const box = document.getElementById('trial-npc-list');
  const cnt = document.getElementById('trial-npc-count');
  if (!box) return;

  const searchEl = document.getElementById('trial-npc-search');
  const kw = (searchEl && searchEl.value ? searchEl.value : '').trim().toLowerCase();

  const list = trialAllCards.filter(c => {
    if (!kw) return true;
    const n = (c.name || '').toLowerCase();
    return n.indexOf(kw) >= 0 || String(c.id).indexOf(kw) >= 0;
  });

  if (cnt) cnt.textContent = list.length + '/' + trialAllCards.length;

  if (!list.length) {
    box.innerHTML = '<div class="trial-npc-empty">没有匹配的角色</div>';
    return;
  }

  box.innerHTML = list.map(c => {
    const active = (c.filename === trialSelected) ? ' active' : '';
    const initial = (c.name || '?').slice(0, 1);
    const av = c.portrait_file
      ? '<img class="trial-npc-av" alt="" src="portraits/' +
        encodeURIComponent(c.portrait_file) + '">'
      : '<span class="trial-npc-av trial-npc-av-ph">' + escapeHtml(initial) + '</span>';
    return '<div class="trial-npc-item' + active + '" data-fn="' + escapeHtml(c.filename) +
           '" onclick="trialSelectNpc(this.dataset.fn)">' +
           av +
           '<div class="trial-npc-txt">' +
             '<div class="trial-npc-name">' + escapeHtml(c.name) + '</div>' +
             '<div class="trial-npc-id">ID ' + c.id + '</div>' +
           '</div></div>';
  }).join('');
}

async function trialSelectNpc(fn) {
  trialSelected = fn;
  const hid = document.getElementById('trial-npc');
  if (hid) hid.value = fn;
  trialRenderNpcList();
  await trialLoadCtx();
}

// ---------- 游戏段落 ----------

async function trialLoadFragments() {
  try {
    const res = await pywebview.api.chat_trial_fragments();
    if (!res || !res.ok) return;
    trialFragments = (res.fragments || []).map(f => ({ ...f, on: false }));
    // 默认勾上三段最基础的，能体现「游戏里到底灌了什么」
    const def = ['守住世界观', '通用要求', '赠物通则'];
    trialFragments.forEach(f => { f.on = def.indexOf(f.title) >= 0; });
    trialRenderFrags();
  } catch (e) { /* 没数据就静默跳过 */ }
}

function trialRenderFrags() {
  const cnt = document.getElementById('trial-frag-count');
  const on = trialFragments.filter(f => f.on).length;
  if (cnt) cnt.textContent = on + '/' + trialFragments.length;

  const box = document.getElementById('trial-frags-body');
  if (!box || !trialFragOpen) return;
  box.innerHTML = trialFragments.map((f, i) =>
    '<label class="trial-frag">' +
      '<input type="checkbox" data-i="' + i + '"' + (f.on ? ' checked' : '') +
      ' onchange="trialFragChange(this)">' +
      '<span class="trial-frag-name">' + escapeHtml(f.title) + '</span>' +
      '<span class="trial-frag-meta">' + f.pct + '% · ' + f.chars + '字</span>' +
    '</label>').join('');
}

function trialFragChange(el) {
  const i = parseInt(el.dataset.i, 10);
  if (trialFragments[i]) trialFragments[i].on = el.checked;
  trialRenderFrags();
  trialUpdateStat();
  trialReset();
}

function trialToggleFrags() {
  trialFragOpen = !trialFragOpen;
  const body = document.getElementById('trial-frags-body');
  const arrow = document.getElementById('trial-frag-arrow');
  if (body) body.style.display = trialFragOpen ? 'block' : 'none';
  if (arrow) arrow.textContent = trialFragOpen ? '收起' : '展开';
  if (trialFragOpen) trialRenderFrags();
}

// ---------- 模型 ----------

async function trialLoadModels() {
  const sel = document.getElementById('trial-model');
  const badge = document.getElementById('trial-model-badge');
  try {
    const res = await pywebview.api.chat_trial_models();
    if (!res || !res.ok) {
      if (sel) sel.innerHTML = '<option value="">连不上本地节点</option>';
      if (badge) { badge.textContent = '未连接'; badge.className = 'trial-badge bad'; }
      return;
    }
    trialBase = res.base || '';
    trialModels = res.models || [];
    if (sel) {
      if (!trialModels.length) {
        sel.innerHTML = '<option value="">节点上没有模型</option>';
      } else {
        sel.innerHTML = trialModels.map(m =>
          '<option value="' + escapeHtml(m.name) + '">' + escapeHtml(m.name) +
          ' · ' + (m.size / 1073741824).toFixed(1) + 'G</option>').join('');
        sel.value = trialModels[0].name;
      }
    }
    if (badge) {
      badge.textContent = trialModels.length + ' 个模型可用';
      badge.className = 'trial-badge ok';
    }
  } catch (e) {
    if (badge) { badge.textContent = '连接失败'; badge.className = 'trial-badge bad'; }
  }
}

// ---------- 上下文 / 统计 ----------

async function trialLoadCtx() {
  const stat = document.getElementById('trial-stat');
  if (!trialSelected) return;

  try {
    trialCtx = await pywebview.api.chat_trial_context(trialSelected);
  } catch (e) {
    trialCtx = { ok: false, msg: String(e) };
  }

  if (!trialCtx || !trialCtx.ok) {
    if (stat) stat.textContent = '读取失败：' + ((trialCtx && trialCtx.msg) || '');
    return;
  }

  const useCard = document.getElementById('trial-use-card');
  const useMem = document.getElementById('trial-use-memory');
  if (useCard) { useCard.disabled = !trialCtx.has_card; useCard.checked = !!trialCtx.has_card; }
  if (useMem) { useMem.disabled = !trialCtx.has_memory; useMem.checked = !!trialCtx.has_memory; }

  trialUpdateStat();
  trialReset();
}

function trialFragChars() {
  let n = 0;
  trialFragments.forEach(f => { if (f.on && f.text) n += f.text.length; });
  return n;
}

function trialUpdateStat() {
  const stat = document.getElementById('trial-stat');
  if (!stat || !trialCtx || !trialCtx.ok) return;
  const useCard = document.getElementById('trial-use-card');
  const useMem = document.getElementById('trial-use-memory');
  const withCard = useCard && useCard.checked;
  const withMem = useMem && useMem.checked;
  const frag = trialFragChars();

  let sys = trialCtx.base_chars + frag;
  if (withCard) sys += trialCtx.card_chars;
  if (withMem) sys += trialCtx.memory_chars;

  const parts = ['设定 ' + trialCtx.base_chars];
  if (frag) parts.push('段落 ' + frag);
  if (withCard) parts.push('名片 ' + trialCtx.card_chars);
  if (withMem) parts.push('记忆 ' + trialCtx.memory_chars);
  stat.innerHTML = parts.join(' · ') + '<br>system 合计 <b>' + sys + '</b> 字';
}

function trialBuildSystem() {
  if (!trialCtx || !trialCtx.ok) return '';
  const useCard = document.getElementById('trial-use-card');
  const useMem = document.getElementById('trial-use-memory');

  let s = trialCtx.base;
  if (useCard && useCard.checked && trialCtx.user_card) {
    s += '\n\n## 角色演绎要求（优先于以上所有属性）\n' + trialCtx.user_card;
  }
  if (useMem && useMem.checked && trialCtx.memory) {
    s += '\n\n## 过往要事\n' + trialCtx.memory;
  }

  // 勾选的游戏段落按原样拼进来 —— 这就是「游戏里实际会灌什么」
  const picked = trialFragments.filter(f => f.on && f.text);
  if (picked.length) {
    s += '\n\n' + picked.map(f => '## ' + f.title + '\n' + f.text).join('\n\n');
  }
  return s;
}

// ---------- 对话 ----------

function trialAppend(role, text) {
  const log = document.getElementById('trial-log');
  if (!log) return null;
  const d = document.createElement('div');
  d.className = 'trial-msg ' + role;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

function trialReset() {
  trialMessages = [];
  const log = document.getElementById('trial-log');
  if (log) log.innerHTML = '';
  if (trialCtx && trialCtx.ok) {
    trialAppend('sys', '已载入「' + trialCtx.npc_name + '」的设定，说句话开始。');
  }
}

async function trialSend() {
  if (trialBusy) return;
  const ta = document.getElementById('trial-text');
  const text = ta ? ta.value.trim() : '';
  if (!text) return;
  if (!trialCtx || !trialCtx.ok) { alert('还没有可用的 NPC 设定'); return; }

  const modelSel = document.getElementById('trial-model');
  const model = modelSel ? modelSel.value : '';
  if (!model) { alert('本地节点上没有可用模型'); return; }

  if (ta) ta.value = '';
  trialAppend('user', text);
  trialMessages.push({ role: 'user', content: text });

  trialBusy = true;
  const btn = document.getElementById('trial-send');
  if (btn) btn.disabled = true;
  const bubble = trialAppend('bot', '…');

  const msgs = [{ role: 'system', content: trialBuildSystem() }].concat(trialMessages);
  const tempEl = document.getElementById('trial-temp');

  try {
    const res = await pywebview.api.chat_trial_send({
      base: trialBase,
      model: model,
      messages: msgs,
      temperature: tempEl ? parseFloat(tempEl.value) : 0.85,
      max_tokens: 900,
    });
    if (!res || !res.ok) {
      bubble.textContent = '✗ ' + ((res && res.msg) || '请求失败');
      bubble.classList.add('err');
    } else {
      bubble.textContent = res.reply;
      const meta = document.createElement('div');
      meta.className = 'trial-meta';
      meta.textContent = res.elapsed + 's · ' + res.reply.length + ' 字 · ' + model;
      bubble.appendChild(meta);
      trialMessages.push({ role: 'assistant', content: res.reply });
      const log = document.getElementById('trial-log');
      if (log) log.scrollTop = log.scrollHeight;
    }
  } catch (e) {
    bubble.textContent = '✗ ' + e;
    bubble.classList.add('err');
  }

  trialBusy = false;
  if (btn) btn.disabled = false;
  if (ta) ta.focus();
}
