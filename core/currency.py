"""
Курсы валют к рублю по данным Центрального банка РФ.

Источник — официальный XML ЦБ: https://www.cbr.ru/scripts/XML_daily.asp
(кодировка windows-1251). Курс публикуется раз в рабочий день,
поэтому результат кэшируется на 30 минут.

Изменение считается относительно предыдущей публикации: запрашивается
XML за день до даты текущего курса — если в этот день публикации не было
(выходной), ЦБ отдаёт ближайшую предыдущую.
"""

import re
import ssl
import time
import logging
from datetime import date, datetime, timedelta

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

# Валюты в порядке вывода: код -> подпись в ответе
_CURRENCIES = {
    "USD": "USD",
    "EUR": "EUR",
    "CNY": "CNY",
}

_CACHE_TTL = 30 * 60
_cache: tuple[float, str] | None = None


async def _fetch(day: date | None = None) -> str:
    """Забирает XML ЦБ (за конкретную дату или самый свежий)."""
    params = {"date_req": day.strftime("%d/%m/%Y")} if day else None
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(_URL, params=params, ssl=ssl_ctx,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"cbr.ru код {resp.status}")
            raw = await resp.read()
    return raw.decode("windows-1251", errors="replace")


def _parse_date(xml: str) -> date | None:
    m = re.search(r'Date="(\d{2}\.\d{2}\.\d{4})"', xml)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%d.%m.%Y").date()


def _parse_rates(xml: str) -> dict[str, float]:
    """CharCode -> курс за 1 единицу валюты (Value / Nominal)."""
    rates: dict[str, float] = {}
    for block in re.findall(r"<Valute\b.*?</Valute>", xml, re.S):
        code = re.search(r"<CharCode>(\w+)</CharCode>", block)
        value = re.search(r"<Value>([\d,]+)</Value>", block)
        nominal = re.search(r"<Nominal>(\d+)</Nominal>", block)
        if not (code and value and nominal):
            continue
        if code.group(1) not in _CURRENCIES:
            continue
        rates[code.group(1)] = float(value.group(1).replace(",", ".")) / int(nominal.group(1))
    return rates


async def get_rates() -> str:
    global _cache

    if _cache and time.time() - _cache[0] < _CACHE_TTL:
        return _cache[1]

    try:
        xml = await _fetch()
        rates = _parse_rates(xml)
    except Exception as e:
        return f"❌ Ошибка запроса к ЦБ РФ: {e}"

    if not rates:
        logger.warning("currency: курсы не найдены в XML ЦБ")
        return "❌ Не удалось получить курсы валют"

    cur_date = _parse_date(xml)

    prev: dict[str, float] = {}
    if cur_date:
        try:
            prev = _parse_rates(await _fetch(cur_date - timedelta(days=1)))
        except Exception as e:
            logger.warning(f"currency: предыдущий курс не получен: {e}")

    header = f"💱 ЦБ РФ на {cur_date.strftime('%d.%m')}:" if cur_date else "💱 ЦБ РФ:"
    lines = [header]
    for code, label in _CURRENCIES.items():
        rate = rates.get(code)
        if rate is None:
            continue
        line = f"{label} {rate:.2f}".replace(".", ",")
        old = prev.get(code)
        if old is not None:
            delta = rate - old
            line += f" ({delta:+.2f})".replace(".", ",")
        lines.append(line)

    result = "\n".join(lines)
    _cache = (time.time(), result)
    return result
