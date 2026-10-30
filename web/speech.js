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
// ===== 分类标签 =====
//
// 语音页原本一屏 6 张卡片，找一样东西要滚半天。按用途分四组：
//   音频 = 语速后处理（以后 EQ 也放这）
//   朗读 = 让 NPC 说话的总开关
//   口吻 = 按角色/原型看 delivery 怎么拼
//   诊断 = MOD 捕获记录 + 检验
let speechTab = 'audio';

function switchSpeechTab(pane) {
  speechTab = pane;
  document.querySelectorAll('[data-speechtab]').forEach((el) => {
    el.classList.toggle('active', el.dataset.speechtab === pane);
  });
  document.querySelectorAll('#page-speech [data-speechpane]').forEach((el) => {
    el.style.display = (el.dataset.speechpane === pane) ? '' : 'none';
  });
}

async function speechOnEnter() {
  switchSpeechTab(speechTab);     // 先按当前分类把无关卡片收起来
  await speechLoadStatus();
  if (!speechReady) {
    await speechPreview();
  }
  // 音频后处理参数（语速）
  await voiceRefresh();
  await ttsPostLoad();
  // MOD 诊断跟着一起刷新（捕获状态可能变了）
  await modDiagRefresh();
}

// ===== 音频后处理：语速 / 音高 / EQ =====
//
// 三个参数都在代理侧对音频做处理（core_proxy 的 /v1/audio/speech）：
//   语速 = WSOLA 变速不变调
//   音高 = WSOLA 拉伸 + 重采样（净效果音高变、时长不变）
//   EQ  = 峰值滤波，抬/压某一段
// 为什么不在模型侧调：Breeze 的输出时长由文本决定，instruct 对语速是噪声
// （三组参考音实测，同词换参考音结论就反）。
function _ttspostEls() {
  return {
    speed: document.getElementById('ttspost-speed'),
    pitch: document.getElementById('ttspost-pitch'),
    eqfreq: document.getElementById('ttspost-eqfreq'),
    eqgain: document.getElementById('ttspost-eqgain'),
    eqq: document.getElementById('ttspost-eqq'),
    volume: document.getElementById('ttspost-volume'),
    enabled: document.getElementById('ttspost-enabled'),
    speedVal: document.getElementById('ttspost-speed-val'),
    pitchVal: document.getElementById('ttspost-pitch-val'),
    eqfreqVal: document.getElementById('ttspost-eqfreq-val'),
    eqgainVal: document.getElementById('ttspost-eqgain-val'),
    eqqVal: document.getElementById('ttspost-eqq-val'),
    volVal: document.getElementById('ttspost-volume-val'),
    badge: document.getElementById('ttspost-badge'),
  };
}

async function ttsPostLoad() {
  const el = _ttspostEls();
  try {
    const res = await pywebview.api.tts_post_status();
    if (!res || !res.ok) {
      if (el.badge) el.badge.textContent = '不可用';
      return;
    }
    if (el.speed) el.speed.value = res.speed;
    if (el.pitch) el.pitch.value = res.pitch;
    if (el.eqfreq) el.eqfreq.value = res.eq_freq;
    if (el.eqgain) el.eqgain.value = res.eq_gain;
    if (el.eqq) el.eqq.value = res.eq_q;
    if (el.volume) el.volume.value = res.volume;
    if (el.enabled) el.enabled.checked = res.enabled !== false;
    ttsPostPreview();
  } catch (e) {
    if (el.badge) el.badge.textContent = '读取失败';
  }
}

