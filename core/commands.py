from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from .traffic import get_traffic_omsk
from .weather import get_daily_forecast, get_weather, to_lat
from .openmeteo_omsk import get_weather as openmeteo_get_weather

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
    return "📋 Команды:\n\t🏓 /ping - проверка\n\t🌤 /weather <город> - погода\n\t🚗 /traffic - пробки в Омске\n\t❓ /help - справка"


async def _traffic(ctx: Context) -> str | None:
    return await get_traffic_omsk()


async def _weather(ctx: Context) -> str | None:
    if not ctx.args:
        return "❓ Использование: /weather <город>  например: /weather Москва"
    if not ctx.weather_api_key:
        return "❌ API ключ погоды не настроен"
    result = await get_weather(ctx.args, ctx.weather_api_key)
    logger.info(f"   🌤 Погода для «{ctx.args}» получена")
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

async def _weather2(ctx: Context) -> str | None:
    result = await openmeteo_get_weather() if not ctx.args else await openmeteo_get_weather(ctx.args)
    logger.info(f"   🌤 Погода для «{ctx.args}» получена: {result}")
    return str(result)

async def _test(ctx: Context) -> str | None: 
    return "Very long string to test splitting!!! Очень длинная строка чтобы проверить разбиение на несколько сообщений! УРА УРА :) 😁 😁 😁"

COMMANDS: dict[str, Callable[..., Coroutine]] = {
    "/ping": _ping,
    "/help": _help,
    "/weather": _weather,
    "/weathernow": _weathernow,
    "/traffic": _traffic,
    "/test": _test,
    "/weather2": _weather2,
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
    
    logger.info(f"route_data = {route_data}")

    ctx = Context(
        text=text, args=args, hops=hops, route_data=route_data,
        weather_api_key=weather_api_key, config=config, mc=mc,
    )

    result = await handler(ctx)

    return result
