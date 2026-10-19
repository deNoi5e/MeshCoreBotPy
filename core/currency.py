"""
Курсы валют к рублю по данным Центрального банка РФ.

Источник — официальный XML ЦБ: https://www.cbr.ru/scripts/XML_daily.asp
(кодировка windows-1251). Курс публикуется раз в рабочий день,
поэтому результат кэшируется на 30 минут.

Изменение считается относительно предыдущей публикации: запрашивается
XML за день до даты текущего курса — если в этот день публикации не было
(выходной), ЦБ отдаёт ближайшую предыдущую.
"""

import math
import re
import ssl
import time
import logging
from datetime import date, datetime, timedelta

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

# Валюты в порядке вывода: код -> (подпись в ответе, за сколько единиц курс).
# Тенге — за 100, как котирует ЦБ: курс за 1 тенге (~0,15 ₽) с двумя знаками
# после запятой не показывал бы изменения.
_CURRENCIES = {
    "USD": ("USD", 1),
    "EUR": ("EUR", 1),
    "CNY": ("CNY", 1),
    "KZT": ("100 KZT", 100),
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


def _parse_rates(xml: str, only=None) -> dict[str, float]:
    """CharCode -> курс за 1 единицу валюты (Value / Nominal).

    only — набор кодов, которые нужны (None — все валюты из XML).
    """
    rates: dict[str, float] = {}
    for block in re.findall(r"<Valute\b.*?</Valute>", xml, re.S):
        code = re.search(r"<CharCode>(\w+)</CharCode>", block)
        value = re.search(r"<Value>([\d,]+)</Value>", block)
        nominal = re.search(r"<Nominal>(\d+)</Nominal>", block)
        if not (code and value and nominal):
            continue
        if only is not None and code.group(1) not in only:
            continue
        rates[code.group(1)] = float(value.group(1).replace(",", ".")) / int(nominal.group(1))
    return rates


def _parse_names(xml: str) -> dict[str, str]:
    """CharCode -> название валюты по-русски («Доллар США»)."""
    names: dict[str, str] = {}
    for block in re.findall(r"<Valute\b.*?</Valute>", xml, re.S):
        code = re.search(r"<CharCode>(\w+)</CharCode>", block)
        name = re.search(r"<Name>(.*?)</Name>", block)
        if code and name:
            names[code.group(1)] = name.group(1).strip()
    return names


async def get_rates() -> str:
    global _cache

    if _cache and time.time() - _cache[0] < _CACHE_TTL:
        return _cache[1]

    try:
        xml = await _fetch()
        rates = _parse_rates(xml, _CURRENCIES)
    except Exception as e:
        return f"❌ Ошибка запроса к ЦБ РФ: {e}"

    if not rates:
        logger.warning("currency: курсы не найдены в XML ЦБ")
        return "❌ Не удалось получить курсы валют"

    cur_date = _parse_date(xml)

    prev: dict[str, float] = {}
    if cur_date:
        try:
            prev = _parse_rates(await _fetch(cur_date - timedelta(days=1)), _CURRENCIES)
        except Exception as e:
            logger.warning(f"currency: предыдущий курс не получен: {e}")

    header = f"💱 ЦБ РФ на {cur_date.strftime('%d.%m')}:" if cur_date else "💱 ЦБ РФ:"
    lines = [header]
    for code, (label, units) in _CURRENCIES.items():
        rate = rates.get(code)
        if rate is None:
            continue
        rate *= units
        line = f"{label} {rate:.2f}".replace(".", ",")
        old = prev.get(code)
        if old is not None:
            delta = rate - old * units
            line += f" ({delta:+.2f})".replace(".", ",")
        lines.append(line)

    result = "\n".join(lines)
    _cache = (time.time(), result)
    return result


_AMOUNT = re.compile(r"\d+(?:[.,]\d+)?")
_CODE = re.compile(r"[A-Za-z]{3}")
_MAX_AMOUNT = 1e12

_all_cache: tuple[float, date | None, dict[str, float], dict[str, str]] | None = None


async def _get_all_rates() -> tuple[date | None, dict[str, float], dict[str, str]]:
    """Курсы (за 1 единицу) и названия всех валют ЦБ (кэш на 30 минут)."""
    global _all_cache
    if _all_cache and time.time() - _all_cache[0] < _CACHE_TTL:
        return _all_cache[1:]
    xml = await _fetch()
    rates = _parse_rates(xml)
    if not rates:
        raise RuntimeError("курсы не найдены в XML")
    cur_date = _parse_date(xml)
    names = _parse_names(xml)
    # рубля в XML нет — он нужен как валюта «в» по умолчанию и как «из»
    rates["RUB"] = 1.0
    names["RUB"] = "Российский рубль"
    _all_cache = (time.time(), cur_date, rates, names)
    return cur_date, rates, names


def _fmt(value: float) -> str:
    """12345.6 -> «12 345,60»."""
    return f"{value:,.2f}".translate(str.maketrans({",": " ", ".": ","}))


def _money(v: float) -> str:
    """>= 1 — два знака; меньше — четыре значащих («0,18» у KZT съело бы курс)."""
    if v >= 1 or v <= 0:
        return _fmt(v)
    digits = min(3 - math.floor(math.log10(v)), 10)
    return f"{v:.{digits}f}".replace(".", ",")


def _rate_txt(v: float) -> str:
    """Курс: четыре значащих цифры, но не меньше двух знаков («83,62», «1,121», «0,002202»)."""
    if v <= 0:
        return _fmt(v)
    digits = min(max(2, 3 - math.floor(math.log10(v))), 10)
    return f"{v:,.{digits}f}".translate(str.maketrans({",": " ", ".": ","}))


async def convert(args: str) -> str:
    """Пересчёт суммы из одной валюты в другую (по умолчанию в рубли).

    «65 USD», «USD 65», «usd» (= 1 единица), «65 USD EUR», «USD EUR».
    """
    codes = [c.upper() for c in _CODE.findall(args)]
    amounts = _AMOUNT.findall(args)
    leftover = _AMOUNT.sub("", _CODE.sub("", args)).strip()
    if not 1 <= len(codes) <= 2 or len(amounts) > 1 or leftover:
        return "❌ Формат: /rate 65 USD [EUR] (сумма и валюта «в»; по умолчанию рубли)"
    code = codes[0]
    target = codes[1] if len(codes) == 2 else "RUB"
    amount = float(amounts[0].replace(",", ".")) if amounts else 1.0
    if amount > _MAX_AMOUNT:
        return "❌ Слишком большая сумма"

    try:
        cur_date, rates, names = await _get_all_rates()
    except Exception as e:
        return f"❌ Ошибка запроса к ЦБ РФ: {e}"

    for c in (code, target):
        if c not in rates:
            return f"❌ Валюта {c} не найдена в курсах ЦБ РФ"
    cross = rates[code] / rates[target]  # сколько target в одной единице code

    on = f" на {cur_date.strftime('%d.%m')}" if cur_date else ""
    amount_txt = f"{amount:g}".replace(".", ",") if amount < 1e6 else _fmt(amount)
    name = f" ({names[code]})" if code in names else ""
    if target == "RUB":
        unit, unit_name = "₽", ""
    else:
        unit, unit_name = target, f" ({names[target]})" if target in names else ""

    if amount == 1:
        return f"💱 1 {code}{name} = {_rate_txt(cross)} {unit}{unit_name}\nЦБ РФ{on}"
    return (f"💱 {amount_txt} {code}{name} = {_money(amount * cross)} {unit}{unit_name}\n"
            f"(1 {code} = {_rate_txt(cross)} {unit}, ЦБ РФ{on})")
