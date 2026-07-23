"""Классификация пивных стилей в 15 канонических семей.

Решает проблему: в базе ~592 уникальных стиля на смеси ru/en, многие из которых
синонимичны («IPA - American» и «Американский ИПА»). Для удобной навигации все
стили относятся к одной из 15 семей по keyword-правилам.

Покрытие по данным: ~97.3% позиций классифицируются в 15 семей, ~2.7% — Other.
"""

from __future__ import annotations

# Канонический список семей в порядке приоритета (важен для перекрытий).
# Non-Alcoholic и Cider проверяются раньше IPA, чтобы «Безалкогольный ИПА» → Non-Alcoholic.
FAMILY_KEY: str = "style_family"

# Каждое правило: (family_id, [keywords], icon, ru_title, description)
# keywords — подстроки в lower-case, которые ищутся в названии стиля.
FAMILIES: list[tuple[str, list[str], str, str, str]] = [
    (
        "non-alcoholic",
        ["non-alcoholic", "безалког", "без алког", "0.0%", "0,0%", "non alcoholic"],
        "🥤",
        "Безалкогольные",
        "Пиво с содержанием алкоголя менее 0.5%. Сохраняет вкус и аромат оригинального стиля.",
    ),
    (
        "cider",
        ["сидр", "cider", "сайзер", "cidre", "ice cider"],
        "🍏",
        "Сидры",
        "Слабоалкогольный напиток из сброженного яблочного сока. От сухих игристых до сладких.",
    ),
    (
        "mead",
        ["mead", "медовух", "медом", "melomel", "метеглин", "бракет", "медовуха",
         "медомел", "смузи мид", "smoothie mead"],
        "🍯",
        "Медовухи",
        "Напиток из сброженного мёда, часто с фруктами и специями.",
    ),
    (
        "lambic",
        ["lambic", "ламбик", "gueuze", "гёз", "гёза", "kriek", "крик",
         "фруктовый ламбик", "фаро", "faro"],
        "🍇",
        "Ламбики",
        "Бельгийские кислые эли спонтанного брожения. Гёз — купаж молодых и старых ламбиков.",
    ),
    (
        "sour",
        ["sour", "кисл", "гозе", "gose", "berliner", "берлинер", "берлинер вайссе",
         "смузи", "smoothie", "pastry sour", "фруктовый кислый", "кислый эль",
         "томатный гозе", "kettle sour"],
        "🍋",
        "Кислые (Sour)",
        "Пива с заметной кислотностью: от освежающих гозе до десертных смузи-сауэров.",
    ),
    (
        "stout",
        ["stout", "стаут", "паstry стаут", "dessert stout", "десертный стаут"],
        "🍫",
        "Стауты",
        "Тёмные обжаренные эли: от сухих ирландских до сладких молочных и плотных имперских.",
    ),
    (
        "porter",
        ["porter", "портер", "балтийский портер"],
        "☕",
        "Портеры",
        "Тёмные эли с жареным солодом, родственные стаутам, часто с шоколадно-кофейным профилем.",
    ),
    (
        "ipa",
        ["ipa", "ипа", "india pale", "нью ингланд", "new england", "hazy",
         "double ipa", "triple ipa", "двойной ипа", "тройной ипа"],
        "🍻",
        "IPA",
        "India Pale Ale и его производные: американские, новые-английские, двойные, тройные.",
    ),
    (
        "pale-ale",
        ["pale ale", "светлый эль", "apa", "американский светлый", "светлый эль"],
        "🌱",
        "Пейл-эли",
        "Питкие хмелевые эли, менее горькие чем IPA. American и New England версии.",
    ),
    (
        "lager",
        ["lager", "лагер", "pilsner", "пилснер", "pils", "helles", "хеллес",
         "бок", "bock", "dunkel", "дункель", "märzen", "мерцен", "шварц", "schwarz",
         "rausch", "райх", "kellerbier", "келлербир"],
        "🪣",
        "Лагеры",
        "Нижнего брожения: от светлых хеллесов и пилснеров до тёмных боков и дункелей.",
    ),
    (
        "wheat",
        ["hefeweizen", "пшенич", "witbier", "бланш", "blanche", "wheat", "дункельвайс",
         "weissbier", "вайсбир", "пшеничное", "weizen"],
        "🌾",
        "Пшеничные",
        "Эли с высокой долей пшеничного солода: немецкие хефевайцен с банан-гвоздикой и бельгийские витбьеры.",
    ),
    (
        "belgian",
        ["belgian", "бельгий", "saison", "сейзон", "сэзон", "tripel", "трипл",
         "квадрюпель", "quadrupel", "quad", "trappist", "траппист", "abbey", "аббат",
         "dubbel", "дюббель", "blond ale"],
        "🏆",
        "Бельгийские",
        "Траппистские и аббатские стили: трипели, квадрюпели, сезон, блонд.",
    ),
    (
        "wild-ale",
        ["wild ale", "дикий эль", "brett", "бретт"],
        "🍄",
        "Дикие эли",
        "Эли, сброженные с дикими дрожжами Brettanomyces — фанк, землистость, комплексность.",
    ),
    (
        "fruit-beer",
        ["fruit beer", "фруктовое пиво", "фруктовый эль", "pumpkin", "тыквенное",
         "grape ale", "виноградный", "radler", "радлер", "shandy", "шенди"],
        "🍒",
        "Фруктовое пиво",
        "Пиво с фруктовыми добавками: тыквенное, вишнёвое, виноградное и эксперименты.",
    ),
    (
        "barleywine",
        ["barleywine", "барливайн", "барливайн", "strong ale", "крепкий эль",
         "wee heavy", "scotch ale", "скотч", "wheat wine", "rye wine", "рожевое вино",
         "old ale"],
        "🥃",
        "Барливайн / Крепкие",
        "Очень крепкие эли (8-12%+): barleywine, strong ale, wee heavy, wheat wine.",
    ),
]

