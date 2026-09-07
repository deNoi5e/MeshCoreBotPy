"""
Часы Судного дня (Doomsday Clock) — символическая оценка близости
человечества к глобальной катастрофе от Bulletin of the Atomic Scientists.

Официального API нет, поэтому, как и core/traffic.py, — HTML-скрапинг
главной страницы https://thebulletin.org/doomsday-clock/ регуляркой.
Bulletin обновляет значение примерно раз в год (обычно в конце января на
пресс-конференции), так что результат кэшируется на длительный срок —
частые запросы не нужны и не желательны.

Страница даёт заголовок вида "It is now 85 seconds to midnight" и абзац
"On January 27, 2026, the Doomsday Clock was set at 85 seconds to
midnight" — второй попутно даёт дату объявления. Оба текста — устойчивая
формулировка, которую Bulletin использует из года в год, но, как и
любой скрапинг, это зависит от вёрстки сайта и может сломаться при её
смене (см. предупреждение в core/gismeteo_omsk.py).
"""

import logging
import re
import ssl
import time

import aiohttp
import certifi

logger = logging.getLogger(__name__)

CLOCK = "\U0001F55B"  # 🕛

_URL = "https://thebulletin.org/doomsday-clock/"
_SOURCE = "thebulletin.org/doomsday-clock"

_HEADLINE_RE = re.compile(
    r"It is now\s+(\d+)\s+(second|minute)s?\s+to midnight", re.IGNORECASE)
_ANNOUNCED_RE = re.compile(
    r"On\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4}),\s+the Doomsday Clock was set",
    re.IGNORECASE)

_MONTHS_RU = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}

_CACHE_TTL = 24 * 3600
_cache: tuple[float, str] | None = None


def _short_date(text: str) -> str:
    """`January 27, 2026` -> `27.01.26`. При неожиданном формате — пусто."""
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    if not m:
        return ""
    month = _MONTHS_RU.get(m.group(1))
    if not month:
        return ""
    return f"{int(m.group(2)):02d}.{month}.{m.group(3)[2:]}"


async def _fetch_doomsday() -> str:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(_URL, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return f"❌ thebulletin.org недоступен (код {resp.status})"
                html = await resp.text()
    except Exception as e:
        return f"❌ Ошибка запроса: {e}"

    m = _HEADLINE_RE.search(html)
    if not m:
        logger.warning("doomsday: не найден заголовок часов в HTML")
        return f"❌ Не удалось получить данные, см. {_SOURCE}"

    amount, unit = int(m.group(1)), m.group(2).lower()
    unit_ru = "сек" if unit == "second" else "мин"

    date_match = _ANNOUNCED_RE.search(html)
    day = _short_date(date_match.group(1)) if date_match else ""
    suffix = f" ({day})" if day else ""

    return f"{CLOCK} Судный день: {amount} {unit_ru} до полуночи{suffix} {_SOURCE}"


async def get_doomsday() -> str:
    global _cache

    if _cache and time.time() - _cache[0] < _CACHE_TTL:
        return _cache[1]

    result = await _fetch_doomsday()
    if not result.startswith("❌"):
        _cache = (time.time(), result)
    return result
