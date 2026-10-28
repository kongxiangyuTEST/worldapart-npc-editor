/**
 * 语音风格页（实验性）
 *
 * 目的：把「双层 delivery」这块能力做成看得见、可试算、能开关的最小入口。
 *
 * 为什么默认关闭：这功能只在部分模型上验证过，规则补表演也还在调；
 * 而且编辑器自带「检查更新」会检测上传内容，所以不能默认开启。
 * 用户想试就自己打开，觉得不对随时关掉 —— 关掉后转发链路一字不改。
 *
 * 后端接口（editor.py 的 API 类）：
 *   speech_style_status()        -> 开关状态、词表规模、口吻档案加载情况
 *   speech_style_preview(...)    -> 试算 compose / normalize / perceive
 *   save_speech_style_flags(...) -> 只改这三个开关
 */

let speechReady = false;   // 词表是否已加载到下拉框
let speechBusy = false;
let speechNpcs = [];       // [{id, name}]，来自后端 status

// ===== MOD 诊断（stage2a 捕获）=====
//
// 为什么要有这块：MOD 的 Stage2A 捕获会把它实际收到的回复和实际发给 TTS
// 的请求体落盘。这是唯一能判定「我们写的 a1ttsv2 有没有被 MOD 看到」的手段。
// 编辑器原来的日志页只读 core_proxy 的 stdout，看不到这些。

async function modDiagRefresh() {
  const badge = document.getElementById('moddiag-badge');
  const status = document.getElementById('moddiag-status');
  const layout = document.getElementById('moddiag-layout');
  const sel = document.getElementById('moddiag-files');
  const detail = document.getElementById('moddiag-detail');

  try {
    const st = await pywebview.api.mod_diag_status();
    if (!st || !st.ok) {
      if (badge) badge.textContent = '不可用';
      return;
    }

    if (!st.enabled) {
      if (badge) { badge.textContent = '未开启'; badge.className = 'speech-badge'; }
      if (status) {
        status.innerHTML = '捕获<b>未开启</b>。需要在 MOD 配置里把 <code>[Stage2A] Enabled</code> 设为 true，' +
          '重开游戏后才会记录。<br><span class="speech-dim">配置文件：' + escapeHtml(st.config_path || '未找到') + '</span>';
      }
      if (layout) layout.style.display = 'none';
      return;
    }

    if (!st.count) {
      if (badge) { badge.textContent = '已开启 · 无记录'; badge.className = 'speech-badge on'; }
      if (status) {
        status.innerHTML = '捕获<b>已开启</b>，但还没有记录。跑一局游戏、跟 NPC 说几句话就会落盘。' +
          '<br><span class="speech-dim">目录：' + escapeHtml(st.dir || '') + '</span>';
      }
      if (layout) layout.style.display = 'none';
      return;
    }

    if (badge) { badge.textContent = '已开启 · ' + st.count + ' 条'; badge.className = 'speech-badge on'; }
    if (status) {
      status.innerHTML = '捕获<b>已开启</b>，共 ' + st.count + ' 条记录。' +
        '<br><span class="speech-dim">目录：' + escapeHtml(st.dir || '') + '</span>';
    }
    if (layout) layout.style.display = 'flex';

    const res = await pywebview.api.mod_diag_list(50);
    if (res && res.ok && sel) {
      sel.innerHTML = res.files.map(f =>
        `<option value="${escapeHtml(f.name)}">${escapeHtml(f.label || f.name)}　${f.mtime}</option>`
      ).join('') || '<option value="">（无）</option>';
      if (res.files.length) modDiagRead(res.files[0].name);
    }
  } catch (e) {
    if (badge) badge.textContent = '出错';
    if (status) status.textContent = '读取失败: ' + e;
  }
}

