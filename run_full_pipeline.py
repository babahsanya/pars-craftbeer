"""Полный конвейер обновления данных пивной энциклопедии.

Запускает по очереди:
  1) Парсер craftbeer78.ru (--refresh) — наполняет description/IBU/стиль/состав и т.д.
  2) Кеш картинок image_cache.py — скачивает фото бутылок локально
  3) Справочник стилей style_guide.py — обновляет BJCP-описания

Все шаги поддерживают resume (повторный запуск продолжит с места остановки),
поэтому процесс можно прервать Ctrl+C и запустить снова.

Использование:
    python run_full_pipeline.py                # все 3 шага
    python run_full_pipeline.py --step parse   # только парсер
    python run_full_pipeline.py --step images  # только картинки
    python run_full_pipeline.py --step styles  # только справочник стилей
    python run_full_pipeline.py --limit 500    # ограничить (для теста)

Запускайте из папки проекта. Использует Python из .venv если он есть.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
PY = sys.executable  # python, которым запущен этот скрипт


def banner(title: str) -> None:
    line = "=" * 70
    print(f"\n{line}\n  {title}\n{line}", flush=True)


def run(cmd: list[str], step_name: str) -> int:
    """Запускает subprocess и стримит вывод. Возвращает код возврата."""
    print(f"\n▶ Запуск: {' '.join(cmd)}", flush=True)
    start = time.time()
    try:
        ret = subprocess.call(cmd, cwd=str(APP_ROOT))
    except KeyboardInterrupt:
        print(f"\n⚠ {step_name}: прервано пользователем (Ctrl+C)", flush=True)
        return 130
    elapsed = time.time() - start
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    status = "✅ УСПЕХ" if ret == 0 else f"❌ КОД {ret}"
    print(f"\n{status} — {step_name} ({mins} мин {secs} сек)", flush=True)
    return ret


def step_parse(limit: int | None, fresh: bool, failed_only: bool) -> int:
    banner("ШАГ 1/3 — ПАРСИНГ craftbeer78.ru")
    cmd = [PY, "craftbeer_global_parser.py", "--refresh"]
    if fresh:
        cmd.append("--fresh")
    elif failed_only:
        cmd.append("--failed-only")
    if limit:
        cmd += ["--limit", str(limit)]
    print(
        "Перевычитывает все страницы из базы, обновляя description/IBU/стиль/состав/цены.\n"
        "Resume: повторный запуск продолжит с места остановки (если без --fresh).",
        flush=True,
    )
    return run(cmd, "Парсинг")


def step_images(limit: int | None, max_per_beer: int) -> int:
    banner("ШАГ 2/3 — КЕШ КАРТИНОК")
    cmd = [PY, "image_cache.py", "--max-per-beer", str(max_per_beer)]
    if limit:
        cmd += ["--limit", str(limit)]
    print(
        "Скачивает реальные фото бутылок (/images_beers/) в static/images/<id>/.\n"
        "Resume: уже скачанные позиции пропускаются.",
        flush=True,
    )
    return run(cmd, "Кеш картинок")


def step_styles() -> int:
    banner("ШАГ 3/3 — СПРАВОЧНИК СТИЛЕЙ BJCP")
    return run([PY, "style_guide.py"], "Справочник стилей")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Полный конвейер обновления пивной энциклопедии.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python run_full_pipeline.py                    # всё, с resume\n"
            "  python run_full_pipeline.py --limit 200        # тест на 200 позиций\n"
            "  python run_full_pipeline.py --step parse       # только парсер\n"
            "  python run_full_pipeline.py --fresh            # парсер с нуля\n"
            "  python run_full_pipeline.py --failed-only      # перепроверить ошибки\n"
        ),
    )
    ap.add_argument(
        "--step",
        choices=["parse", "images", "styles", "all"],
        default="all",
        help="Какой шаг запустить (по умолчанию: all).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество позиций (для парсера и картинок).",
    )
    ap.add_argument(
        "--max-per-beer",
        type=int,
        default=5,
        help="Максимум фото на позицию для image_cache (по умолчанию: 5).",
    )
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="Парсер с нуля (игнорировать parse_progress).",
    )
    ap.add_argument(
        "--failed-only",
        action="store_true",
        help="Парсер: повторить только ранее упавшие URL.",
    )
    args = ap.parse_args()

    # Предупреждение о времени полного прогона
    if args.step in ("all", "parse") and not args.limit:
        total_est = 7733 * 0.8 / 60  # ~0.8 сек/позицию
        print(
            f"ℹ️  Полный парсинг 7733 позиций займёт ~{total_est:.0f} минут.\n"
            "   Можно прервать Ctrl+C и перезапустить — продолжит с места остановки.\n"
            "   Для теста: python run_full_pipeline.py --limit 100",
            flush=True,
        )
        time.sleep(2)

    results = {}
    if args.step in ("all", "parse"):
        results["parse"] = step_parse(args.limit, args.fresh, args.failed_only)
    if args.step in ("all", "images"):
        results["images"] = step_images(args.limit, args.max_per_beer)
    if args.step in ("all", "styles"):
        results["styles"] = step_styles()

    banner("ИТОГ")
    for name, code in results.items():
        status = "✅" if code == 0 else ("⚠ прервано" if code == 130 else "❌")
        print(f"  {status}  {name}")

    overall = 0 if all(c in (0, 130) for c in results.values()) else 1
    print(
        f"\n{'✅ Конвейер завершён.' if overall == 0 else '❌ Конвейер завершился с ошибками.'}\n"
        "Запустить веб-энциклопедию: python app.py → http://127.0.0.1:8000",
        flush=True,
    )
    return overall


if __name__ == "__main__":
    sys.exit(main())
