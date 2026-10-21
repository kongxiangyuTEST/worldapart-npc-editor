// web/memory.js —— 长期记忆页

let memoryItems = [];
let memoryCurrentId = null;
let memorySearchBound = false;

const MEMORY_EMPTY_DETAIL =
  '<div style="color:#aaa; text-align:center; margin-top:100px; font-size:15px;">👈 请在左侧选择一个角色查看其记忆</div>';

function memoryFail(html) {
  const box = document.getElementById('memory-list');
  if (box) box.innerHTML = html;
}

// 记忆目录会跟着 exe 走（打包后是 exe 同级的 memory\），这里把它显示出来
function setMemoryDirPath(p) {
  const el = document.getElementById('memory-dir-path');
  if (!el) return;
  el.textContent = '记忆目录：' + (p || '未知');
  el.title = p || '';
}

async function openMemoryDir() {
  try {
    const res = await pywebview.api.open_memory_dir();
    setMemoryDirPath((res && res.path) || undefined);
    if (!res || !res.ok) alert('打开失败：' + ((res && res.msg) || '未知错误'));
  } catch (e) {
    alert('打开失败：' + ((e && e.message) || e));
  }
}

function bindMemorySearch() {
  if (memorySearchBound) return;
  const input = document.getElementById('memory-search');
  if (!input) return;
  input.addEventListener('input', () => {
    renderMemoryList(memoryItems);
  });
  memorySearchBound = true;
}

// ==================== 列表 ====================

async function initMemoryPage() {
  const listContainer = document.getElementById('memory-list');
  if (!listContainer) return;

  bindMemorySearch();
  listContainer.innerHTML =
    '<div style="padding:20px; color:#888; text-align:center;">正在刷新列表...</div>';

  let res;
  try {
    res = await pywebview.api.get_memory_list();
  } catch (e) {
    memoryFail(
      '<div style="color:#c33;padding:20px;font-size:13px;line-height:1.6;">' +
      '<b>读取失败</b><br>' + escapeHtml((e && e.message) || String(e)) + '</div>'
    );
    return;
  }

  if (!res || res.ok === false) {
    const msg = (res && (res.error || res.msg)) || '未知错误';
    const path = (res && res.debug_path) || '未知';
    setMemoryDirPath(path === '未知' ? '' : path);
    const tb = res && res.traceback
      ? '<pre style="font-size:11px;color:#888;white-space:pre-wrap;margin-top:10px;">' +
      escapeHtml(res.traceback) + '</pre>'
      : '';
    memoryFail(
      '<div style="color:#c33;padding:20px;font-size:13px;line-height:1.6;">' +
      '<b>读取失败</b><br>原因：' + escapeHtml(msg) +
      '<br>目录：' + escapeHtml(path) + tb + '</div>'
    );
    return;
  }

  memoryItems = res.items || [];
  setMemoryDirPath(res.debug_path || '');
  renderMemoryList(memoryItems, res.debug_path || '');

  const detail = document.getElementById('memory-detail');
  const stillThere = memoryItems.some(it => String(it.id) === String(memoryCurrentId));
  if (detail && !stillThere) {
    memoryCurrentId = null;
    detail.innerHTML = MEMORY_EMPTY_DETAIL;
  }
}

function renderMemoryList(items, debugPath) {
  const box = document.getElementById('memory-list');
  if (!box) return;

  const searchEl = document.getElementById('memory-search');
  const kw = ((searchEl && searchEl.value) || '').trim().toLowerCase();

  const list = (items || []).filter(it => {
    if (!kw) return true;
    return String(it.name || '').toLowerCase().includes(kw) ||
      String(it.id || '').includes(kw);
  });

  box.innerHTML = '';

  if (!list.length) {
    const hint = kw
      ? '没有匹配「' + escapeHtml(kw) + '」的记忆'
      : '暂无记忆<br><span style="font-size:11px;color:#aaa;margin-top:8px;display:block;">(' +
      escapeHtml(debugPath || '') + ')</span>';
    box.innerHTML =
      '<div style="padding:20px;color:#888;text-align:center;">' + hint + '</div>';
    return;
  }

  for (const it of list) {
    const row = document.createElement('div');
    row.className = 'nav-item memory-list-item';
    row.style.padding = '10px 12px';
    row.style.margin = '4px 0';
    row.style.borderRadius = '6px';
    row.style.cursor = 'pointer';
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.background = String(it.id) === String(memoryCurrentId) ? '#eef4fb' : 'transparent';

    const nameEl = document.createElement('span');
    nameEl.style.fontWeight = 'bold';
    nameEl.style.color = '#333';
    nameEl.style.fontSize = '14px';
    nameEl.textContent = it.name || '(未命名)';

    const idEl = document.createElement('span');
    idEl.style.cssText =
      'color:#999;font-size:11px;background:#f0f0f0;padding:2px 6px;border-radius:10px;';
    idEl.textContent = 'ID: ' + it.id;

    row.appendChild(nameEl);
    row.appendChild(idEl);

    row.onmouseover = () => { row.style.background = '#f0f4f8'; };
    row.onmouseout = () => {
      row.style.background = String(it.id) === String(memoryCurrentId) ? '#eef4fb' : 'transparent';
    };
    row.onclick = () => loadMemoryDetail(it.id, it.name);

    box.appendChild(row);
  }
}

