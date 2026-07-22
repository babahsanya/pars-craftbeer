"""Локальный кеш картинок пива.

Проходит по всем products_full, фильтрует additional_images (только /images_beers/),
скачивает через пул потоков и сохраняет в static/images/<id>/main.jpg и
static/images/<id>/gallery/<N>.jpg.

Idempotent: уже скачанные позиции пропускаются. Можно прерывать и перезапускать.

CLI:
    python image_cache.py                  # полный прогон
    python image_cache.py --limit 100      # первые 100 позиций (по id)
    python image_cache.py --max-per-beer 3 # максимум 3 фото на позицию
    python image_cache.py --force          # перекачать даже уже скачанные
    python image_cache.py --workers 4      # кол-во потоков
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

APP_ROOT = Path(__file__).resolve().parent
DB_PATH = APP_ROOT / "beer_database.db"
IMAGES_ROOT = APP_ROOT / "static" / "images"
LOG_PATH = APP_ROOT / "image_cache.log"

MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 МБ — отсекаем случайные огромные файлы
REQUEST_TIMEOUT = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("image_cache")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            "Referer": "https://craftbeer78.ru/",
        }
    )
    retry = Retry(
        total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504)
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


SESSION = make_session()


def is_beer_photo(url: str) -> bool:
    """Только реальные фото пива. Совпадает с фильтром в парсере."""
    u = (url or "").lower()
    if "/images_beers/" not in u:
        return False
    if u.endswith(".svg"):
        return False
    if "menu_" in u or "logo" in u or "banner" in u or "icon" in u:
        return False
    return True


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("/"):
        url = "https://craftbeer78.ru" + url
    return url


def extract_urls(additional_images: str | None, image_url: str | None) -> list[str]:
    """Достаём и фильтруем список URL картинок для позиции."""
    urls: list[str] = []
    if additional_images:
        try:
            parsed = json.loads(additional_images)
            if isinstance(parsed, list):
                urls.extend(str(u) for u in parsed if u)
        except (ValueError, TypeError):
            pass
    if image_url:
        urls.append(image_url)
    # нормализация + фильтр + дедуп
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        n = normalize_url(u)
        if n and is_beer_photo(n) and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def ext_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"):
        if path.endswith(ext):
            return ext
    return ".jpg"


def download_one(url: str, dest: Path) -> bool:
    """Скачивает один файл. Возвращает True при успехе."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        if resp.status_code != 200:
            log.debug("HTTP %s для %s", resp.status_code, url)
            return False
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if content_type and not content_type.startswith("image/"):
            log.debug("Не-картинка (%s): %s", content_type, url)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=16 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    log.debug("Слишком большой файл: %s", url)
                    return False
                f.write(chunk)
        tmp.replace(dest)
        return True
    except requests.RequestException as e:
        log.debug("Ошибка загрузки %s: %s", url, e)
        return False
    except OSError as e:
        log.debug("IO ошибка %s: %s", url, e)
        return False


def cache_beer(beer_id: int, urls: list[str], max_per_beer: int, force: bool) -> tuple[int, list[str]]:
    """Скачивает картинки для одной позиции. Возвращает (id, список локальных путей)."""
    if not urls:
        return beer_id, []

    beer_dir = IMAGES_ROOT / str(beer_id)
    gallery_dir = beer_dir / "gallery"

    # Главное фото
    main_ext = ext_from_url(urls[0])
    main_path = beer_dir / f"main{main_ext}"

    if force and main_path.exists():
        main_path.unlink()

    saved_paths: list[str] = []

    if download_one(urls[0], main_path):
        saved_paths.append(f"static/images/{beer_id}/{main_path.name}")

    # Галерея (остальные, до max_per_beer-1)
    if max_per_beer > 1 and len(urls) > 1:
        gallery_urls = urls[1 : max_per_beer]
        for idx, url in enumerate(gallery_urls, start=1):
            ext = ext_from_url(url)
            gpath = gallery_dir / f"{idx}{ext}"
            if force and gpath.exists():
                gpath.unlink()
            if download_one(url, gpath):
                saved_paths.append(f"static/images/{beer_id}/gallery/{gpath.name}")

    return beer_id, saved_paths


