// ===== 试聊页 =====
// 拿一个 NPC 的真实卡片 + 长期记忆，直接跟本地模型对话。
// 用处：不启动游戏也能验证「改完名片 / 记忆之后，这个 NPC 说话像不像样」。
//
// 为什么不走 core_proxy：那边要完整的游戏 prompt 才会注入，试聊手里只有卡片；
// 而且试聊的目的是挑本地模型，走直连更干净，也不会污染游戏的记忆文件。

let trialCtx = null;         // chat_trial_context 的返回
let trialMessages = [];      // 对话历史（不含 system）
let trialBusy = false;
let trialBase = '';          // 本地节点地址
let trialModels = [];

async function initTrialPage() {
  // NPC 下拉：从全局卡片列表填
  const npcSel = document.getElementById('trial-npc');
  if (npcSel) {
    const cards = (typeof allCards !== 'undefined' && allCards) ? allCards : [];
    if (cards.length) {
      npcSel.innerHTML = cards.map(c => {
        const fn = (typeof c === 'string') ? c : (c.filename || '');
        const label = fn.replace(/^\d+-/, '').replace(/\.md$/, '');
        return `<option value="${escapeHtml(fn)}">${escapeHtml(label)}</option>`;
      }).join('');
    } else {
      npcSel.innerHTML = '<option value="">（还没有卡片）</option>';
    }
    npcSel.onchange = trialLoadCtx;
  }

  await trialLoadModels();

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

  await trialLoadCtx();
}

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
          `<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)} · ` +
          `${(m.size / 1073741824).toFixed(1)}G</option>`).join('');
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

async function trialLoadCtx() {
  const npcSel = document.getElementById('trial-npc');
  const stat = document.getElementById('trial-stat');
  if (!npcSel || !npcSel.value) return;

  try {
    trialCtx = await pywebview.api.chat_trial_context(npcSel.value);
  } catch (e) {
    trialCtx = { ok: false, msg: String(e) };
  }

  if (!trialCtx || !trialCtx.ok) {
    if (stat) stat.textContent = '读取失败：' + ((trialCtx && trialCtx.msg) || '');
    return;
  }

  // 没有名片 / 没有记忆时，把开关置灰，避免用户以为注入了
  const useCard = document.getElementById('trial-use-card');
  const useMem = document.getElementById('trial-use-memory');
  if (useCard) { useCard.disabled = !trialCtx.has_card; useCard.checked = !!trialCtx.has_card; }
  if (useMem) { useMem.disabled = !trialCtx.has_memory; useMem.checked = !!trialCtx.has_memory; }

  trialUpdateStat();
  trialReset();
}

function trialUpdateStat() {
  const stat = document.getElementById('trial-stat');
  if (!stat || !trialCtx || !trialCtx.ok) return;
  const useCard = document.getElementById('trial-use-card');
  const useMem = document.getElementById('trial-use-memory');
  const withCard = useCard && useCard.checked;
  const withMem = useMem && useMem.checked;

  let sys = trialCtx.base_chars;
  if (withCard) sys += trialCtx.card_chars;
  if (withMem) sys += trialCtx.memory_chars;

  const parts = ['设定 ' + trialCtx.base_chars + ' 字'];
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
  return s;
}

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

  const sys = trialBuildSystem();
  const msgs = [{ role: 'system', content: sys }].concat(trialMessages);
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
