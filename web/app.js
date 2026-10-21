// ===== 入口 =====

let allCards = [];

async function init() {
  document.querySelectorAll('.nav-item').forEach(el => {
    el.onclick = () => switchPage(el.dataset.page);
  });

  try {
    allCards = await pywebview.api.get_cards_list();
    document.getElementById('header-status').textContent =
      `共 ${allCards.length} 张卡片`;
  } catch (e) {
    document.getElementById('header-status').textContent = '加载失败: ' + e;
    console.error(e);
    return;
  }

  const steps = [
    ['initCardsPage', initCardsPage],
    ['initEditPage', initEditPage],
    ['initServicesPage', initServicesPage],
    ['initLogsPage', initLogsPage],
    ['initPromptsPage', initPromptsPage],
    ['initMemoryPage', initMemoryPage],
  ];

  // 加载游戏目录显示
  setTimeout(loadGameDirDisplay, 100);

  for (const [name, fn] of steps) {
    try {
      if (typeof fn === 'function') {
        fn(allCards);
      } else {
        console.error(`函数 ${name} 未定义！`);
        document.getElementById('header-status').textContent = `函数 ${name} 未定义`;
      }
    } catch (e) {
      console.error(`${name} 失败:`, e);
      document.getElementById('header-status').textContent = `${name} 失败: ${e.message}`;
    }
  }
}

function switchPage(name) {
  
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === name);
  });
  document.querySelectorAll('.page').forEach(el => {
    el.classList.toggle('active', el.id === 'page-' + name);
  });

  // 切到非"人物卡"页时，隐藏底部卡片按钮
  if (name !== 'cards') {
    showFooterButtons(false, false);
    setFootLeft('就绪');
  }

  if (name === 'cards' && typeof backToCards === 'function') {
    if (document.getElementById('edit-page').style.display === 'flex') return;
    backToCards();
  }
  
  if (name === 'services' && typeof loadGameDirDisplay === 'function') {
    loadGameDirDisplay();
  }
}

  if (name === 'memory' && typeof initMemoryPage === 'function') {
    initMemoryPage();
  }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])
  );
}

function showFooterButtons(showSave, showDelete) {
  document.getElementById('btn-save').style.display = showSave ? 'inline-block' : 'none';
  document.getElementById('btn-delete').style.display = showDelete ? 'inline-block' : 'none';
}

function setFootLeft(text) {
  document.getElementById('foot-left').textContent = text;
}

window.addEventListener('pywebviewready', init);
setTimeout(() => {
  const s = document.getElementById('header-status');
  if (s && s.textContent === '加载中...') init();
}, 800);

// ==================== 游戏目录管理 ====================

async function loadGameDirDisplay() {
  const input = document.getElementById('game-dir-display');
  if (!input) return;
  try {
    const res = await pywebview.api.check_game_dir();
    if (res.ok) {
      input.value = res.game_dir || '';
    } else {
      input.value = '（未设置：' + (res.msg || '') + '）';
    }
  } catch (e) {
    input.value = '读取失败';
  }
}

async function onChangeGameDir() {
  if (!confirm('修改游戏目录会重启 core_proxy，确定继续？')) return;
  const input = document.getElementById('game-dir-display');
  const res = await pywebview.api.pick_and_save_game_dir();
  if (!res.ok) {
    if (res.msg !== '未选择') alert('✗ ' + (res.msg || '失败'));
    return;
  }
  if (input) input.value = res.game_dir;
  // 刷新卡片列表（因为卡片来自新目录）
  setTimeout(async () => {
    try {
      const cards = await pywebview.api.get_cards_list();
      if (typeof allCards !== 'undefined') allCards = cards;
      if (typeof cardsRef !== 'undefined') cardsRef = cards;
      document.getElementById('header-status').textContent = `共 ${cards.length} 张卡片`;
    } catch (e) {}
  }, 1200);
  alert('✓ 已保存，core_proxy 已重启');
}