// ==================== 详情 ====================

async function loadMemoryDetail(npcId, npcName) {
  const res = await pywebview.api.get_memory(npcId, npcName);
  if (!res || !res.ok) {
    alert('读取记忆失败：' + ((res && res.msg) || '未知错误'));
    return;
  }
  memoryCurrentId = npcId;
  renderMemoryList(memoryItems);

  const data = res.data || {};
  const detailContainer = document.getElementById('memory-detail');

  detailContainer.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #0056b3; padding-bottom:10px; margin-bottom:20px;">
            <h3 style="margin:0; color:#0056b3; font-size:20px;">${escapeHtml(npcName)} <span style="font-size:12px; font-weight:normal; color:#888;">(ID: ${escapeHtml(npcId)})</span></h3>
            <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                <button onclick="triggerMemoryMerge('${escapeHtml(npcId)}', '${escapeHtml(npcName)}')" style="padding:8px 20px; border-radius:20px; font-weight:bold; cursor:pointer; background:#e0f2fe; color:#0369a1; border:1px solid #7dd3fc;">🤖 立即 AI 总结</button>
                <button class="primary" onclick="saveMemoryData('${escapeHtml(npcId)}', '${escapeHtml(npcName)}')" style="padding:8px 20px; border-radius:20px; font-weight:bold; cursor:pointer; background:#0056b3; color:#fff; border:none; box-shadow:0 2px 6px rgba(0,86,179,0.3);">💾 保存记忆</button>
                <button onclick="clearMemoryData('${escapeHtml(npcId)}', '${escapeHtml(npcName)}')" style="padding:8px 20px; border-radius:20px; font-weight:bold; cursor:pointer; background:#fee2e2; color:#dc2626; border:1px solid #fca5a5;">🗑️ 彻底清空</button>
            </div>
        </div>
        <span id="m-msg" style="color:green; font-size:13px; margin-bottom:10px; display:block; text-align:right; font-weight:bold;"></span>

        <div style="display:grid; grid-template-columns: 1fr; gap: 20px;">
            <div style="background:#f8fbfe; border:1px solid #e0ebf5; padding:15px; border-radius:8px;">
                <label style="display:block; margin-bottom:8px; font-weight:bold; color:#0056b3; font-size:13px;">📍 当前时间地点</label>
                <input type="text" id="m-tl" value="${escapeHtml(data.time_location || '')}" style="width:100%; box-sizing:border-box; padding:10px; border:1px solid #cce0f5; border-radius:6px; outline:none;">
            </div>

            <div style="display:flex; gap:20px; flex-wrap:wrap;">
                <div style="flex:1; min-width:300px; background:#fff; border:1px solid #eee; padding:15px; border-radius:8px; box-shadow:0 1px 4px rgba(0,0,0,0.02);">
                    <label style="display:block; margin-bottom:8px; font-weight:bold; color:#555; font-size:13px;">📖 故事早期梗概</label>
                    <textarea id="m-gist" style="height:100px; width:100%; box-sizing:border-box; padding:10px; border:1px solid #ddd; border-radius:6px; resize:vertical; font-family:inherit; line-height:1.5;">${escapeHtml(data.early_gist || '')}</textarea>
                </div>
                <div style="flex:2; min-width:300px; background:#fff; border:1px solid #eee; padding:15px; border-radius:8px; box-shadow:0 1px 4px rgba(0,0,0,0.02);">
                    <label style="display:block; margin-bottom:8px; font-weight:bold; color:#555; font-size:13px;">🎬 最近剧情</label>
                    <textarea id="m-summary" style="height:100px; width:100%; box-sizing:border-box; padding:10px; border:1px solid #ddd; border-radius:6px; resize:vertical; font-family:inherit; line-height:1.5;">${escapeHtml(data.long_summary || '')}</textarea>
                </div>
            </div>

            <div style="background:#fafafa; border:1px solid #eee; padding:20px; border-radius:8px;">
                <label style="display:block; margin-bottom:15px; font-weight:bold; color:#333; font-size:15px; border-left:4px solid #0056b3; padding-left:10px;">📌 事实条目</label>
                <div id="m-facts" style="display:flex; flex-direction:column; gap:15px;"></div>
            </div>
        </div>
    `;

  const factsContainer = document.getElementById('m-facts');
  let factsHtml = '';
  for (const [cat, list] of Object.entries(data.facts || {})) {
    factsHtml += `
            <div style="background:#fff; border:1px solid #ebebeb; border-radius:8px; padding:15px; box-shadow:0 1px 3px rgba(0,0,0,0.02);">
                <div style="font-weight:bold; color:#d9534f; margin-bottom:10px; font-size:14px; border-bottom:1px dashed #eee; padding-bottom:5px;">📁 ${escapeHtml(cat)}</div>
        `;

    if (!list || list.length === 0) {
      factsHtml += '<div style="color:#bbb; font-size:12px; font-style:italic;">（暂无记录）</div>';
    }

    (list || []).forEach(item => {
      factsHtml += `
                <div style="display:flex; gap:15px; align-items:center; margin-bottom:8px; padding:8px; background:#fdfdfd; border:1px solid #f0f0f0; border-radius:6px;">
                    <input type="text" class="fact-text" value="${escapeHtml(item.text || '')}" style="flex:1; padding:8px; border:1px solid #e0e0e0; border-radius:4px; font-size:13px;">
                    <div style="display:flex; align-items:center; gap:5px;" title="重要度(1-5)">
                        <span style="font-size:12px; color:#666;">🌟</span>
                        <input type="number" class="fact-imp" value="${Number(item.importance) || 3}" min="1" max="5" style="width:45px; padding:6px; border:1px solid #e0e0e0; border-radius:4px; text-align:center;">
                    </div>
                    <div style="display:flex; align-items:center; gap:5px;" title="未被提及的轮数">
                        <span style="font-size:12px; color:#666;">⏳</span>
                        <input type="number" class="fact-age" value="${Number(item.age_count) || 0}" readonly style="width:45px; padding:6px; border:1px solid #eee; border-radius:4px; background:#f5f5f5; color:#888; text-align:center;">
                    </div>
                </div>
            `;
    });
    factsHtml += '</div>';
  }
  factsContainer.innerHTML = factsHtml || '<span style="color:#888;">暂无事实条目</span>';
}

async function saveMemoryData(npcId, npcName) {
  const res = await pywebview.api.get_memory(npcId, npcName);
  if (!res || !res.ok) return;
  let data = res.data;

  const tl = document.getElementById('m-tl');
  const gist = document.getElementById('m-gist');
  const summary = document.getElementById('m-summary');
  if (tl) data.time_location = tl.value;
  if (gist) data.early_gist = gist.value;
  if (summary) data.long_summary = summary.value;

  const factRows = document.querySelectorAll('#m-facts .fact-text');
  let idx = 0;
  for (const list of Object.values(data.facts || {})) {
    for (const item of list) {
      const row = factRows[idx];
      if (row) {
        item.text = row.value;
        const parentDiv = row.closest('div');
        const impInput = parentDiv ? parentDiv.querySelector('.fact-imp') : null;
        if (impInput) item.importance = parseInt(impInput.value, 10) || 3;
      }
      idx++;
    }
  }

  const saveRes = await pywebview.api.save_memory(npcId, npcName, data);
  const msgEl = document.getElementById('m-msg');
  if (saveRes && saveRes.ok) {
    if (msgEl) {
      msgEl.innerText = '✅ 记忆已成功保存！';
      setTimeout(() => { msgEl.innerText = ''; }, 2500);
    }
  } else if (msgEl) {
    msgEl.innerText = '❌ 保存失败！';
  }
}

async function clearMemoryData(npcId, npcName) {
  if (!confirm(`确定要彻底清空【${npcName}】的所有记忆吗？\n清空后，AI 将完全忘记你们发生过的事。`)) return;
  const res = await pywebview.api.clear_memory(npcId, npcName);
  if (res && res.ok) {
    alert('已清空该角色的长期记忆！');
    memoryCurrentId = null;
    const detail = document.getElementById('memory-detail');
    if (detail) detail.innerHTML = MEMORY_EMPTY_DETAIL;
    initMemoryPage();
  } else {
    alert('清空失败，请查看日志页的控制台输出。');
  }
}

async function triggerMemoryMerge(npcId, npcName) {
  const msgEl = document.getElementById('m-msg');
  if (msgEl) {
    msgEl.innerText = '🤖 正在呼叫 AI 总结，请稍候...';
    msgEl.style.color = '#0369a1';
  }

  const res = await pywebview.api.trigger_memory_merge(npcId, npcName);
  if (res && res.ok) {
    if (msgEl) {
      msgEl.innerText = '✅ ' + res.msg;
      msgEl.style.color = 'green';
    }
    loadMemoryDetail(npcId, npcName);
  } else if (msgEl) {
    msgEl.innerText = '❌ 失败：' + ((res && res.msg) || '未知错误');
    msgEl.style.color = 'red';
  }
}
