// ===== 卡片列表页 =====

const PAGE_SIZE = 10;
let currentPage = 1;
let filteredCards = [];
let cardsRef = null;

function initCardsPage(cards) {
  cardsRef = cards;
  filteredCards = cards.slice();
  renderCardsPage();

  const search = document.getElementById('cards-search');
  search.addEventListener('input', applyCardsFilter);
}

function applyCardsFilter() {
  const kw = document.getElementById('cards-search').value.trim();
  if (!kw) {
    filteredCards = cardsRef.slice();
  } else {
    filteredCards = cardsRef.filter(c =>
      c.name.includes(kw) || String(c.id).includes(kw)
    );
  }
  currentPage = 1;
  renderCardsPage();
}

function renderCardsPage() {
  const total = filteredCards.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (currentPage > totalPages) currentPage = totalPages;

  const start = (currentPage - 1) * PAGE_SIZE;
  const pageItems = filteredCards.slice(start, start + PAGE_SIZE);

  document.getElementById('cards-count').textContent = `${total} 张卡片`;
  document.getElementById('pager-text').textContent = `${currentPage} / ${totalPages}`;
  document.getElementById('btn-prev').disabled = currentPage <= 1;
  document.getElementById('btn-next').disabled = currentPage >= totalPages;

  const grid = document.getElementById('card-grid');
  grid.innerHTML = '';

  for (const card of pageItems) {
    const initial = (card.name || '?')[0];

    // 头像部分：有头像就显示图片，没有就显示字
    let portraitHTML;
    if (card.portrait_file) {
      portraitHTML = `
        <img class="portrait-img" src="portraits/${card.portrait_file}" alt="${escapeHtml(card.name)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
        <div class="portrait-fallback" style="display:none;">${escapeHtml(initial)}</div>
      `;
    } else {
      portraitHTML = `<div class="portrait-fallback">${escapeHtml(initial)}</div>`;
    }

    const div = document.createElement('div');
    div.className = 'npc-card';
    div.onclick = () => openCard(card.filename);
    div.innerHTML = `
      <div class="portrait">
        ${portraitHTML}
        <div class="no-img-label">ID ${card.id}</div>
      </div>
      <div class="info">
        <div class="name">
          <span>${escapeHtml(card.name)}</span>
        </div>
        <div class="title">${escapeHtml(card.filename)}</div>
      </div>
    `;
    grid.appendChild(div);
  }
}

function prevPage() {
  if (currentPage > 1) { currentPage--; renderCardsPage(); }
}

function nextPage() {
  const totalPages = Math.max(1, Math.ceil(filteredCards.length / PAGE_SIZE));
  if (currentPage < totalPages) { currentPage++; renderCardsPage(); }
}

function backToCards() {
  document.getElementById('cards-page').style.display = 'flex';
  document.getElementById('edit-page').style.display = 'none';
  showFooterButtons(false, false);
  setFootLeft('就绪');
  renderCardsPage();
}

function openCard(filename) {
  if (typeof showCardEdit === 'function') {
    showCardEdit(filename);
  }
}

async function openCreateDialog() {
  const idStr = prompt('请输入新卡片的 NPC ID（数字）：');
  if (!idStr) return;
  const npcId = parseInt(idStr);
  if (isNaN(npcId)) {
    alert('ID 必须是数字');
    return;
  }
  const name = prompt('请输入新卡片的名字：');
  if (!name) return;

  const res = await pywebview.api.create_card(npcId, name);
  if (!res.ok) {
    alert(res.msg || '创建失败');
    return;
  }
  allCards = await pywebview.api.get_cards_list();
  cardsRef = allCards;
  filteredCards = allCards.slice();
  currentPage = 1;
  renderCardsPage();
  document.getElementById('header-status').textContent = `共 ${allCards.length} 张卡片`;
  alert(`已创建：${res.filename}`);
}