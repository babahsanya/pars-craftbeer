"""Пивная энциклопедия — главный Flask-сервер.

Запуск: python app.py
Открывается на http://127.0.0.1:8000/

Маршруты:
    /                       Главная
    /search?q=              Поиск
    /api/suggest?q=         AJAX-подсказки (JSON)
    /beer/<id>              Карточка пива
    /styles                 Все стили
    /style/<slug>           Страница стиля
    /breweries              Все пивовары
    /brewery/<slug>         Страница пивовара
    /country/<name>         Страница страны
    /catalog                Каталог с фильтрами
    /random                 Редирект на случайную карточку
    /compare?id1=&id2=      Сравнение
    /top                    Подборки
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import unicodedata
from collections import namedtuple
from pathlib import Path

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from style_families import (
    all_family_ids,
    classify_style,
    family_icon,
    family_meta,
    family_title,
)
import search_engine

APP_ROOT = Path(__file__).resolve().parent
DB_PATH = APP_ROOT / "beer_database.db"
PAGE_SIZE = 60
MAX_SUGGEST = 10

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


# =============================================================================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# =============================================================================

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        if not DB_PATH.exists():
            abort(500, description=f"База не найдена: {DB_PATH}")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


_slug_strip_re = re.compile(r"[^\w\s-]", re.UNICODE)
_slug_space_re = re.compile(r"[\s_]+")


def slugify(text: str) -> str:
    """Безопасный slug для URL. Сохраняет кириллицу."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).strip().lower()
    text = _slug_strip_re.sub("", text)
    text = _slug_space_re.sub("-", text)
    # Схлопываем подряд идущие дефисы в один
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text


def static_url(path: str | None) -> str:
    """Преобразует 'static/images/1/main.jpg' в url_for('static', filename='images/1/main.jpg')."""
    if not path:
        return ""
    p = path
    if p.startswith("static/"):
        p = p[len("static/"):]
    return url_for("static", filename=p)


def parse_price_int(price_str: str | None) -> int | None:
    if not price_str:
        return None
    m = re.search(r"\d+", str(price_str).replace(" ", ""))
    return int(m.group()) if m else None


def gallery_urls(row: sqlite3.Row) -> list[str]:
    """Список URL локальных фото галереи для позиции."""
    result: list[str] = []
    main = row["local_image"] if "local_image" in row.keys() else None
    if main:
        result.append(static_url(main))
    additional = row["local_gallery"] if "local_gallery" in row.keys() else None
    if additional:
        try:
            items = json.loads(additional)
            if isinstance(items, list):
                for it in items:
                    if it:
                        result.append(static_url(it))
        except (ValueError, TypeError):
            pass
    return result


# =============================================================================
# КОНТЕКСТНЫЙ ПРОЦЕССОР (доступно во всех шаблонах)
# =============================================================================

@app.context_processor
def inject_helpers():
    return {
        "abv_per_rub": lambda b: _fmt_abv_per_rub(b),
        "price_per_100ml": lambda b: _fmt_price_per_100ml(b),
    }


def _fmt_abv_per_rub(b) -> str:
    if not b:
        return "—"
    abv = b["abv"] if "abv" in b.keys() else None
    price = parse_price_int(b["price"] if "price" in b.keys() else None)
    vol = b["volume"] if "volume" in b.keys() else None
    if not abv or not price or not vol or price == 0:
        return "—"
    ml_alc = vol * abv / 100.0
    if ml_alc == 0:
        return "—"
    rub_per_ml_alc = price / ml_alc
    return f"{rub_per_ml_alc:.2f} ₽/мл алкогол"


def _fmt_price_per_100ml(b) -> str:
    if not b:
        return "—"
    price = parse_price_int(b["price"] if "price" in b.keys() else None)
    vol = b["volume"] if "volume" in b.keys() else None
    if not price or not vol or vol == 0:
        return "—"
    return f"{price * 100.0 / vol:.1f} ₽"


# =============================================================================
# МАРШРУТЫ
# =============================================================================