FALLBACK_FAMILY = (
    "other",
    [],
    "🥂",
    "Прочее",
    "Стили, не попавшие в основные семьи: гибриды, эксперименты, исторические стили.",
)

# Словарь для быстрого доступа по family_id
FAMILY_BY_ID: dict[str, tuple[str, list[str], str, str, str]] = {
    f[0]: f for f in FAMILIES
}
FAMILY_BY_ID[FALLBACK_FAMILY[0]] = FALLBACK_FAMILY


def classify_style(style: str | None) -> str:
    """Возвращает family_id для данного стиля.

    >>> classify_style("IPA - American")
    'ipa'
    >>> classify_style("Американский ИПА")
    'ipa'
    >>> classify_style("Безалкогольный ИПА")
    'non-alcoholic'
    >>> classify_style("Sour - Fruited")
    'sour'
    >>> classify_style("Some Unknown Weird Style")
    'other'
    """
    if not style:
        return FALLBACK_FAMILY[0]
    s = str(style).lower()
    for family_id, keywords, *_ in FAMILIES:
        for kw in keywords:
            if kw in s:
                return family_id
    return FALLBACK_FAMILY[0]


def family_meta(family_id: str) -> tuple[str, str, str]:
    """Возвращает (icon, ru_title, description) для family_id."""
    f = FAMILY_BY_ID.get(family_id, FALLBACK_FAMILY)
    return f[2], f[3], f[4]


def family_title(family_id: str) -> str:
    return FAMILY_BY_ID.get(family_id, FALLBACK_FAMILY)[3]


def family_icon(family_id: str) -> str:
    return FAMILY_BY_ID.get(family_id, FALLBACK_FAMILY)[2]


def all_family_ids() -> list[str]:
    """Все family_id в каноническом порядке + other в конце."""
    return [f[0] for f in FAMILIES] + [FALLBACK_FAMILY[0]]


if __name__ == "__main__":
    # Самотестирование на нескольких примерах
    import doctest
    doctest.testmod(verbose=False)
    print("✅ style_families: классификатор готов")
    print(f"   {len(FAMILIES)} семей + fallback 'other'")
    for fid, _, icon, title, _ in FAMILIES:
        print(f"   {icon} {fid:18s} — {title}")
