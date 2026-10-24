// ===== 试聊页 =====
// 用真实卡片 + 记忆 + 可选游戏段落，跟模型对话。
//
// 连接只有一个地址框：云端 / 局域网 / 本机都是填地址（填到端口即可），
// 云端再加个密钥。比「三段切换」简单，也不绑死在某个人的环境上。
// 密钥不出后端：前端拿不到明文，发请求时由 editor.py 从 user_config 取。

let trialCtx = null;
let trialMessages = [];
let trialBusy = false;
let trialAllCards = [];
let trialSelected = '';
let trialFragments = [];
let trialFragOpen = false;
let trialSource = { base: '', model: '', has_key: false };

async function initTrialPage() {
  const search = document.getElementById('trial-npc-search');
  if (search) search.oninput = trialRenderNpcList;

  await trialLoadSource();

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

  await trialLoadFragments();
  await trialLoadGguf();

  trialAllCards = (typeof allCards !== 'undefined' && allCards) ? allCards : [];
  trialRenderNpcList();
  if (trialAllCards.length) await trialSelectNpc(trialAllCards[0].filename);
}

// ---------- 连接与模型 ----------

async function trialLoadSource() {
  try {
    trialSource = await pywebview.api.trial_source_get();
  } catch (e) {
    trialSource = { ok: false, base: '', model: '', has_key: false };
  }
  if (!trialSource || !trialSource.ok) {
    trialSource = { ok: false, base: '', model: '', has_key: false };
  }

  const baseIn = document.getElementById('trial-base');
  const keyIn = document.getElementById('trial-key');
  if (baseIn) baseIn.value = trialSource.base || '';
  if (keyIn) {
    keyIn.value = '';
    keyIn.placeholder = trialSource.has_key
      ? ('已保存 ' + (trialSource.key_masked || '') + '，留空 = 不改')
      : '云端 API 才需要，留空即可';
  }

  await trialLoadModels();
}

async function trialRefreshModels() {
  await trialLoadModels(true);
}

async function trialLoadModels(save) {
  const sel = document.getElementById('trial-model');
  const badge = document.getElementById('trial-model-badge');
  const baseIn = document.getElementById('trial-base');
  const base = baseIn ? baseIn.value.trim() : '';

  if (!base) {
    if (sel) sel.innerHTML = '<option value="">先填连接地址</option>';
    if (badge) { badge.textContent = '未连接'; badge.className = 'trial-badge bad'; }
    return;
  }

  if (sel) sel.innerHTML = '<option value="">读取中…</option>';

  let res = null;
  try {
    res = await pywebview.api.chat_trial_models(base, '');
  } catch (e) {
    res = { ok: false, msg: String(e) };
  }

  if (!res || !res.ok) {
    if (sel) sel.innerHTML = '<option value="">连不上</option>';
    if (badge) { badge.textContent = '连接失败'; badge.className = 'trial-badge bad'; }
    return;
  }

  const models = res.models || [];
  if (sel) {
    if (!models.length) {
      sel.innerHTML = '<option value="">这个地址没有模型</option>';
    } else {
      sel.innerHTML = models.map(m =>
        '<option value="' + escapeHtml(m.name) + '">' + escapeHtml(m.name) +
        (m.size ? ' · ' + (m.size / 1073741824).toFixed(1) + 'G' : '') +
        '</option>').join('');
      const prefer = (trialSource && trialSource.model) || '';
      sel.value = (prefer && models.some(m => m.name === prefer)) ? prefer : models[0].name;
    }
  }
  if (badge) {
    badge.textContent = models.length + ' 个模型可用';
    badge.className = 'trial-badge ok';
  }

  if (save) trialSaveSource(true);
}

async function trialSaveSource(silent) {
  const baseIn = document.getElementById('trial-base');
  const keyIn = document.getElementById('trial-key');
  const sel = document.getElementById('trial-model');
  try {
    const res = await pywebview.api.trial_source_save({
      base: baseIn ? baseIn.value.trim() : '',
      model: sel ? sel.value : '',
      key: keyIn ? keyIn.value.trim() : '',
    });
    if (res && res.ok) {
      if (keyIn) keyIn.value = '';
      trialSource.has_key = true;
    } else if (!silent) {
      alert('保存失败：' + ((res && res.msg) || ''));
    }
  } catch (e) {
    if (!silent) alert('保存失败：' + e);
  }
}

