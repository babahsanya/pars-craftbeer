// Instant-поиск на главной странице.
// При вводе ≥2 символов карточки пива появляются прямо на странице (через AJAX),
// без перехода на /search. Пустая строка — возвращаем дефолтный контент.

(function () {
  const input = document.getElementById("heroSearchInput");
  const resultsBox = document.getElementById("heroSearchResults");
  const defaultContent = document.getElementById("defaultHomeContent");
  const hint = document.getElementById("heroSearchHint");
  if (!input || !resultsBox || !defaultContent) return;

  let timer = null;
  let lastQuery = "";
  let reqId = 0;

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  function renderCard(it) {
    const img = it.image
      ? `<img src="${it.image}" alt="${escapeHtml(it.name)}" loading="lazy">`
      : `<span class="no-img">🍺</span>`;
    const abv = it.abv ? `<span class="pill abv">${it.abv}%</span>` : "";
    const vol = it.volume ? `<span class="pill">${it.volume} мл</span>` : "";
    const price = it.price ? `<span class="pill price">${escapeHtml(it.price)}</span>` : "";
    const styleLine = it.style ? `<div class="card-style">${escapeHtml(it.style)}</div>` : "";
    return `<a class="beer-card" href="${it.url}">
      <div class="card-img">${img}</div>
      <div class="card-body">
        <div class="card-name">${escapeHtml(it.name)}</div>
        <div class="card-producer">${escapeHtml(it.producer || "")}</div>
        ${styleLine}
        <div class="card-meta">${abv}${vol}${price}</div>
      </div>
    </a>`;
  }

  function showResults(data) {
    if (!data.results || data.results.length === 0) {
      // Если есть подсказка «может вы имели в виду» — покажем её
      if (data.correction) {
        resultsBox.innerHTML =
          `<div class="empty-state">` +
          `<div class="big">🔍</div>` +
          `<p>Ничего не найдено.</p>` +
          `<p style="margin-top: 12px;">Возможно, вы имели в виду:` +
          `<a class="btn btn-primary" style="margin-left:8px;" href="/search?q=${encodeURIComponent(data.correction)}">${escapeHtml(data.correction)}</a></p>` +
          `</div>`;
      } else {
        resultsBox.innerHTML = `<div class="empty-state"><div class="big">🔍</div><p>Ничего не найдено</p></div>`;
      }
      return;
    }
    let html = "";
    // Блок «может вы имели в виду» сверху результатов
    if (data.correction) {
      html +=
        `<div class="correction-block" style="grid-column: 1 / -1;">` +
        `<span class="corr-icon">💡</span>` +
        `<span class="corr-text">Возможно, вы имели в виду: ` +
        `<a href="/search?q=${encodeURIComponent(data.correction)}"><strong>${escapeHtml(data.correction)}</strong></a>?</span>` +
        `</div>`;
    }
    html += data.results.map(renderCard).join("");
    resultsBox.innerHTML = html;
  }

  function resetToDefault() {
    resultsBox.innerHTML = "";
    defaultContent.style.display = "";
    if (hint) hint.textContent = "Начните вводить — результаты появятся здесь";
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (q === lastQuery) return;
    lastQuery = q;

    if (q.length < 2) {
      resetToDefault();
      return;
    }

    defaultContent.style.display = "none";
    if (hint) hint.textContent = "Поиск...";

    clearTimeout(timer);
    timer = setTimeout(() => {
      const myReqId = ++reqId;
      fetch("/api/search?q=" + encodeURIComponent(q))
        .then((r) => r.json())
        .then((data) => {
          // защита от устаревшего ответа
          if (myReqId !== reqId) return;
          if (hint) hint.textContent = `Найдено: ${data.count}`;
          showResults(data);
        })
        .catch(() => {
          if (myReqId !== reqId) return;
          if (hint) hint.textContent = "Ошибка поиска";
        });
    }, 250);
  });

  // Enter → переход на полную страницу поиска
  const form = document.getElementById("heroSearchForm");
  if (form) {
    form.addEventListener("submit", (e) => {
      const q = input.value.trim();
      if (q.length >= 2) {
        e.preventDefault();
        window.location.href = "/search?q=" + encodeURIComponent(q);
      } else {
        e.preventDefault();
      }
    });
  }
})();
