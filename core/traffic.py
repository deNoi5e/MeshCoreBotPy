import re
import ssl
import logging
import aiohttp
import certifi

logger = logging.getLogger(__name__)

_URL = "https://ngs55.ru/maps-traffic/"

_ICONS = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}

_DESCRIPTIONS = [
    (2,  "свободно"),
    (4,  "небольшие пробки"),
    (6,  "умеренные пробки"),
    (8,  "серьёзные пробки"),
    (10, "стоим"),
]


def _score_form(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "балл"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "балла"
    return "баллов"


def _describe(score: int) -> str:
    for threshold, desc in _DESCRIPTIONS:
        if score <= threshold:
            return desc
    return "стоим"


async def get_traffic_omsk() -> str:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(_URL, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return f"❌ ngs55.ru недоступен (код {resp.status})"
                html = await resp.text()
    except Exception as e:
        return f"❌ Ошибка запроса: {e}"

    m = re.search(r'level_\w+\s+(green|yellow|orange|red)_\w+[^>]*>(\d+)<', html)
    if not m:
        logger.warning("traffic: не найден балл в HTML")
        return "❌ Не удалось получить данные о пробках"

    color = m.group(1)
    score = int(m.group(2))
    icon = _ICONS.get(color, "🚗")
    desc = _describe(score)
    word = _score_form(score)

    return f"{icon} Омск: {score} {word} — {desc}"