// ---------- models/ 目录 ----------

async function trialLoadGguf() {
  const sel = document.getElementById('trial-gguf');
  const note = document.getElementById('local-note');
  if (!sel) return;
  try {
    const res = await pywebview.api.models_scan();
    if (!res || !res.ok) {
      if (note) note.textContent = '扫描 models/ 失败';
      sel.innerHTML = '<option value="">—</option>';
      return;
    }
    const files = (res.files || []).filter(f => !f.imported);
    if (note) {
      note.textContent = (res.files && res.files.length)
        ? ('models/ 里 ' + res.files.length + ' 个 GGUF，' + files.length + ' 个未导入')
        : 'models/ 是空的（把 GGUF 放进去）';
      if (!res.local_available) note.textContent += ' · 本机没检测到 Ollama';
    }
    sel.innerHTML = files.length
      ? files.map(f => '<option value="' + escapeHtml(f.file) + '">' +
          escapeHtml(f.file) + ' · ' + (f.size / 1073741824).toFixed(1) + 'G' +
          '</option>').join('')
      : '<option value="">（没有待导入的）</option>';
  } catch (e) {
    if (note) note.textContent = '扫描失败：' + e;
  }
}

async function trialImportGguf() {
  const sel = document.getElementById('trial-gguf');
  const file = sel ? sel.value : '';
  if (!file) { alert('没有待导入的 GGUF'); return; }
  const note = document.getElementById('local-note');
  if (note) note.textContent = '正在导入 ' + file + '，可能要几分钟…';
  try {
    const res = await pywebview.api.models_import(file);
    if (!res || !res.ok) {
      if (note) note.textContent = '导入失败：' + ((res && res.msg) || '');
      return;
    }
    if (note) note.textContent = '已导入：' + res.model;
    await trialLoadGguf();
    await trialLoadModels();
  } catch (e) {
    if (note) note.textContent = '导入失败：' + e;
  }
}

// ---------- NPC ----------

function trialRenderNpcList() {
  const sel = document.getElementById('trial-npc');
  const cnt = document.getElementById('trial-npc-count');
  if (!sel) return;

  const searchEl = document.getElementById('trial-npc-search');
  const kw = (searchEl && searchEl.value ? searchEl.value : '').trim().toLowerCase();

  const list = trialAllCards.filter(c => {
    if (!kw) return true;
    const n = (c.name || '').toLowerCase();
    return n.indexOf(kw) >= 0 || String(c.id).indexOf(kw) >= 0;
  });

  if (cnt) cnt.textContent = list.length + '/' + trialAllCards.length;

  sel.innerHTML = list.length
    ? list.map(c => '<option value="' + escapeHtml(c.filename) + '">' +
        escapeHtml(c.name) + '（ID ' + c.id + '）</option>').join('')
    : '<option value="">没有匹配的角色</option>';

  if (trialSelected) sel.value = trialSelected;
}

async function trialSelectNpc(fn) {
  trialSelected = fn;
  trialRenderNpcList();
  await trialLoadCtx();
}

// ---------- 游戏段落 ----------

async function trialLoadFragments() {
  try {
    const res = await pywebview.api.chat_trial_fragments();
    if (!res || !res.ok) return;
    trialFragments = (res.fragments || []).map(f => ({ ...f, on: false }));
    const def = ['守住世界观', '通用要求', '赠物通则'];
    trialFragments.forEach(f => { f.on = def.indexOf(f.title) >= 0; });
    trialRenderFrags();
  } catch (e) { /* 没数据跳过 */ }
}

function trialRenderFrags() {
  const cnt = document.getElementById('trial-frag-count');
  if (cnt) cnt.textContent = trialFragments.filter(f => f.on).length + '/' + trialFragments.length;
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

  const baseIn = document.getElementById('trial-base');
  const sel = document.getElementById('trial-model');
  const base = baseIn ? baseIn.value.trim() : '';
  const model = sel ? sel.value : '';
  if (!base || !model) { alert('先填连接地址并选一个模型'); return; }

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
      base: base,
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