@app.route("/")
def index():
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM products_full")
    total_beers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT style) FROM products_full WHERE style IS NOT NULL AND style != ''")
    total_styles = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT producer) FROM products_full WHERE producer IS NOT NULL AND producer != ''")
    total_breweries = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT brewery_country) FROM products_full WHERE brewery_country IS NOT NULL AND brewery_country != ''")
    total_countries = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM products_full WHERE local_image IS NOT NULL AND local_image != ''")
    with_photos = cur.fetchone()[0]

    # Топ стилей (по количеству)
    cur.execute(
        "SELECT style, COUNT(*) AS c, AVG(abv) AS a FROM products_full "
        "WHERE style IS NOT NULL AND style != '' GROUP BY style ORDER BY c DESC LIMIT 12"
    )
    top_styles = [{"style": r["style"], "count": r["c"], "avg_abv": r["a"], "slug": slugify(r["style"])} for r in cur.fetchall()]

    # Топ пивоварен
    cur.execute(
        "SELECT producer, brewery_country, COUNT(*) AS c FROM products_full "
        "WHERE producer IS NOT NULL AND producer != '' GROUP BY producer ORDER BY c DESC LIMIT 12"
    )
    top_breweries = [{"producer": r["producer"], "country": r["brewery_country"], "count": r["c"], "slug": slugify(r["producer"])} for r in cur.fetchall()]

    # Топ стран
    cur.execute(
        "SELECT brewery_country, COUNT(*) AS c FROM products_full "
        "WHERE brewery_country IS NOT NULL AND brewery_country != '' GROUP BY brewery_country ORDER BY c DESC LIMIT 12"
    )
    top_countries = [{"country": r["brewery_country"], "count": r["c"]} for r in cur.fetchall()]

    # Случайные с фото
    cur.execute(
        "SELECT id, name, producer, style, abv, volume, price, local_image "
        "FROM products_full WHERE local_image IS NOT NULL AND local_image != '' "
        "ORDER BY RANDOM() LIMIT 12"
    )
    random_beers = cur.fetchall()

    # Чипсы семей стилей для быстрого входа
    family_chips = []
    for fid in all_family_ids():
        icon, title, _ = family_meta(fid)
        row = cur.execute(
            "SELECT COUNT(*) AS n FROM products_full WHERE style_family = ?", (fid,)
        ).fetchone()
        family_chips.append({"id": fid, "icon": icon, "title": title, "count": row["n"]})
    family_chips.sort(key=lambda f: (f["id"] == "other", -f["count"]))

    # Топ пивоварен как ранжированный чарт (с прогресс-баром)
    max_brewery_count = top_breweries[0]["count"] if top_breweries else 1
    for i, b in enumerate(top_breweries, 1):
        b["rank"] = i
        b["pct"] = int(b["count"] / max_brewery_count * 100) if max_brewery_count else 0

    # Страны с эмодзи-флагами
    country_flags = {
        "Россия": "🇷🇺", "Бельгия": "🇧🇪", "Нидерланды": "🇳🇱",
        "Великобритания": "🇬🇧", "Чехия": "🇨🇿", "Франция": "🇫🇷",
        "Ирландия": "🇮🇪", "США": "🇺🇸", "Германия": "🇩🇪", "Швеция": "🇸🇪",
        "Канада": "🇨🇦", "Финляндия": "🇫🇮", "Швейцария": "🇨🇭", "Чили": "🇨🇱",
        "Испания": "🇪🇸", "Вьетнам": "🇻🇳", "Китай": "🇨🇳", "Таиланд": "🇹🇭",
        "Италия": "🇮🇹", "Дания": "🇩🇰", "Норвегия": "🇳🇴", "Польша": "🇵🇱",
        "Япония": "🇯🇵", "Австрия": "🇦🇹", "Португалия": "🇵🇹",
    }
    for c in top_countries:
        c["flag"] = country_flags.get(c["country"], "🌐")

    return render_template(
        "index.html",
        total_beers=total_beers,
        total_styles=total_styles,
        total_breweries=total_breweries,
        total_countries=total_countries,
        with_photos=with_photos,
        top_styles=top_styles,
        top_breweries=top_breweries,
        top_countries=top_countries,
        random_beers=random_beers,
        family_chips=family_chips,
    )


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    correction = None
    candidates = []
    groups = []
    if q and len(q) >= 2:
        db = get_db()
        res = search_engine.search(q, db, limit=60)
        results = res["results"]
        correction = res.get("correction")
        candidates = res.get("candidates", [])
        groups = res.get("groups", [])
        # подтягиваем local_image для всех результатов
        for item in results:
            if item.get("image") is None:
                row = db.execute(
                    "SELECT local_image FROM products_full WHERE id = ?", (item["id"],)
                ).fetchone()
                item["image"] = static_url(row["local_image"]) if row and row["local_image"] else None
            item["url"] = url_for("beer_detail", beer_id=item["id"])
    return render_template(
        "search.html",
        q=q,
        results=results,
        correction=correction,
        candidates=candidates,
        groups=groups,
    )


@app.route("/api/suggest")
def api_suggest():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return []
    db = get_db()
    res = search_engine.search(q, db, limit=MAX_SUGGEST)
    out = []
    for item in res["results"]:
        out.append({
            "id": item["id"],
            "name": item["name"],
            "producer": item["producer"],
            "style": item["style"],
            "abv": item["abv"],
            "local_image": None,  # заполним ниже через static_url
            "score": item["score"],
        })
        # подтягиваем local_image для превью
        if item["image"] is None:
            row = db.execute(
                "SELECT local_image FROM products_full WHERE id = ?", (item["id"],)
            ).fetchone()
            if row and row["local_image"]:
                out[-1]["local_image"] = static_url(row["local_image"])
    # Подсказка «может вы имели в виду»
    correction = res.get("correction")
    return {"suggestions": out, "correction": correction}


