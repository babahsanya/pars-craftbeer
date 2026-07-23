#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("craftbeer_global_parser.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


class CraftBeerGlobalParser:
    def __init__(self):
        self.base_url = "https://craftbeer78.ru"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )

        retries = Retry(
            total=0,  # ВАЖНО: 0 — urllib3 НЕ делает своих retry.
            # Всю retry-логику ведёт _fetch_with_retry (3 попытки с паузами 5/15/30с).
            # Раньше тут было total=3, что давало двойной retry (urllib3 + наш) и
            # при первой же ошибке session «отравлялась» и кидала RetryError
            # на каждый последующий запрос без реальной попытки.
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Задержки (сек)
        self.request_delay = 0.4
        self.base_request_delay = 0.4  # исходная, для адаптивного замедления
        self.discovery_delay = 0.2
        self.page_delay = 0.2

        self.discovered_urls = set()
        self.processed_urls = set()
        self.failed_urls = set()

        # --- Состояние надёжности ---
        # Флаг аккуратного завершения (ставится signal handler при Ctrl+C).
        # Главный цикл проверяет его между URL — позволяет закончить текущий запрос.
        self.shutdown_requested = False
        # Circuit breaker: счётчик подряд идущих ошибок.
        # При достижении threshold делаем паузу и проверяем живость сайта.
        self.consecutive_errors = 0
        self.circuit_threshold = 10
        # Лимит per-URL попыток при сетевых сбоях (не путать с urllib3 retries).
        self.url_max_retries = 3
        # Паузы между per-URL попытками (в секундах): 5, 15, 30
        self.url_retry_delays = (5, 15, 30)
        # Пауза circuit breaker при массовых сбоях (сек)
        self.circuit_pause = 60
        # Пауза при явной блокировке (429/503) (сек)
        self.block_pause = 120

        self.stats = {
            "total_discovered": 0,
            "total_processed": 0,
            "successful_parses": 0,
            "failed_parses": 0,
            "database_updates": 0,
            "retries_used": 0,
            "circuit_pauses": 0,
        }

    def _normalize_url(self, url):
        parsed = urlparse(url)
        normalized = parsed._replace(fragment="").geturl()
        if normalized.endswith("/"):
            normalized = normalized.rstrip("/")
        return normalized

    # ------------------------------------------------------------------
    # Надёжный HTTP-запрос: retry на сетевые сбои + circuit breaker.
    # ------------------------------------------------------------------
    def _fetch_with_retry(self, url):
        """Запрашивает URL с per-URL retry и circuit breaker.

        Возвращает кортеж (response | None, error_class) где error_class:
          - None          — успех, response валидный (raise_for_status уже сделан)
          - 'permanent'   — 404/410, страница удалена (повторять бессмысленно)
          - 'blocked'     — 403/429, блокировка/лимит (требуется пауза)
          - 'server'      — 5xx от сервера (временная, повторяем)
          - 'network'     — таймаут/обрыв связи (повторяем)
          - 'shutdown'    — запрошен выход через Ctrl+C
        """
        last_error = None
        for attempt in range(self.url_max_retries):
            # Проверяем shutdown между попытками
            if self.shutdown_requested:
                return None, "shutdown"

            try:
                resp = self.session.get(url, timeout=15)

                # 200 OK — успех
                if resp.status_code == 200:
                    self.consecutive_errors = 0
                    # Возвращаемся к нормальной скорости после замедления
                    self.request_delay = self.base_request_delay
                    return resp, None

                # Классификация HTTP-ошибок
                if resp.status_code in (404, 410):
                    last_error = "permanent"
                    break  # повторять бессмысленно — выходим сразу
                elif resp.status_code in (403, 429):
                    last_error = "blocked"
                    # Замедляемся при блокировке
                    self._throttle()
                elif 500 <= resp.status_code < 600:
                    last_error = "server"
                else:
                    last_error = "server"

                logging.warning(
                    f"   ⚠ HTTP {resp.status_code} (попытка {attempt+1}/{self.url_max_retries})"
                )

            except requests.exceptions.Timeout:
                last_error = "network"
                logging.warning(
                    f"   ⚠ Таймаут (попытка {attempt+1}/{self.url_max_retries})"
                )
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ) as e:
                last_error = "network"
                logging.warning(
                    f"   ⚠ Обрыв связи: {type(e).__name__} (попытка {attempt+1}/{self.url_max_retries})"
                )
            except requests.exceptions.RequestException as e:
                last_error = "network"
                logging.warning(
                    f"   ⚠ Сетевая ошибка: {type(e).__name__} (попытка {attempt+1}/{self.url_max_retries})"
                )

            # Пауза перед следующей попыткой (кроме последней и permanent)
            if attempt < self.url_max_retries - 1 and last_error != "permanent":
                delay = self.url_retry_delays[
                    min(attempt, len(self.url_retry_delays) - 1)
                ]
                logging.info(f"   ⏳ Повтор через {delay} сек...")
                for _ in range(delay):
                    if self.shutdown_requested:
                        return None, "shutdown"
                    time.sleep(1)
                self.stats["retries_used"] += 1

        # Все попытки исчерпаны
        self._register_error()
        return None, last_error or "network"

    def _throttle(self):
        """Замедляет запросы при признаках блокировки (429/403)."""
        new_delay = min(self.request_delay * 2, 3.0)
        if new_delay != self.request_delay:
            logging.info(
                f"   🐌 Замедление: задержка {self.request_delay} → {new_delay} сек"
            )
            self.request_delay = new_delay

    def _register_error(self):
        """Регистрирует ошибку в circuit breaker. При пороге — пауза."""
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.circuit_threshold:
            self._trigger_circuit_breaker()

    def _trigger_circuit_breaker(self):
        """Массовые ошибки подряд — сайт скорее всего лежит. Пауза + проверка."""
        self.stats["circuit_pauses"] += 1
        logging.warning(
            f"   🛑 Circuit breaker: {self.consecutive_errors} ошибок подряд. "
            f"Сайт кажется недоступным — пауза {self.circuit_pause} сек."
        )
        # Паузим с возможностью раннего выхода по shutdown
        for i in range(self.circuit_pause):
            if self.shutdown_requested:
                return
            time.sleep(1)
        # Проверяем живость: пробуем базовый URL
        try:
            r = self.session.get(self.base_url, timeout=10)
            if r.status_code == 200:
                logging.info("   ✅ Сайт снова отвечает. Продолжаем.")
                self.consecutive_errors = 0
                self.request_delay = self.base_request_delay
            else:
                logging.warning(
                    f"   ⚠ Сайт отвечает {r.status_code}. Продолжаем с осторожностью."
                )
        except Exception:
            logging.error(
                f"   ❌ Сайт всё ещё недоступен. Продолжаем, но ошибки вероятны."
            )

    def create_enhanced_database(self):
        """Создаем улучшенную структуру базы данных"""

        logging.info("🗄️ Создание улучшенной структуры базы данных...")

        conn = sqlite3.connect("beer_database.db")
        cursor = conn.cursor()

        # Создаем новую таблицу для полных данных
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products_full (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Основная информация
                name TEXT NOT NULL,
                producer TEXT,
                brewery_full_name TEXT,
                brewery_country TEXT,
                brewery_city TEXT,

                -- Характеристики напитка
                category TEXT,  -- пиво, сидр, медовуха
                style TEXT,
                substyle TEXT,
                abv REAL,       -- крепость
                volume INTEGER, -- объем в мл
                ibu INTEGER,    -- горечь
                og_value TEXT,  -- плотность (OG) из additionalProperty
                color TEXT,     -- цвет

                -- Органолептические свойства
                aroma TEXT,
                taste TEXT,
                description TEXT,
                mouthfeel TEXT,
                appearance TEXT,

                -- Дополнительная информация
                ingredients TEXT,
                hops TEXT,
                malt TEXT,
                yeast TEXT,
                additives TEXT,

                -- Коммерческая информация
                price TEXT,
                availability TEXT,
                barcode TEXT,

                -- Рейтинги и отзывы
                rating REAL,
                rating_count INTEGER,
                reviews_count INTEGER,

                -- Парные продукты
                food_pairing TEXT,
                serving_temp TEXT,
                serving_glass TEXT,

                -- Техническая информация
                original_url TEXT UNIQUE,
                url_hash TEXT UNIQUE,
                image_url TEXT,
                additional_images TEXT, -- JSON массив
                local_image TEXT,       -- путь к локальному кешу
                local_gallery TEXT,     -- JSON массив локальных путей галереи

                -- Метаданные парсинга
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                parse_date TIMESTAMP,
                parse_success INTEGER DEFAULT 0,
                parse_attempts INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """
        )

        # Создаем индексы для быстрого поиска
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_products_name ON products_full(name)",
            "CREATE INDEX IF NOT EXISTS idx_products_producer ON products_full(producer)",
            "CREATE INDEX IF NOT EXISTS idx_products_category ON products_full(category)",
            "CREATE INDEX IF NOT EXISTS idx_products_url_hash ON products_full(url_hash)",
            "CREATE INDEX IF NOT EXISTS idx_products_parse_success ON products_full(parse_success)",
        ]

        for index_sql in indexes:
            cursor.execute(index_sql)

        conn.commit()
        conn.close()

        logging.info("✅ База данных создана и проиндексирована")

    def discover_all_product_urls(self):
        """Полное исследование сайта для поиска всех товаров"""

        logging.info("🔍 ГЛОБАЛЬНОЕ ИССЛЕДОВАНИЕ САЙТА CRAFTBEER78.RU")
        logging.info("=" * 60)

        # 1. Основные каталоги (правильные URL)
        main_catalogs = [
            "/bottled-beers",  # пиво
            "/bottled-cider",  # сидр
            "/bottled-mead",  # медовуха
        ]

        for catalog in main_catalogs:
            self._explore_catalog(catalog)
            time.sleep(self.discovery_delay)

        # 2. Пагинация в каталогах
        self._explore_catalog_pagination()

        # 3. Дополнительные разделы с товарами
        additional_sections = [
            "/novinki-piva-i-cidra",  # новинки
            "/beer-style/ipa-india-pale-ale",  # IPA
            "/beer-style/smoothie-beer",  # смузи
            "/beer-style/alcohol-free",  # безалкогольное
        ]

        for section in additional_sections:
            self._explore_section(section)
            time.sleep(self.discovery_delay)

        # 4. Sitemap (если доступен)
        self._explore_sitemap()

        logging.info(
            f"🎯 ВСЕГО ОБНАРУЖЕНО: {len(self.discovered_urls)} уникальных товаров"
        )
        self.stats["total_discovered"] = len(self.discovered_urls)

        return list(self.discovered_urls)

    def _explore_catalog(self, catalog_url):
        """Исследуем основной каталог"""

        logging.info(f"📂 Исследуем каталог: {catalog_url}")

        try:
            url = self.base_url + catalog_url
            response = self.session.get(url, timeout=15)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")

                # Ищем все ссылки на товары
                links = soup.find_all("a", href=True)
                catalog_count = 0

                for link in links:
                    if isinstance(link, Tag):
                        href = str(link.get("href", ""))

                        if href and self._is_product_link(href):
                            full_url = self._normalize_url(urljoin(self.base_url, href))

                            if full_url not in self.discovered_urls:
                                self.discovered_urls.add(full_url)
                                catalog_count += 1

                logging.info(f"   ✅ Найдено: {catalog_count} товаров")

                # Проверяем есть ли пагинация
                pagination_links = soup.find_all("a", href=True)
                for link in pagination_links:
                    if isinstance(link, Tag):
                        href = str(link.get("href", ""))
                        text = link.get_text(strip=True).lower()

                        if href and (
                            "page=" in href
                            or "следующая" in text
                            or "next" in text
                            or text.isdigit()
                        ):
                            # Найдена пагинация
                            self._explore_catalog_pages(catalog_url, soup)
                            break

            else:
                logging.warning(
                    f"❌ Каталог {catalog_url} недоступен: {response.status_code}"
                )

        except Exception as e:
            logging.error(f"❌ Ошибка в каталоге {catalog_url}: {e}")

    def _explore_catalog_pages(self, catalog_url, first_page_soup):
        """Исследуем страницы каталога"""

        logging.info(f"📄 Исследуем пагинацию каталога: {catalog_url}")

        # Определяем максимальное количество страниц
        max_page = 1
        pagination_links = first_page_soup.find_all("a", href=True)

        for link in pagination_links:
            if isinstance(link, Tag):
                href = str(link.get("href", ""))
                if "page=" in href:
                    match = re.search(r"page=(\d+)", href)
                    if match:
                        try:
                            page_num = int(match.group(1))
                            max_page = max(max_page, page_num)
                        except ValueError:
                            pass

        logging.info(f"   📄 Найдено страниц: {max_page}")

        # Обходим все страницы
        for page in range(2, min(max_page + 1, 21)):  # Ограничиваем 20 страницами
            try:
                page_url = f"{self.base_url}{catalog_url}?page={page}"
                response = self.session.get(page_url, timeout=10)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    page_products = 0
                    links = soup.find_all("a", href=True)

                    for link in links:
                        if isinstance(link, Tag):
                            href = str(link.get("href", ""))
                            if href and self._is_product_link(href):
                                full_url = self._normalize_url(
                                    urljoin(self.base_url, href)
                                )
                                if full_url not in self.discovered_urls:
                                    self.discovered_urls.add(full_url)
                                    page_products += 1

                    if page_products == 0:  # Если товаров нет, прекращаем
                        break

                    if page % 5 == 0:
                        logging.info(f"   📄 Страница {page}: {page_products} товаров")

                    time.sleep(self.page_delay)
                else:
                    break

            except Exception as e:
                logging.warning(f"Ошибка на странице {page}: {e}")
                break

    def _explore_catalog_pagination(self):
        """Дополнительная проверка пагинации"""

        catalogs_with_pagination = ["/bottled-beers", "/bottled-cider", "/bottled-mead"]

        for catalog in catalogs_with_pagination:
            for page in range(1, 11):  # Проверяем до 10 страниц
                try:
                    url = f"{self.base_url}{catalog}?page={page}"
                    response = self.session.get(url, timeout=10)

                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, "html.parser")

                        page_products = 0
                        links = soup.find_all("a", href=True)

                        for link in links:
                            if isinstance(link, Tag):
                                href = str(link.get("href", ""))
                                if href and self._is_product_link(href):
                                    full_url = self._normalize_url(
                                        urljoin(self.base_url, href)
                                    )
                                    if full_url not in self.discovered_urls:
                                        self.discovered_urls.add(full_url)
                                        page_products += 1

                        if page_products == 0:
                            break

                        time.sleep(self.page_delay)
                    else:
                        break

                except Exception as e:
                    break

    def _explore_section(self, section_url):
        """Исследуем дополнительные разделы"""

        try:
            url = self.base_url + section_url
            response = self.session.get(url, timeout=15)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")

                section_count = 0
                links = soup.find_all("a", href=True)

                for link in links:
                    if isinstance(link, Tag):
                        href = str(link.get("href", ""))
                        if href and self._is_product_link(href):
                            full_url = self._normalize_url(urljoin(self.base_url, href))
                            if full_url not in self.discovered_urls:
                                self.discovered_urls.add(full_url)
                                section_count += 1

                if section_count > 0:
                    logging.info(f"   📂 {section_url}: {section_count} товаров")

        except Exception as e:
            logging.warning(f"Ошибка в разделе {section_url}: {e}")

    def _explore_sitemap(self):
        """Исследуем sitemap.xml"""

        logging.info("🗺️ Исследуем sitemap...")

        try:
            sitemap_url = f"{self.base_url}/sitemap.xml"
            response = self.session.get(sitemap_url, timeout=10)

            if response.status_code == 200:
                # Парсим как обычный HTML, так как BeautifulSoup может не найти XML парсер
                content = response.text

                # Ищем URL в sitemap
                url_pattern = (
                    r"<loc>(https://craftbeer78\.ru/(?:beer|cider|mead)/[^<]+)</loc>"
                )
                sitemap_urls = re.findall(url_pattern, content)

                sitemap_count = 0
                for url in sitemap_urls:
                    normalized_url = self._normalize_url(url)
                    if normalized_url not in self.discovered_urls:
                        self.discovered_urls.add(normalized_url)
                        sitemap_count += 1

                logging.info(f"   ✅ Из sitemap: {sitemap_count} товаров")

        except Exception as e:
            logging.warning(f"Ошибка чтения sitemap: {e}")

    def _is_product_link(self, href):
        """Проверяем, является ли ссылка товаром"""

        if not href:
            return False

        href = str(href).lower()

        # Включаем товары
        product_patterns = ["/beer/", "/cider/", "/mead/"]

        has_product_pattern = any(pattern in href for pattern in product_patterns)

        if not has_product_pattern:
            return False

        # Исключаем служебные страницы
        exclude_patterns = [
            "/search",
            "/filter",
            "/sort",
            "/page",
            "/category",
            "/style",
            "/country",
            "/volume",
            "/taste",
            "/from",
            "-style/",
            "-taste/",
            "-from/",
            "-volume/",
            "/photo",
            "/reviews",
        ]

        for pattern in exclude_patterns:
            if pattern in href:
                return False

        # Проверяем что это конкретный товар (имеет название после category/)
        parts = href.split("/")
        if len(parts) >= 3:
            category_part = None
            product_part = None

            for i, part in enumerate(parts):
                if part in ["beer", "cider", "mead"]:
                    category_part = part
                    if i + 1 < len(parts):
                        product_part = parts[i + 1]
                    break

            # Если есть название товара после категории
            return category_part and product_part and len(product_part) > 0

        return False

    def parse_product_comprehensive(self, url):
        """Всесторонний парсинг товара"""

        try:
            url = self._normalize_url(url)
            logging.info(f"🔍 Парсим: {url}")

            # Надёжный запрос с retry на сетевые сбои + circuit breaker.
            # Возвращает (response, error_class) — None во втором значит успех.
            response, error_class = self._fetch_with_retry(url)

            # Различаем типы неудач
            if error_class == "shutdown":
                logging.info("   🛑 Выход по запросу пользователя (Ctrl+C)")
                return {
                    "original_url": url,
                    "url_hash": hashlib.md5(url.encode()).hexdigest(),
                    "parse_date": datetime.now().isoformat(),
                    "parse_success": 0,
                    "parse_attempts": 1,
                    "_error_class": "shutdown",
                }
            if response is None:
                # Все попытки исчерпаны — записываем класс ошибки для прогресса
                logging.error(
                    f"❌ Не удалось получить {url} после {self.url_max_retries} попыток "
                    f"(тип: {error_class})"
                )
                return {
                    "original_url": url,
                    "url_hash": hashlib.md5(url.encode()).hexdigest(),
                    "parse_date": datetime.now().isoformat(),
                    "parse_success": 0,
                    "parse_attempts": 1,
                    "_error_class": error_class,
                }

            soup = BeautifulSoup(response.text, "html.parser")

            # Базовая структура данных
            product_data = {
                "original_url": url,
                "url_hash": hashlib.md5(url.encode()).hexdigest(),
                "parse_date": datetime.now().isoformat(),
                "parse_success": 0,
                "parse_attempts": 1,
            }

            # Определяем категорию из URL
            if "/beer/" in url:
                product_data["category"] = "пиво"
            elif "/cider/" in url:
                product_data["category"] = "сидр"
            elif "/mead/" in url:
                product_data["category"] = "медовуха"

            def _clean_text(text):
                return re.sub(r"\s+", " ", str(text)).strip()

            def _parse_number(text):
                match = re.search(r"([\d.,]+)", str(text))
                if not match:
                    return None
                try:
                    return float(match.group(1).replace(",", "."))
                except ValueError:
                    return None

            def _parse_abv(text):
                value = _parse_number(text)
                if value is None:
                    return None
                if 0.1 <= value <= 30.0:
                    return value
                return None

            def _parse_volume(text):
                value = _parse_number(text)
                if value is None:
                    return None
                text_lower = str(text).lower()
                if "мл" in text_lower:
                    return int(value)
                if "л" in text_lower:
                    return int(value * 1000)
                if 1 <= value <= 3000:
                    return int(value)
                return None

            def _parse_ibu(text):
                value = _parse_number(text)
                if value is None:
                    return None
                if 0 <= value <= 200:
                    return int(value)
                return None

            # 1. Название товара
            title_selectors = [
                "h1",
                "title",
                ".product-title",
                ".beer-title",
                '[itemprop="name"]',
                ".product-name",
            ]

            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if title and len(title) > 3:
                        # Очищаем название от служебного текста
                        title = re.sub(r"\s*\|\s*craftbeer78\.ru.*", "", title)
                        title = re.sub(r"\s*-\s*craftbeer78\.ru.*", "", title)
                        title = re.sub(r"Пиво\s+", "", title, flags=re.IGNORECASE)
                        title = re.sub(r"Сидр\s+", "", title, flags=re.IGNORECASE)
                        title = re.sub(r"Медовуха\s+", "", title, flags=re.IGNORECASE)
                        title = re.sub(r"\s*\(\d+[.,]\d+л\)", "", title)
                        title = re.sub(r"\.\s*Купить.*", "", title, flags=re.IGNORECASE)
                        # SEO-хвосты: «... — Фруктовый берлинер вайссе купить в СПб»
                        title = re.sub(
                            r"\s*[—–-]\s*[^—–-]*(?:купить|в спб|в санкт-петербурге|спб).*",
                            "",
                            title,
                            flags=re.IGNORECASE,
                        )
                        title = re.sub(
                            r"\s+купить.*", "", title, flags=re.IGNORECASE
                        )
                        title = title.strip()
                        product_data["name"] = title
                        break

            # 2. Производитель и пивоварня
            producer_divs = soup.find_all(
                "div", class_="product_page_properties_table_row_70"
            )
            for div in producer_divs:
                if isinstance(div, Tag):
                    spans = div.find_all("span")
                    for span in spans:
                        if isinstance(span, Tag) and span.get("itemprop") == "brand":
                            product_data["producer"] = span.get_text(strip=True)
                            break

                    # География пивоварни
                    links = div.find_all("a")
                    for link in links:
                        if isinstance(link, Tag):
                            href = str(link.get("href", ""))
                            text = link.get_text(strip=True)

                            if "/breweries/country/" in href:
                                if "brewery_country" not in product_data:
                                    product_data["brewery_country"] = text
                                else:
                                    product_data["brewery_city"] = text

            # 2.1 JSON-LD (структурированные данные) — приоритетный источник.
            # Именно здесь craftbeer78.ru отдаёт чистые Стиль/ABV/IBU/Плотность/Состав
            # через additionalProperty. HTML-regex'ы ниже — только fallback.
            _AVAIL_MAP = {
                "instock": "В наличии",
                "in stock": "В наличии",
                "outofstock": "Нет в наличии",
                "out of stock": "Нет в наличии",
                "soldout": "Распродано",
                "preorder": "Предзаказ",
                "discontinued": "Снято с продажи",
            }

            def _norm_availability(raw: str) -> str:
                """Схлопывает 'https://schema.org/OutOfStock' -> 'Нет в наличии'."""
                s = _clean_text(raw)
                low = s.lower()
                # если это полный URL schema.org
                if "schema.org/" in low:
                    tail = low.rsplit("/", 1)[-1]
                    return _AVAIL_MAP.get(tail, s)
                return _AVAIL_MAP.get(low, s)

            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.get_text(strip=True))
                except Exception:
                    continue

                candidates = data if isinstance(data, list) else [data]
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    # Нас интересует только Product
                    if str(item.get("@type", "")).lower() != "product":
                        continue

                    if "name" in item:
                        json_name = _clean_text(item.get("name"))
                        # JSON-LD name имеет приоритет над h1, который часто
                        # несёт SEO-хвост: «13 — Фруктовый берлинер купить в СПб».
                        if json_name:
                            existing = product_data.get("name", "")
                            # Перезаписываем, если JSON-LD короче (значит чище),
                            # либо если в текущем есть SEO-мусор.
                            seo_garbage = any(
                                g in existing.lower()
                                for g in ["купить", " в спб", " в санкт", " — "]
                            )
                            if seo_garbage or len(json_name) < len(existing) or not existing:
                                product_data["name"] = json_name

                    brand = item.get("brand")
                    if brand and "producer" not in product_data:
                        if isinstance(brand, dict):
                            product_data["producer"] = _clean_text(brand.get("name"))
                        else:
                            product_data["producer"] = _clean_text(brand)

                    if "description" in item and "description" not in product_data:
                        desc = _clean_text(item.get("description"))
                        if len(desc) > 20:
                            product_data["description"] = desc[:5000]

                    image = item.get("image")
                    if image:
                        images = image if isinstance(image, list) else [image]
                        images = [self._normalize_url(u) for u in images if u]
                        if images and "image_url" not in product_data:
                            product_data["image_url"] = images[0]
                        if images:
                            product_data["additional_images"] = json.dumps(
                                images, ensure_ascii=False
                            )

                    offers = item.get("offers")
                    if offers:
                        offers_list = offers if isinstance(offers, list) else [offers]
                        for offer in offers_list:
                            if not isinstance(offer, dict):
                                continue
                            price = offer.get("price")
                            if price and "price" not in product_data:
                                price_value = _parse_number(price)
                                if price_value is not None:
                                    product_data["price"] = f"{int(price_value)} ₽"
                            availability = offer.get("availability")
                            if availability and "availability" not in product_data:
                                product_data["availability"] = _norm_availability(
                                    availability
                                )

                    sku = item.get("sku") or item.get("mpn")
                    if sku and "barcode" not in product_data:
                        product_data["barcode"] = _clean_text(sku)

                    gtin = item.get("gtin13") or item.get("gtin12") or item.get("gtin")
                    if gtin and "barcode" not in product_data:
                        product_data["barcode"] = _clean_text(gtin)

                    aggregate = item.get("aggregateRating")
                    if isinstance(aggregate, dict):
                        rating_value = _parse_number(aggregate.get("ratingValue"))
                        if rating_value is not None and "rating" not in product_data:
                            product_data["rating"] = rating_value
                        rating_count = _parse_number(aggregate.get("ratingCount"))
                        if (
                            rating_count is not None
                            and "rating_count" not in product_data
                        ):
                            product_data["rating_count"] = int(rating_count)
                        review_count = _parse_number(aggregate.get("reviewCount"))
                        if (
                            review_count is not None
                            and "reviews_count" not in product_data
                        ):
                            product_data["reviews_count"] = int(review_count)

                    # additionalProperty — основной источник характеристик.
                    # Пары {name, value}: Стиль, Крепость (ABV), Горечь (IBU),
                    # Плотность (OG), Объем, Состав и т.д.
                    for prop in item.get("additionalProperty", []) or []:
                        if not isinstance(prop, dict):
                            continue
                        pname = _clean_text(prop.get("name", "")).lower()
                        pvalue = _clean_text(prop.get("value", ""))
                        if not pname or not pvalue:
                            continue

                        if pname == "стиль" and "style" not in product_data:
                            product_data["style"] = pvalue
                        elif pname in ("крепость", "крепость (abv)", "abv") and "abv" not in product_data:
                            abv_value = _parse_abv(pvalue)
                            if abv_value is not None:
                                product_data["abv"] = abv_value
                        elif pname in ("горечь", "горечь (ibu)", "ibu") and "ibu" not in product_data:
                            ibu_value = _parse_ibu(pvalue)
                            if ibu_value is not None:
                                product_data["ibu"] = ibu_value
                        elif pname in ("плотность", "плотность (og)", "og") and "og_value" not in product_data:
                            # OG в % — не наша колонка, но сохраним в ingredients как доп. инфо
                            product_data.setdefault("og_value", pvalue)
                        elif pname == "объем" and "volume" not in product_data:
                            volume_value = _parse_volume(pvalue)
                            if volume_value is not None:
                                product_data["volume"] = volume_value
                        elif pname == "состав" and "ingredients" not in product_data:
                            product_data["ingredients"] = pvalue

            # 3. Крепость (ABV)
            abv_divs = soup.find_all("div")
            for div in abv_divs:
                if isinstance(div, Tag):
                    style = str(div.get("style", ""))
                    if "font-family:" in style and "Alfa Slab One" in style:
                        abv_text = div.get_text(strip=True)
                        abv_value = _parse_abv(abv_text)
                        if abv_value is not None:
                            product_data["abv"] = abv_value
                        break

            # 4. Объем
            volume_links = soup.find_all("a")
            for link in volume_links:
                if isinstance(link, Tag):
                    href = str(link.get("href", ""))
                    if (
                        "/beer-volume/" in href
                        or "/cider-volume/" in href
                        or "/mead-volume/" in href
                        or "/volume/" in href
                    ):
                        volume_text = link.get_text(strip=True)
                        volume_value = _parse_volume(volume_text)
                        if volume_value is not None:
                            product_data["volume"] = volume_value
                        break

            # 5. Цена
            page_text = soup.get_text(" ")
            price_selectors = [
                '[itemprop="price"]',
                ".price_marker span",
                ".product_page_price",
                ".price",
            ]
            for selector in price_selectors:
                price_elem = soup.select_one(selector)
                if price_elem:
                    price_value = _parse_number(price_elem.get_text(" ", strip=True))
                    if price_value is not None:
                        product_data["price"] = f"{int(price_value)} ₽"
                        break

            if "price" not in product_data:
                price_patterns = [
                    r"(\d+)\s*₽",
                    r"(\d+)\s*руб",
                    r"Price[:\s]*(\d+)",
                    r"Цена[:\s]*(\d+)",
                ]
                for pattern in price_patterns:
                    price_match = re.search(pattern, page_text, re.IGNORECASE)
                    if price_match:
                        product_data["price"] = price_match.group(1) + " ₽"
                        break

            # 6. Описание (полное)
            description_selectors = [
                ".product_page_text",
                ".product_page_about",
                ".product_page_description",
                ".product-description",
                ".beer-description",
                ".product_page_info",
                ".product-info",
                ".beer-info",
                'div[itemprop="description"]',
                '[itemprop="description"]',
                ".description",
            ]

            description_text = None
            for selector in description_selectors:
                desc_elem = soup.select_one(selector)
                if desc_elem:
                    desc_text = desc_elem.get_text(" ", strip=True)
                    desc_text = _clean_text(desc_text)
                    if len(desc_text) > 20:
                        description_text = desc_text
                        break

            if not description_text:
                # Fallback: объединяем осмысленные абзацы
                paragraphs = []
                for p in soup.select("p"):
                    text = _clean_text(p.get_text(" ", strip=True))
                    if len(text) >= 50:
                        paragraphs.append(text)
                if paragraphs:
                    description_text = "\n".join(paragraphs)

            if not description_text:
                # Fallback: пробуем JSON-LD
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        data = json.loads(script.get_text(strip=True))
                        if isinstance(data, list):
                            candidates = data
                        else:
                            candidates = [data]
                        for item in candidates:
                            if isinstance(item, dict) and "description" in item:
                                desc = _clean_text(str(item.get("description", "")))
                                if len(desc) > 20:
                                    description_text = desc
                                    break
                        if description_text:
                            break
                    except Exception:
                        continue

            if description_text:
                product_data["description"] = description_text[:5000]

            # 7. IBU (горечь) — расширяем под форматы «IBU: 50», «IBU 50», «50 IBU», «горечь: 50»
            ibu_patterns = [
                r"IBU\s*[:=]?\s*([\d]+(?:[.,]\d+)?)",
                r"([\d]+(?:[.,]\d+)?)\s*IBU",
                r"горечь\s*[:=]?\s*([\d]+(?:[.,]\d+)?)",
            ]
            if "ibu" not in product_data:
                for pattern in ibu_patterns:
                    ibu_match = re.search(pattern, page_text, re.IGNORECASE)
                    if ibu_match:
                        ibu_value = _parse_ibu(ibu_match.group(1))
                        if ibu_value is not None:
                            product_data["ibu"] = ibu_value
                            break

            # 8. Изображения — только реальные фото пива из /images_beers/
            # (отсекаем логотипы, SVG, иконки, баннеры сайта).
            def _is_beer_photo(url: str) -> bool:
                u = url.lower()
                if "/images_beers/" not in u:
                    return False
                if u.endswith(".svg"):
                    return False
                if "menu_" in u or "logo" in u or "banner" in u or "icon" in u:
                    return False
                return True

            img_candidates: list[str] = []
            # Приоритет 1: itemprop="contentUrl" (галерея на craftbeer78.ru)
            for img_elem in soup.select('img[itemprop="contentUrl"]'):
                if isinstance(img_elem, Tag):
                    candidate = (
                        str(img_elem.get("data-src") or img_elem.get("src") or "")
                    )
                    if candidate:
                        img_candidates.append(candidate)
            # Приоритет 2: любые img с /images_beers/ в src/data-src
            for img_elem in soup.select("img"):
                if isinstance(img_elem, Tag):
                    candidate = str(
                        img_elem.get("data-src") or img_elem.get("src") or ""
                    )
                    if "/images_beers/" in candidate:
                        img_candidates.append(candidate)

            # Нормализуем, фильтруем, убираем дубли
            normalized: list[str] = []
            for c in img_candidates:
                if not c:
                    continue
                if c.startswith("/"):
                    c = self.base_url + c
                c = self._normalize_url(c)
                if _is_beer_photo(c) and c not in normalized:
                    normalized.append(c)

            if normalized:
                product_data["image_url"] = normalized[0]
                product_data["additional_images"] = json.dumps(
                    normalized, ensure_ascii=False
                )

            # 9. Стиль пива — приоритет у JSON-LD additionalProperty (блок 2.1).
            # Regex по page_text намеренно убран: он ловил мусор из описаний
            # вида «...сварено в стиле Молочный стаут...». Таблица свойств
            # (блок 9.1 ниже) даёт чистый стиль как fallback.

            # 9.1 Читаем свойства из таблицы/списка характеристик
            properties = []
            properties_rows = soup.select(
                'div[class*="product_page_properties_table_row"], '
                'li[class*="product_page_properties_table_row"], '
                'tr[class*="product_page_properties_table_row"]'
            )
            for row in properties_rows:
                text = _clean_text(row.get_text(" ", strip=True))
                if ":" in text:
                    label, value = text.split(":", 1)
                    properties.append((label.strip(), value.strip()))

            for li in soup.select(
                ".product_page_properties_table li, .product_properties li, .product_params li"
            ):
                text = _clean_text(li.get_text(" ", strip=True))
                if ":" in text:
                    label, value = text.split(":", 1)
                    properties.append((label.strip(), value.strip()))

            for label, value in properties:
                label_lower = label.lower()

                if "производ" in label_lower or "brand" in label_lower:
                    product_data.setdefault("producer", value)
                elif "пивовар" in label_lower or "brewery" in label_lower:
                    product_data.setdefault("brewery_full_name", value)
                elif "страна" in label_lower or "country" in label_lower:
                    product_data.setdefault("brewery_country", value)
                elif "город" in label_lower or "city" in label_lower:
                    product_data.setdefault("brewery_city", value)
                elif "подст" in label_lower or "substyle" in label_lower:
                    product_data.setdefault("substyle", value)
                elif "стиль" in label_lower or "style" in label_lower:
                    product_data.setdefault("style", value)
                elif "креп" in label_lower or "abv" in label_lower:
                    abv_value = _parse_abv(value)
                    if abv_value is not None:
                        product_data.setdefault("abv", abv_value)
                elif "объ" in label_lower or "volume" in label_lower:
                    volume_value = _parse_volume(value)
                    if volume_value is not None:
                        product_data.setdefault("volume", volume_value)
                elif "ibu" in label_lower or "горечь" in label_lower:
                    ibu_value = _parse_ibu(value)
                    if ibu_value is not None:
                        product_data.setdefault("ibu", ibu_value)
                elif "цвет" in label_lower or "color" in label_lower:
                    product_data.setdefault("color", value)
                elif "ингредиент" in label_lower or "состав" in label_lower:
                    product_data.setdefault("ingredients", value)
                elif "хмель" in label_lower or "hops" in label_lower:
                    product_data.setdefault("hops", value)
                elif "солод" in label_lower or "malt" in label_lower:
                    product_data.setdefault("malt", value)
                elif "дрож" in label_lower or "yeast" in label_lower:
                    product_data.setdefault("yeast", value)
                elif "добав" in label_lower or "adjunct" in label_lower:
                    product_data.setdefault("additives", value)
                elif "темпера" in label_lower or "serving temp" in label_lower:
                    product_data.setdefault("serving_temp", value)
                elif "бокал" in label_lower or "glass" in label_lower:
                    product_data.setdefault("serving_glass", value)
                elif "сочет" in label_lower or "food" in label_lower:
                    product_data.setdefault("food_pairing", value)
                elif (
                    "штрих" in label_lower
                    or "barcode" in label_lower
                    or "ean" in label_lower
                ):
                    product_data.setdefault("barcode", value)
                elif "рейтинг" in label_lower or "rating" in label_lower:
                    rating_value = _parse_number(value)
                    if rating_value is not None:
                        product_data.setdefault("rating", rating_value)
                elif "отзыв" in label_lower or "review" in label_lower:
                    reviews_value = _parse_number(value)
                    if reviews_value is not None:
                        product_data.setdefault("reviews_count", int(reviews_value))

            # 10. Дополнительные характеристики

            # Наличие
            if "availability" not in product_data:
                availability_text = ""
                availability_block = soup.find(
                    string=re.compile("в наличии|нет в наличии", re.IGNORECASE)
                )
                if availability_block:
                    availability_text = _clean_text(availability_block)
                elif re.search(r"в наличии", page_text, re.IGNORECASE):
                    availability_text = "В наличии"
                elif re.search(r"нет в наличии|отсутств", page_text, re.IGNORECASE):
                    availability_text = "Нет в наличии"

                if availability_text:
                    product_data["availability"] = availability_text

            # Цвет
            color_keywords = [
                "золотистый",
                "янтарный",
                "темный",
                "светлый",
                "черный",
                "коричневый",
                "медный",
                "рыжий",
            ]
            for color in color_keywords:
                if color in page_text.lower():
                    product_data["color"] = color
                    break

            # Аромат и вкус — берём только из таблицы свойств (блок 9.1 выше).
            # Ранее здесь был regex по page_text, который ловил мусорные фрагменты
            # («е и крепость 10 градусов»). Намеренно убран — см. properties в блоке 9.1.

            # Ингредиенты — извлекаются только из таблицы свойств (блок 9.1).
            # Regex по page_text убран: ловил мусорные фрагменты из произвольного текста.

            # Успешность парсинга
            required_fields = ["name"]
            important_fields = ["producer", "abv", "volume", "category"]

            has_required = all(
                field in product_data and product_data[field]
                for field in required_fields
            )
            has_important = sum(
                1
                for field in important_fields
                if field in product_data and product_data[field]
            )

            if has_required and has_important >= 2:
                product_data["parse_success"] = 1

            # Логируем ключевые данные
            key_fields = ["name", "producer", "abv", "volume", "category", "price"]
            parsed_data = {
                k: v for k, v in product_data.items() if k in key_fields and v
            }
            logging.info(f"   ✅ Извлечено: {parsed_data}")

            return product_data

        except Exception as e:
            logging.error(f"❌ Ошибка парсинга {url}: {e}")
            return {
                "original_url": url,
                "url_hash": hashlib.md5(url.encode()).hexdigest(),
                "parse_date": datetime.now().isoformat(),
                "parse_success": 0,
                "parse_attempts": 1,
            }

    def save_to_database(self, product_data):
        """Сохраняем данные в базу"""

        conn = sqlite3.connect("beer_database.db")
        cursor = conn.cursor()

        try:
            # Проверяем, существует ли товар
            cursor.execute(
                "SELECT id FROM products_full WHERE url_hash = ?",
                (product_data["url_hash"],),
            )
            existing = cursor.fetchone()

            if existing:
                # Обновляем существующий
                update_fields = []
                values = []

                for key, value in product_data.items():
                    if (
                        key not in ["id", "first_seen", "parse_attempts"]
                        and value is not None
                    ):
                        update_fields.append(f"{key} = ?")
                        values.append(value)

                update_fields.append("parse_attempts = COALESCE(parse_attempts, 0) + 1")
                values.append(product_data["url_hash"])
                query = f"UPDATE products_full SET {', '.join(update_fields)}, last_updated = CURRENT_TIMESTAMP WHERE url_hash = ?"
                cursor.execute(query, values)

                if cursor.rowcount > 0:
                    self.stats["database_updates"] += 1
            else:
                # Вставляем новый
                fields = list(product_data.keys())
                placeholders = ", ".join(["?" for _ in fields])
                values = list(product_data.values())

                query = f"INSERT INTO products_full ({', '.join(fields)}) VALUES ({placeholders})"
                cursor.execute(query, values)

                if cursor.rowcount > 0:
                    self.stats["database_updates"] += 1

            conn.commit()
            return True

        except Exception as e:
            logging.error(f"❌ Ошибка сохранения в БД: {e}")
            return False
        finally:
            conn.close()

    def run_global_parsing(
        self, max_products=None, start_fresh=True, use_cached_urls_if_available=True
    ):
        """Запуск глобального парсинга"""

        logging.info("🌍 ЗАПУСК ГЛОБАЛЬНОГО ПАРСИНГА CRAFTBEER78.RU")
        logging.info("=" * 60)

        start_time = datetime.now()

        # 1. Создаем базу данных
        self.create_enhanced_database()

        # 2. Исследуем сайт
        if start_fresh:
            if use_cached_urls_if_available and os.path.exists("discovered_urls.txt"):
                with open("discovered_urls.txt", "r", encoding="utf-8") as f:
                    discovered_urls = [line.strip() for line in f if line.strip()]
                logging.info(f"📂 Загружено {len(discovered_urls)} URL из файла")
            else:
                discovered_urls = self.discover_all_product_urls()
        else:
            # Загружаем ранее найденные URL
            try:
                with open("discovered_urls.txt", "r", encoding="utf-8") as f:
                    discovered_urls = [line.strip() for line in f if line.strip()]
                logging.info(f"📂 Загружено {len(discovered_urls)} URL из файла")
            except FileNotFoundError:
                discovered_urls = self.discover_all_product_urls()

        self.stats["total_discovered"] = len(discovered_urls)

        discovered_urls = sorted({self._normalize_url(url) for url in discovered_urls})

        # Фильтруем служебные URL (photo, reviews и т.д.)
        before_filter = len(discovered_urls)
        discovered_urls = [
            url
            for url in discovered_urls
            if not any(
                pattern in url.lower() for pattern in ["/photo", "/reviews", "/review"]
            )
        ]
        logging.info(
            f"🧹 Отфильтровано {before_filter - len(discovered_urls)} служебных URL"
        )

        if max_products:
            discovered_urls = discovered_urls[:max_products]

        # Сохраняем список URL
        with open("discovered_urls.txt", "w", encoding="utf-8") as f:
            for url in discovered_urls:
                f.write(url + "\n")

        logging.info(f"🎯 К обработке: {len(discovered_urls)} товаров")

        if len(discovered_urls) == 0:
            logging.error("❌ Не найдено товаров для парсинга!")
            return self.stats

        # 3. Парсим каждый товар
        for i, url in enumerate(discovered_urls, 1):
            if url in self.processed_urls:
                continue

            logging.info(f"\n📦 {i}/{len(discovered_urls)}: {url}")

            # Парсим товар
            product_data = self.parse_product_comprehensive(url)

            if product_data.get("parse_success"):
                # Сохраняем в базу
                if self.save_to_database(product_data):
                    self.stats["successful_parses"] += 1

                    # Показываем ключевые данные
                    key_data = []
                    for field in [
                        "name",
                        "producer",
                        "abv",
                        "volume",
                        "category",
                        "price",
                    ]:
                        if field in product_data and product_data[field]:
                            key_data.append(f"{field}={product_data[field]}")

                    logging.info(f"   ✅ УСПЕШНО: {', '.join(key_data)}")
                else:
                    self.stats["failed_parses"] += 1
                    logging.error(f"   ❌ Ошибка сохранения")
            else:
                self.stats["failed_parses"] += 1
                logging.error(f"   ❌ Парсинг неудачен")
                self.failed_urls.add(url)

            self.processed_urls.add(url)
            self.stats["total_processed"] += 1

            # Пауза между запросами
            time.sleep(self.request_delay)

            # Промежуточная статистика
            if i % 10 == 0:
                elapsed = datetime.now() - start_time
                success_rate = (
                    (
                        self.stats["successful_parses"]
                        / self.stats["total_processed"]
                        * 100
                    )
                    if self.stats["total_processed"] > 0
                    else 0
                )

                logging.info(f"\n📊 ПРОМЕЖУТОЧНАЯ СТАТИСТИКА:")
                logging.info(
                    f"   Обработано: {self.stats['total_processed']}/{len(discovered_urls)}"
                )
                logging.info(
                    f"   Успешно: {self.stats['successful_parses']} ({success_rate:.1f}%)"
                )
                logging.info(f"   Ошибок: {self.stats['failed_parses']}")
                logging.info(f"   Время: {elapsed}")

        # Финальная статистика
        total_time = datetime.now() - start_time
        success_rate = (
            (self.stats["successful_parses"] / self.stats["total_processed"] * 100)
            if self.stats["total_processed"] > 0
            else 0
        )

        logging.info(f"\n🎉 ГЛОБАЛЬНЫЙ ПАРСИНГ ЗАВЕРШЕН!")
        logging.info(f"📊 ФИНАЛЬНАЯ СТАТИСТИКА:")
        logging.info(f"   Обнаружено URL: {self.stats['total_discovered']}")
        logging.info(f"   Обработано: {self.stats['total_processed']}")
        logging.info(f"   Успешно спарсено: {self.stats['successful_parses']}")
        logging.info(f"   Неудачных: {self.stats['failed_parses']}")
        logging.info(f"   Обновлений БД: {self.stats['database_updates']}")
        logging.info(f"   Процент успеха: {success_rate:.1f}%")
        logging.info(f"   Общее время: {total_time}")

        return self.stats

    # ------------------------------------------------------------------
    # Checkpoint-таблица parse_progress для resume длительных прогонов
    # ------------------------------------------------------------------
    def _ensure_progress_table(self):
        conn = sqlite3.connect("beer_database.db")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parse_progress (
                    url TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _mark_progress(self, url: str, status: str):
        conn = sqlite3.connect("beer_database.db")
        try:
            conn.execute(
                "INSERT INTO parse_progress (url, status, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(url) DO UPDATE SET status = excluded.status, "
                "updated_at = CURRENT_TIMESTAMP",
                (url, status),
            )
            conn.commit()
        finally:
            conn.close()

    def _load_done_urls(self) -> set:
        """URL со статусом ok — пропускаются при resume."""
        conn = sqlite3.connect("beer_database.db")
        try:
            cur = conn.execute(
                "SELECT url FROM parse_progress WHERE status = 'ok'"
            )
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    def _load_failed_urls(self) -> list:
        """URL с ошибками — для режима --failed-only.

        Permanent-ошибки (error_permanent = 404/410, удалённые страницы)
        по умолчанию исключаются — их перепроверка бессмысленна.
        """
        conn = sqlite3.connect("beer_database.db")
        try:
            cur = conn.execute(
                "SELECT DISTINCT url FROM parse_progress "
                "WHERE status != 'ok' AND status != 'error_permanent'"
            )
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    def _reset_progress(self):
        conn = sqlite3.connect("beer_database.db")
        try:
            conn.execute("DELETE FROM parse_progress")
            conn.commit()
        finally:
            conn.close()

    def run_refresh_parsing(self, max_products=None, resume=True, failed_only=False):
        """Перевычитывание всех существующих позиций из базы.

        Берёт original_url из products_full, перевычитывает каждую страницу,
        делает UPDATE по url_hash (существующий товар не дублируется).
        Используется для наполнения description/IBU/чистых картинок после
        правок парсера без повторной discovery-фазы.

        Resume: прогресс сохраняется в таблице parse_progress.
        При повторном запуске уже обработанные URL пропускаются.
        failed_only: обработать только URL из прошлых неудачных прогонов.
        """
        logging.info("🔄 ЗАПУСК ОБНОВЛЕНИЯ (REFRESH) CRAFTBEER78.RU")
        logging.info("=" * 60)
        start_time = datetime.now()

        # Убеждаемся, что таблица существует
        self.create_enhanced_database()
        self._ensure_progress_table()

        # Грузим URL из базы
        conn = sqlite3.connect("beer_database.db")
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT original_url FROM products_full "
                "WHERE original_url IS NOT NULL AND original_url != '' "
                "ORDER BY id"
            )
            all_urls = [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

        # Фильтруем служебные
        all_urls = [
            u
            for u in all_urls
            if not any(p in u.lower() for p in ["/photo", "/reviews", "/review"])
        ]
        # Дедуп + нормализация
        all_urls = sorted({self._normalize_url(u) for u in all_urls})

        # Resume: убираем уже успешно обработанные
        if failed_only:
            urls = self._load_failed_urls()
            logging.info(f"🎯 Режим failed-only: перепроверяем {len(urls)} ошибок")
        elif resume:
            done = self._load_done_urls()
            urls = [u for u in all_urls if u not in done]
            skipped = len(all_urls) - len(urls)
            logging.info(
                f"📂 Resume: пропущено {skipped} уже обработанных, осталось {len(urls)}"
            )
        else:
            urls = list(all_urls)
            # Сбрасываем прогресс при свежем старте
            self._reset_progress()

        if max_products:
            urls = urls[:max_products]

        self.stats["total_discovered"] = len(urls)
        logging.info(f"🎯 К обновлению: {len(urls)} позиций")

        if not urls:
            logging.warning("⚠️ Нечего обновлять (всё уже обработано или пусто)")
            return self.stats

        for i, url in enumerate(urls, 1):
            # Проверяем запрос на завершение между URL
            if self.shutdown_requested:
                logging.info("\n🛑 Получен сигнал завершения. Аккуратный выход.")
                logging.info(
                    f"   Прогресс сохранён: {self.stats['total_processed']}/{len(urls)} обработано."
                )
                logging.info("   Повторный запуск продолжит с этого места (resume).")
                break

            logging.info(f"\n📦 {i}/{len(urls)}: {url}")

            product_data = self.parse_product_comprehensive(url)
            ok = bool(product_data.get("parse_success"))
            # Класс ошибки из _fetch_with_retry (network/blocked/server/permanent/shutdown)
            error_class = product_data.get("_error_class")

            if ok:
                if self.save_to_database(product_data):
                    self.stats["successful_parses"] += 1
                    self._mark_progress(url, "ok")
                    key_data = []
                    for field in [
                        "name",
                        "producer",
                        "abv",
                        "volume",
                        "category",
                        "price",
                    ]:
                        if field in product_data and product_data[field]:
                            key_data.append(f"{field}={product_data[field]}")
                    logging.info(f"   ✅ ОБНОВЛЕНО: {', '.join(key_data)}")
                else:
                    self.stats["failed_parses"] += 1
                    self._mark_progress(url, "save_error")
                    logging.error("   ❌ Ошибка сохранения в БД")
            else:
                # Выход по Ctrl+C — не помечаем как ошибку, просто выходим
                if error_class == "shutdown":
                    break
                self.stats["failed_parses"] += 1
                # Классифицированный статус для parse_progress
                status = f"error_{error_class}" if error_class else "parse_error"
                self._mark_progress(url, status)
                self.failed_urls.add(url)
                logging.error(
                    f"   ❌ Парсинг неудачен (статус: {status})"
                )

            self.processed_urls.add(url)
            self.stats["total_processed"] += 1
            time.sleep(self.request_delay)

            if i % 10 == 0:
                elapsed = datetime.now() - start_time
                success_rate = (
                    (self.stats["successful_parses"] / self.stats["total_processed"] * 100)
                    if self.stats["total_processed"] > 0
                    else 0
                )
                # ETA
                speed = self.stats["total_processed"] / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
                remaining = len(urls) - i
                eta_sec = remaining / speed if speed > 0 else 0
                logging.info(
                    f"   Обработано: {self.stats['total_processed']}/{len(urls)}"
                )
                logging.info(
                    f"   Успешно: {self.stats['successful_parses']} ({success_rate:.1f}%)"
                )
                logging.info(f"   Ошибок: {self.stats['failed_parses']}")
                logging.info(
                    f"   Retry использовано: {self.stats['retries_used']}, "
                    f"пауз circuit breaker: {self.stats['circuit_pauses']}"
                )
                logging.info(f"   Время: {elapsed}")
                if eta_sec > 0:
                    logging.info(
                        f"   ETA: ~{int(eta_sec // 60)} мин {int(eta_sec % 60)} сек"
                    )

            # Двойная проверка shutdown (после долгого запроса с retry)
            if self.shutdown_requested:
                logging.info("\n🛑 Получен сигнал завершения. Аккуратный выход.")
                logging.info(
                    f"   Прогресс сохранён: {self.stats['total_processed']}/{len(urls)} обработано."
                )
                logging.info("   Повторный запуск продолжит с этого места (resume).")
                break

        total_time = datetime.now() - start_time
        success_rate = (
            (self.stats["successful_parses"] / self.stats["total_processed"] * 100)
            if self.stats["total_processed"] > 0
            else 0
        )
        logging.info("\n🎉 ОБНОВЛЕНИЕ ЗАВЕРШЕНО!" + (" (прервано пользователем)" if self.shutdown_requested else ""))
        logging.info("📊 ФИНАЛЬНАЯ СТАТИСТИКА:")
        logging.info(f"   Обработано: {self.stats['total_processed']}")
        logging.info(f"   Успешно: {self.stats['successful_parses']}")
        logging.info(f"   Ошибок: {self.stats['failed_parses']}")
        logging.info(f"   Обновлений БД: {self.stats['database_updates']}")
        logging.info(f"   Retry использовано: {self.stats['retries_used']}")
        logging.info(f"   Пауз circuit breaker: {self.stats['circuit_pauses']}")
        logging.info(f"   Процент успеха: {success_rate:.1f}%")
        logging.info(f"   Общее время: {total_time}")
        if self.shutdown_requested:
            logging.info(
                "\n💡 Прогресс сохранён в parse_progress. "
                "Перезапустите команду — продолжит с места остановки."
            )

        return self.stats


if __name__ == "__main__":
    import argparse as _argparse

    _cli = _argparse.ArgumentParser(
        description="Парсер craftbeer78.ru: извлечение исчерпывающей информации о пиве."
    )
    _cli.add_argument(
        "--full",
        action="store_true",
        help="Полный парсинг по discovered_urls.txt ( discovery заново, если файла нет).",
    )
    _cli.add_argument(
        "--refresh",
        action="store_true",
        help="Перевычитать все страницы из существующей базы (UPDATE по original_url).",
    )
    _cli.add_argument(
        "--failed-only",
        action="store_true",
        help="С --refresh: повторить только ранее упавшие URL.",
    )
    _cli.add_argument(
        "--fresh",
        action="store_true",
        help="С --refresh: игнорировать прогресс и начать заново (очищает parse_progress).",
    )
    _cli.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество товаров (для теста).",
    )
    _args = _cli.parse_args()

    parser = CraftBeerGlobalParser()

    # --- Graceful shutdown: Ctrl+C = аккуратный выход с сохранением прогресса ---
    # Первый Ctrl+C ставит флаг, процесс доходит до проверки и выходит чисто.
    # Второй Ctrl+C = немедленный выход (os._exit).
    import signal as _signal
    import os as _os

    def _handle_sigint(signum, frame):
        if parser.shutdown_requested:
            # Второе нажатие — жёсткий выход
            print("\n⛔ Повторный Ctrl+C — принудительный выход.", flush=True)
            _os._exit(1)
        parser.shutdown_requested = True
        print(
            "\n🛑 Получен Ctrl+C. Завершаю текущий запрос и сохраняю прогресс...\n"
            "   (повторный Ctrl+C = принудительный выход)",
            flush=True,
        )

    _signal.signal(_signal.SIGINT, _handle_sigint)

    if _args.refresh:
        stats = parser.run_refresh_parsing(
            max_products=_args.limit,
            resume=not _args.fresh,
            failed_only=_args.failed_only,
        )
    elif _args.full:
        stats = parser.run_global_parsing()
    else:
        # По умолчанию — тест на 100 товаров
        stats = parser.run_global_parsing(max_products=100)

    print(
        f"\n🌟 РЕЗУЛЬТАТ: {stats['successful_parses']} товаров успешно обработано из {stats['total_processed']}"
    )
