# 🍺 Пивная энциклопедия

Веб-энциклопедия крафтового пива, сидра и медовухи. ~7733 позиций, ~231 стиль, ~277 пивоварен, 19 стран. Данные парсятся с [craftbeer78.ru](https://craftbeer78.ru), стиль — Flask + SQLite, тёмная тема.

## 📦 Состав проекта

| Файл | Назначение |
|---|---|
| `app.py` | **Главный Flask-сервер** (энциклопедия) → http://127.0.0.1:8000 |
| `craftbeer_global_parser.py` | Парсер craftbeer78.ru, наполняет базу |
| `image_cache.py` | Скачивает фото бутылок в `static/images/` |
| `style_guide.py` | Справочник стилей BJCP (31 стиль) в таблицу `beer_styles` |
| `run_full_pipeline.py` | Конвейер: парсер → картинки → стили (одной командой) |
| `db_viewer.py` | Админ-просмотрщик таблиц (старый, не трогали) |
| `beer_database.db` | SQLite — основная база |
| `templates/` | HTML-шаблоны (Jinja2) |
| `static/` | CSS, JS, кеш картинок |
| `requirements.txt` | Зависимости |

## 🚀 Быстрый старт

```bash
# 1. Установить зависимости (Python 3.10+)
pip install -r requirements.txt

# 2. Запустить веб-энциклопедию (данные уже в базе)
python app.py
# → http://127.0.0.1:8000
```

## 🔄 Обновление данных (полный прогон)

Одна команда — парсер + картинки + справочник стилей:

```bash
python run_full_pipeline.py
```

Полный прогон занимает ~50–60 минут (парсер) + ~10 минут (картинки). **Можно прервать Ctrl+C и запустить снова** — продолжит с места остановки (resume через `parse_progress` таблицу).

### Постепенный запуск

```bash
# Сначала тест на 200 позиций (проверить, что всё работает)
python run_full_pipeline.py --limit 200

# Если ок — полный прогон
python run_full_pipeline.py

# Только конкретный шаг
python run_full_pipeline.py --step parse    # парсер
python run_full_pipeline.py --step images   # картинки
python run_full_pipeline.py --step styles   # справочник стилей

# Перепроверить только упавшие URL
python run_full_pipeline.py --failed-only

# Начать парсер с нуля (игнорировать прогресс)
python run_full_pipeline.py --fresh
```

### Отдельные команды

```bash
# Парсер
python craftbeer_global_parser.py --refresh              # с resume
python craftbeer_global_parser.py --refresh --fresh      # с нуля
python craftbeer_global_parser.py --refresh --limit 100  # тест
python craftbeer_global_parser.py --refresh --failed-only

# Кеш картинок (1 главное + до 4 галерейных на позицию)
python image_cache.py --limit 500     # пробная партия
python image_cache.py                 # всё (~38 000 файлов)
python image_cache.py --max-per-beer 3

# Справочник стилей
python style_guide.py
python style_guide.py --stats         # сводка по стилям
```

## 🗂 База данных

**Таблица `products_full`** (7733 строк, 44 колонки):
- Идентификация: `name`, `producer`, `category` (пиво/сидр/медовуха), `style`, `substyle`
- География: `brewery_country`, `brewery_city`, `brewery_full_name`
- Характеристики: `abv`, `volume`, `ibu`, `og_value`, `color`
- Органолептика: `aroma`, `taste`, `description`, `mouthfeel`, `appearance`
- Состав: `ingredients`, `hops`, `malt`, `yeast`, `additives`
- Коммерция: `price`, `availability`, `barcode`
- Рейтинги: `rating`, `rating_count`, `reviews_count`
- Гастрономия: `food_pairing`, `serving_temp`, `serving_glass`
- Картинки: `image_url`, `additional_images`, `local_image`, `local_gallery`
- Служебное: `original_url`, `url_hash`, `parse_date`, `parse_success`, `last_updated`

**Таблица `beer_styles`** — справочник BJCP (31 стиль): описание, аромат, вкус, история, ABV/IBU/OG/FG диапазоны, бокал, температура подачи.

**Таблица `parse_progress`** — checkpoint для resume парсера.

## 🌐 Маршруты энциклопедии

| URL | Назначение |
|---|---|
| `/` | Главная: статистика, топ стилей/пивоваров/стран, случайные бутылки |
| `/search?q=` | Поиск по названию/пивовару/стилю/стране |
| `/api/suggest?q=` | AJAX-подсказки (JSON) |
| `/beer/<id>` | Карточка пива: фото, характеристики, описание, BJCP-блок, похожие |
| `/styles` | Все стили |
| `/style/<slug>` | Страница стиля: BJCP + статистика + топ пивоварен/позиций |
| `/breweries` | Все пивовары |
| `/brewery/<slug>` | Страница пивовара: распределение по стилям + товары |
| `/country/<name>` | Страница страны |
| `/catalog` | Каталог с фильтрами (название/стиль/страна/ABV) |
| `/top` | Подборки: крепкое, лёгкое, новинки, доступное |
| `/compare?id1=&id2=` | Сравнение двух позиций бок о бок |
| `/random` | Случайная карточка |

## ⚙️ Технические детали

**Парсер** приоритезирует JSON-LD `additionalProperty` (стиль/ABV/IBU/состав — самый чистый источник), fallback — таблица свойств DOM (`product_page_properties_table_row`). Regex по `page_text` намеренно убран: он ловил мусор из описаний.

**Кеш картинок** берёт только `/images_beers/` URL (отсекает логотипы/SVG/баннеры), 8 потоков, idempotent.

**Resume**: прогресс парсера и скачанных картинок хранится в БД — прерывание/перезапуск безопасны.

## 📊 Наполненность после полного обновления (ожидаемая)

На тесте из 25 позиций:
- `name`, `style`, `abv`, `ibu`, `description`, `ingredients`, `og_value`, `barcode`, `price`, `availability`, `image_url` — **~100%**
- `color` — ~44% (сайт отдаёт не всегда)
- `aroma`, `taste` — низко (сайт отдаёт редко)
