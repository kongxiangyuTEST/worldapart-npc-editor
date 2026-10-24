// ===== 试聊页 =====
// 用真实卡片 + 记忆 + 可选的游戏段落，跟模型对话。
//
// 模型来源分三段（本机 / 局域网 / API）。这三类本质上都是 OpenAI 兼容端点，
// 差别只在地址和要不要密钥，所以底层是同一份 { base, model }，三段只是入口。
// 云端的明文密钥不出后端：前端只传 api_id，editor.py 自己从 user_config 取。

let trialCtx = null;
let trialMessages = [];
let trialBusy = false;
let trialAllCards = [];
let trialSelected = '';
let trialFragments = [];
let trialFragOpen = false;
let trialSource = 'local';    // local | lan | api
let trialSources = null;      // model_sources_get 的结果

async function initTrialPage() {
  const search = document.getElementById('trial-npc-search');
  if (search) search.oninput = trialRenderNpcList;

  await trialLoadSources();

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

  trialAllCards = (typeof allCards !== 'undefined' && allCards) ? allCards : [];
  trialRenderNpcList();
  if (trialAllCards.length) await trialSelectNpc(trialAllCards[0].filename);
}

// ---------- 模型来源 ----------

async function trialLoadSources() {
  try {
    trialSources = await pywebview.api.model_sources_get();
  } catch (e) {
    trialSources = { ok: false, msg: String(e) };
  }
  if (!trialSources || !trialSources.ok) {
    trialSources = { ok: false, local_base: '', lan_nodes: [], api_services: [] };
  }

  // 局域网节点下拉
  const nodeSel = document.getElementById('trial-node');
  if (nodeSel) {
    const nodes = trialSources.lan_nodes || [];
    nodeSel.innerHTML = nodes.length
      ? nodes.map(n => '<option value="' + escapeHtml(n.id) + '">' +
          escapeHtml(n.name) + ' · ' + escapeHtml(n.base.replace(/^https?:\/\//, '')) +
          '</option>').join('')
      : '<option value="">（还没配节点）</option>';
  }

  // API 段
  const baseIn = document.getElementById('trial-api-base');
  const modelIn = document.getElementById('trial-api-model');
  const keyIn = document.getElementById('trial-api-key');
  const apis = trialSources.api_services || [];
  if (apis.length) {
    const a = apis[0];
    if (baseIn) baseIn.value = a.base || '';
    if (modelIn) modelIn.value = a.model || '';
    if (keyIn) keyIn.placeholder = a.has_key ? ('已保存 ' + a.key_masked + '（留空=不改）') : 'sk-…';
  } else if (keyIn) {
    keyIn.placeholder = 'sk-…';
  }

  trialSetSource(trialSource, true);
  await trialLoadModels();
  await trialLoadGguf();
}

// 重新读节点配置（含后端兜底的默认节点）并拉模型
async function trialReloadNodes() {
  await trialLoadSources();
}

function trialSetSource(kind, silent) {
  trialSource = kind;
  ['local', 'lan', 'api'].forEach(k => {
    const seg = document.getElementById('seg-' + k);
    if (seg) seg.classList.toggle('on', k === kind);
    const pane = document.getElementById('src-' + k);
    if (pane) pane.style.display = (k === kind) ? 'block' : 'none';
  });
  // 切段不再自动拉模型 —— 每次点过去都要等一次网络往返，会卡一下。
  // 改成用户点旁边的「刷新」。只有首次进页面（silent）才自动拉一次。
  if (silent) trialLoadModels();
}

async function trialLoadModels() {
  const badge = document.getElementById('trial-model-badge');
  let base = '';
  let key = '';
  let sel = null;

  if (trialSource === 'local') {
    base = (trialSources && trialSources.local_base) || '';
    sel = document.getElementById('trial-model-local');
  } else if (trialSource === 'lan') {
    const nodeSel = document.getElementById('trial-node');
    const id = nodeSel ? nodeSel.value : '';
    const node = ((trialSources && trialSources.lan_nodes) || []).find(n => n.id === id);
    base = node ? node.base : '';
    sel = document.getElementById('trial-model-lan');
  } else {
    const baseIn = document.getElementById('trial-api-base');
    base = baseIn ? baseIn.value.trim() : '';
    sel = null;   // API 段的模型名手填，不用下拉
  }

  if (sel) sel.innerHTML = '<option value="">读取中…</option>';
  if (!base) {
    if (sel) sel.innerHTML = '<option value="">没有可用地址</option>';
    if (badge) { badge.textContent = '未连接'; badge.className = 'trial-badge bad'; }
    return;
  }

  try {
    const res = await pywebview.api.chat_trial_models(base, key);
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
        sel.value = models[0].name;
      }
    }
    if (badge) {
      badge.textContent = models.length + ' 个模型可用';
      badge.className = 'trial-badge ok';
    }
  } catch (e) {
    if (badge) { badge.textContent = '连接失败'; badge.className = 'trial-badge bad'; }
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
      note.textContent = res.files && res.files.length
        ? ('models/ 里 ' + res.files.length + ' 个 GGUF，' + files.length + ' 个未导入')
        : 'models/ 是空的（把 GGUF 放进去）';
    }
    sel.innerHTML = files.length
      ? files.map(f => '<option value="' + escapeHtml(f.file) + '">' +
          escapeHtml(f.file) + ' · ' + (f.size / 1073741824).toFixed(1) + 'G' +
          '</option>').join('')
      : '<option value="">（没有待导入的）</option>';
    if (!res.local_available && note) {
      note.textContent += ' · 本机没检测到 Ollama';
    }
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

async function trialSaveApi() {
  const baseIn = document.getElementById('trial-api-base');
  const keyIn = document.getElementById('trial-api-key');
  const modelIn = document.getElementById('trial-api-model');
  const warn = document.querySelector('#src-api .trial-warn');
  const payload = {
    model_sources: {
      local_base: (trialSources && trialSources.local_base) || '',
      lan_nodes: (trialSources && trialSources.lan_nodes) || [],
      api_services: [{
        id: (trialSources && trialSources.api_services && trialSources.api_services[0]
             && trialSources.api_services[0].id) || 'api-1',
        name: 'API',
        base: baseIn ? baseIn.value.trim() : '',
        model: modelIn ? modelIn.value.trim() : '',
        key: keyIn ? keyIn.value.trim() : '',
      }],
    },
  };
  try {
    const res = await pywebview.api.model_sources_save(payload);
    if (!res || !res.ok) {
      if (warn) warn.textContent = '保存失败：' + ((res && res.msg) || '');
      return;
    }
    if (keyIn) keyIn.value = '';
    await trialLoadSources();
    if (warn) {
      warn.textContent = '已保存。密钥只存在本机 user_config.json，不上传、不进仓库。';
    }
  } catch (e) {
    if (warn) warn.textContent = '保存失败：' + e;
  }
}

// 当前在用的 { base, model, api_id }
function trialCurrentModel() {
  if (trialSource === 'local') {
    const el = document.getElementById('trial-model-local');
    return { base: (trialSources && trialSources.local_base) || '', model: el ? el.value : '', api_id: '' };
  }
  if (trialSource === 'lan') {
    const nodeSel = document.getElementById('trial-node');
    const modelSel = document.getElementById('trial-model-lan');
    const node = ((trialSources && trialSources.lan_nodes) || [])
      .find(n => n.id === (nodeSel ? nodeSel.value : ''));
    return { base: node ? node.base : '', model: modelSel ? modelSel.value : '', api_id: '' };
  }
  const baseIn = document.getElementById('trial-api-base');
  const modelIn = document.getElementById('trial-api-model');
  const apis = (trialSources && trialSources.api_services) || [];
  return {
    base: baseIn ? baseIn.value.trim() : '',
    model: modelIn ? modelIn.value.trim() : '',
    api_id: apis.length ? apis[0].id : '',
  };
}

// ---------- NPC 列表 ----------

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
  } catch (e) { /* 没数据就跳过 */ }
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

  const cur = trialCurrentModel();
  if (!cur.base || !cur.model) { alert('先选一个模型'); return; }

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
      base: cur.base,
      model: cur.model,
      api_id: cur.api_id,
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
      meta.textContent = res.elapsed + 's · ' + res.reply.length + ' 字 · ' + cur.model;
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