def iter_beers(limit: int | None) -> Iterable[tuple[int, str | None, str | None]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        sql = "SELECT id, image_url, additional_images FROM products_full ORDER BY id"
        params: tuple = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        cur.execute(sql, params)
        for row in cur:
            yield row[0], row[1], row[2]
    finally:
        conn.close()


def ensure_local_columns() -> None:
    """Добавляет колонки local_image/local_gallery, если их нет."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(products_full)")
        existing = {row[1] for row in cur.fetchall()}
        if "local_image" not in existing:
            cur.execute("ALTER TABLE products_full ADD COLUMN local_image TEXT")
            log.info("Добавлена колонка local_image")
        if "local_gallery" not in existing:
            cur.execute("ALTER TABLE products_full ADD COLUMN local_gallery TEXT")
            log.info("Добавлена колонка local_gallery")
        conn.commit()
    finally:
        conn.close()


def save_local_to_db(beer_id: int, paths: list[str]) -> None:
    """Сохраняет главный путь и список галерейных в БД."""
    if not paths:
        return
    main = paths[0]
    gallery = paths[1:]
    gallery_json = json.dumps(gallery, ensure_ascii=False) if gallery else None
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE products_full SET local_image = ?, local_gallery = ? WHERE id = ?",
            (main, gallery_json, beer_id),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Кеш картинок пива в static/images/")
    ap.add_argument("--limit", type=int, default=None, help="Ограничить кол-во позиций")
    ap.add_argument(
        "--max-per-beer", type=int, default=5, help="Максимум фото на позицию (1+)"
    )
    ap.add_argument("--force", action="store_true", help="Перекачать существующие")
    ap.add_argument("--workers", type=int, default=8, help="Потоков скачивания")
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Пропускать позиции, где local_image уже заполнен (по умолчанию вкл.)",
    )
    ap.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Обрабатывать даже позиции с уже заполненным local_image",
    )
    args = ap.parse_args()

    if args.max_per_beer < 1:
        log.error("--max-per-beer должен быть >= 1")
        return 2

    if not DB_PATH.exists():
        log.error("База не найдена: %s", DB_PATH)
        return 2

    IMAGES_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_local_columns()

    # Загружаем набор для обработки
    todo: list[tuple[int, list[str]]] = []
    skipped_existing = 0
    skipped_no_urls = 0

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for beer_id, image_url, additional_images in iter_beers(args.limit):
        if args.skip_existing and not args.force:
            cur.execute("SELECT local_image FROM products_full WHERE id = ?", (beer_id,))
            row = cur.fetchone()
            if row and row[0]:
                skipped_existing += 1
                continue
        urls = extract_urls(additional_images, image_url)
        if not urls:
            skipped_no_urls += 1
            continue
        todo.append((beer_id, urls))
    conn.close()

    log.info(
        "К обработке: %d позиций (пропущено: %d с кешем, %d без URL)",
        len(todo),
        skipped_existing,
        skipped_no_urls,
    )

    if not todo:
        log.info("Нечего делать. Выход.")
        return 0

    start = time.time()
    success = 0
    failed = 0
    processed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(cache_beer, beer_id, urls, args.max_per_beer, args.force): beer_id
            for beer_id, urls in todo
        }
        for fut in as_completed(futures):
            beer_id = futures[fut]
            processed += 1
            try:
                _, paths = fut.result()
            except Exception as e:
                log.error("Ошибка для id=%d: %s", beer_id, e)
                failed += 1
                continue
            if paths:
                save_local_to_db(beer_id, paths)
                success += 1
            else:
                failed += 1
            if processed % 25 == 0:
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed > 0 else 0
                log.info(
                    "Прогресс: %d/%d (ok=%d, fail=%d), %.1f поз/сек",
                    processed,
                    len(todo),
                    success,
                    failed,
                    rate,
                )

    elapsed = time.time() - start
    log.info(
        "Готово. Обработано: %d, успешно: %d, ошибок: %d, время: %.1f сек",
        processed,
        success,
        failed,
        elapsed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
