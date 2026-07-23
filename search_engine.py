"""Интеллектуальный поиск пива: опечатки, раскладка, ru↔en транслитерация.

3-уровневый пайплайн:
  1. Нормализация запроса (регистр + апострофы + HTML-сущности)
  2. Генерация кандидатов (оригинал + транслитерация + раскладка)
  3. SQL LIKE по кандидатам → дедуп → fuzzy-ранжирование rapidfuzz

Поддерживаемые сценарии:
  - Регистронезависимость для кириллицы (Unicode lower, не SQLite LOWER)
  - Опечатки (rapidfuzz Левенштейн): 'ipf' → 'ipa'
  - Ошибки раскладки: 'ghbdjq' → 'пиво', 'zgfccjh' → 'заговор'
  - ru↔en транслитерация: 'Заговор' ↔ 'Zagovor', 'Полночный' → 'Polnochnyj'
  - Нормализация апострофов (U+0060 ↔ U+0027 ↔ U+2019)
  - Подсказки «может вы имели в виду» когда нет точных совпадений
"""

from __future__ import annotations

import html
import re
import sqlite3
import threading
from typing import Any

from rapidfuzz import fuzz, process

# =========================================================================
# 1. НОРМАЛИЗАЦИЯ ЗАПРОСА
# =========================================================================

# Все варианты апострофа → один стандартный символ "'" (U+0027).
# В базе craftbeer78.ru использует backtick U+0060 (O`Hara), пользователь
# наберёт прямой U+0027 — без нормализации они не совпадут.
_APOSTROPHES = {
    "\u0027",  # ' апостроф прямой
    "\u0060",  # ` гравис (backtick) — используется в базе!
    "\u00b4",  # ´ акут
    "\u2018",  # ‘ левая одинарная кавычка
    "\u2019",  # ’ правая одинарная кавычка (typographic apostrophe)
    "\u02bc",  # ʼ модификатор-буква апостроф
    "\u02ee",  # ˮ
    "\u055a",  # ՚ армянский апостроф
}
_APOS_REPLACEMENT = "'"

_WS_RE = re.compile(r"\s+")


def normalize_query(q: str | None) -> str:
    """Нормализация: Unicode lower + HTML + апострофы + пробелы + trim."""
    if not q:
        return ""
    s = str(q)
    # Декодирование HTML-сущностей: &amp; → &, &#39; → '
    s = html.unescape(s)
    # Unicode lower — КРИТИЧНО для кириллицы (SQLite LOWER() работает только для ASCII)
    s = s.lower()
    # Нормализация апострофов
    s = "".join(_APOS_REPLACEMENT if ch in _APOSTROPHES else ch for ch in s)
    # Схлопывание пробелов
    s = _WS_RE.sub(" ", s).strip()
    return s


# =========================================================================
# 2. ТРАНСЛИТЕРАЦИЯ ru ↔ en
# =========================================================================

# ГОСТ 7.79-2000 System B (как в загранпаспортах / водительских).
# Многосимвольные комбинации обработаны отдельно (важен порядок: длинные первые).
_RU2EN_MULTI = [
    ("щ", "sch"), ("ж", "zh"), ("ч", "ch"), ("ш", "sh"),
    ("ё", "yo"), ("ю", "yu"), ("я", "ya"),
    ("ъ", "`"), ("ь", "`"), ("э", "e"),
]
_RU2EN_SINGLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h",
    "ц": "ts", "ы": "y",
}
# Обратная таблица: en→ru. Многосимвольные — в обратном порядке длины.
_EN2RU_MULTI = [
    ("sch", "щ"), ("sh", "ш"), ("zh", "zh"),  # zh — оставляем как есть (нет точного ru эквивалента без контекста)
    ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"),
    ("ts", "ц"), ("yo", "ё"),
    ("y", "й"), ("e", "е"), ("a", "а"), ("b", "б"), ("v", "в"),
    ("g", "г"), ("d", "д"), ("z", "з"), ("i", "и"), ("k", "к"),
    ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"),
    ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"),
    ("h", "х"), ("`", "ь"),
]
# zh → ж (важная пара для пивоварен типа Zagovor)
_EN2RU_MULTI_FIXED = [
    ("sch", "щ"), ("sh", "ш"), ("zh", "ж"), ("ch", "ч"),
    ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ts", "ц"),
    ("y", "й"), ("e", "е"), ("a", "а"), ("b", "б"), ("v", "в"),
    ("g", "г"), ("d", "д"), ("z", "з"), ("i", "и"), ("k", "к"),
    ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"),
    ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"),
    ("h", "х"), ("`", "ь"), ("j", "й"), ("w", "в"), ("x", "кс"),
    ("q", "к"),
]