async function modDiagRead(name) {
  const box = document.getElementById('moddiag-detail');
  if (!box || !name) return;
  box.textContent = '读取中…';
  try {
    const r = await pywebview.api.mod_diag_read(name);
    if (!r || !r.ok) {
      box.textContent = '读取失败: ' + ((r && r.msg) || '未知错误');
      return;
    }

    const lines = [];
    const total = r.round_count || 0;
    const ok = r.rounds_with_script || 0;
    const audio = r.audio_count || 0;

    // 判定一：台本有没有到达 MOD
    if (total && ok === total) {
      lines.push(`<div class="moddiag-ok">✓ ${ok}/${total} 轮台本有效 —— 我们写的 a1ttsv2 全部传到 MOD 手上了</div>`);
    } else if (ok > 0) {
      lines.push(`<div class="moddiag-warn">! 只有 ${ok}/${total} 轮台本有效 —— 部分轮次模型没吐台本</div>`);
    } else if (total) {
      lines.push(`<div class="moddiag-bad">✗ ${total} 轮全部没有台本 —— 没传到 MOD（或被游戏洗掉了）</div>`);
    }

    // 判定二：到底出声了没有
    if (audio > 0) {
      lines.push(`<div class="moddiag-ok">✓ 已生成 ${audio} 段音频 —— NPC 确实出声了</div>`);
    } else if (ok > 0) {
      lines.push(`<div class="moddiag-warn">! 台本有效但还没看到音频 —— 可能被 CanSpeakReply 拦了或 TTS 服务没起</div>`);
    }

    // 逐轮明细
    if (total) {
      lines.push('<div class="moddiag-sec">逐轮明细</div>');
      const rows = r.rounds.map((x, i) => {
        const good = (x.effective || x.script_status || '').indexOf('valid') >= 0;
        const mark = good ? '<span class="moddiag-ok-inline">✓</span>'
                          : '<span class="moddiag-bad-inline">✗</span>';
        return `${mark} 第${i + 1}轮「${escapeHtml(x.player || '')}」` +
          `　台本=${escapeHtml(x.effective || x.script_status || '?')}` +
          (x.mapped ? `　游戏归一化后=${escapeHtml(x.mapped)}` : '');
      });
      lines.push('<div class="moddiag-rounds">' + rows.join('<br>') + '</div>');
    }

    if (audio && r.audio_files) {
      lines.push('<div class="moddiag-sec">已生成的音频</div><div class="moddiag-rounds">' +
        r.audio_files.map(f => `${escapeHtml(f.name)}　${f.kb} KB`).join('<br>') + '</div>');
    }

    if (r.bad_lines) {
      lines.push(`<div class="moddiag-sec">（有 ${r.bad_lines} 行无法解析，已跳过）</div>`);
    }

    box.innerHTML = lines.join('');
  } catch (e) {
    box.textContent = '读取失败: ' + e;
    return;
  }
  // 台本状态之外，再补一段「为什么某轮没出声」
  await modDiagAppendErrors();
}

// 静音原因：读 MOD 的恢复结果（BepInEx 日志）与 last-error
//
// 为什么需要：stage2a 里 a1ttsv2Status=valid 只说明「台本到了 MOD」，
// 不代表会出声。真正决定出不出声的是 MOD 的 recovery：
//   round 可用 → 用缓存台本 → 出声
//   round 丢失 → 只能解析已被游戏洗过的文本 → 静音
// 实测 108 轮里 2 次静音，都是 NPC 送礼那轮（round=0 round_unavailable），
// 属 MOD 内部缺陷。看不出来源就只会以为是我们没写台本。
async function modDiagAppendErrors() {
  const box = document.getElementById('moddiag-detail');
  if (!box) return;
  let r;
  try {
    r = await pywebview.api.mod_diag_errors();
  } catch (e) {
    return;
  }
  if (!r || !r.ok) return;

  const parts = [];

  // 1. 逐轮恢复结果（最近若干轮）
  const rounds = r.rounds || [];
  if (rounds.length) {
    const silent = r.silent_rounds || 0;
    if (silent) {
      parts.push(`<div class="moddiag-bad">! 最近 ${rounds.length} 轮里有 ${silent} 轮没出声（下面标红的那几行）</div>`);
    } else {
      parts.push(`<div class="moddiag-ok">✓ 最近 ${rounds.length} 轮全部出声</div>`);
    }
    const rows = rounds.map(x => {
      const ok = x.effective === 'valid';
      const mark = ok ? '<span class="moddiag-ok-inline">✓</span>'
                      : '<span class="moddiag-bad-inline">✗</span>';
      const tail = ok ? '' : `　<span class="speech-dim">${escapeHtml(x.result)}</span>`;
      return `${mark} 第 ${x.round || '?'} 轮　${escapeHtml(x.effective)}${tail}`;
    });
    parts.push('<div class="moddiag-sec">逐轮出声结果（MOD 恢复日志）</div>' +
      '<div class="moddiag-rounds">' + rows.join('<br>') + '</div>');

    // 只提示最后一条异常的解释，避免刷屏
    const bad = rounds.filter(x => x.effective !== 'valid').slice(-1)[0];
    if (bad && bad.hint) {
      parts.push(`<div class="moddiag-warn">${escapeHtml(bad.hint)}</div>`);
    }
  }

  // 2. MOD 自己记的最近一次失败
  const le = r.last_error;
  if (le && le.context) {
    parts.push('<div class="moddiag-sec">MOD 记录的最近一次失败</div>' +
      `<div class="moddiag-rounds">${escapeHtml(le.first_seen)}　${escapeHtml(le.stage)}　` +
      `${escapeHtml(le.context)}${le.occurrences > 1 ? `（${le.occurrences} 次）` : ''}` +
      `${le.recovered ? '　已恢复' : '　未恢复'}</div>`);
  }

  if (parts.length) {
    box.innerHTML += parts.join('');
  }
}

