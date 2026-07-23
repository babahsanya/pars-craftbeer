"""Заполнение колонки style_family в products_full.

Запускается один раз для существующих данных + автоматически после refresh-парсинга
(подключён в run_full_pipeline.py). Idempotent: повторный запуск обновляет family
для всех позиций заново (полезно при правке правил в style_families.py).

CLI:
    python normalize_styles.py            # заполнить + статистика
    python normalize_styles.py --stats    # только статистика
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from style_families import classify_style, family_meta, all_family_ids

APP_ROOT = Path(__file__).resolve().parent
DB_PATH = APP_ROOT / "beer_database.db"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("normalize_styles")


def ensure_column() -> bool:
    """Добавляет колонку style_family если её нет. Возвращает True если добавил."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(products_full)")
        existing = {row[1] for row in cur.fetchall()}
        if "style_family" not in existing:
            conn.execute("ALTER TABLE products_full ADD COLUMN style_family TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_products_style_family "
                "ON products_full(style_family)"
            )
            conn.commit()
            log.info("Добавлена колонка style_family + индекс")
            return True
        # индекс мог отсутствовать если колонка добавлена без него
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_style_family "
            "ON products_full(style_family)"
        )
        conn.commit()
        return False
    finally:
        conn.close()


def populate() -> tuple[int, int]:
    """Заполняет style_family для всех строк. Возвращает (всего, классифицированных)."""
    conn = sqlite3.connect(DB_PATH)
    total = 0
    classified = 0  # всё что не 'other'
    try:
        # Читаем батчами, обновляем по одной (или батчем через UPDATE)
        # Для 7733 строк проще: один SELECT + пакетный UPDATE с executemany по id
        cur = conn.execute(
            "SELECT id, style FROM products_full ORDER BY id"
        )
        rows = cur.fetchall()
        updates: list[tuple[str, int]] = []
        for beer_id, style in rows:
            family = classify_style(style)
            updates.append((family, beer_id))
            total += 1
            if family != "other":
                classified += 1

        conn.executemany(
            "UPDATE products_full SET style_family = ? WHERE id = ?", updates
        )
        conn.commit()
    finally:
        conn.close()
    return total, classified


def show_stats() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        # Распределение по семьям
        print("=" * 60)
        print("Распределение позиций по семьям стилей")
        print("=" * 60)
        cur = conn.execute(
            "SELECT style_family, COUNT(*) AS n FROM products_full "
            "GROUP BY style_family ORDER BY n DESC"
        )
        rows = cur.fetchall()
        total_all = sum(n for _, n in rows)
        for family_id, n in rows:
            icon, title, _ = family_meta(family_id if family_id else "other")
            pct = 100 * n / total_all if total_all else 0
            # сколько уникальных стилей в семье
            cur2 = conn.execute(
                "SELECT COUNT(DISTINCT style) FROM products_full WHERE style_family = ?",
                (family_id,),
            )
            styles_n = cur2.fetchone()[0]
            print(f"  {icon} {title:28s} {n:5d} ({pct:5.1f}%)  {styles_n:3d} стилей")
        print(f"  {'ИТОГО':30s} {total_all:5d}")

        # Не классифицировано
        cur3 = conn.execute(
            "SELECT COUNT(*) FROM products_full WHERE style_family IS NULL"
        )
        none_n = cur3.fetchone()[0]
        print(f"\nБез family (NULL): {none_n}")
        return 0
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Нормализация стилей → семьи")
    ap.add_argument("--stats", action="store_true", help="Только показать статистику")
    args = ap.parse_args()

    if not DB_PATH.exists():
        log.error("База не найдена: %s", DB_PATH)
        return 2

    if args.stats:
        return show_stats()

    ensure_column()
    total, classified = populate()
    log.info(
        "Готово: %d позиций обработано, %d (%.1f%%) классифицированы в 15 семей",
        total,
        classified,
        (100 * classified / total if total else 0),
    )
    show_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
