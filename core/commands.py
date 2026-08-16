from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from .weather import get_daily_forecast, get_weather, get_cached_weather, to_lat
from .narodmon import format_myweather, parse_narodmon_sensors
from .yandex_weather import get_yandex_weather, get_yandex_forecast, get_yandex_forecast

logger = logging.getLogger(__name__)


@dataclass
class Context:
    text: str
    args: str           # текст после имени команды
    hops: int
    route_data: dict | None
    weather_api_key: str
    config: dict
    mc: Any


async def _ping(ctx: Context) -> str | None:
    if ctx.hops == 0:
        route_info = "Direct 📡"
    elif ctx.route_data:
        path = ctx.route_data.get('path', '')
        path_len = ctx.route_data.get('path_len', ctx.hops)
        addrs = [path[i:i+2] for i in range(0, len(path), 2)]
        route_info = f"{path_len} хопов: {' → '.join(addrs)}"
        logger.info(f"   🔍 /ping: route={ctx.route_data}, info={route_info}")
    else:
        route_info = f"{ctx.hops} хопов"
        logger.info(f"   🔍 /ping: hops={ctx.hops}, маршрут не найден в кэше")
    return f"🏓 pong ({route_info})"


async def _help(ctx: Context) -> str | None:
    return "🏓 /ping -проверка\n🌤 /weather -погода\n🌡 /myweather -что за окном\n🌥 /yapogoda -Япогода\n📅 /yaprognoz -Япрогноз"


async def _weather(ctx: Context) -> str | None:
    if not ctx.weather_api_key:
        return "❌ API ключ погоды не настроен"

    wb = ctx.config.get("weather_broadcast", {})
    default_city = wb.get("city", "Omsk")

    city = ctx.args.strip() if ctx.args.strip() else default_city
    is_default = city.lower() == default_city.lower()

    if is_default:
        cached = get_cached_weather(city)
        if cached:
            logger.info(f"   🌤 Погода для «{city}» из кэша")
            return cached
        logger.info(f"   🌤 Кэш пуст для «{city}», делаю запрос к API")

    result = await get_weather(city, ctx.weather_api_key)
    logger.info(f"   🌤 Погода для «{city}» получена")
    return result


async def _myweather(ctx: Context) -> str | None:
    narodmon_cfg = ctx.config.get("narodmon", {})
    api_key = narodmon_cfg.get("api_key", "")
    sensors_raw = narodmon_cfg.get("sensors_raw", "")
    if not api_key:
        return "❌ NARODMON_API_KEY не настроен"
    if not sensors_raw:
        return "❌ NARODMON_SENSORS не настроен"
    sensors = parse_narodmon_sensors(sensors_raw)
    result = await format_myweather(sensors, api_key)
    logger.info(f"   🌡 /myweather: {result}")
    return result


async def _yapogoda(ctx: Context) -> str | None:
    ya_cfg = ctx.config.get("yandex_weather", {})
    api_key = ya_cfg.get("api_key", "")
    lat = ya_cfg.get("lat", 0.0)
    lon = ya_cfg.get("lon", 0.0)
    if not api_key:
        return "❌ YANDEX_WEATHER_API_KEY не настроен"
    if not lat or not lon:
        return "❌ YANDEX_WEATHER_LAT/LON не настроены"
    result = await get_yandex_weather(api_key, lat, lon)
    logger.info(f"   🌥 /yapogoda: {result}")
    return result


async def _yaprognoz(ctx: Context) -> str | None:
    ya_cfg = ctx.config.get("yandex_weather", {})
    api_key = ya_cfg.get("api_key", "")
    lat = ya_cfg.get("lat", 0.0)
    lon = ya_cfg.get("lon", 0.0)
    if not api_key:
        return "❌ YANDEX_WEATHER_API_KEY не настроен"
    if not lat or not lon:
        return "❌ YANDEX_WEATHER_LAT/LON не настроены"
    result = await get_yandex_forecast(api_key, lat, lon)
    logger.info(f"   🌥 /yaprognoz: {result}")
    return result


async def _yaprognoz(ctx: Context) -> str | None:
    ya_cfg = ctx.config.get("yandex_weather", {})
    api_key = ya_cfg.get("api_key", "")
    lat = ya_cfg.get("lat", 0.0)
    lon = ya_cfg.get("lon", 0.0)
    if not api_key:
        return "❌ YANDEX_WEATHER_API_KEY не настроен"
    if not lat or not lon:
        return "❌ YANDEX_WEATHER_LAT/LON не настроены"
    result = await get_yandex_forecast(api_key, lat, lon)
    logger.info(f"   📅 /yaprognoz: {result}")
    return result


async def _weathernow(ctx: Context) -> str | None:
    if not ctx.weather_api_key:
        return "❌ API ключ погоды не настроен"
    wb = ctx.config.get("weather_broadcast", {})
    city = wb.get("city", "Omsk")
    channel_idx = wb.get("channel_idx", 2)
    try:
        forecast = to_lat(await get_daily_forecast(city, ctx.weather_api_key))
        await ctx.mc.commands.send_chan_msg(channel_idx, forecast)
        logger.info(f"   📤 /weathernow: отправлено в канал {channel_idx}: {forecast}")
    except Exception as e:
        return f"❌ Ошибка: {e}"
    return None


COMMANDS: dict[str, Callable[..., Coroutine]] = {
    "/ping": _ping,
    "/help": _help,
    "/weather": _weather,
    "/myweather": _myweather,
    "/yapogoda": _yapogoda,
    "/yaprognoz": _yaprognoz,
    "/weathernow": _weathernow,
}


async def dispatch(text: str, *, hops: int, route_data: dict | None,
                   weather_api_key: str, config: dict, mc: Any) -> str | None:
    if not text.startswith('/'):
        return None
    parts = text.split(None, 1)
    cmd, args = parts[0], (parts[1] if len(parts) > 1 else "")
    handler = COMMANDS.get(cmd)
    if handler is None:
        return None
    ctx = Context(
        text=text, args=args, hops=hops, route_data=route_data,
        weather_api_key=weather_api_key, config=config, mc=mc,
    )
    return await handler(ctx)
