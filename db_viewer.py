from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List, Tuple

from flask import Flask, abort, render_template_string, request, url_for

APP_ROOT = Path(__file__).resolve().parent
DB_PATH = APP_ROOT / "beer_database.db"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

app = Flask(__name__)


INDEX_TEMPLATE = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Каталог базы</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #0b1220;
        --panel: #111827;
        --panel-2: #0f172a;
        --text: #e2e8f0;
        --muted: #94a3b8;
        --accent: #38bdf8;
        --border: #1f2937;
        --chip: #1e293b;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
        background: radial-gradient(1200px 600px at 10% 0%, #0f172a 0%, #0b1220 60%);
        color: var(--text);
      }
      a { color: var(--accent); text-decoration: none; }
      .container { max-width: 1100px; margin: 32px auto; padding: 0 20px; }
      .header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
      .title { font-size: 28px; font-weight: 700; letter-spacing: 0.2px; }
      .subtitle { color: var(--muted); font-size: 13px; }
      .search { width: 320px; max-width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel-2); color: var(--text); }
      .grid { margin-top: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
      .card {
        background: linear-gradient(180deg, rgba(17,24,39,0.9) 0%, rgba(17,24,39,0.7) 100%);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 16px;
        transition: transform 0.12s ease, border-color 0.12s ease;
      }
      .card:hover { transform: translateY(-2px); border-color: #334155; }
      .card h3 { margin: 0 0 8px 0; font-size: 16px; }
      .pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: var(--chip); font-size: 12px; color: var(--muted); }
      .muted { color: var(--muted); font-size: 12px; }
    </style>
  </head>
  <body>
    <div class="container">
      <div class="header">
        <div>
          <div class="title">Каталог SQLite базы</div>
          <div class="subtitle">Файл: {{ db_path }}</div>
        </div>
        <input class="search" id="tableSearch" type="text" placeholder="Поиск таблицы..." oninput="filterTables()" />
      </div>

      <div class="grid" id="tableGrid">
        {% for name, count in tables %}
          <div class="card" data-name="{{ name | lower }}">
            <h3><a href="{{ url_for('view_table', table_name=name) }}">{{ name }}</a></h3>
            <div class="pill">{{ count }} записей</div>
            <div class="muted">Открыть таблицу</div>
          </div>
        {% endfor %}
      </div>
    </div>

    <script>
      function filterTables() {
        const value = (document.getElementById('tableSearch').value || '').toLowerCase();
        const cards = document.querySelectorAll('#tableGrid .card');
        cards.forEach(card => {
          const name = card.getAttribute('data-name');
          card.style.display = name.includes(value) ? '' : 'none';
        });
      }
    </script>
  </body>
</html>
"""

CATALOG_TEMPLATE = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Каталог пива</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #0b1220;
        --panel: #111827;
        --panel-2: #0f172a;
        --text: #e2e8f0;
        --muted: #94a3b8;
        --accent: #38bdf8;
        --border: #1f2937;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
        background: radial-gradient(1200px 600px at 10% 0%, #0f172a 0%, #0b1220 60%);
        color: var(--text);
      }
      a { color: var(--accent); text-decoration: none; }
      .container { max-width: 1400px; margin: 20px auto; padding: 0 20px 40px; }
      .header { margin-bottom: 20px; }
      .title { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
      .subtitle { color: var(--muted); font-size: 13px; }

      .layout { display: block; }

      .filters {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 16px;
        margin-bottom: 16px;
        position: sticky;
        top: 12px;
        z-index: 5;
      }

      .filter-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
        cursor: pointer;
        user-select: none;
      }

      .filter-header h3 { margin: 0; font-size: 16px; }
      .toggle-btn { background: var(--border); border: 0; color: var(--text); padding: 4px 8px; border-radius: 6px; cursor: pointer; }

      .filters-content { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
      .filters-content.collapsed { display: none; }

      .filter-group { margin-bottom: 0; }
      .filter-group label.group-label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 8px; color: var(--muted); }

      input[type="text"] {
        width: 100%;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--panel-2);
        color: var(--text);
        font-size: 13px;
      }

      .list-search {
        margin-bottom: 8px;
      }

      .checkbox-list {
        max-height: 200px;
        overflow-y: auto;
        background: var(--panel-2);
        border-radius: 8px;
        padding: 8px;
      }

      .checkbox-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 4px 0;
        font-size: 13px;
      }

      .checkbox-item input[type="checkbox"] {
        width: 16px;
        height: 16px;
        cursor: pointer;
      }

      .range-inputs {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }

      .range-inputs input {
        width: 100%;
        padding: 6px 8px;
        border-radius: 6px;
        border: 1px solid var(--border);
        background: var(--panel-2);
        color: var(--text);
        font-size: 12px;
      }

      .filters-actions { display: flex; gap: 8px; margin-top: 12px; }
      .apply-btn {
        padding: 10px 14px;
        border: 0;
        border-radius: 8px;
        background: var(--accent);
        color: #0b1220;
        font-weight: 600;
        cursor: pointer;
        font-size: 14px;
      }
      .reset-btn {
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid var(--border);
        color: var(--text);
        background: transparent;
        font-size: 14px;
      }

      .results { min-height: 400px; }
      .results-header {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 16px;
        margin-bottom: 16px;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }

      .table-wrap {
        overflow: auto;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: var(--panel);
      }
      table { border-collapse: collapse; width: 100%; min-width: 1200px; }
      th, td { border-bottom: 1px solid var(--border); padding: 10px; font-size: 13px; white-space: nowrap; }
      .wrap { white-space: normal; min-width: 320px; }
      th { background: #0f172a; text-align: left; position: sticky; top: 0; z-index: 1; }
      tr:nth-child(even) td { background: rgba(15,23,42,0.6); }
      .link { color: var(--accent); }

      @media (max-width: 900px) {
        .filters-content { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <div class="container">
      <div class="header">
        <div class="title">🍺 Каталог пива</div>
        <div class="subtitle"><a href="{{ url_for('index') }}">← К списку таблиц</a> • Найдено: {{ total_results }}</div>
      </div>

      <div class="layout">
        <aside class="filters">
          <div class="filter-header" onclick="toggleFilters()">
            <h3>Фильтры</h3>
            <button class="toggle-btn" id="toggleBtn">Скрыть</button>
          </div>

          <form method="get" id="filterForm">
            <div class="filters-content" id="filtersContent">

              <!-- Поиск по названию -->
              <div class="filter-group">
                <label class="group-label">Название</label>
                <input type="text" name="name" value="{{ filters.name }}" placeholder="Введите название..." />
              </div>

              <!-- Поиск по производителю -->
              <div class="filter-group">
                <label class="group-label">Производитель</label>
                <input type="text" name="producer" value="{{ filters.producer }}" placeholder="Введите производителя..." />
              </div>

              <!-- Страна -->
              {% if filter_options.countries %}
              <div class="filter-group">
                <label class="group-label">Страна</label>
                <input class="list-search" type="text" data-target="countries-list" placeholder="Поиск по странам..." />
                <div class="checkbox-list" id="countries-list">
                  {% for country in filter_options.countries %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="country" value="{{ country }}"
                        {% if country in filters.countries %}checked{% endif %} />
                      {{ country }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Город -->
              {% if filter_options.cities %}
              <div class="filter-group">
                <label class="group-label">Город</label>
                <input class="list-search" type="text" data-target="cities-list" placeholder="Поиск по городам..." />
                <div class="checkbox-list" id="cities-list">
                  {% for city in filter_options.cities %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="city" value="{{ city }}"
                        {% if city in filters.cities %}checked{% endif %} />
                      {{ city }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Категория -->
              {% if filter_options.categories %}
              <div class="filter-group">
                <label class="group-label">Категория</label>
                <input class="list-search" type="text" data-target="categories-list" placeholder="Поиск по категориям..." />
                <div class="checkbox-list" id="categories-list">
                  {% for cat in filter_options.categories %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="category" value="{{ cat }}"
                        {% if cat in filters.categories %}checked{% endif %} />
                      {{ cat }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Стиль -->
              {% if filter_options.styles %}
              <div class="filter-group">
                <label class="group-label">Стиль</label>
                <input class="list-search" type="text" data-target="styles-list" placeholder="Поиск по стилям..." />
                <div class="checkbox-list" id="styles-list">
                  {% for style in filter_options.styles %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="style" value="{{ style }}"
                        {% if style in filters.styles %}checked{% endif %} />
                      {{ style }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Подстиль -->
              {% if filter_options.substyles %}
              <div class="filter-group">
                <label class="group-label">Подстиль</label>
                <input class="list-search" type="text" data-target="substyles-list" placeholder="Поиск по подстилям..." />
                <div class="checkbox-list" id="substyles-list">
                  {% for substyle in filter_options.substyles %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="substyle" value="{{ substyle }}"
                        {% if substyle in filters.substyles %}checked{% endif %} />
                      {{ substyle }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Крепость ABV -->
              <div class="filter-group">
                <label class="group-label">Крепость (ABV %)</label>
                <div class="range-inputs">
                  <input type="number" name="abv_min" value="{{ filters.abv_min }}" placeholder="От" step="0.1" />
                  <input type="number" name="abv_max" value="{{ filters.abv_max }}" placeholder="До" step="0.1" />
                </div>
              </div>

              <!-- Объем -->
              {% if filter_options.volumes %}
              <div class="filter-group">
                <label class="group-label">Объем (мл)</label>
                <input class="list-search" type="text" data-target="volumes-list" placeholder="Поиск по объемам..." />
                <div class="checkbox-list" id="volumes-list">
                  {% for vol in filter_options.volumes %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="volume" value="{{ vol }}"
                        {% if vol|string in filters.volumes %}checked{% endif %} />
                      {{ vol }} мл
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Горечь IBU -->
              <div class="filter-group">
                <label class="group-label">Горечь (IBU)</label>
                <div class="range-inputs">
                  <input type="number" name="ibu_min" value="{{ filters.ibu_min }}" placeholder="От" />
                  <input type="number" name="ibu_max" value="{{ filters.ibu_max }}" placeholder="До" />
                </div>
              </div>

              <!-- Цвет -->
              {% if filter_options.colors %}
              <div class="filter-group">
                <label class="group-label">Цвет</label>
                <input class="list-search" type="text" data-target="colors-list" placeholder="Поиск по цветам..." />
                <div class="checkbox-list" id="colors-list">
                  {% for color in filter_options.colors %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="color" value="{{ color }}"
                        {% if color in filters.colors %}checked{% endif %} />
                      {{ color }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Аромат -->
              {% if filter_options.aromas %}
              <div class="filter-group">
                <label class="group-label">Аромат</label>
                <input class="list-search" type="text" data-target="aromas-list" placeholder="Поиск по ароматам..." />
                <div class="checkbox-list" id="aromas-list">
                  {% for aroma in filter_options.aromas %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="aroma" value="{{ aroma }}"
                        {% if aroma in filters.aromas %}checked{% endif %} />
                      {{ aroma }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

              <!-- Вкус -->
              {% if filter_options.tastes %}
              <div class="filter-group">
                <label class="group-label">Вкус</label>
                <input class="list-search" type="text" data-target="tastes-list" placeholder="Поиск по вкусам..." />
                <div class="checkbox-list" id="tastes-list">
                  {% for taste in filter_options.tastes %}
                    <label class="checkbox-item">
                      <input type="checkbox" name="taste" value="{{ taste }}"
                        {% if taste in filters.tastes %}checked{% endif %} />
                      {{ taste }}
                    </label>
                  {% endfor %}
                </div>
              </div>
              {% endif %}

            </div>

            <div class="filters-actions">
              <button type="submit" class="apply-btn">Применить фильтры</button>
              <a class="reset-btn" href="{{ url_for('catalog') }}">Сбросить</a>
            </div>
          </form>
        </aside>

        <main class="results">
          <div class="results-header">
            <span>Найдено товаров: <strong>{{ total_results }}</strong></span>
          </div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Название</th>
                  <th>Производитель</th>
                  <th>Страна</th>
                  <th>Город</th>
                  <th>Категория</th>
                  <th>Стиль</th>
                  <th>Подстиль</th>
                  <th>ABV %</th>
                  <th>Объем (мл)</th>
                  <th>IBU</th>
                  <th>Цвет</th>
                  <th>Аромат</th>
                  <th>Вкус</th>
                  <th>Описание</th>
                  <th>Ссылка</th>
                </tr>
              </thead>
              <tbody>
                {% for product in products %}
                  <tr>
                    <td>{{ product.name or '' }}</td>
                    <td>{{ product.producer or '' }}</td>
                    <td>{{ product.brewery_country or '' }}</td>
                    <td>{{ product.brewery_city or '' }}</td>
                    <td>{{ product.category or '' }}</td>
                    <td>{{ product.style or '' }}</td>
                    <td>{{ product.substyle or '' }}</td>
                    <td>{{ product.abv or '' }}</td>
                    <td>{{ product.volume or '' }}</td>
                    <td>{{ product.ibu or '' }}</td>
                    <td>{{ product.color or '' }}</td>
                    <td>{{ product.aroma or '' }}</td>
                    <td>{{ product.taste or '' }}</td>
                    <td class="wrap">{{ product.description or '' }}</td>
                    <td>
                      {% if product.original_url %}
                        <a class="link" href="{{ product.original_url }}" target="_blank">Открыть</a>
                      {% endif %}
                    </td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>

          {% if not products %}
            <div style="text-align: center; padding: 60px 20px; color: var(--muted);">
              Товары не найдены. Попробуйте изменить фильтры.
            </div>
          {% endif %}
        </main>
      </div>
    </div>

    <script>
      function toggleFilters() {
        const content = document.getElementById('filtersContent');
        const btn = document.getElementById('toggleBtn');
        content.classList.toggle('collapsed');
        btn.textContent = content.classList.contains('collapsed') ? 'Показать' : 'Скрыть';
      }

      const form = document.getElementById('filterForm');
      const debounceMs = 450;
      let debounceTimer = null;

      function scheduleSubmit() {
        if (!form) {
          return;
        }
        if (debounceTimer) {
          clearTimeout(debounceTimer);
        }
        debounceTimer = setTimeout(() => {
          form.submit();
        }, debounceMs);
      }

      if (form) {
        const fields = form.querySelectorAll('input, select');
        fields.forEach((field) => {
          if (field.classList.contains('list-search')) {
            field.addEventListener('input', (event) => {
              const targetId = event.target.getAttribute('data-target');
              if (!targetId) {
                return;
              }
              const list = document.getElementById(targetId);
              if (!list) {
                return;
              }
              const value = (event.target.value || '').toLowerCase();
              list.querySelectorAll('.checkbox-item').forEach((item) => {
                const text = item.textContent.toLowerCase();
                item.style.display = text.includes(value) ? '' : 'none';
              });
            });
            return;
          }
          if (field.type === 'text' || field.type === 'number') {
            field.addEventListener('input', scheduleSubmit);
          } else {
            field.addEventListener('change', scheduleSubmit);
          }
        });
      }
    </script>
  </body>
</html>
"""

TABLE_TEMPLATE = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Таблица {{ table_name }}</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #0b1220;
        --panel: #111827;
        --panel-2: #0f172a;
        --text: #e2e8f0;
        --muted: #94a3b8;
        --accent: #38bdf8;
        --border: #1f2937;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
        background: radial-gradient(1200px 600px at 10% 0%, #0f172a 0%, #0b1220 60%);
        color: var(--text);
      }
      a { color: var(--accent); text-decoration: none; }
      .container { max-width: 1200px; margin: 24px auto; padding: 0 20px 40px; }
      .card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 16px; }
      .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 12px; }
      .title { font-size: 20px; font-weight: 700; }
      .meta { font-size: 12px; color: var(--muted); }
      .controls { display: flex; gap: 8px; flex-wrap: wrap; }
      input[type="text"] { width: 280px; max-width: 100%; padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel-2); color: var(--text); }
      select { padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel-2); color: var(--text); }
      button { padding: 8px 12px; border-radius: 10px; border: 0; background: var(--accent); color: #0b1220; cursor: pointer; font-weight: 600; }
      .table-wrap { overflow: auto; border-radius: 12px; border: 1px solid var(--border); }
      table { border-collapse: collapse; width: 100%; min-width: 760px; }
      th, td { border-bottom: 1px solid var(--border); padding: 10px; font-size: 13px; white-space: nowrap; }
      th { background: #0f172a; text-align: left; position: sticky; top: 0; z-index: 1; }
      tr:nth-child(even) td { background: rgba(15,23,42,0.6); }
      .pagination { margin-top: 12px; display: flex; gap: 8px; align-items: center; }
      .pagination a, .pagination span { padding: 6px 10px; border-radius: 8px; background: #1f2937; color: var(--text); }
    </style>
  </head>
  <body>
    <div class="container">
      <div class="card" style="margin-bottom: 12px;">
        <div><a href="{{ url_for('index') }}">← К списку таблиц</a></div>
        <div class="toolbar">
          <div>
            <div class="title">{{ table_name }}</div>
            <div class="meta">Строк: {{ total_rows }}</div>
          </div>
          <form method="get" class="controls">
            <input type="text" name="q" placeholder="Поиск по строке..." value="{{ q }}" />
            <select name="page_size">
              {% for size in [20, 50, 100, 200] %}
                <option value="{{ size }}" {% if size == page_size %}selected{% endif %}>{{ size }}</option>
              {% endfor %}
            </select>
            <button type="submit">Искать</button>
          </form>
        </div>
      </div>

      <div class="card">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                {% for col in columns %}
                  <th>{{ col }}</th>
                {% endfor %}
              </tr>
            </thead>
            <tbody>
              {% for row in rows %}
                <tr>
                  {% for cell in row %}
                    <td>{{ cell }}</td>
                  {% endfor %}
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        <div class="pagination">
          {% if page > 1 %}
            <a href="{{ url_for('view_table', table_name=table_name, page=page-1, page_size=page_size, q=q) }}">Назад</a>
          {% endif %}
          <span>Стр. {{ page }}</span>
          {% if has_more %}
            <a href="{{ url_for('view_table', table_name=table_name, page=page+1, page_size=page_size, q=q) }}">Вперёд</a>
          {% endif %}
        </div>
      </div>
    </div>
  </body>
</html>
"""


def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"База не найдена: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_tables(conn: sqlite3.Connection) -> List[Tuple[str, int]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    tables = [row[0] for row in cur.fetchall()]
    results: List[Tuple[str, int]] = []
    for name in tables:
        try:
            cur.execute(f"SELECT COUNT(1) FROM {quote_ident(name)}")
            count = cur.fetchone()[0]
        except sqlite3.Error:
            count = 0
        results.append((name, count))
    return results


def get_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({quote_ident(table_name)})")
    return [row[1] for row in cur.fetchall()]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def build_search_clause(columns: Iterable[str], query: str) -> Tuple[str, List[str]]:
    if not query:
        return "", []
    like = f"%{query}%"
    clauses = [f"CAST({quote_ident(col)} AS TEXT) LIKE ?" for col in columns]
    return "WHERE " + " OR ".join(clauses), [like] * len(clauses)


@app.route("/")
def index():
    with get_connection() as conn:
        tables = get_tables(conn)
    return render_template_string(INDEX_TEMPLATE, tables=tables, db_path=DB_PATH)


@app.route("/table/<table_name>")
def view_table(table_name: str):
    page = max(int(request.args.get("page", 1)), 1)
    page_size = int(request.args.get("page_size", DEFAULT_PAGE_SIZE))
    page_size = min(max(page_size, 10), MAX_PAGE_SIZE)
    q = request.args.get("q", "").strip()

    with get_connection() as conn:
        columns = get_columns(conn, table_name)
        if not columns:
            abort(404, description="Таблица не найдена")

        where_sql, params = build_search_clause(columns, q)
        offset = (page - 1) * page_size

        cur = conn.cursor()
        count_sql = f"SELECT COUNT(1) FROM {quote_ident(table_name)} {where_sql}"
        cur.execute(count_sql, params)
        total_rows = cur.fetchone()[0]

        query_sql = (
            f"SELECT * FROM {quote_ident(table_name)} {where_sql} " "LIMIT ? OFFSET ?"
        )
        cur.execute(query_sql, params + [page_size + 1, offset])
        fetched = cur.fetchall()
        has_more = len(fetched) > page_size
        rows = fetched[:page_size]

    return render_template_string(
        TABLE_TEMPLATE,
        table_name=table_name,
        columns=columns,
        rows=rows,
        page=page,
        page_size=page_size,
        q=q,
        total_rows=total_rows,
        has_more=has_more,
    )


@app.route("/catalog")
def catalog():
    """Каталог пива с фильтрами"""

    # Получаем параметры фильтров
    filters = {
        "name": request.args.get("name", "").strip(),
        "producer": request.args.get("producer", "").strip(),
        "countries": request.args.getlist("country"),
        "cities": request.args.getlist("city"),
        "categories": request.args.getlist("category"),
        "styles": request.args.getlist("style"),
        "substyles": request.args.getlist("substyle"),
        "abv_min": request.args.get("abv_min", "").strip(),
        "abv_max": request.args.get("abv_max", "").strip(),
        "volumes": request.args.getlist("volume"),
        "ibu_min": request.args.get("ibu_min", "").strip(),
        "ibu_max": request.args.get("ibu_max", "").strip(),
        "colors": request.args.getlist("color"),
        "aromas": request.args.getlist("aroma"),
        "tastes": request.args.getlist("taste"),
    }

    with get_connection() as conn:
        cur = conn.cursor()

        # Получаем опции для фильтров
        filter_options = {}

        # Страны
        cur.execute(
            "SELECT DISTINCT brewery_country FROM products_full WHERE brewery_country IS NOT NULL AND brewery_country != '' ORDER BY brewery_country"
        )
        filter_options["countries"] = [row[0] for row in cur.fetchall()]

        # Города
        cur.execute(
            "SELECT DISTINCT brewery_city FROM products_full WHERE brewery_city IS NOT NULL AND brewery_city != '' ORDER BY brewery_city"
        )
        filter_options["cities"] = [row[0] for row in cur.fetchall()]

        # Категории
        cur.execute(
            "SELECT DISTINCT category FROM products_full WHERE category IS NOT NULL AND category != '' ORDER BY category"
        )
        filter_options["categories"] = [row[0] for row in cur.fetchall()]

        # Стили
        cur.execute(
            "SELECT DISTINCT style FROM products_full WHERE style IS NOT NULL AND style != '' ORDER BY style"
        )
        filter_options["styles"] = [row[0] for row in cur.fetchall()]

        # Подстили
        cur.execute(
            "SELECT DISTINCT substyle FROM products_full WHERE substyle IS NOT NULL AND substyle != '' ORDER BY substyle"
        )
        filter_options["substyles"] = [row[0] for row in cur.fetchall()]

        # Объемы
        cur.execute(
            "SELECT DISTINCT volume FROM products_full WHERE volume IS NOT NULL ORDER BY volume"
        )
        filter_options["volumes"] = [row[0] for row in cur.fetchall()]

        # Цвета
        cur.execute(
            "SELECT DISTINCT color FROM products_full WHERE color IS NOT NULL AND color != '' ORDER BY color"
        )
        filter_options["colors"] = [row[0] for row in cur.fetchall()]

        # Ароматы
        cur.execute(
            "SELECT DISTINCT aroma FROM products_full WHERE aroma IS NOT NULL AND aroma != '' ORDER BY aroma"
        )
        filter_options["aromas"] = [row[0] for row in cur.fetchall()]

        # Вкусы
        cur.execute(
            "SELECT DISTINCT taste FROM products_full WHERE taste IS NOT NULL AND taste != '' ORDER BY taste"
        )
        filter_options["tastes"] = [row[0] for row in cur.fetchall()]

        # Строим SQL запрос с фильтрами
        query = "SELECT * FROM products_full WHERE 1=1"
        params = []

        # Название
        if filters["name"]:
            query += " AND name LIKE ?"
            params.append(f"%{filters['name']}%")

        # Производитель
        if filters["producer"]:
            query += " AND producer LIKE ?"
            params.append(f"%{filters['producer']}%")

        # Страна
        if filters["countries"]:
            placeholders = ",".join(["?" for _ in filters["countries"]])
            query += f" AND brewery_country IN ({placeholders})"
            params.extend(filters["countries"])

        # Город
        if filters["cities"]:
            placeholders = ",".join(["?" for _ in filters["cities"]])
            query += f" AND brewery_city IN ({placeholders})"
            params.extend(filters["cities"])

        # Категория
        if filters["categories"]:
            placeholders = ",".join(["?" for _ in filters["categories"]])
            query += f" AND category IN ({placeholders})"
            params.extend(filters["categories"])

        # Стиль
        if filters["styles"]:
            placeholders = ",".join(["?" for _ in filters["styles"]])
            query += f" AND style IN ({placeholders})"
            params.extend(filters["styles"])

        # Подстиль
        if filters["substyles"]:
            placeholders = ",".join(["?" for _ in filters["substyles"]])
            query += f" AND substyle IN ({placeholders})"
            params.extend(filters["substyles"])

        # Крепость ABV
        if filters["abv_min"]:
            query += " AND abv >= ?"
            params.append(float(filters["abv_min"]))
        if filters["abv_max"]:
            query += " AND abv <= ?"
            params.append(float(filters["abv_max"]))

        # Объем
        if filters["volumes"]:
            placeholders = ",".join(["?" for _ in filters["volumes"]])
            query += f" AND volume IN ({placeholders})"
            params.extend([int(v) for v in filters["volumes"]])

        # Горечь IBU
        if filters["ibu_min"]:
            query += " AND ibu >= ?"
            params.append(int(filters["ibu_min"]))
        if filters["ibu_max"]:
            query += " AND ibu <= ?"
            params.append(int(filters["ibu_max"]))

        # Цвет
        if filters["colors"]:
            placeholders = ",".join(["?" for _ in filters["colors"]])
            query += f" AND color IN ({placeholders})"
            params.extend(filters["colors"])

        # Аромат
        if filters["aromas"]:
            placeholders = ",".join(["?" for _ in filters["aromas"]])
            query += f" AND aroma IN ({placeholders})"
            params.extend(filters["aromas"])

        # Вкус
        if filters["tastes"]:
            placeholders = ",".join(["?" for _ in filters["tastes"]])
            query += f" AND taste IN ({placeholders})"
            params.extend(filters["tastes"])

        query += " ORDER BY name"

        cur.execute(query, params)
        products = cur.fetchall()
        total_results = len(products)

    return render_template_string(
        CATALOG_TEMPLATE,
        products=products,
        total_results=total_results,
        filters=filters,
        filter_options=filter_options,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
