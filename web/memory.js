// web/memory.js

async function initMemoryPage() {
    const res = await pywebview.api.get_memory_list();
    const listContainer = document.getElementById('memory-list');
    if (!listContainer) return;
    
    listContainer.innerHTML = '';
    
    if (!res.ok || !res.list || res.list.length === 0) {
        listContainer.innerHTML = '<div style="padding:20px;color:#888;text-align:center;">暂无记忆</div>';
        return;
    }
    
    res.list.forEach(filename => {
        const parts = filename.split('-');
        const npcId = parts[0];
        const npcName = parts.slice(1).join('-');
        
        const div = document.createElement('div');
        div.className = 'nav-item memory-list-item';
        div.style.padding = '10px 12px';
        div.style.margin = '4px 0';
        div.style.borderRadius = '6px';
        div.style.cursor = 'pointer';
        div.style.display = 'flex';
        div.style.justifyContent = 'space-between';
        div.style.alignItems = 'center';
        
        div.onmouseover = () => div.style.background = '#f0f4f8';
        div.onmouseout = () => div.style.background = 'transparent';
        
        div.innerHTML = `
            <span style="font-weight:bold; color:#333; font-size:14px;">${npcName}</span>
            <span style="color:#999; font-size:11px; background:#f0f0f0; padding:2px 6px; border-radius:10px;">ID: ${npcId}</span>
        `;
        div.onclick = () => loadMemoryDetail(npcId, npcName);
        listContainer.appendChild(div);
    });
}