@app.route("/beer/<int:beer_id>")
def beer_detail(beer_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM products_full WHERE id = ?", (beer_id,)).fetchone()
    if not row:
        abort(404)

    # Информация о стиле
    style_info = None
    style_slug = slugify(row["style"]) if row["style"] else None
    if row["style"]:
        style_info = db.execute(
            "SELECT * FROM beer_styles WHERE style = ?", (row["style"],)
        ).fetchone()

    # Похожие (тот же стиль, не та же позиция)
    similar = []
    if row["style"]:
        similar = db.execute(
            "SELECT id, name, producer, abv, price, local_image FROM products_full "
            "WHERE style = ? AND id != ? ORDER BY RANDOM() LIMIT 6",
            (row["style"], beer_id),
        ).fetchall()

    # Slug пивовара для ссылки
    brewery_slug = slugify(row["producer"]) if row["producer"] else None

    gallery = gallery_urls(row)
    main_image = gallery[0] if gallery else None
    gallery_only = gallery[1:] if len(gallery) > 1 else []

    return render_template(
        "beer_detail.html",
        beer=row,
        main_image=main_image,
        gallery=gallery_only,
        style_info=style_info,
        style_slug=style_slug,
        brewery_slug=brewery_slug,
        similar=similar,
    )


@app.route("/api/search")
def api_search():
    """JSON: карточки пива для instant-поиска на главной.
    Возвращает id, name, producer, style, style_family, abv, volume, price,
    local_image, original_url — всё что нужно для рендера карточки на клиенте.
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": [], "count": 0, "correction": None})
    db = get_db()
    res = search_engine.search(q, db, limit=24)
    results = []
    for item in res["results"]:
        # подтягиваем local_image (search_engine его не заполняет)
        img = item.get("image")
        if img is None:
            row = db.execute(
                "SELECT local_image FROM products_full WHERE id = ?", (item["id"],)
            ).fetchone()
            img = static_url(row["local_image"]) if row and row["local_image"] else None
        results.append({
            "id": item["id"],
            "name": item["name"],
            "producer": item["producer"],
            "style": item["style"],
            "style_family": item["style_family"],
            "abv": item["abv"],
            "volume": item["volume"],
            "price": item["price"],
            "image": img,
            "url": url_for("beer_detail", beer_id=item["id"]),
            "score": item["score"],
            "tier": item["tier"],
        })
    # Группы для UI (с теми же image/url, что и в results)
    id_to_item = {it["id"]: it for it in results}
    groups = []
    for g in res.get("groups", []):
        group_items = []
        for gi in g["items"]:
            enriched = id_to_item.get(gi["id"])
            if enriched:
                group_items.append(enriched)
        if group_items:
            groups.append({"tier": g["tier"], "label": g["label"], "items": group_items})
    return jsonify({
        "results": results,
        "groups": groups,
        "count": res["count"],
        "correction": res.get("correction"),
        "candidates": res.get("candidates", []),
    })


@app.route("/style-families")
def style_families():
    """Сетка 16 семей стилей с иконками, названиями и кол-вом позиций."""
    db = get_db()
    cur = db.cursor()
    families = []
    for fid in all_family_ids():
        icon, title, desc = family_meta(fid)
        row = cur.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT style) AS s, AVG(abv) AS a "
            "FROM products_full WHERE style_family = ?",
            (fid,),
        ).fetchone()
        # самый частый стиль в семье
        top_style_row = cur.execute(
            "SELECT style FROM products_full WHERE style_family = ? "
            "AND style IS NOT NULL AND style != '' "
            "GROUP BY style ORDER BY COUNT(*) DESC LIMIT 1",
            (fid,),
        ).fetchone()
        families.append({
            "id": fid,
            "icon": icon,
            "title": title,
            "description": desc,
            "count": row["n"],
            "styles_count": row["s"],
            "avg_abv": row["a"],
            "top_style": top_style_row["style"] if top_style_row else None,
        })
    # Сортировка: основные семьи по убыванию кол-ва, 'other' всегда в конце
    families.sort(key=lambda f: (f["id"] == "other", -f["count"]))
    return render_template("style_families.html", families=families)


@app.route("/style-family/<family>")
def style_family_detail(family: str):
    """Страница семьи стилей: описание + конкретные стили + топ позиций/пивоварен."""
    # Валидация family
    if family not in all_family_ids():
        abort(404)
    icon, title, description = family_meta(family)
    db = get_db()
    cur = db.cursor()

    # Статистика семьи
    stat = cur.execute(
        "SELECT COUNT(*) AS n, AVG(abv) AS a, MIN(abv) AS mn, MAX(abv) AS mx, "
        "COUNT(DISTINCT producer) AS producers, "
        "COUNT(DISTINCT brewery_country) AS countries, "
        "COUNT(local_image) AS photos "
        "FROM products_full WHERE style_family = ?",
        (family,),
    ).fetchone()
    avg_price = cur.execute(
        "SELECT AVG(CAST(REPLACE(REPLACE(price, ' ₽', ''), ' ', '') AS REAL)) AS a "
        "FROM products_full WHERE style_family = ? AND price LIKE '%₽'",
        (family,),
    ).fetchone()["a"]
    stats = {
        "count": stat["n"],
        "avg_abv": stat["a"],
        "min_abv": stat["mn"],
        "max_abv": stat["mx"],
        "producers": stat["producers"],
        "countries": stat["countries"],
        "with_photos": stat["photos"],
        "avg_price_num": avg_price,
    }

    # Конкретные стили внутри семьи (отсортированы по кол-ву)
    style_rows = cur.execute(
        "SELECT style, COUNT(*) AS c FROM products_full "
        "WHERE style_family = ? AND style IS NOT NULL AND style != '' "
        "GROUP BY style ORDER BY c DESC",
        (family,),
    ).fetchall()
    styles_list = [
        {"style": r["style"], "count": r["c"], "slug": slugify(r["style"])}
        for r in style_rows
    ]

    # Топ пивоварен в семье
    top_breweries = cur.execute(
        "SELECT producer, COUNT(*) AS c FROM products_full "
        "WHERE style_family = ? AND producer IS NOT NULL AND producer != '' "
        "GROUP BY producer ORDER BY c DESC LIMIT 12",
        (family,),
    ).fetchall()
    top_breweries = [
        {"producer": r["producer"], "count": r["c"], "slug": slugify(r["producer"])}
        for r in top_breweries
    ]

    # Топ позиций (приоритет с фото)
    top_beers = cur.execute(
        "SELECT id, name, producer, style, abv, price, local_image FROM products_full "
        "WHERE style_family = ? ORDER BY (local_image IS NOT NULL) DESC, RANDOM() LIMIT 12",
        (family,),
    ).fetchall()

    return render_template(
        "style_family_detail.html",
        family=family,
        icon=icon,
        title=title,
        description=description,
        stats=stats,
        styles_list=styles_list,
        top_breweries=top_breweries,
        top_beers=top_beers,
    )


@app.route("/styles")
def styles():
    db = get_db()
    rows = db.execute(
        "SELECT p.style, COUNT(*) AS c, AVG(p.abv) AS a FROM products_full p "
        "WHERE p.style IS NOT NULL AND p.style != '' "
        "GROUP BY p.style ORDER BY c DESC"
    ).fetchall()
    # Какие стили есть в справочнике
    guide_styles = {
        r["style"] for r in db.execute("SELECT style FROM beer_styles").fetchall()
    }
    styles_list = [
        {
            "style": r["style"],
            "count": r["c"],
            "avg_abv": r["a"],
            "slug": slugify(r["style"]),
            "in_guide": r["style"] in guide_styles,
        }
        for r in rows
    ]
    return render_template("styles.html", styles=styles_list, guide_count=len(guide_styles))


@app.route("/style/<slug>")
def style_detail(slug: str):
    db = get_db()
    # Находим стиль по slug
    rows = db.execute(
        "SELECT DISTINCT style FROM products_full WHERE style IS NOT NULL AND style != ''"
    ).fetchall()
    target = None
    for r in rows:
        if slugify(r["style"]) == slug:
            target = r["style"]
            break
    if not target:
        abort(404)

    guide = db.execute("SELECT * FROM beer_styles WHERE style = ?", (target,)).fetchone()

    # Статистика
    cur = db.execute(
        "SELECT COUNT(*) AS c, AVG(abv) AS a, MIN(abv) AS mn, MAX(abv) AS mx, "
        "COUNT(DISTINCT producer) AS producers, COUNT(DISTINCT brewery_country) AS countries, "
        "COUNT(local_image) AS photos "
        "FROM products_full WHERE style = ?",
        (target,),
    ).fetchone()

    avg_price = db.execute(
        "SELECT AVG(CAST(REPLACE(REPLACE(price, ' ₽', ''), ' ', '') AS REAL)) AS a "
        "FROM products_full WHERE style = ? AND price LIKE '%₽'",
        (target,),
    ).fetchone()["a"]

    stats = {
        "avg_abv": cur["a"],
        "min_abv": cur["mn"],
        "max_abv": cur["mx"],
        "avg_price_num": avg_price,
        "producers": cur["producers"],
        "countries": cur["countries"],
        "with_photos": cur["photos"],
    }

    # Топ пивоварен в стиле
    top_breweries = db.execute(
        "SELECT producer, COUNT(*) AS c FROM products_full "
        "WHERE style = ? AND producer IS NOT NULL AND producer != '' "
        "GROUP BY producer ORDER BY c DESC LIMIT 12",
        (target,),
    ).fetchall()
    top_breweries = [{"producer": r["producer"], "count": r["c"], "slug": slugify(r["producer"])} for r in top_breweries]

    # Топ позиций (приоритет — с фото)
    top_beers = db.execute(
        "SELECT id, name, producer, abv, price, local_image FROM products_full "
        "WHERE style = ? ORDER BY (local_image IS NOT NULL) DESC, RANDOM() LIMIT 12",
        (target,),
    ).fetchall()

    # Расчёт позиций для графика ABV
    abv_bar = {"range_left": 0, "range_width": 0, "avg_left": 0}
    if guide and guide["abv_min"] is not None:
        scale_max = 20.0
        abv_bar = {
            "range_left": (guide["abv_min"] / scale_max) * 100,
            "range_width": max(((guide["abv_max"] - guide["abv_min"]) / scale_max) * 100, 2),
            "avg_left": min(((stats["avg_abv"] or 0) / scale_max) * 100, 100) if stats["avg_abv"] else 0,
        }

    return render_template(
        "style_detail.html",
        style_name=target,
        slug=slug,
        count=cur["c"],
        guide=guide,
        stats=stats,
        top_breweries=top_breweries,
        top_beers=top_beers,
        abv_bar=abv_bar,
    )


@app.route("/breweries")
def breweries():
    db = get_db()
    rows = db.execute(
        "SELECT producer, brewery_country, COUNT(*) AS c FROM products_full "
        "WHERE producer IS NOT NULL AND producer != '' "
        "GROUP BY producer ORDER BY c DESC"
    ).fetchall()
    breweries_list = [
        {
            "producer": r["producer"],
            "country": r["brewery_country"],
            "count": r["c"],
            "slug": slugify(r["producer"]),
        }
        for r in rows
    ]
    return render_template("breweries.html", breweries=breweries_list)


@app.route("/brewery/<slug>")
def brewery_detail(slug: str):
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT producer, brewery_country, brewery_city FROM products_full "
        "WHERE producer IS NOT NULL AND producer != ''"
    ).fetchall()
    target_producer = None
    target_country = None
    target_city = None
    for r in rows:
        if slugify(r["producer"]) == slug:
            target_producer = r["producer"]
            target_country = r["brewery_country"]
            target_city = r["brewery_city"]
            break
    if not target_producer:
        abort(404)

    # Распределение по стилям
    style_rows = db.execute(
        "SELECT style, COUNT(*) AS c FROM products_full "
        "WHERE producer = ? AND style IS NOT NULL AND style != '' "
        "GROUP BY style ORDER BY c DESC",
        (target_producer,),
    ).fetchall()
    style_breakdown = [
        {"style": r["style"], "count": r["c"], "slug": slugify(r["style"])} for r in style_rows
    ]

    # Фильтр по стилю
    current_style = request.args.get("style") or ""
    if current_style:
        beers = db.execute(
            "SELECT id, name, style, abv, volume, price, local_image FROM products_full "
            "WHERE producer = ? AND style = ? ORDER BY name",
            (target_producer, current_style),
        ).fetchall()
    else:
        beers = db.execute(
            "SELECT id, name, style, abv, volume, price, local_image FROM products_full "
            "WHERE producer = ? ORDER BY name",
            (target_producer,),
        ).fetchall()

    # Статистика
    cur = db.execute(
        "SELECT COUNT(*) AS c, AVG(abv) AS a, COUNT(DISTINCT style) AS s, "
        "COUNT(local_image) AS photos FROM products_full WHERE producer = ?",
        (target_producer,),
    ).fetchone()
    avg_price = db.execute(
        "SELECT AVG(CAST(REPLACE(REPLACE(price, ' ₽', ''), ' ', '') AS REAL)) AS a "
        "FROM products_full WHERE producer = ? AND price LIKE '%₽'",
        (target_producer,),
    ).fetchone()["a"]
    stats = {
        "avg_abv": cur["a"],
        "avg_price_num": avg_price,
        "styles": cur["s"],
        "with_photos": cur["photos"],
    }

    return render_template(
        "brewery_detail.html",
        brewery_name=target_producer,
        brewery_slug=slug,
        country=target_country,
        city=target_city,
        total=cur["c"],
        stats=stats,
        style_breakdown=style_breakdown,
        beers=beers,
        current_style=current_style,
    )


@app.route("/country/<path:name>")
def country_detail(name: str):
    db = get_db()
    # name может прийти url-encoded; Flask декодирует, но проверим оба варианта
    country = name
    row = db.execute(
        "SELECT COUNT(*) AS c FROM products_full WHERE brewery_country = ?", (country,)
    ).fetchone()
    if not row or row["c"] == 0:
        abort(404)

    total = row["c"]
    breweries_count = db.execute(
        "SELECT COUNT(DISTINCT producer) FROM products_full WHERE brewery_country = ?",
        (country,),
    ).fetchone()[0]

    # Статистика
    cur = db.execute(
        "SELECT AVG(abv) AS a, COUNT(DISTINCT style) AS s FROM products_full WHERE brewery_country = ?",
        (country,),
    ).fetchone()
    avg_price = db.execute(
        "SELECT AVG(CAST(REPLACE(REPLACE(price, ' ₽', ''), ' ', '') AS REAL)) AS a "
        "FROM products_full WHERE brewery_country = ? AND price LIKE '%₽'",
        (country,),
    ).fetchone()["a"]
    stats = {
        "avg_abv": cur["a"],
        "avg_price_num": avg_price,
        "styles": cur["s"],
    }

    top_breweries = db.execute(
        "SELECT producer, COUNT(*) AS c FROM products_full "
        "WHERE brewery_country = ? AND producer IS NOT NULL AND producer != '' "
        "GROUP BY producer ORDER BY c DESC LIMIT 12",
        (country,),
    ).fetchall()
    top_breweries = [{"producer": r["producer"], "count": r["c"], "slug": slugify(r["producer"])} for r in top_breweries]

    top_beers = db.execute(
        "SELECT id, name, producer, abv, price, local_image FROM products_full "
        "WHERE brewery_country = ? ORDER BY (local_image IS NOT NULL) DESC, RANDOM() LIMIT 12",
        (country,),
    ).fetchall()

    return render_template(
        "country.html",
        country=country,
        total=total,
        breweries_count=breweries_count,
        stats=stats,
        top_breweries=top_breweries,
        top_beers=top_beers,
    )


@app.route("/catalog")
def catalog():
    db = get_db()
    filters = {
        "name": (request.args.get("name") or "").strip(),
        "producer": (request.args.get("producer") or "").strip(),
        "family": (request.args.get("family") or "").strip(),
        "style": (request.args.get("style") or "").strip(),
        "country": (request.args.get("country") or "").strip(),
        "abv_min": (request.args.get("abv_min") or "").strip(),
        "abv_max": (request.args.get("abv_max") or "").strip(),
        "price_min": (request.args.get("price_min") or "").strip(),
        "price_max": (request.args.get("price_max") or "").strip(),
        "with_photo": (request.args.get("with_photo") or "").strip(),
    }
    page = max(int(request.args.get("page", 1)), 1)
    offset = (page - 1) * PAGE_SIZE

    where = ["1=1"]
    params: list = []
    if filters["name"]:
        where.append("name LIKE ?")
        params.append(f"%{filters['name']}%")
    if filters["producer"]:
        where.append("producer LIKE ?")
        params.append(f"%{filters['producer']}%")
    # Семья стилей (чипс) — приоритет, фильтрует по style_family
    if filters["family"] and filters["family"] in all_family_ids():
        where.append("style_family = ?")
        params.append(filters["family"])
    if filters["style"]:
        where.append("style = ?")
        params.append(filters["style"])
    if filters["country"]:
        where.append("brewery_country = ?")
        params.append(filters["country"])
    if filters["abv_min"]:
        try:
            where.append("abv >= ?")
            params.append(float(filters["abv_min"]))
        except ValueError:
            pass
    if filters["abv_max"]:
        try:
            where.append("abv <= ?")
            params.append(float(filters["abv_max"]))
        except ValueError:
            pass
    if filters["price_min"]:
        # цена в формате "379 ₽" — извлекаем число в подзапросе
        try:
            where.append(
                "CAST(REPLACE(REPLACE(REPLACE(price, ' ₽', ''), ' ', ''), CHAR(160), '') AS REAL) >= ?"
            )
            params.append(float(filters["price_min"]))
        except ValueError:
            pass
    if filters["price_max"]:
        try:
            where.append(
                "CAST(REPLACE(REPLACE(REPLACE(price, ' ₽', ''), ' ', ''), CHAR(160), '') AS REAL) <= ?"
            )
            params.append(float(filters["price_max"]))
        except ValueError:
            pass
    if filters["with_photo"]:
        where.append("local_image IS NOT NULL AND local_image != ''")

    where_sql = " AND ".join(where)
    beers = db.execute(
        f"SELECT id, name, producer, style, style_family, abv, volume, price, local_image "
        f"FROM products_full WHERE {where_sql} ORDER BY name LIMIT ? OFFSET ?",
        params + [PAGE_SIZE + 1, offset],
    ).fetchall()
    has_more = len(beers) > PAGE_SIZE
    beers = beers[:PAGE_SIZE]

    total_row = db.execute(
        f"SELECT COUNT(*) AS c FROM products_full WHERE {where_sql}", params
    ).fetchone()

    # Опции для фильтров: конкретные стили фильтруем по выбранной семье
    style_sql = (
        "SELECT DISTINCT style FROM products_full "
        "WHERE style IS NOT NULL AND style != ''"
    )
    style_params: list = []
    if filters["family"] and filters["family"] in all_family_ids():
        style_sql += " AND style_family = ?"
        style_params.append(filters["family"])
    style_sql += " ORDER BY style"
    style_options = [
        r["style"] for r in db.execute(style_sql, style_params).fetchall()
    ]
    country_options = [r["brewery_country"] for r in db.execute(
        "SELECT DISTINCT brewery_country FROM products_full WHERE brewery_country IS NOT NULL AND brewery_country != '' ORDER BY brewery_country"
    ).fetchall()]

    # Чипсы семей с кол-вом для UI
    family_chips = []
    for fid in all_family_ids():
        icon, title, _ = family_meta(fid)
        row = db.execute(
            "SELECT COUNT(*) AS n FROM products_full WHERE style_family = ?", (fid,)
        ).fetchone()
        family_chips.append({"id": fid, "icon": icon, "title": title, "count": row["n"]})
    family_chips.sort(key=lambda f: (f["id"] == "other", -f["count"]))

    next_page_params = {**request.args.to_dict(), "page": page + 1}

    return render_template(
        "catalog.html",
        beers=beers,
        total=total_row["c"],
        filters=filters,
        style_options=style_options,
        country_options=country_options,
        family_chips=family_chips,
        page=page,
        page_size=PAGE_SIZE,
        has_more=has_more,
        next_page_params=next_page_params,
    )


@app.route("/random")
def random_beer():
    db = get_db()
    # Приоритет — позициям с фото
    row = db.execute(
        "SELECT id FROM products_full WHERE local_image IS NOT NULL AND local_image != '' "
        "ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    if not row:
        row = db.execute("SELECT id FROM products_full ORDER BY RANDOM() LIMIT 1").fetchone()
    if not row:
        abort(404)
    return redirect(url_for("beer_detail", beer_id=row["id"]))


@app.route("/top")
def top():
    db = get_db()
    strongest = db.execute(
        "SELECT id, name, producer, abv, local_image FROM products_full "
        "WHERE abv IS NOT NULL ORDER BY abv DESC LIMIT 12"
    ).fetchall()
    lightest = db.execute(
        "SELECT id, name, producer, abv, local_image FROM products_full "
        "WHERE abv IS NOT NULL AND abv > 0.5 ORDER BY abv ASC LIMIT 12"
    ).fetchall()
    newest = db.execute(
        "SELECT id, name, producer, style, local_image FROM products_full "
        "WHERE parse_date IS NOT NULL ORDER BY parse_date DESC LIMIT 12"
    ).fetchall()
    # Самое дешёвое — нужно распарсить цену в SQL
    cheapest = db.execute(
        "SELECT id, name, producer, price, abv, local_image FROM products_full "
        "WHERE price LIKE '%₽' "
        "ORDER BY CAST(REPLACE(REPLACE(REPLACE(price, ' ₽', ''), ' ', ''), CHAR(160), '') AS REAL) ASC LIMIT 12"
    ).fetchall()
    return render_template(
        "top.html",
        strongest=strongest,
        lightest=lightest,
        newest=newest,
        cheapest=cheapest,
    )


@app.route("/status")
def status():
    """Дашборд прогресса парсинга в реальном времени."""
    db = get_db()
    cur = db.cursor()

    # Цель — все позиции в products_full
    total_target = cur.execute("SELECT COUNT(*) FROM products_full").fetchone()[0]

    # Прогресс из parse_progress
    rows = cur.execute(
        "SELECT status, COUNT(*) AS c FROM parse_progress GROUP BY status"
    ).fetchall()
    progress = {r["status"]: r["c"] for r in rows}
    ok = progress.get("ok", 0)
    errors = sum(v for k, v in progress.items() if k != "ok")
    total_done = ok + errors
    remaining = max(total_target - total_done, 0)
    pct = (total_done / total_target * 100) if total_target else 0
    success_rate = (ok / total_done * 100) if total_done else 0

    # Детализация ошибок по классам (только если есть ошибки)
    error_meta = {
        "error_network":   ("📡", "Сетевой сбой",     "обрыв/таймаут — перепроверить"),
        "error_blocked":   ("🚫", "Блокировка",       "403/429 — пауза и повтор"),
        "error_server":    ("🔧", "Ошибка сервера",    "5xx — временно, перепроверить"),
        "error_permanent": ("🗑️", "Удалена 404",      "страницы нет — пропустить"),
        "parse_error":     ("⚠️", "Старый формат",     "до классификации, перепроверить"),
        "save_error":      ("💾", "Ошибка БД",         "проблема записи, перепроверить"),
    }
    error_breakdown = []
    for status, count in sorted(progress.items()):
        if status == "ok" or count == 0:
            continue
        icon, title, hint = error_meta.get(status, ("❓", status, ""))
        error_breakdown.append({
            "status": status,
            "icon": icon,
            "title": title,
            "count": count,
            "hint": hint,
        })

    # Хронология (первая/последняя запись в parse_progress)
    chrono = cur.execute(
        "SELECT MIN(updated_at) AS mn, MAX(updated_at) AS mx FROM parse_progress"
    ).fetchone()
    first_seen = chrono["mn"] if chrono else None
    last_seen = chrono["mx"] if chrono else None

    # ETA: берём скорость по последним ~50 записям (расчёт в SQL, в UTC)
    eta_text = ""
    try:
        # Разница между самой старой и новой из последних 50 записей (в сек)
        span_row = cur.execute(
            "SELECT CAST(strftime('%s', MAX(updated_at)) AS INTEGER) - "
            "CAST(strftime('%s', MIN(updated_at)) AS INTEGER) AS span, "
            "COUNT(*) AS n FROM ("
            "  SELECT updated_at FROM parse_progress ORDER BY updated_at DESC LIMIT 50"
            ")"
        ).fetchone()
        if span_row and span_row["n"] and span_row["n"] >= 10 and span_row["span"] and span_row["span"] > 0:
            speed = span_row["n"] / span_row["span"]
            if speed > 0:
                eta_sec = remaining / speed
                if eta_sec >= 3600:
                    eta_text = f"{int(eta_sec // 3600)} ч {int((eta_sec % 3600) // 60)} мин"
                elif eta_sec >= 60:
                    eta_text = f"{int(eta_sec // 60)} мин"
                else:
                    eta_text = f"{int(eta_sec)} сек"
    except Exception:
        pass

    # Наполненность по ключевым полям (живые данные)
    fields_map = [
        ("name", "Название"),
        ("style", "Стиль"),
        ("abv", "Крепость"),
        ("ibu", "IBU"),
        ("description", "Описание"),
        ("ingredients", "Состав"),
        ("og_value", "Плотность OG"),
        ("barcode", "Штрихкод"),
        ("price", "Цена"),
        ("color", "Цвет"),
        ("local_image", "Кеш фото"),
    ]
    fillness = []
    for field, label in fields_map:
        n = cur.execute(
            f"SELECT COUNT(*) FROM products_full WHERE {field} IS NOT NULL AND CAST({field} AS TEXT) != ''"
        ).fetchone()[0]
        fillness.append({
            "field": field,
            "label": label,
            "count": n,
            "total": total_target,
            "pct": (n / total_target * 100) if total_target else 0,
        })

    # Доп. метрики
    with_photos = cur.execute(
        "SELECT COUNT(*) FROM products_full WHERE local_image IS NOT NULL AND local_image != ''"
    ).fetchone()[0]
    styles_in_guide = cur.execute("SELECT COUNT(*) FROM beer_styles").fetchone()[0]

    # ===================================================================
    # ОПРЕДЕЛЕНИЕ СОСТОЯНИЯ ПАРСЕРА (жив/висит/завершён)
    # Ключевая логика дашборда: сравниваем время последней записи в
    # parse_progress с текущим. Если запись свежая (< 90 сек) — парсер
    # активен. Если давно (> 90 сек) и есть что парсить — скорее всего висит
    # или в паузе circuit breaker.
    #
    # ВАЖНО: SQLite CURRENT_TIMESTAMP хранится в UTC, а datetime.now() —
    # локальное время. Поэтому разницу считаем ВНУТРИ SQL через
    # strftime('%s', 'now') — тоже UTC, корректное сравнение.
    # ===================================================================
    health = "idle"
    health_icon = "⚪"
    health_title = "Парсер не запущен"
    health_sub = "Запустите: python craftbeer_global_parser.py --refresh"
    last_activity_ago = None

    if last_seen:
        # Считаем возраст последней записи прямо в SQL (обе даты в UTC)
        age_row = cur.execute(
            "SELECT CAST(strftime('%s', 'now') AS INTEGER) - "
            "CAST(strftime('%s', MAX(updated_at)) AS INTEGER) AS age "
            "FROM parse_progress"
        ).fetchone()
        if age_row and age_row["age"] is not None:
            last_activity_ago = age_row["age"]

    if total_done == 0:
        health = "idle"
        health_icon = "⚪"
        health_title = "Парсинг ещё не запускался"
        health_sub = "Запустите: python craftbeer_global_parser.py --refresh"
    elif remaining == 0:
        if errors == 0:
            health = "done"
            health_icon = "🎉"
            health_title = "Парсинг полностью завершён"
            health_sub = f"Все {total_target} позиций обработаны успешно"
        else:
            health = "warn"
            health_icon = "⚠️"
            health_title = "Парсинг завершён с ошибками"
            health_sub = f"{errors} ошибок — перепроверьте: --refresh --failed-only"
    elif last_activity_ago is not None:
        if last_activity_ago < 90:
            health = "running"
            health_icon = "🟢"
            ago = f"{int(last_activity_ago)} сек назад"
            health_title = "Парсер работает"
            health_sub = f"Последняя запись: {ago} · успешность {success_rate:.0f}%"
        elif last_activity_ago < 300:
            health = "waiting"
            health_icon = "🟡"
            ago_min = int(last_activity_ago // 60)
            ago_sec = int(last_activity_ago % 60)
            health_title = "Парсер в паузе"
            health_sub = (
                f"Нет активности {ago_min} мин {ago_sec} сек — вероятно "
                f"circuit breaker или медленный ответ сайта"
            )
        else:
            health = "stalled"
            health_icon = "🔴"
            ago_min = int(last_activity_ago // 60)
            health_title = "Парсер похоже завис"
            health_sub = (
                f"Нет активности {ago_min} мин. Перезапустите: "
                f"python craftbeer_global_parser.py --refresh (продолжит с места)"
            )

    return render_template(
        "status.html",
        total_target=total_target,
        total_done=total_done,
        ok=ok,
        errors=errors,
        remaining=remaining,
        pct=pct,
        success_rate=success_rate,
        eta_text=eta_text,
        fillness=fillness,
        with_photos=with_photos,
        styles_in_guide=styles_in_guide,
        error_breakdown=error_breakdown,
        health=health,
        health_icon=health_icon,
        health_title=health_title,
        health_sub=health_sub,
        last_seen=last_seen,
    )


@app.route("/compare")
def compare():
    db = get_db()
    id1 = request.args.get("id1", type=int)
    id2 = request.args.get("id2", type=int)

    beer1 = None
    beer2 = None
    if id1:
        beer1 = db.execute("SELECT * FROM products_full WHERE id = ?", (id1,)).fetchone()
    if id2:
        beer2 = db.execute("SELECT * FROM products_full WHERE id = ?", (id2,)).fetchone()

    # Список для выбора второй позиции
    all_beers = db.execute(
        "SELECT id, name, producer FROM products_full ORDER BY name LIMIT 500"
    ).fetchall()

    return render_template("compare.html", beer1=beer1, beer2=beer2, all_beers=all_beers)


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found(_e):
    html = (
        "<div class='empty-state'>"
        "<div class='big'>404</div>"
        "<p>Страница не найдена</p>"
        "<p><a class='btn btn-primary' href='/'>На главную</a></p>"
        "</div>"
    )
    return render_template_string_404(html), 404


def render_template_string_404(content_html: str):
    """Рендер 404 с минимальным base-каркасом."""
    from flask import render_template_string
    return render_template_string(
        """
        <!doctype html>
        <html lang="ru"><head><meta charset="utf-8">
        <title>404 — Пивная энциклопедия</title>
        <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
        </head><body>
        <header class="site-header"><div class="header-inner">
          <a class="logo" href="{{ url_for('index') }}">🍺 <span>Пивная энциклопедия</span></a>
        </div></header>
        <main class="container">""" + content_html + """</main>
        </body></html>
        """
    )


if __name__ == "__main__":
    print("🍺 Пивная энциклопедия запускается на http://127.0.0.1:8000")
    app.run(host="127.0.0.1", port=8000, debug=True)