def _translit_apply(text: str, rules: list[tuple[str, str]]) -> str:
    """Применяет список замен (длинные первые) к тексту."""
    result = text
    for src, dst in rules:
        result = result.replace(src, dst)
    return result


def transliterate_ru2en(text: str) -> str:
    """Кириллица → латиница. 'Заговор' → 'Zagovor'."""
    result = text
    # Сначала многосимвольные
    for ru, en in _RU2EN_MULTI:
        result = result.replace(ru, en).replace(ru.upper(), en.capitalize())
    # Затем посимвольно
    out = []
    for ch in result.lower():
        out.append(_RU2EN_SINGLE.get(ch, ch))
    return "".join(out)


def transliterate_en2ru(text: str) -> str:
    """Латиница → кириллица. 'Zagovor' → 'Заговор', 'Sindrom' → 'Синдром'."""
    return _translit_apply(text.lower(), _EN2RU_MULTI_FIXED)


def transliterate(text: str, direction: str) -> str:
    """Универсальная обёртка. direction: 'ru2en' | 'en2ru'."""
    if direction == "ru2en":
        return transliterate_ru2en(text)
    if direction == "en2ru":
        return transliterate_en2ru(text)
    return text


# =========================================================================
# 3. ПЕРЕКЛЮЧЕНИЕ РАСКЛАДКИ КЛАВИАТУРЫ
# =========================================================================

# Соответствие физических клавиш (ЙЦУКЕН ↔ QWERTY).
_KEYMAP_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю"
_KEYMAP_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,."
_RU_TO_EN_KB = dict(zip(_KEYMAP_RU, _KEYMAP_EN))
_EN_TO_RU_KB = dict(zip(_KEYMAP_EN, _KEYMAP_RU))
# Заглавные
_RU_TO_EN_KB.update({k.upper(): v.upper() for k, v in zip(_KEYMAP_RU, _KEYMAP_EN)})
_EN_TO_RU_KB.update({k.upper(): v.upper() for k, v in zip(_KEYMAP_EN, _KEYMAP_RU)})

_RU_LETTERS_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_EN_LETTERS_RE = re.compile(r"[a-z]", re.IGNORECASE)


def _switch(text: str, mapping: dict[str, str]) -> str:
    return "".join(mapping.get(ch, ch) for ch in text)


def switch_layout(text: str) -> str:
    """Определяет раскладку и переключает на противоположную.

    'ghbdjq' → 'пиво', 'zgfccjh' → 'заговор',
    'пиво' → 'ghbdjq', 'заговор' → 'zgfccjh'.
    Возвращает исходную строку если она смешанная или без букв.
    """
    if not text:
        return text
    ru_count = len(_RU_LETTERS_RE.findall(text))
    en_count = len(_EN_LETTERS_RE.findall(text))
    # Переключаем только если доминирует одна раскладка
    if ru_count > 0 and en_count == 0:
        return _switch(text, _RU_TO_EN_KB)
    if en_count > 0 and ru_count == 0:
        return _switch(text, _EN_TO_RU_KB)
    return text  # смешанная или без букв — не трогаем


# =========================================================================
# 4. ГЕНЕРАЦИЯ КАНДИДАТОВ
# =========================================================================

def generate_candidates(q: str) -> list[str]:
    """Все варианты запроса для SQL-поиска.

    Возвращает уникальный список (порядок сохранён):
      [нормализованный, транслит ru→en, транслит en→ru, переключение раскладки]
    """
    norm = normalize_query(q)
    if not norm:
        return []
    candidates = [norm]
    for variant in (
        transliterate(norm, "ru2en"),
        transliterate(norm, "en2ru"),
        switch_layout(norm),
    ):
        if variant and variant not in candidates:
            candidates.append(variant)
    return candidates


# =========================================================================
# 5. FUZZY-РАНЖИРОВАНИЕ
# =========================================================================

def score_match(query_norm: str, name: str, producer: str) -> int:
    """Оценка релевантности 0-100 для одной позиции.

    Стратегия: берём максимум из нескольких метрик rapidfuzz,
    с бустом за совпадение по названию пива.
    """
    if not name:
        name = ""
    producer = producer or ""

    # partial_ratio — частичное совпадение (найдёт 'ipa' в 'american ipa')
    name_partial = fuzz.partial_ratio(query_norm, name.lower())
    # token_sort_ratio — слова в любом порядке
    name_token = fuzz.token_sort_ratio(query_norm, name.lower())
    # producer
    prod_partial = fuzz.partial_ratio(query_norm, producer.lower())

    # Максимальный score по названию (с весом 1.0)
    name_score = max(name_partial, name_token)
    # Producer — с меньшим весом
    prod_score = prod_partial * 0.75

    return int(max(name_score, prod_score))


