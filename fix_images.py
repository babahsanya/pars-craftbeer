"""Одноразовая миграция: очистить чужие фото из additional_images и local_gallery.

Проблема: старый парсер собирал все img[src*="/images_beers/"] со страницы,
включая 20 фото из блока «похожие товары» (div.beer_logo). В результате
в галерее каждого пива лежали чужие картинки.

Решение: для каждой позиции оставляем только те URL из additional_images,
имя файла которых содержит slug этого товара (из original_url).
slug 'st-louis-premium-kriek-...' должен быть в имени файла настоящего фото.

Также:
- Обновляет additional_images (оставляет только свои URL)
- Обновляет image_url (первый свой)
- Сбрасывает local_image/local_gallery (перескачает image_cache.py)
- Удаляет старые локальные файлы галереи (чужие)

CLI:
    python fix_images.py            # применить
    python fix_images.py --dry-run  # только отчёт, без изменений
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
DB_PATH = APP_ROOT / "beer_database.db"
IMAGES_ROOT = APP_ROOT / "static" / "images"


def normalize_for_match(s: str) -> str:
    """Нормализация для сравнения slug и имени файла: только a-z0-9."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def slug_from_url(url: str) -> str:
    """Достаёт slug товара из original_url."""
    return url.rstrip("/").split("/")[-1].lower()


def filter_own_images(original_url: str, additional_images: str) -> list[str]:
    """Оставляет только URL фотографий, принадлежащих этому товару.

    Критерий: нормализованный slug (первые ~20 символов) присутствует в
    нормализованном имени файла фото. Это надёжно: craftbeer78.ru называет
    файлы фото по slugу товара.
    """
    if not additional_images or not original_url:
        return []
    try:
        arr = json.loads(additional_images)
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
        return []

    slug = slug_from_url(original_url)
    if not slug:
        return []
    slug_norm = normalize_for_match(slug)
    # Берём первые 20 символов slugа для совпадения — достаточно уникально.
    slug_prefix = slug_norm[:20]
    if len(slug_prefix) < 6:
        return []  # слишком короткий slug — рискованно

    own: list[str] = []
    seen: set[str] = set()
    for u in arr:
        if not u or "/images_beers/" not in u:
            continue
        fname = u.split("/")[-1]
        fname_norm = normalize_for_match(fname)
        if slug_prefix in fname_norm and u not in seen:
            seen.add(u)
            own.append(u)
    return own


def main() -> int:
    ap = argparse.ArgumentParser(description="Очистка чужих фото из галерей")
    ap.add_argument("--dry-run", action="store_true", help="Только отчёт, без изменений")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"База не найдена: {DB_PATH}")
        return 2

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, original_url, image_url, additional_images, local_image, local_gallery "
        "FROM products_full ORDER BY id"
    ).fetchall()

    stats = {
        "total": 0,
        "had_foreign": 0,        # у скольких были чужие фото
        "images_removed": 0,     # всего удалено чужих URL
        "no_own_image": 0,       # нет ни одного своего фото после фильтра
        "local_dirs_deleted": 0, # удалено папок static/images/<id>
    }

    for row in rows:
        stats["total"] += 1
        own = filter_own_images(row["original_url"], row["additional_images"])
        old_count = 0
        try:
            old_arr = json.loads(row["additional_images"] or "[]")
            old_count = sum(1 for u in old_arr if "/images_beers/" in u)
        except (ValueError, TypeError):
            pass

        removed = old_count - len(own)
        if removed > 0:
            stats["had_foreign"] += 1
            stats["images_removed"] += removed

        if not own:
            # Нет своего фото — сбрасываем всё
            if old_count > 0:
                stats["no_own_image"] += 1
            if not args.dry_run:
                # удаляем локальную папку если была
                beer_dir = IMAGES_ROOT / str(row["id"])
                if beer_dir.exists():
                    shutil.rmtree(beer_dir, ignore_errors=True)
                    stats["local_dirs_deleted"] += 1
                cur.execute(
                    "UPDATE products_full SET image_url = NULL, "
                    "additional_images = NULL, local_image = NULL, local_gallery = NULL "
                    "WHERE id = ?",
                    (row["id"],),
                )
            continue

        new_image_url = own[0]
        new_additional = json.dumps(own, ensure_ascii=False)
        needs_update = (
            new_image_url != row["image_url"]
            or new_additional != row["additional_images"]
        )

        if needs_update and not args.dry_run:
            # Сбрасываем local_gallery (старые чужие файлы) — local_image
            # обновится при перескачке. Удаляем всю папку, чтобы кеш
            # пересоздался чисто.
            beer_dir = IMAGES_ROOT / str(row["id"])
            if beer_dir.exists():
                shutil.rmtree(beer_dir, ignore_errors=True)
                stats["local_dirs_deleted"] += 1
            cur.execute(
                "UPDATE products_full SET image_url = ?, additional_images = ?, "
                "local_image = NULL, local_gallery = NULL WHERE id = ?",
                (new_image_url, new_additional, row["id"]),
            )

    if not args.dry_run:
        conn.commit()
    conn.close()

    print("=" * 60)
    print("Отчёт очистки фото" + (" (DRY RUN, изменения не применены)" if args.dry_run else ""))
    print("=" * 60)
    print(f"Всего позиций проверено:     {stats['total']}")
    print(f"С чужими фото (исправлено):  {stats['had_foreign']}")
    print(f"Чужих URL удалено:           {stats['images_removed']}")
    print(f"Без своего фото (сброшено):  {stats['no_own_image']}")
    print(f"Локальных папок удалено:     {stats['local_dirs_deleted']}")
    print()
    print("Далее запустите: python image_cache.py  (перескачать правильные фото)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