async function loadMemoryDetail(npcId, npcName) {
    const res = await pywebview.api.get_memory(npcId, npcName);
    if (!res.ok) return;
    
    const data = res.data;
    const detailContainer = document.getElementById('memory-detail');
    
    detailContainer.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #0056b3; padding-bottom:10px; margin-bottom:20px;">
            <h3 style="margin:0; color:#0056b3; font-size:20px;">${npcName} <span style="font-size:12px; font-weight:normal; color:#888;">(ID: ${npcId})</span></h3>
            <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                <button onclick="triggerMemoryMerge('${npcId}', '${npcName}')" style="padding:8px 20px; border-radius:20px; font-weight:bold; cursor:pointer; background:#e0f2fe; color:#0369a1; border:1px solid #7dd3fc;">🤖 立即 AI 总结</button>
                <button class="primary" onclick="saveMemoryData('${npcId}', '${npcName}')" style="padding:8px 20px; border-radius:20px; font-weight:bold; cursor:pointer; background:#0056b3; color:#fff; border:none; box-shadow:0 2px 6px rgba(0,86,179,0.3);">💾 保存记忆</button>
                <button onclick="clearMemoryData('${npcId}', '${npcName}')" style="padding:8px 20px; border-radius:20px; font-weight:bold; cursor:pointer; background:#fee2e2; color:#dc2626; border:1px solid #fca5a5;">🗑️ 彻底清空</button>
            </div>
        </div>
        <span id="m-msg" style="color:green; font-size:13px; margin-bottom:10px; display:block; text-align:right; font-weight:bold;"></span>

        <div style="display:grid; grid-template-columns: 1fr; gap: 20px;">
            <!-- 时间地点 -->
            <div style="background:#f8fbfe; border:1px solid #e0ebf5; padding:15px; border-radius:8px;">
                <label style="display:block; margin-bottom:8px; font-weight:bold; color:#0056b3; font-size:13px;">📍 当前时间地点</label>
                <input type="text" id="m-tl" value="${data.time_location || ''}" style="width:100%; box-sizing:border-box; padding:10px; border:1px solid #cce0f5; border-radius:6px; outline:none;">
            </div>
            
            <!-- 梗概与剧情 -->
            <div style="display:flex; gap:20px; flex-wrap:wrap;">
                <div style="flex:1; min-width:300px; background:#fff; border:1px solid #eee; padding:15px; border-radius:8px; box-shadow:0 1px 4px rgba(0,0,0,0.02);">
                    <label style="display:block; margin-bottom:8px; font-weight:bold; color:#555; font-size:13px;">📖 故事早期梗概</label>
                    <textarea id="m-gist" style="height:100px; width:100%; box-sizing:border-box; padding:10px; border:1px solid #ddd; border-radius:6px; resize:vertical; font-family:inherit; line-height:1.5;">${data.early_gist || ''}</textarea>
                </div>
                <div style="flex:2; min-width:300px; background:#fff; border:1px solid #eee; padding:15px; border-radius:8px; box-shadow:0 1px 4px rgba(0,0,0,0.02);">
                    <label style="display:block; margin-bottom:8px; font-weight:bold; color:#555; font-size:13px;">🎬 最近剧情</label>
                    <textarea id="m-summary" style="height:100px; width:100%; box-sizing:border-box; padding:10px; border:1px solid #ddd; border-radius:6px; resize:vertical; font-family:inherit; line-height:1.5;">${data.long_summary || ''}</textarea>
                </div>
            </div>
            
            <!-- 事实条目 -->
            <div style="background:#fafafa; border:1px solid #eee; padding:20px; border-radius:8px;">
                <label style="display:block; margin-bottom:15px; font-weight:bold; color:#333; font-size:15px; border-left:4px solid #0056b3; padding-left:10px;">📌 事实条目</label>
                <div id="m-facts" style="display:flex; flex-direction:column; gap:15px;"></div>
            </div>
        </div>
    `;
    
    // 渲染事实条目（卡片形式）
    const factsContainer = document.getElementById('m-facts');
    let factsHtml = '';
    for (const [cat, list] of Object.entries(data.facts || {})) {
        factsHtml += `
            <div style="background:#fff; border:1px solid #ebebeb; border-radius:8px; padding:15px; box-shadow:0 1px 3px rgba(0,0,0,0.02);">
                <div style="font-weight:bold; color:#d9534f; margin-bottom:10px; font-size:14px; border-bottom:1px dashed #eee; padding-bottom:5px;">📁 ${cat}</div>
        `;
        
        if (list.length === 0) {
            factsHtml += `<div style="color:#bbb; font-size:12px; font-style:italic;">（暂无记录）</div>`;
        }
        
        list.forEach((item, i) => {
            factsHtml += `
                <div style="display:flex; gap:15px; align-items:center; margin-bottom:8px; padding:8px; background:#fdfdfd; border:1px solid #f0f0f0; border-radius:6px;">
                    <input type="text" class="fact-text" value="${item.text}" style="flex:1; padding:8px; border:1px solid #e0e0e0; border-radius:4px; font-size:13px;">
                    <div style="display:flex; align-items:center; gap:5px;" title="重要度(1-5)">
                        <span style="font-size:12px; color:#666;">🌟</span>
                        <input type="number" class="fact-imp" value="${item.importance}" min="1" max="5" style="width:45px; padding:6px; border:1px solid #e0e0e0; border-radius:4px; text-align:center;">
                    </div>
                    <div style="display:flex; align-items:center; gap:5px;" title="未被提及的轮数">
                        <span style="font-size:12px; color:#666;">⏳</span>
                        <input type="number" class="fact-age" value="${item.age_count || 0}" readonly style="width:45px; padding:6px; border:1px solid #eee; border-radius:4px; background:#f5f5f5; color:#888; text-align:center;">
                    </div>
                </div>
            `;
        });
        factsHtml += `</div>`;
    }
    factsContainer.innerHTML = factsHtml || '<span style="color:#888;">暂无事实条目</span>';
}

async function saveMemoryData(npcId, npcName) {
    const res = await pywebview.api.get_memory(npcId, npcName);
    if (!res.ok) return;
    let data = res.data;
    
    data.time_location = document.getElementById('m-tl').value;
    data.early_gist = document.getElementById('m-gist').value;
    data.long_summary = document.getElementById('m-summary').value;
    
    const factRows = document.querySelectorAll('#m-facts .fact-text');
    let idx = 0;
    for (const [cat, list] of Object.entries(data.facts || {})) {
        list.forEach((item, i) => {
            if(factRows[idx]) {
                item.text = factRows[idx].value;
                const impInput = factRows[idx].closest('div').querySelector('.fact-imp');
                if(impInput) item.importance = parseInt(impInput.value) || 3;
            }
            idx++;
        });
    }
    
    const saveRes = await pywebview.api.save_memory(npcId, npcName, data);
    const msgEl = document.getElementById('m-msg');
    if (saveRes.ok) {
        msgEl.innerText = '✅ 记忆已成功保存！';
        setTimeout(() => msgEl.innerText = '', 2500);
    } else {
        msgEl.innerText = '❌ 保存失败！';
    }
}

async function clearMemoryData(npcId, npcName) {
    if (!confirm(`确定要彻底清空【${npcName}】的所有记忆吗？\n清空后，AI 将完全忘记你们发生过的事。`)) return;
    const res = await pywebview.api.clear_memory(npcId, npcName);
    if (res.ok) {
        alert("已清空该角色的长期记忆！");
        initMemoryPage(); // 刷新列表
        document.getElementById('memory-detail').innerHTML = '<div style="color:#aaa; text-align:center; margin-top:100px; font-size:15px;">👈 请在左侧选择一个角色查看其记忆</div>';
    } else {
        alert("清空失败，请查看控制台日志。");
    }
}

// 立即 AI 总结
async function triggerMemoryMerge(npcId, npcName) {
    const msgEl = document.getElementById('m-msg');
    msgEl.innerText = "🤖 正在呼叫 AI 总结，请稍候...";
    msgEl.style.color = "#0369a1";
    
    const res = await pywebview.api.trigger_memory_merge(npcId, npcName);
    if (res.ok) {
        msgEl.innerText = "✅ " + res.msg;
        msgEl.style.color = "green";
        loadMemoryDetail(npcId, npcName); // 重新加载数据
    } else {
        msgEl.innerText = "❌ 失败：" + res.msg;
        msgEl.style.color = "red";
    }
}