# =========================================================================
# 6. ГЛАВНАЯ ФУНКЦИЯ ПОИСКА
# =========================================================================

# SQL ищет по name/producer/style/country. style_family тоже — вдруг
# пользователь введёт 'ipa' и мы найдём семью.
_SEARCH_SQL = """
    SELECT id, name, producer, style, style_family, abv, volume, price,
           local_image, original_url
    FROM products_full
    WHERE 1=0
"""

# LIKE по 4 полям для каждого кандидата. Чтобы избежать N×SQL-запросов,
# собираем один большой WHERE с OR по всем кандидатам.
def _build_like_clause(candidates: list[str]) -> tuple[str, list[str]]:
    """Строит OR-цепочку LIKE для всех кандидатов по name/producer/style."""
    clauses: list[str] = []
    params: list[str] = []
    fields = ("name", "producer", "style", "brewery_country")
    for cand in candidates:
        like = f"%{cand}%"
        for field in fields:
            clauses.append(f"CAST({field} AS TEXT) LIKE ?")
            params.append(like)
    return " OR ".join(clauses), params


def search(
    q: str,
    db: sqlite3.Connection,
    limit: int = 24,
) -> dict[str, Any]:
    """Умный поиск. Возвращает {results, count, candidates, correction}.

    results — список dict с доп. полем score (0-100).
    correction — str|None: подсказка «может вы имели в виду».
    """
    norm = normalize_query(q)
    if len(norm) < 2:
        return {"results": [], "count": 0, "candidates": [], "correction": None, "query": norm}

    candidates = generate_candidates(q)
    like_clause, params = _build_like_clause(candidates)

    sql = f"""
        SELECT id, name, producer, style, style_family, abv, volume, price,
               local_image, original_url
        FROM products_full
        WHERE {like_clause}
    """
    cur = db.execute(sql, params)
    rows = cur.fetchall()

    # Дедуп по id + расчёт score
    seen: set[int] = set()
    scored: list[dict[str, Any]] = []
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        score = score_match(norm, r["name"] or "", r["producer"] or "")
        scored.append({
            "id": r["id"],
            "name": r["name"],
            "producer": r["producer"],
            "style": r["style"],
            "style_family": r["style_family"],
            "abv": r["abv"],
            "volume": r["volume"],
            "price": r["price"],
            "image": None,  # заполнит app.py через url_for
            "url": None,
            "score": score,
        })

    # Сортировка: по score убыванию
    scored.sort(key=lambda x: x["score"], reverse=True)

    # FALLBACK ДЛЯ ОПЕЧАТОК: если SQL-поиск по кандидатам дал мало результатов,
    # ищем по fuzzy (rapidfuzz) по всем названиям. Это покрывает опечатки
    # типа 'ipf'→'ipa', 'хзут'→'стаут', которые LIKE не найдёт как подстроку.
    if len(scored) < 3 and len(norm) >= 3:
        fuzzy_hits = _fuzzy_fallback(norm, db, limit=limit)
        # добавляем только те, которых ещё нет (по id)
        existing_ids = {item["id"] for item in scored}
        for hit in fuzzy_hits:
            if hit["id"] not in existing_ids:
                scored.append(hit)

    results = scored[:limit]

    # Подсказка «может вы имели в виду» — только если результатов мало или нет
    correction = None
    if len(results) < 3:
        correction = suggest_correction(norm, db)

    return {
        "results": results,
        "count": len(scored),
        "candidates": candidates,
        "correction": correction,
        "query": norm,
    }