function ttsPostPreview() {
  const el = _ttspostEls();
  const sp = el.speed ? Number(el.speed.value) : 1;
  const pt = el.pitch ? Number(el.pitch.value) : 0;
  const ef = el.eqfreq ? Number(el.eqfreq.value) : 2000;
  const eg = el.eqgain ? Number(el.eqgain.value) : 0;
  const eq = el.eqq ? Number(el.eqq.value) : 1;
  const vol = el.volume ? Number(el.volume.value) : 0;

  if (el.speedVal) el.speedVal.textContent = sp.toFixed(2) + 'x';
  if (el.pitchVal) el.pitchVal.textContent = (pt > 0 ? '+' : '') + pt.toFixed(1) + ' 半音';
  if (el.eqfreqVal) el.eqfreqVal.textContent = Math.round(ef) + ' Hz';
  if (el.eqgainVal) el.eqgainVal.textContent = (eg > 0 ? '+' : '') + eg.toFixed(1) + ' dB';
  if (el.eqqVal) {
    el.eqqVal.textContent = eq.toFixed(1) + '（' + (eq >= 2 ? '窄' : eq >= 1 ? '中等' : '宽') + '）';
  }
  if (el.volVal) el.volVal.textContent = (vol > 0 ? '+' : '') + vol.toFixed(1) + ' dB';

  const sh = document.getElementById('ttspost-speed-hint');
  if (sh) {
    sh.textContent = sp === 1 ? '1.0 = 原速。'
      : sp <= 1.3 ? `${sp.toFixed(2)}x：这个区间几乎无损，推荐。`
      : sp <= 1.6 ? `${sp.toFixed(2)}x：开始有轻微拼接感。`
      : `${sp.toFixed(2)}x：拼接点很密，可能出现金属音。`;
  }
  const ph = document.getElementById('ttspost-pitch-hint');
  if (ph) {
    ph.textContent = pt === 0 ? '0 = 不动。'
      : Math.abs(pt) <= 2 ? `${pt > 0 ? '+' : ''}${pt.toFixed(1)} 半音：较自然。`
      : `${pt > 0 ? '+' : ''}${pt.toFixed(1)} 半音：幅度较大，可能听出处理痕迹。`;
  }
  const eh = document.getElementById('ttspost-eq-hint');
  if (eh) {
    eh.textContent = eg === 0 ? '增益 0 = 不做 EQ。'
      : Math.round(ef) + 'Hz ' + (eg > 0 ? '提' : '压') + Math.abs(eg).toFixed(1) + 'dB，'
        + 'Q=' + eq.toFixed(1)
        + '（影响' + (eq >= 2 ? '很窄' : eq >= 1 ? '中等' : '很宽') + '的一段）。'
        + (ef >= 1500 && ef <= 4000 && eq >= 0.8 ? ' 这一段是人声清晰度区。' : '');
  }

  const dirty = sp !== 1 || pt !== 0 || eg !== 0 || vol !== 0;
  const on = el.enabled ? el.enabled.checked : true;
  if (el.badge) {
    el.badge.textContent = !on ? '已关闭' : (dirty ? '已调整' : '原样');
    el.badge.className = 'speech-badge' + (on && dirty ? ' on' : '');
  }
}

async function ttsPostSave() {
  const el = _ttspostEls();
  const btn = document.getElementById('ttspost-save');
  if (!el.speed) return;
  if (btn) { btn.disabled = true; btn.textContent = '保存中'; }
  try {
    const res = await pywebview.api.save_tts_post(
      Number(el.speed.value), Number(el.pitch.value),
      Number(el.eqfreq.value), Number(el.eqgain.value),
      Number(el.eqq.value), Number(el.volume.value),
      el.enabled ? el.enabled.checked : true);
    if (res && res.ok) {
      if (btn) btn.textContent = '已保存';
      ttsPostPreview();
    } else {
      if (btn) btn.textContent = '保存失败';
    }
  } catch (e) {
    if (btn) btn.textContent = '保存失败';
  }
  setTimeout(() => { if (btn) { btn.disabled = false; btn.textContent = '保存'; } }, 1200);
}

async function ttsPostToggle() {
  const el = _ttspostEls();
  const on = el.enabled ? el.enabled.checked : true;
  try {
    await pywebview.api.save_tts_post(null, null, null, null, null, null, on);
  } catch (e) { /* 存盘失败不回滚勾选，下次保存会再写一次 */ }
  ttsPostPreview();
}

