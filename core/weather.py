import asyncio
import logging
import ssl
from datetime import datetime, timezone, timedelta

import aiohttp
import certifi

logger = logging.getLogger(__name__)

# Кириллические омоглифы → ASCII (1 байт вместо 2)
_CYR_HOMOGLYPHS = str.maketrans({
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c',
    'у': 'y', 'х': 'x', 'ь': 'b',
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M',
    'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X',
})


def to_lat(text: str) -> str:
    return text.translate(_CYR_HOMOGLYPHS)


# ---------------------------------------------------------------------------
# Кэш погоды для города по умолчанию
# Обновляется каждый час фоновой задачей weather_cache_updater()
# ---------------------------------------------------------------------------
_weather_cache: dict[str, str] = {}   # city.lower() -> строка погоды
_weather_cache_time: dict[str, datetime] = {}   # city.lower() -> время обновления


def get_cached_weather(city: str) -> str | None:
    """Вернуть закэшированную строку погоды или None если кэша нет."""
    return _weather_cache.get(city.lower())


def _set_cached_weather(city: str, text: str) -> None:
    key = city.lower()
    _weather_cache[key] = text
    _weather_cache_time[key] = datetime.now(tz=timezone.utc)
    logger.info(f"🗃  Кэш погоды обновлён для «{city}»: {text}")


def _weather_icon(cond_id: int) -> str:
    if cond_id < 300:
        return "⛈"
    if cond_id < 600:
        return "🌧"
    if cond_id < 700:
        return "❄"
    if cond_id < 800:
        return "🌫"
    if cond_id == 800:
        return "☀"
    return "⛅"


def _pressure_mmhg(hpa: float) -> int:
    """Перевод гПа → мм рт.ст."""
    return round(hpa * 0.750064)


async def get_weather(city: str, api_key: str) -> str:
    """Запрос текущей погоды через /weather endpoint."""
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": api_key, "units": "metric", "lang": "ru"}
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx) as resp:
                if resp.status == 404:
                    return f"❌ Город «{city}» не найден"
                if resp.status != 200:
                    return f"❌ Ошибка сервиса погоды (код {resp.status})"
                data = await resp.json()
    except asyncio.TimeoutError:
        return "❌ Сервис погоды не отвечает"
    except Exception as e:
        return f"❌ Ошибка запроса: {e}"

    icon = _weather_icon(data["weather"][0]["id"])
    desc = data["weather"][0]["description"]
    temp = round(data["main"]["temp"])
    feels = round(data["main"]["feels_like"])
    humidity = data["main"]["humidity"]
    wind = round(data["wind"]["speed"])
    pressure = _pressure_mmhg(data["main"].get("pressure", 0))
    city_name = data["name"]

    feels_str = f"/{feels}°" if feels != temp else ""
    # Пример: ☀ Bratsk: ясно +5°/+3° 💧60% 💨4м/с 755мм
    result = f"{icon} {city_name}: {desc} {temp}°{feels_str} 💧{humidity}% 💨{wind}м/с {pressure}мм"
    # Обрезать если не укладывается в 143 байта
    if len(result.encode("utf-8")) > 143:
        result = f"{icon} {city_name}: {desc} {temp}°{feels_str} 💧{humidity}% 💨{wind}м/с"
    return result


