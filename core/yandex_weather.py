import asyncio
import logging
import ssl
from datetime import datetime, timezone

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_API_URL = "https://api.weather.yandex.ru/v2/forecast"

# ---------------------------------------------------------------------------
# Кэш Яндекс погоды
# ---------------------------------------------------------------------------
_cache_current: str | None = None      # строка текущей погоды (/yapogoda)
_cache_forecast: str | None = None     # строка прогноза на сегодня (/yaprognoz)
_cache_time: datetime | None = None    # время последнего обновления


def get_cached_ya_current() -> str | None:
    return _cache_current


def get_cached_ya_forecast() -> str | None:
    return _cache_forecast


def _set_cache(current: str, forecast: str) -> None:
    global _cache_current, _cache_forecast, _cache_time
    _cache_current = current
    _cache_forecast = forecast
    _cache_time = datetime.now(tz=timezone.utc)
    logger.info(f"🗃  Кэш Яндекс погоды обновлён")


# ---------------------------------------------------------------------------
# Справочники
# ---------------------------------------------------------------------------
_CONDITIONS = {
    "clear":                  "ясно",
    "partly-cloudy":          "малооблачно",
    "cloudy":                 "облачно",
    "overcast":               "пасмурно",
    "light-rain":             "небольшой дождь",
    "rain":                   "дождь",
    "heavy-rain":             "сильный дождь",
    "showers":                "ливень",
    "wet-snow":               "дождь со снегом",
    "light-snow":             "небольшой снег",
    "snow":                   "снег",
    "snow-showers":           "снегопад",
    "hail":                   "град",
    "thunderstorm":           "гроза",
    "thunderstorm-with-rain": "дождь с грозой",
    "thunderstorm-with-hail": "гроза с градом",
}

_ICONS = {
    "clear":                  "☀",
    "partly-cloudy":          "🌤",
    "cloudy":                 "⛅",
    "overcast":               "☁",
    "light-rain":             "🌦",
    "rain":                   "🌧",
    "heavy-rain":             "🌧",
    "showers":                "🌧",
    "wet-snow":               "🌨",
    "light-snow":             "🌨",
    "snow":                   "❄",
    "snow-showers":           "❄",
    "hail":                   "🌨",
    "thunderstorm":           "⛈",
    "thunderstorm-with-rain": "⛈",
    "thunderstorm-with-hail": "⛈",
}

_PREC_TYPES = {0: None, 1: "дождь", 2: "дождь со снегом", 3: "снег", 4: "град"}

_WIND_DIRS = {
    "n": "С", "ne": "СВ", "e": "В", "se": "ЮВ",
    "s": "Ю", "sw": "ЮЗ", "w": "З", "nw": "СЗ", "c": "штиль",
}


def _fit(s: str) -> str:
    """Обрезать строку до 143 байт."""
    enc = s.encode("utf-8")
    if len(enc) <= 143:
        return s
    return enc[:143].decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Запрос к API
# ---------------------------------------------------------------------------
async def _fetch_yandex(api_key: str, lat: float, lon: float) -> dict:
    """Запросить данные Яндекс Погоды. Возвращает JSON или бросает исключение."""
    params = {
        "lat": lat, "lon": lon,
        "lang": "ru_RU", "limit": 1,
        "hours": "false", "extra": "false",
    }
    headers = {"X-Yandex-Weather-Key": api_key}
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession() as session:
        async with session.get(
            _API_URL, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx,
        ) as resp:
            if resp.status == 403:
                raise RuntimeError("неверный API ключ (403)")
            if resp.status == 404:
                raise RuntimeError("координаты не найдены (404)")
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.json(content_type=None)


def _build_current(data: dict) -> str:
    """Сформировать строку текущей погоды из JSON ответа."""
    fact = data.get("fact", {})
    condition = fact.get("condition", "")
    icon = _ICONS.get(condition, "🌡")
    cond_ru = _CONDITIONS.get(condition, condition)
    temp = fact.get("temp", 0)
    feels = fact.get("feels_like", temp)
    humidity = fact.get("humidity", 0)
    wind = fact.get("wind_speed", 0)
    pressure = fact.get("pressure_mm", 0)

    feels_str = f"/{feels:+d}°" if feels != temp else ""

    # Вероятность осадков из day_short
    prec_str = ""
    forecasts = data.get("forecasts", [])
    if forecasts:
        day_short = forecasts[0].get("parts", {}).get("day_short", {})
        prec_prob = day_short.get("prec_prob", 0)
        prec_type = day_short.get("prec_type", 0)
        if prec_prob and _PREC_TYPES.get(prec_type):
            prec_str = f" 🌂{prec_prob}%"

    result = (
        f"{icon} {cond_ru} {temp:+d}°C{feels_str} "
        f"💧{humidity}% 💨{wind:.0f}м/с {pressure}мм{prec_str}"
    )
    if len(result.encode("utf-8")) > 143:
        result = f"{icon} {cond_ru} {temp:+d}°C{feels_str} 💧{humidity}% 💨{wind:.0f}м/с{prec_str}"
    return _fit(result)