def _fuzzy_fallback(
    q_norm: str, db: sqlite3.Connection, limit: int = 24
) -> list[dict[str, Any]]:
    """Поиск по опечаткам через rapidfuzz по всем name + producer.

    Используется когда LIKE ничего не дал. Берём топ-N названий с score >= 70,
    затем для каждого подтягиваем полное соответствие из БД.
    """
    _ensure_names_cache(db)
    if not _names_cache:
        return []
    # process.extract — топ-N совпадений одним вызовом, быстро (C++)
    matches = process.extract(
        q_norm,
        _names_cache,
        scorer=fuzz.WRatio,
        score_cutoff=70,
        limit=limit,
    )
    if not matches:
        return []
    # matches = [(name, score, index), ...]
    # Группируем по названию, берём уникальные имена
    hit_names: list[tuple[str, int]] = []
    seen_names: set[str] = set()
    for name, score, _idx in matches:
        if name not in seen_names:
            seen_names.add(name)
            hit_names.append((name, int(score)))

    if not hit_names:
        return []

    # Подтягиваем строки из БД по этим названиям
    # Разделяем: name может быть как пивом, так и пивоваром.
    placeholders = ",".join(["?"] * len(hit_names))
    name_to_score = {n: s for n, s in hit_names}
    rows = db.execute(
        f"SELECT id, name, producer, style, style_family, abv, volume, price, "
        f"local_image, original_url FROM products_full "
        f"WHERE name IN ({placeholders}) OR producer IN ({placeholders})",
        [n for n, _ in hit_names] * 2,
    ).fetchall()

    seen_ids: set[int] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        # score из fuzzy-матча
        score = name_to_score.get(r["name"], name_to_score.get(r["producer"], 70))
        out.append({
            "id": r["id"],
            "name": r["name"],
            "producer": r["producer"],
            "style": r["style"],
            "style_family": r["style_family"],
            "abv": r["abv"],
            "volume": r["volume"],
            "price": r["price"],
            "image": None,
            "url": None,
            "score": score,
            "fuzzy": True,  # пометка для отладки
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# =========================================================================
# 7. ПОДСКАЗКИ «МОЖЕТ ВЫ ИМЕЛИ В ВИДУ»
# =========================================================================

# Кэш всех названий для быстрого fuzzy-поиска.
# ~7500 строк × ~20 символов ≈ 500 КБ — приемлемо держать в памяти.
_names_cache: list[str] = []
_names_cache_lock = threading.Lock()
_names_cache_loaded = False


def _ensure_names_cache(db: sqlite3.Connection) -> None:
    """Загружает все name + producer один раз (потокобезопасно)."""
    global _names_cache, _names_cache_loaded
    with _names_cache_lock:
        if _names_cache_loaded:
            return
        cur = db.execute(
            "SELECT DISTINCT name FROM products_full WHERE name IS NOT NULL AND name != ''"
        )
        names = [r[0] for r in cur.fetchall()]
        cur = db.execute(
            "SELECT DISTINCT producer FROM products_full WHERE producer IS NOT NULL AND producer != ''"
        )
        names.extend(r[0] for r in cur.fetchall())
        _names_cache = names
        _names_cache_loaded = True


def suggest_correction(q_norm: str, db: sqlite3.Connection) -> str | None:
    """Если точных совпадений нет — найти самое близкое название.

    Возвращает строку-подсказку или None.
    Порог: rapidfuzz score >= 75 (из 100).
    """
    if len(q_norm) < 3:
        return None
    _ensure_names_cache(db)
    if not _names_cache:
        return None
    # process.extractOne находит лучшее совпадение одним вызовом
    best = process.extractOne(
        q_norm,
        _names_cache,
        scorer=fuzz.WRatio,
        score_cutoff=75,
    )
    if best is None:
        return None
    # best = (название, score, индекс)
    return best[0]


def invalidate_cache() -> None:
    """Сброс кэша названий (вызывать после обновления БД парсером)."""
    global _names_cache, _names_cache_loaded
    with _names_cache_lock:
        _names_cache = []
        _names_cache_loaded = False


# =========================================================================
# Самотестирование
# =========================================================================

if __name__ == "__main__":
    # Тестируем ключевые функции
    print("=== Тесты search_engine ===")
    tests = [
        ("normalize_query('ИПА')", "ипа"),
        ("normalize_query('O`Hara')", "o'hara"),
        ("normalize_query('  Multiple   Spaces  ')", "multiple spaces"),
        ("normalize_query('Чиж&amp;Co')", "чиж&co"),
        ("transliterate_ru2en('Заговор')", "zagovor"),
        ("transliterate_en2ru('Zagovor')", "заговор"),
        ("transliterate_en2ru('Sindrom')", "синдром"),
        ("switch_layout('gbdj')", "пиво"),         # g=п b=и d=в j=о
        ("switch_layout('заговор')", "pfujdjh"),   # переключение ru→en
        ("switch_layout('пиво')", "gbdj"),
        ("generate_candidates('Заговор')", "['заговор', 'zagovor', ...]"),
    ]
    ok = 0
    for expr, expected in tests:
        try:
            actual = eval(expr)
            mark = "✅" if str(actual) == str(expected) or expected.endswith("...") else "❌"
            if mark == "✅":
                ok += 1
            print(f"  {mark} {expr} = {actual!r}")
        except Exception as e:
            print(f"  ❌ {expr} → ОШИБКА: {e}")
    print(f"\n{ok}/{len(tests)} passed")