async function modDiagOpenDir() {
  try {
    const r = await pywebview.api.mod_diag_open_dir();
    if (r && !r.ok) alert('打不开: ' + (r.msg || ''));
  } catch (e) {
    alert('打不开: ' + e);
  }
}

// 页面进入：每次刷新状态（开关可能被别处改过）
async function speechOnEnter() {
  await speechLoadStatus();
  if (!speechReady) {
    await speechPreview();
  }
  // MOD 诊断跟着一起刷新（捕获状态可能变了）
  await modDiagRefresh();
}

async function speechLoadStatus() {
  const badge = document.getElementById('speech-badge');
  const stat = document.getElementById('speech-stat');
  try {
    const res = await pywebview.api.speech_style_status();
    if (!res || !res.ok) {
      if (badge) badge.textContent = '不可用';
      if (stat) stat.textContent = (res && res.msg) || '语音风格层读取失败';
      return;
    }

    // 开关回填
    const v2 = document.getElementById('speech-toggle-v2');
    const inj = document.getElementById('speech-toggle-inject');
    const auto = document.getElementById('speech-toggle-auto');
    const scr = document.getElementById('speech-toggle-script');
    if (v2) v2.checked = !!res.enabled;
    if (inj) inj.checked = !!res.inject;
    if (auto) auto.checked = !!res.auto_perf;
    if (scr) scr.checked = !!res.script_v2;

    if (badge) {
      // 徽章反映「NPC 会不会说话」这件事，比 style_v2 更贴近用户诉求
      if (res.script_v2) {
        badge.textContent = '会说话';
        badge.className = 'speech-badge on';
      } else {
        badge.textContent = '不说话';
        badge.className = 'speech-badge';
      }
    }

    // 统计
    const cards = res.cards || {};
    const profCount = res.profiles || 0;
    const perfCount = res.performances || 0;
    const archCount = res.archetypes ? Object.keys(res.archetypes).length : 0;
    if (stat) {
      stat.textContent =
        `词表：${profCount} 种角色口吻 × ${perfCount} 种表演 = ${profCount * perfCount} 种组合` +
        `　|　角色卡：${cards.cards || 0} 张，覆盖 ${archCount} 种个性原型` +
        `　|　上限 ${res.delivery_max || 80} 字` +
        `　|　高频情绪词 ${res.emotion_words || 0} 个`;
    }

    // 下拉框（只填一次）
    if (!speechReady) {
      speechFillSelect('speech-profile', res.profiles_list, 'actor_profile');
      speechFillSelect('speech-performance', res.performances_list, 'performance');
      // 表演下拉给「按角色查看」也填一份
      speechFillSelect('speech-npc-performance', res.performances_list, 'performance');
      // 默认选中两个常用的
      const ps = document.getElementById('speech-profile');
      const pf = document.getElementById('speech-performance');
      if (ps) ps.value = 'hostess';
      if (pf) pf.value = 'ask';
      const np = document.getElementById('speech-npc-performance');
      if (np) np.value = 'ask';

      speechReady = true;
      // NPC 列表随 status 一起拿到了，直接填
      speechNpcs = res.npcs || [];
      speechFillNpcSelect();
    }
  } catch (e) {
    if (badge) badge.textContent = '出错';
    if (stat) stat.textContent = '读取失败: ' + e;
  }
}

function speechFillSelect(id, items, kind) {
  const sel = document.getElementById(id);
  if (!sel) return;
  const opts = [];
  if (items && typeof items === 'object') {
    // profiles_list / performances_list 都是 {key: 描述} 的字典
    Object.keys(items).forEach(k => {
      const desc = items[k];
      opts.push(`<option value="${escapeHtml(k)}">${escapeHtml(k)} — ${escapeHtml(desc)}</option>`);
    });
  }
  sel.innerHTML = opts.join('') || '<option value="">（无）</option>';
}

// 填充 NPC 下拉。1591 张卡全塞进去会卡，默认只放前 200 个，靠搜索框缩小范围。
function speechFillNpcSelect(filter) {
  const sel = document.getElementById('speech-npc');
  if (!sel) return;
  const kw = (filter || '').trim();
  let list;
  if (kw) {
    list = speechNpcs.filter(x =>
      x.name.includes(kw) || String(x.id).includes(kw));
  } else {
    list = speechNpcs.slice(0, 200);
  }
  const head = kw
    ? `<option value="">（匹配 ${list.length} 个）</option>`
    : `<option value="">（前 200 个，可搜索）</option>`;
  sel.innerHTML = head + list.map(x =>
    `<option value="${escapeHtml(x.id)}">${escapeHtml(x.name)}（${escapeHtml(x.id)}）</option>`
  ).join('');

  // 首次进来默认选苏倾盏，方便一眼看到效果
  if (!kw) {
    const wanted = speechNpcs.find(x => x.name === '苏倾盏');
    if (wanted) sel.value = String(wanted.id);
  }
  speechLookupNpc();
}