def _build_forecast(data: dict) -> str:
    """Сформировать строку прогноза на сегодня из JSON ответа."""
    forecasts = data.get("forecasts", [])
    if not forecasts:
        return "❌ Нет данных прогноза"

    parts = forecasts[0].get("parts", {})
    day = parts.get("day_short", {})
    night = parts.get("night_short", {})

    condition = day.get("condition", "")
    icon = _ICONS.get(condition, "🌡")
    cond_ru = _CONDITIONS.get(condition, condition)

    t_day = day.get("temp", 0)
    t_night = night.get("temp", 0)
    feels_day = day.get("feels_like", t_day)
    wind = day.get("wind_speed", 0)
    humidity = day.get("humidity", 0)
    pressure = day.get("pressure_mm", 0)
    prec_prob = day.get("prec_prob", 0)
    prec_type = day.get("prec_type", 0)

    feels_str = f"/{feels_day:+d}°" if feels_day != t_day else ""
    prec_str = ""
    if prec_prob and _PREC_TYPES.get(prec_type):
        prec_str = f" 🌂{prec_prob}%"

    # Формат: ⛅ облачно день+18°/+16° ночь+8° 💧70% 💨4м/с 748мм 🌂30%
    result = (
        f"{icon} {cond_ru} д{t_day:+d}°{feels_str} н{t_night:+d}° "
        f"💧{humidity}% 💨{wind:.0f}м/с {pressure}мм{prec_str}"
    )
    if len(result.encode("utf-8")) > 143:
        result = (
            f"{icon} {cond_ru} д{t_day:+d}°{feels_str} н{t_night:+d}° "
            f"💧{humidity}% 💨{wind:.0f}м/с{prec_str}"
        )
    if len(result.encode("utf-8")) > 143:
        result = f"{icon} {cond_ru} д{t_day:+d}° н{t_night:+d}° 💧{humidity}% 💨{wind:.0f}м/с"
    return _fit(result)


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------
async def get_yandex_weather(api_key: str, lat: float, lon: float) -> str:
    """Текущая погода — из кэша или свежий запрос."""
    cached = get_cached_ya_current()
    if cached:
        logger.info("   🌥 /yapogoda из кэша")
        return cached
    try:
        data = await _fetch_yandex(api_key, lat, lon)
        current = _build_current(data)
        forecast = _build_forecast(data)
        _set_cache(current, forecast)
        return current
    except asyncio.TimeoutError:
        return "❌ Яндекс Погода не отвечает"
    except Exception as e:
        return f"❌ Яндекс Погода: {e}"


async def get_yandex_forecast(api_key: str, lat: float, lon: float) -> str:
    """Прогноз на сегодня — из кэша или свежий запрос."""
    cached = get_cached_ya_forecast()
    if cached:
        logger.info("   🌥 /yaprognoz из кэша")
        return cached
    try:
        data = await _fetch_yandex(api_key, lat, lon)
        current = _build_current(data)
        forecast = _build_forecast(data)
        _set_cache(current, forecast)
        return forecast
    except asyncio.TimeoutError:
        return "❌ Яндекс Погода не отвечает"
    except Exception as e:
        return f"❌ Яндекс Погода: {e}"


async def yandex_weather_cache_updater(config: dict) -> None:
    """Фоновая задача: обновляет кэш Яндекс погоды каждый час (HH:00 UTC)."""
    from core.weather import _secs_to_next_hour_boundary  # локальный импорт
    ya = config.get("yandex_weather", {})
    api_key = ya.get("api_key", "")
    lat = ya.get("lat", 0.0)
    lon = ya.get("lon", 0.0)

    if not api_key or not lat or not lon:
        logger.info("🌥 Яндекс погода: кэш не запускается (нет настроек)")
        return

    # Первый запрос при старте
    try:
        data = await _fetch_yandex(api_key, lat, lon)
        _set_cache(_build_current(data), _build_forecast(data))
    except Exception as e:
        logger.error(f"❌ Яндекс погода: ошибка первого запроса: {e}")

    while True:
        wait_sec = _secs_to_next_hour_boundary(1)
        logger.info(f"🗃  Следующее обновление кэша Яндекс погоды через {wait_sec/60:.1f} мин")
        await asyncio.sleep(wait_sec)
        try:
            data = await _fetch_yandex(api_key, lat, lon)
            _set_cache(_build_current(data), _build_forecast(data))
        except Exception as e:
            logger.error(f"❌ Яндекс погода: ошибка обновления кэша: {e}")