async def get_daily_forecast(city: str, api_key: str) -> str:
    """Прогноз на ближайшие 24ч через /forecast endpoint."""
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"q": city, "appid": api_key, "units": "metric", "lang": "ru", "cnt": 8}
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx) as resp:
                if resp.status == 404:
                    return f"Город {city} не найден"
                if resp.status != 200:
                    return f"Ошибка сервиса погоды (код {resp.status})"
                data = await resp.json()
    except asyncio.TimeoutError:
        return "Сервис погоды не отвечает"
    except Exception as e:
        return f"Ошибка запроса: {e}"

    items = data.get("list", [])
    if not items:
        return "Нет данных прогноза"

    city_name = data["city"]["name"]
    temps = [i["main"]["temp"] for i in items]
    feels = [i["main"]["feels_like"] for i in items]
    winds = [i["wind"]["speed"] for i in items]
    pressures = [i["main"].get("pressure", 0) for i in items]
    humidities = [i["main"]["humidity"] for i in items]
    descs = [i["weather"][0]["description"] for i in items]
    desc = max(set(descs), key=descs.count)
    icon = _weather_icon(items[len(items) // 2]["weather"][0]["id"])

    t_min, t_max = round(min(temps)), round(max(temps))
    f_min, f_max = round(min(feels)), round(max(feels))
    wind_max = round(max(winds))
    pressure_avg = _pressure_mmhg(sum(pressures) / len(pressures))
    humidity_avg = round(sum(humidities) / len(humidities))

    feels_str = f"/{f_min}..{f_max}°" if (f_min != t_min or f_max != t_max) else ""

    # Пример: ⛅ Bratsk: облачно +3..+8°/+1..+6° 💧72% 💨5м/с 748мм
    result = (
        f"{icon} {city_name}: {desc} {t_min}..{t_max}°{feels_str} "
        f"💧{humidity_avg}% 💨{wind_max}м/с {pressure_avg}мм"
    )
    if len(result.encode("utf-8")) > 143:
        result = (
            f"{icon} {city_name}: {desc} {t_min}..{t_max}°{feels_str} "
            f"💧{humidity_avg}% 💨{wind_max}м/с"
        )
    if len(result.encode("utf-8")) > 143:
        result = f"{icon} {city_name}: {desc} {t_min}..{t_max}° 💨{wind_max}м/с"
    return result


def _secs_to_next_hour_boundary(interval_h: int) -> float:
    """Секунд до ближайшего начала часа, кратного interval_h (по UTC).

    Например interval_h=2: ближайшее из 00:00, 02:00, 04:00 ... 22:00 UTC.
    interval_h=1: ближайшее HH:00:00 UTC.
    """
    now_utc = datetime.now(tz=timezone.utc)
    current_hour = now_utc.hour
    next_boundary_hour = ((current_hour // interval_h) + 1) * interval_h
    days_offset = next_boundary_hour // 24
    next_boundary_hour = next_boundary_hour % 24
    target = now_utc.replace(
        hour=next_boundary_hour, minute=0, second=0, microsecond=0
    ) + timedelta(days=days_offset)
    return (target - now_utc).total_seconds()


def _parse_broadcast_period(period: str) -> tuple[str, int]:
    """Разобрать WEATHER_BROADCAST_PERIOD.

    Возвращает (mode, value):
      mode='off'    — отключено
      mode='daily'  — раз в день, value не используется
      mode='hourly' — каждые value часов (1..12)
    """
    p = period.strip().lower()
    if p in ("0", "off", ""):
        return ("off", 0)
    if p == "1d":
        return ("daily", 0)
    if p.endswith("h"):
        try:
            hours = int(p[:-1])
            if 1 <= hours <= 12:
                return ("hourly", hours)
        except ValueError:
            pass
    logger.warning(f"⚠️  Неизвестный WEATHER_BROADCAST_PERIOD='{period}', используется 1d")
    return ("daily", 0)


async def _refresh_cache(city: str, api_key: str) -> None:
    """Запросить свежую погоду и положить в кэш."""
    try:
        text = await get_weather(city, api_key)
        if not text.startswith("❌"):
            _set_cached_weather(city, text)
        else:
            logger.warning(f"⚠️  Не удалось обновить кэш: {text}")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления кэша погоды: {e}")


async def _send_cached_weather(mc, city: str, channel_idx: int) -> None:
    """Отправить погоду из кэша в канал. Кэш должен быть уже обновлён."""
    cached = get_cached_weather(city)
    if not cached:
        logger.warning(f"⚠️  Кэш погоды для «{city}» пуст, отправка отменена")
        return
    forecast = to_lat(cached)
    cache_time = _weather_cache_time.get(city.lower())
    age = (datetime.now(tz=timezone.utc) - cache_time).total_seconds() if cache_time else 0
    logger.info(f"📤 Погода (кэш {age/60:.0f} мин) → канал {channel_idx}: {forecast}")
    await mc.commands.send_chan_msg(channel_idx, forecast)


async def weather_cache_updater(config: dict) -> None:
    """Фоновая задача: обновляет кэш погоды в начале каждого часа (HH:00:00 UTC).

    Привязка к началу часа гарантирует, что кэш обновится до того,
    как weather_broadcast_scheduler (hourly-режим) отправит погоду в канал.
    """
    wb = config.get("weather_broadcast", {})
    api_key = config.get("openweathermap_api_key", "")
    city = wb.get("city", "Omsk")

    if not api_key:
        logger.warning("⚠️  weather_cache_updater: OPENWEATHERMAP_API_KEY не задан")
        return

    # Сразу делаем первый запрос при старте бота
    await _refresh_cache(city, api_key)

    while True:
        wait_sec = _secs_to_next_hour_boundary(1)
        logger.info(f"🗃  Следующее обновление кэша погоды через {wait_sec/60:.1f} мин")
        await asyncio.sleep(wait_sec)
        await _refresh_cache(city, api_key)


async def weather_broadcast_scheduler(mc, config: dict) -> None:
    """Фоновая задача рассылки погоды согласно WEATHER_BROADCAST_PERIOD.

    Источник погоды выбирается через WEATHER_SOURCE:
      owm    — OpenWeatherMap (по умолчанию)
      yandex — Яндекс Погода

    Тихий час: с 23:00 до 06:59 местного времени отправка не производится.
    """
    wb = config.get("weather_broadcast")
    if not wb:
        return

    period_str = wb.get("broadcast_period", "1d")
    mode, interval_h = _parse_broadcast_period(period_str)

    if mode == "off":
        logger.info("📭 Рассылка погоды отключена (WEATHER_BROADCAST_PERIOD=0)")
        return

    source = wb.get("weather_source", "owm").lower().strip()
    api_key = config.get("openweathermap_api_key", "")
    city = wb.get("city", "Omsk")
    channel_idx = wb.get("channel_idx", 2)
    tz = timezone(timedelta(hours=wb.get("timezone_offset_hours", 6)))

    logger.info(f"📡 Источник периодической погоды: {source.upper()}")

    def _is_quiet_time(dt: datetime) -> bool:
        h = dt.hour
        return h >= 23 or h < 7

    async def _send_weather() -> None:
        """Отправить погоду из нужного источника."""
        if source == "yandex":
            from .yandex_weather import get_cached_ya_forecast, _fetch_yandex, _build_forecast, _build_current, _set_cache
            ya = config.get("yandex_weather", {})
            ya_key = ya.get("api_key", "")
            lat = ya.get("lat", 0.0)
            lon = ya.get("lon", 0.0)
            if not ya_key:
                logger.warning("⚠️  weather_broadcast: YANDEX_WEATHER_API_KEY не задан")
                return
            # Берём из кэша или обновляем
            cached = get_cached_ya_forecast()
            if cached:
                forecast = cached
                logger.info(f"📤 Яндекс погода (из кэша) → канал {channel_idx}: {forecast}")
            else:
                data = await _fetch_yandex(ya_key, lat, lon)
                _set_cache(_build_current(data), _build_forecast(data))
                forecast = _build_forecast(data)
                logger.info(f"📤 Яндекс погода (свежий запрос) → канал {channel_idx}: {forecast}")
            await mc.commands.send_chan_msg(channel_idx, forecast)
        else:
            # OWM — стандартный путь
            await _send_cached_weather(mc, city, channel_idx)

    if mode == "daily":
        target_hour = wb.get("hour", 7)
        target_minute = wb.get("minute", 30)
        while True:
            now = datetime.now(tz=tz)
            target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_sec = (target - now).total_seconds()
            logger.info(
                f"⏰ Рассылка погоды (1d/{source}): следующая через {wait_sec/3600:.1f} ч "
                f"({target.strftime('%Y-%m-%d %H:%M')} местного)"
            )
            await asyncio.sleep(wait_sec)
            try:
                now = datetime.now(tz=tz)
                if _is_quiet_time(now):
                    logger.info(f"🌙 Тихий час ({now.strftime('%H:%M')}), рассылка пропущена")
                    continue
                # Для OWM — обновляем кэш перед отправкой
                if source == "owm" and api_key:
                    await _refresh_cache(city, api_key)
                await _send_weather()
            except Exception as e:
                logger.error(f"❌ Ошибка рассылки погоды: {e}")

    else:  # hourly
        while True:
            wait_sec = _secs_to_next_hour_boundary(interval_h)
            next_local = datetime.now(tz=tz) + timedelta(seconds=wait_sec)
            logger.info(
                f"⏰ Рассылка погоды ({interval_h}h/{source}): следующая через {wait_sec/60:.1f} мин "
                f"({next_local.strftime('%H:%M')} местного)"
            )
            await asyncio.sleep(wait_sec)
            try:
                now = datetime.now(tz=tz)
                if _is_quiet_time(now):
                    logger.info(f"🌙 Тихий час ({now.strftime('%H:%M')}), рассылка пропущена")
                    continue
                await _send_weather()
            except Exception as e:
                logger.error(f"❌ Ошибка рассылки погоды: {e}")