function ttsPostReset() {
  const el = _ttspostEls();
  if (el.speed) el.speed.value = 1;
  if (el.pitch) el.pitch.value = 0;
  if (el.eqgain) el.eqgain.value = 0;
  if (el.volume) el.volume.value = 0;
  ttsPostPreview();
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

// ===== 角色音色（参考音）=====
//
// 当前角色从 core_proxy 来：对话一次后 /_status 里带 npc；代理重启后
// 读 proxy/last_npc.json（代理每轮写过）。都没有就提示"先聊一句"。
// 换音色 = 覆盖 references/npcs/<角色ID>.wav，覆盖前 voice_refs 会先把原件
// 备份到 references/_voice_backup/，所以「恢复角色默认」随时能退回。
let voiceState = { npc: null, pending: '', applied: '', timer: 0, lastRel: '', lastSrc: '' };

function voiceMsg(text, ok) {
  const el = document.getElementById('voice-msg');
  if (!el) return;
  el.textContent = text || '';
  el.style.opacity = text ? '1' : '0.66';
  el.style.color = ok === false ? '#d98a7a' : '';
}

async function voiceRefresh() {
  const badge = document.getElementById('voice-badge');
  const nameEl = document.getElementById('voice-npc-name');
  const subEl = document.getElementById('voice-npc-sub');
  const pathEl = document.getElementById('voice-applied-path');
  const descEl = document.getElementById('voice-applied-desc');
  try {
    const res = await pywebview.api.voice_status();
    if (!res || !res.ok) {
      if (badge) { badge.textContent = '不可用'; badge.className = 'speech-badge'; }
      if (nameEl) nameEl.textContent = '—';
      if (subEl) subEl.textContent = (res && res.msg) || '读不到参考音目录';
      return;
    }
    voiceState.npc = res.npc || null;
    const npc = res.npc;
    if (badge) {
      badge.textContent = npc ? '已识别' : '待对话';
      badge.className = 'speech-badge' + (npc ? ' on' : '');
    }
    if (nameEl) {
      nameEl.textContent = npc ? npc.name : '还没有角色';
    }
    if (subEl) {
      if (!npc) {
        subEl.textContent = '和任意角色对话一次，这里就会自动显示她/他';
      } else if (!npc.id) {
        subEl.textContent = `角色卡里没有「${npc.name}」，找不到它的 ID，没法换音色`;
      } else {
        subEl.textContent = (res.source === 'proxy' ? '本次对话' : '上次对话')
          + ` · ID ${npc.id}` + (res.applied ? ` · ${res.applied.desc}` : '');
      }
    }
    const ap = res.applied;
    voiceState.applied = (ap && ap.rel) || '';
    if (pathEl) pathEl.textContent = voiceState.applied || '—';
    if (descEl) {
      if (!ap || !ap.exists) {
        descEl.textContent = '';
      } else if (ap.backup) {
        descEl.textContent = (ap.source ? `换自 ${ap.source} · ` : '')
          + `原版已备份（${ap.backup_desc || '已备份'}），可点「恢复角色默认」退回`;
      } else {
        descEl.textContent = '这是角色默认参考音，没有改动过';
      }
    }
    if (!voiceState.pending) {
      voiceState.pending = (ap && (ap.source || ap.rel)) || '';
    }
    voiceRenderPending();
    await voiceLoadList();
  } catch (e) {
    if (badge) badge.textContent = '读取失败';
    voiceMsg('读取失败：' + e, false);
  }
}

async function voiceLoadList() {
  const box = document.getElementById('voice-list');
  if (!box) return;
  const q = (document.getElementById('voice-filter') || {}).value || '';
  try {
    const res = await pywebview.api.voice_list(q);
    if (!res || !res.ok) {
      box.innerHTML = `<div class="voice-list-empty">${escapeHtml((res && res.msg) || '读取失败')}</div>`;
      return;
    }
    voiceState.items = res.items || [];
    if (!voiceState.items.length) {
      box.innerHTML = '<div class="voice-list-empty">没有匹配的音色</div>';
      return;
    }
    const show = voiceState.items.slice(0, 120);
    box.innerHTML = show.map((it) => {
      const on = it.rel === voiceState.applied ? ' on' : '';
      const sel = it.rel === voiceState.pending ? ' sel' : '';
      const tag = it.kind === 'imported' ? ' <span class="voice-tag">导入</span>'
        : it.kind === 'root' ? ' <span class="voice-tag">通用</span>' : '';
      return `<div class="voice-row${on}${sel}" data-rel="${escapeHtml(it.rel)}"`
        + ` onclick="voiceSelect(this)">`
        + `<span class="voice-row-name">${escapeHtml(it.name || it.rel)}</span>`
        + `<span class="voice-row-path">${escapeHtml(it.rel)}${tag}</span></div>`;
    }).join('')
      + (res.total > show.length
        ? `<div class="voice-list-empty">还有 ${res.total - show.length} 项，输入关键词缩小范围</div>`
        : '');
    const cur = voiceState.items.find((i) => i.rel === voiceState.pending);
    const tf = document.getElementById('voice-reftext');
    if (tf && !tf.value.trim() && cur && cur.ref_text) tf.value = cur.ref_text;
  } catch (e) {
    box.innerHTML = `<div class="voice-list-empty">读取失败：${escapeHtml(String(e))}</div>`;
  }
}

function voiceFilterInput() {
  clearTimeout(voiceState.timer);
  voiceState.timer = setTimeout(voiceLoadList, 260);
}

function voiceSelect(row) {
  if (!row) return;
  voiceState.pending = row.dataset.rel || '';
  voiceRenderPending();
  const box = document.getElementById('voice-list');
  if (box) {
    box.querySelectorAll('.voice-row.sel').forEach((r) => r.classList.remove('sel'));
    row.classList.add('sel');
  }
  const item = voiceState.items.find((i) => i.rel === voiceState.pending);
  const txt = document.getElementById('voice-reftext');
  if (txt && item && item.ref_text) txt.value = item.ref_text;
}

function voiceRenderPending() {
  const el = document.getElementById('voice-pending-name');
  if (el) el.textContent = voiceState.pending || '—';
}

function voiceTarget() {
  // 有当前角色就用它；没有（没对话过 / 没开游戏 MOD）就用列表里选中的那一行。
  // 只用列表行的 npc_id：导入/root 类参考音没有 npc_id，不能当目标。
  if (voiceState.npc && voiceState.npc.id) {
    return { id: voiceState.npc.id, name: voiceState.npc.name, from: 'current' };
  }
  const item = voiceState.items.find((i) => i.rel === voiceState.pending);
  if (item && item.npc_id) {
    return { id: item.npc_id, name: item.name || ('ID ' + item.npc_id), from: 'picked' };
  }
  return null;
}

function voicePlaySrc(url) {
  const audio = document.getElementById('voice-audio');
  if (!audio) return;
  voiceState.lastSrc = url;
  audio.src = url;
  audio.play().catch(() => { voiceMsg('浏览器拦了自动播放，再点一次', false); });
}

async function voicePlay() {
  const rel = voiceState.pending || voiceState.applied;
  if (!rel) { voiceMsg('先选一个参考音', false); return; }
  // 同一个文件再点：直接播已经取到的 data URL，不走异步，
  // 免得 WebView 把「异步之后的 play()」当成没有用户手势而拦掉。
  if (voiceState.lastSrc && voiceState.lastRel === rel) {
    voicePlaySrc(voiceState.lastSrc);
    return;
  }
  voiceState.lastRel = rel;
  voiceMsg('读取中…');
  const res = await pywebview.api.voice_preview(rel);
  if (!res || !res.ok) { voiceMsg((res && res.msg) || '读不到文件', false); return; }
  voicePlaySrc(res.url);
  voiceMsg('正在播放参考音本身：' + rel, true);
}

async function voiceSay() {
  // 已停用（2026-10-30）：脱离 MOD 的独立语音方案暂不可行，先不提供引擎试听。
  // 恢复：删掉下面两行 return 即可，其余逻辑没动。
  voiceMsg('引擎试听已停用（等新的语音方案）', false);
  return;
  const rel = voiceState.pending || voiceState.applied;
  if (!rel) { voiceMsg('先选一个参考音', false); return; }
  const txtEl = document.getElementById('voice-reftext');
  const refText = txtEl ? txtEl.value.trim() : '';
  voiceMsg('让引擎念一句，大约等 5~15 秒…');
  const res = await pywebview.api.voice_say(rel, '', refText);
  if (!res || !res.ok) { voiceMsg((res && res.msg) || '引擎没返回', false); return; }
  voicePlaySrc(res.url);
  voiceMsg(`引擎试听完成（「${res.text}」），这就是进游戏后的音色`, true);
}

async function voiceApply() {
  const npc = voiceTarget();
  if (!npc) {
    voiceMsg('先对话一次，或在列表里选中某个角色（要选 npcs/ 开头的）', false);
    return;
  }
  const rel = voiceState.pending;
  if (!rel) { voiceMsg('先选一个参考音', false); return; }
  const res = await pywebview.api.voice_apply(npc.id, rel);
  if (!res || !res.ok) { voiceMsg((res && res.msg) || '应用失败', false); return; }
  voiceMsg(`${npc.name}${npc.from === 'picked' ? '（列表选中）' : ''} → ${res.to}　${res.msg}`, true);
  await voiceRefresh();
}

async function voiceRestore() {
  const npc = voiceTarget();
  if (!npc) { voiceMsg('先对话一次，或在列表里选中某个角色', false); return; }
  const res = await pywebview.api.voice_restore(npc.id);
  if (!res || !res.ok) { voiceMsg((res && res.msg) || '恢复失败', false); return; }
  voiceMsg(`${npc.name}　${res.msg}`, true);
  voiceState.pending = '';
  await voiceRefresh();
}

async function voiceImport() {
  const npc = voiceTarget();
  if (!npc) { voiceMsg('先对话一次，或在列表里选中某个角色，才知道导入给谁', false); return; }
  const res = await pywebview.api.voice_import(npc.id);
  if (!res || !res.ok) {
    if (res && res.msg === '未选择') return;
    voiceMsg((res && res.msg) || '导入失败', false);
    return;
  }
  voiceState.pending = res.rel;
  voiceRenderPending();
  await voiceLoadList();
  voiceMsg(`已导入 ${res.rel}（${res.desc}）— ${res.msg}`, true);
}

