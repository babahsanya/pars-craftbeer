// Избранное и кастомные подборки через localStorage.
// Кнопки ♥ на карточках пива, страница /favorites рендерит содержимое.

(function () {
  const FAV_KEY = "beer_favorites";
  const LISTS_KEY = "beer_lists";

  // ===================== Хранилище =====================
  function getFavorites() {
    try {
      const v = JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
      return Array.isArray(v) ? v : [];
    } catch (e) {
      return [];
    }
  }
  function setFavorites(ids) {
    try { localStorage.setItem(FAV_KEY, JSON.stringify(ids)); } catch (e) {}
    updateHeaderCounter();
  }
  function isFavorite(id) {
    return getFavorites().indexOf(id) !== -1;
  }
  function toggleFavorite(id) {
    const fav = getFavorites();
    const idx = fav.indexOf(id);
    if (idx === -1) {
      fav.push(id);
    } else {
      fav.splice(idx, 1);
    }
    setFavorites(fav);
    return idx === -1; // true если добавили
  }

  function getLists() {
    try {
      const v = JSON.parse(localStorage.getItem(LISTS_KEY) || "{}");
      return typeof v === "object" && v !== null ? v : {};
    } catch (e) {
      return {};
    }
  }
  function setLists(lists) {
    try { localStorage.setItem(LISTS_KEY, JSON.stringify(lists)); } catch (e) {}
    updateHeaderCounter();
  }

  // ===================== Счётчик в шапке =====================
  function updateHeaderCounter() {
    const el = document.getElementById("favHeaderCount");
    if (!el) return;
    const n = getFavorites().length;
    const listsN = Object.keys(getLists()).length;
    el.textContent = n + listsN;
    el.style.display = n + listsN > 0 ? "" : "none";
  }

  // ===================== Кнопки ♥ на карточках =====================
  function initFavoriteButtons() {
    document.querySelectorAll(".fav-btn[data-beer-id]").forEach(function (btn) {
      const id = parseInt(btn.dataset.beerId, 10);
      if (isFavorite(id)) btn.classList.add("active");
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        const added = toggleFavorite(id);
        btn.classList.toggle("active", added);
        // если на странице избранного — перерисуем
        if (document.getElementById("favGrid")) renderFavoritesPage();
      });
    });
  }

  // ===================== Страница /favorites =====================
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderCard(b) {
    const img = b.image
      ? '<img src="' + b.image + '" alt="' + escapeHtml(b.name) + '" loading="lazy">'
      : '<span class="no-img"><span class="no-img-letter">' + escapeHtml((b.name || "?")[0]) + "</span></span>";
    const abv = b.abv ? '<span class="pill abv">' + b.abv + "%</span>" : "";
    const vol = b.volume ? '<span class="pill">' + b.volume + " мл</span>" : "";
    const price = b.price ? '<span class="pill price">' + escapeHtml(b.price) + "</span>" : "";
    const styleLine = b.style ? '<div class="card-style">' + escapeHtml(b.style) + "</div>" : "";
    return (
      '<a class="beer-card" href="' + b.url + '">' +
      '<div class="card-img">' + img + "</div>" +
      '<div class="card-body">' +
      '<div class="card-name">' + escapeHtml(b.name) + "</div>" +
      '<div class="card-producer">' + escapeHtml(b.producer || "") + "</div>" +
      styleLine +
      '<div class="card-meta">' + abv + vol + price + "</div>" +
      "</div></a>"
    );
  }

  function renderFavoritesPage() {
    const grid = document.getElementById("favGrid");
    const empty = document.getElementById("favEmpty");
    const subtitle = document.getElementById("favSubtitle");
    const countEl = document.getElementById("favCount");
    if (!grid) return;

    const ids = getFavorites();
    if (countEl) countEl.textContent = ids.length;
    if (subtitle) subtitle.textContent = ids.length + " позиций в избранном";

    if (ids.length === 0) {
      grid.innerHTML = "";
      if (empty) empty.style.display = "";
      return;
    }
    if (empty) empty.style.display = "none";
    grid.innerHTML = '<div class="empty-state"><p>Загрузка...</p></div>';

    fetch("/api/beers?ids=" + ids.join(","))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.results || data.results.length === 0) {
          grid.innerHTML = '<div class="empty-state"><p>Позиции не найдены (возможно удалены из базы).</p></div>';
          return;
        }
        grid.innerHTML = data.results.map(renderCard).join("");
        initFavoriteButtons();
      })
      .catch(function () {
        grid.innerHTML = '<div class="empty-state"><p>Ошибка загрузки.</p></div>';
      });
  }

  function renderListsPage() {
    const container = document.getElementById("listsContainer");
    const empty = document.getElementById("listsEmpty");
    const countEl = document.getElementById("listsCount");
    if (!container) return;

    const lists = getLists();
    const names = Object.keys(lists);
    if (countEl) countEl.textContent = names.length;
    if (names.length === 0) {
      container.innerHTML = "";
      if (empty) empty.style.display = "";
      return;
    }
    if (empty) empty.style.display = "none";

    container.innerHTML = names
      .map(function (name) {
        const ids = lists[name] || [];
        return (
          '<div class="list-card">' +
          '<div class="lc-header">' +
          '<span class="lc-name">📋 ' + escapeHtml(name) + '</span>' +
          '<span class="lc-count">' + ids.length + ' позиций</span>' +
          '<button class="lc-del" data-list="' + escapeHtml(name) + '" title="Удалить подборку">✕</button>' +
          "</div>" +
          '<div class="lc-body" data-list-body="' + escapeHtml(name) + '">' +
          (ids.length ? "<p>Загрузка...</p>" : "<p class='text-muted'>Подборка пуста</p>") +
          "</div></div>"
        );
      })
      .join("");

    // загрузка карточек для каждой подборки
    names.forEach(function (name) {
      const ids = lists[name] || [];
      const body = container.querySelector('[data-list-body="' + name + '"]');
      if (!body || !ids.length) return;
      fetch("/api/beers?ids=" + ids.join(","))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.results && data.results.length) {
            body.innerHTML = '<div class="grid grid-tight">' + data.results.map(renderCard).join("") + "</div>";
          } else {
            body.innerHTML = "<p class='text-muted'>Пусто</p>";
          }
        });
    });

    // удаление подборки
    container.querySelectorAll(".lc-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const name = btn.dataset.list;
        if (!confirm("Удалить подборку «" + name + "»?")) return;
        const lists = getLists();
        delete lists[name];
        setLists(lists);
        renderListsPage();
      });
    });
  }

  function initCreateListButton() {
    const btn = document.getElementById("createListBtn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      const name = prompt("Название подборки:");
      if (!name) return;
      const lists = getLists();
      if (lists[name]) {
        alert("Подборка с таким именем уже существует");
        return;
      }
      lists[name] = [];
      setLists(lists);
      renderListsPage();
    });
  }

  function initTabs() {
    const tabs = document.querySelectorAll(".fav-tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        const target = tab.dataset.tab;
        document.getElementById("panelFavorites").style.display = target === "favorites" ? "" : "none";
        document.getElementById("panelLists").style.display = target === "lists" ? "" : "none";
      });
    });
  }

  // ===================== Глобальный API для карточек =====================
  // экспортируем для использования из других скриптов (beer_detail)
  window.beerFavorites = {
    getFavorites: getFavorites,
    getLists: getLists,
    setLists: setLists,
    isFavorite: isFavorite,
    toggleFavorite: toggleFavorite,
  };

  // ===================== Инициализация =====================
  document.addEventListener("DOMContentLoaded", function () {
    updateHeaderCounter();
    initFavoriteButtons();
    initTabs();
    initCreateListButton();
    // если на странице избранного — рендерим
    if (document.getElementById("favGrid")) {
      renderFavoritesPage();
      renderListsPage();
    }
  });

  // повторная инициализация кнопок после AJAX-загрузки карточек
  document.addEventListener("cardsUpdated", initFavoriteButtons);
})();
