// Глобальные AJAX-подсказки для строки поиска
(function () {
  const input = document.getElementById("globalSearch");
  const box = document.getElementById("suggestBox");
  if (!input || !box) return;

  let timer = null;
  let lastQuery = "";

  function render(items, correction) {
    if ((!items || items.length === 0) && !correction) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    let html = "";
    // Блок «может вы имели в виду» сверху подсказок
    if (correction) {
      html +=
        `<a class="suggest-item suggest-correction" href="/search?q=${encodeURIComponent(correction)}">` +
        `<div class="si-main"><div class="si-name">💡 ${escapeHtml(correction)}</div>` +
        `<div class="si-meta">возможно, вы имели в виду</div></div></a>`;
    }
    if (items && items.length > 0) {
      html += items
        .map((it) => {
          const img = it.local_image
            ? `<img src="${it.local_image}" alt="">`
            : `<img src="" alt="" style="opacity:0">`;
          const meta = [it.producer, it.style, it.abv ? it.abv + "% ABV" : ""]
            .filter(Boolean)
            .join(" · ");
          return `<a class="suggest-item" href="/beer/${it.id}">
            ${img}
            <div class="si-main">
              <div class="si-name">${escapeHtml(it.name)}</div>
              <div class="si-meta">${escapeHtml(meta)}</div>
            </div>
          </a>`;
        })
        .join("");
    }
    box.innerHTML = html;
    box.hidden = false;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (q === lastQuery) return;
    lastQuery = q;
    if (q.length < 2) {
      box.hidden = true;
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(() => {
      fetch("/api/suggest?q=" + encodeURIComponent(q))
        .then((r) => r.json())
        .then((data) => {
          // Новый формат: {suggestions: [...], correction: str|null}
          // Старый формат (массив) — для совместимости
          if (Array.isArray(data)) {
            render(data, null);
          } else {
            render(data.suggestions || [], data.correction || null);
          }
        })
        .catch(() => {
          box.hidden = true;
        });
    }, 200);
  });

  input.addEventListener("focus", () => {
    if (box.children.length > 0) box.hidden = false;
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !box.contains(e.target)) {
      box.hidden = true;
    }
  });
})();

// Переключение главного фото в галерее карточки пива
(function () {
  const main = document.querySelector(".gallery .main-photo img");
  const thumbs = document.querySelectorAll(".gallery .thumb");
  if (!main || thumbs.length === 0) return;
  thumbs.forEach((t) => {
    t.addEventListener("click", () => {
      const img = t.querySelector("img");
      if (!img) return;
      main.src = img.dataset.full || img.src;
      thumbs.forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
    });
  });
})();