function speechFilterNpc(kw) {
  speechFillNpcSelect(kw);
}

// 按选中的 NPC 查它的口吻
async function speechLookupNpc() {
  const sel = document.getElementById('speech-npc');
  const perf = document.getElementById('speech-npc-performance');
  const box = document.getElementById('speech-npc-result');
  if (!sel || !box) return;
  const nid = sel.value;
  if (!nid) { box.textContent = '—'; return; }
  try {
    const res = await pywebview.api.speech_style_npc(
      nid, perf ? perf.value : '');
    if (!res || !res.ok) {
      box.textContent = '查询失败: ' + ((res && res.msg) || '未知错误');
      return;
    }
    const lines = [];
    if (res.personality) {
      lines.push(`卡片个性：<b>${escapeHtml(res.personality)}</b>` +
        (res.archetype ? ` → 原型 <b>${escapeHtml(res.archetype)}</b>` : ''));
    } else {
      lines.push('<span class="speech-dim">这张卡没填「个性」字段，回落到中性基线</span>');
    }
    if (!res.matched && res.personality) {
      lines.push(`<span class="speech-dim">「${escapeHtml(res.personality)}」不在 14 原型里，回落中性</span>`);
    }
    lines.push(`口吻基线：${escapeHtml(res.profile_desc || '')}`);
    if (res.composed) {
      lines.push(`<span class="speech-dim">这一句合成：</span>${escapeHtml(res.composed)}`);
    }
    box.innerHTML = lines.join('<br>');
  } catch (e) {
    box.textContent = '查询失败: ' + e;
  }
}

// 开关状态变了：立刻保存
async function speechToggle(el) {
  if (speechBusy) return;
  speechBusy = true;
  try {
    const v2 = document.getElementById('speech-toggle-v2');
    const inj = document.getElementById('speech-toggle-inject');
    const auto = document.getElementById('speech-toggle-auto');
    const scr = document.getElementById('speech-toggle-script');
    const res = await pywebview.api.save_speech_style_flags(
      v2 ? v2.checked : null,
      inj ? inj.checked : null,
      auto ? auto.checked : null,
      scr ? scr.checked : null
    );
    if (!res || !res.ok) {
      alert('保存失败: ' + ((res && res.msg) || '未知错误'));
      await speechLoadStatus();   // 回滚显示
      return;
    }
    await speechLoadStatus();
    if (el && el.id === 'speech-toggle-script') {
      setFootLeft(res.script_v2 ? 'NPC 语音：台本已开启（模型会输出 a1ttsv2）'
                                : 'NPC 语音：台本已关闭（不会出声）');
    }
  } catch (e) {
    alert('保存失败: ' + e);
    await speechLoadStatus();
  } finally {
    speechBusy = false;
  }
}

// 试算：合成 / 规整 / 规则判定
async function speechPreview() {
  const prof = document.getElementById('speech-profile');
  const perf = document.getElementById('speech-performance');
  const raw = document.getElementById('speech-raw');
  const npcText = document.getElementById('speech-npc-text');
  const playerText = document.getElementById('speech-player-text');
  if (!prof || !perf) return;

  const boxC = document.getElementById('speech-composed');
  const boxB = document.getElementById('speech-base');
  const boxN = document.getElementById('speech-normalized');
  const boxP = document.getElementById('speech-perceived');
  const lenEl = document.getElementById('speech-len');

  try {
    const res = await pywebview.api.speech_style_preview(
      prof.value,
      perf.value,
      raw ? raw.value : '',
      npcText ? npcText.value : '',
      playerText ? playerText.value : ''
    );
    if (!res || !res.ok) {
      if (boxC) boxC.textContent = '试算失败: ' + ((res && res.msg) || '未知错误');
      return;
    }

    if (boxC) boxC.textContent = res.composed || '—';
    if (lenEl) lenEl.textContent = res.length ? `（${res.length} 字）` : '';
    if (boxB) boxB.textContent = res.base || '—';

    if (boxN) {
      boxN.textContent = res.normalized
        ? res.normalized
        : '（在上方填一段 delivery 原文，这里会显示规整结果）';
    }

    if (boxP) {
      if (res.perceived) {
        boxP.innerHTML =
          `<b>${escapeHtml(res.perceived)}</b>` +
          `　<span class="speech-dim">${escapeHtml(res.perceived_zh || '')}</span><br>` +
          `<span class="speech-dim">按此判定合成：</span>${escapeHtml(res.perceived_delivery || '')}`;
      } else {
        boxP.textContent = '（填一句 NPC 台词，这里会显示规则判定）';
      }
    }
  } catch (e) {
    if (boxC) boxC.textContent = '试算失败: ' + e;
  }
}
