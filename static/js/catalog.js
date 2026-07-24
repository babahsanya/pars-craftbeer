// Каталог: мульти-фильтры, слайдер цены, пресеты, авто-сабмит.
(function () {
  const form = document.getElementById("catalogForm");
  if (!form) return;

  // ===================================================================
  // МУЛЬТИ-ЧИПСЫ СЕМЕЙ
  // ===================================================================
  const familyInput = document.getElementById("familyInput");
  const familyChips = document.querySelectorAll("#familyChips .fs-chip");

  function syncFamilyInput() {
    const active = Array.from(familyChips)
      .filter((c) => c.classList.contains("active"))
      .map((c) => c.dataset.family);
    familyInput.value = active.join(",");
  }

  familyChips.forEach((chip) => {
    chip.addEventListener("click", (e) => {
      e.preventDefault();
      chip.classList.toggle("active");
      syncFamilyInput();
      // авто-сабмит при смене семьи (обновит список стилей)
      form.submit();
    });
  });

  // ===================================================================
  // МУЛЬТИ-СЕЛЕКТЫ (стиль, страна)
  // ===================================================================
  function setupMultiSelect(displayId, optionsId, inputId, cbSelector) {
    const display = document.getElementById(displayId);
    const options = document.getElementById(optionsId);
    const input = document.getElementById(inputId);
    if (!display || !options || !input) return;

    // Открытие/закрытие
    display.addEventListener("click", (e) => {
      e.preventDefault();
      // закрыть другие
      document.querySelectorAll(".fs-ms-options.open").forEach((o) => {
        if (o !== options) o.classList.remove("open");
      });
      options.classList.toggle("open");
    });

    // Закрытие по клику вне
    document.addEventListener("click", (e) => {
      if (!display.contains(e.target) && !options.contains(e.target)) {
        options.classList.remove("open");
      }
    });

    // Чекбоксы
    const checkboxes = options.querySelectorAll(cbSelector);
    function sync() {
      const checked = Array.from(checkboxes)
        .filter((cb) => cb.checked)
        .map((cb) => cb.value);
      input.value = checked.join(",");
      display.textContent = checked.length
        ? `Выбрано: ${checked.length}`
        : display.dataset.default || "Все";
    }
    checkboxes.forEach((cb) => {
      cb.addEventListener("change", () => {
        sync();
        form.submit();
      });
    });
    sync();
  }

  setupMultiSelect(
    "styleMulti", "styleOptions", "styleInput", "input[data-style-cb]"
  );
  // сохраняем дефолтный текст
  const styleDisp = document.getElementById("styleMulti");
  if (styleDisp) {
    const d = styleDisp.querySelector(".fs-ms-display");
    if (d) d.dataset.default = d.textContent.trim();
  }

  setupMultiSelect(
    "countryMulti", "countryOptions", "countryInput", "input[data-country-cb]"
  );
  const countryDisp = document.getElementById("countryMulti");
  if (countryDisp) {
    const d = countryDisp.querySelector(".fs-ms-display");
    if (d) d.dataset.default = d.textContent.trim();
  }

  // ===================================================================
  // ДВОЙНОЙ СЛАЙДЕР ЦЕНЫ
  // ===================================================================
  const minSlider = document.getElementById("priceMinSlider");
  const maxSlider = document.getElementById("priceMaxSlider");
  const minInput = document.getElementById("priceMinInput");
  const maxInput = document.getElementById("priceMaxInput");
  const rangeBar = document.getElementById("priceRangeBar");
  const priceValues = document.getElementById("priceValues");
  const boundMin = parseInt(minSlider.min, 10);
  const boundMax = parseInt(minSlider.max, 10);

  let submitTimer = null;
  function scheduleSubmit(delay) {
    clearTimeout(submitTimer);
    submitTimer = setTimeout(() => form.submit(), delay);
  }

  function updateSliderVisual() {
    let lo = parseInt(minSlider.value, 10);
    let hi = parseInt(maxSlider.value, 10);
    if (lo > hi) {
      // не даём min > max
      if (document.activeElement === minSlider) {
        maxSlider.value = lo;
        hi = lo;
      } else {
        minSlider.value = hi;
        lo = hi;
      }
    }
    const span = boundMax - boundMin;
    const leftPct = span > 0 ? ((lo - boundMin) / span) * 100 : 0;
    const rightPct = span > 0 ? ((hi - boundMin) / span) * 100 : 100;
    if (rangeBar) {
      rangeBar.style.left = leftPct + "%";
      rangeBar.style.right = 100 - rightPct + "%";
    }
    // обновляем hidden inputs
    minInput.value = lo === boundMin ? "" : lo;
    maxInput.value = hi === boundMax ? "" : hi;
    if (priceValues) priceValues.textContent = `${lo}–${hi}`;
  }

  if (minSlider && maxSlider) {
    updateSliderVisual();
    let dragTimer = null;
    minSlider.addEventListener("input", () => {
      updateSliderVisual();
      clearTimeout(dragTimer);
      dragTimer = setTimeout(() => scheduleSubmit(300), 200);
    });
    maxSlider.addEventListener("input", () => {
      updateSliderVisual();
      clearTimeout(dragTimer);
      dragTimer = setTimeout(() => scheduleSubmit(300), 200);
    });
  }

  // ===================================================================
  // ПЕРЕКЛЮЧАТЕЛЬ ВИДА
  // ===================================================================
  document.querySelectorAll(".view-btn[data-view]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      document.getElementById("viewInput").value = btn.dataset.view;
      try { localStorage.setItem("catalog_view", btn.dataset.view); } catch (err) {}
      form.submit();
    });
  });

  // ===================================================================
  // ПРЕСЕТЫ ФИЛЬТРОВ (localStorage)
  // ===================================================================
  const PRESET_KEY = "catalog_presets";
  const saveBtn = document.getElementById("savePresetBtn");
  const presetList = document.getElementById("presetList");

  function getPresets() {
    try {
      return JSON.parse(localStorage.getItem(PRESET_KEY) || "{}");
    } catch (err) {
      return {};
    }
  }

  function savePresets(presets) {
    try {
      localStorage.setItem(PRESET_KEY, JSON.stringify(presets));
    } catch (err) {}
  }

  function renderPresets() {
    if (!presetList) return;
    const presets = getPresets();
    const names = Object.keys(presets);
    if (names.length === 0) {
      presetList.innerHTML = '<div class="fs-preset-empty">Нет сохранённых пресетов</div>';
      return;
    }
    presetList.innerHTML = names
      .map(
        (name) =>
          `<div class="fs-preset-item">
            <a href="/catalog?${presets[name]}" class="fs-preset-link">📌 ${escapeHtml(name)}</a>
            <button type="button" class="fs-preset-del" data-name="${escapeHtml(name)}" title="Удалить">✕</button>
          </div>`
      )
      .join("");
    // обработчики удаления
    presetList.querySelectorAll(".fs-preset-del").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const name = btn.dataset.name;
        const presets = getPresets();
        delete presets[name];
        savePresets(presets);
        renderPresets();
      });
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", (e) => {
      e.preventDefault();
      const name = prompt("Название пресета:");
      if (!name) return;
      // собираем текущие query-params (без page)
      const params = new URLSearchParams(new FormData(form));
      params.delete("page");
      const qs = params.toString();
      const presets = getPresets();
      presets[name] = qs;
      savePresets(presets);
      renderPresets();
    });
  }

  renderPresets();

  // ===================================================================
  // МОБИЛЬНОЕ РАСКРЫТИЕ ФИЛЬТРОВ
  // ===================================================================
  const mobileToggle = document.getElementById("mobileFiltersToggle");
  const sidebar = document.getElementById("filtersSidebar");
  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener("click", () => {
      sidebar.classList.toggle("mobile-open");
    });
  }
})();
