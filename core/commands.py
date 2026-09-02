from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from .currency import get_rates
from .doomsday import get_doomsday
from .mercury import get_mercury
from .moon import OMSK_LAT, OMSK_LON, get_moon
from .traffic import get_traffic_omsk
from .versions import get_latest_versions
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
    sender_key: str = ""
    sender_name: str = ""


async def _ping(ctx: Context) -> str | None:
    if ctx.hops == 0:
        route_info = "Direct 📡"
    elif ctx.route_data:
        path = ctx.route_data.get('path', '')
        path_len = ctx.route_data.get('path_len', ctx.hops)

        step = len(path) // path_len;

        addrs = [path[i:i+step] for i in range(0, len(path), step)]
        route_info = f"{path_len} хопов: {' → '.join(addrs)}"
        logger.info(f"   🔍 /ping: route={ctx.route_data}, info={route_info}")
    else:
        route_info = f"{ctx.hops} хопов"
        logger.info(f"   🔍 /ping: hops={ctx.hops}, маршрут не найден в кэше")
    return f"🏓 pong ({route_info})"


def _resolve_node_name(mc: Any, prefix: str) -> str:
    contact = mc.get_contact_by_key_prefix(prefix)
    logger.info(f"_resolve_node_name: prefix={prefix}, contact={contact}")
    if contact:
        name = contact.get("adv_name")
        if name:
            return name
    return prefix


async def _pingn(ctx: Context) -> str | None:
    if ctx.hops == 0:
        route_info = "Direct 📡"
    elif ctx.route_data:
        path = ctx.route_data.get('path', '')
        path_len = ctx.route_data.get('path_len', ctx.hops)

        step = len(path) // path_len

        addrs = [_resolve_node_name(ctx.mc, path[i:i+step]) for i in range(0, len(path), step)]
        route_info = f"{path_len} хопов: {' → '.join(addrs)}"
        logger.info(f"   🔍 /pingn: route={ctx.route_data}, info={route_info}")
    else:
        route_info = f"{ctx.hops} хопов"
        logger.info(f"   🔍 /pingn: hops={ctx.hops}, маршрут не найден в кэше")
    return f"🏓 pong ({route_info})"


# Справка по командам: иконка + имя для общего "/help" и подробное описание
# (укладывается в 130 байт - лимит одного LoRa-сообщения) для "/help <команда>".
# Ключ - основное имя команды; алиасы перечислены в описании отдельно.
# Часть команд (см. _help.skip) не выводится в кратком списке, но остаётся
# доступной через "/help <команда>" - служебные (/test*) и те, что не влезли
# бы в лимит краткого списка (/moon, /mercury, /doomsday, /weathernow).
HELP_INFO: dict[str, dict[str, str]] = {
    "/ping": {
        "icon": "🏓",
        "long": "🏓 /ping - проверка связи, маршрут (Direct или хопы).",
    },
    "/pingn": {
        "icon": "🏓",
        "long": "🏓 /pingn - как /ping, но хопы с именами узлов.",
    },
    "/weather": {
        "icon": "🌤",
        "long": "🌤 /weather <город> - текущая погода (OpenWeatherMap).",
    },
    "/weather2": {
        "icon": "🌤",
        "long": "🌤 /weather2 <город> - погода (Open-Meteo, без города - Омск).",
    },
    "/traffic": {
        "icon": "🚗",
        "long": "🚗 /traffic - балл пробок в Омске (ngs55.ru).",
    },
    "/rate": {
        "icon": "💱",
        "long": "💱 /rate (/kurs) - курсы USD/EUR/CNY к рублю по ЦБ РФ.",
    },
    "/ver": {
        "icon": "🆕",
        "long": "🆕 /ver (/version) - свежие версии прошивки MeshCore и приложения.",
    },
    "/moon": {
        "icon": "🌔",
        "long": "🌔 /moon (/luna) - фаза Луны, восход/заход, ближайшие пол./новолуние.",
    },
    "/mercury": {
        "icon": "☿",
        "long": "☿ /mercury (/merc, /retro) - идёт ли ретро Меркурия и когда ближайшая.",
    },
    "/help": {
        "icon": "❓",
        "long": "❓ /help [команда] - список команд или справка по одной.",
    },
    "/doomsday": {
        "icon": "🕛",
        "long": "🕛 /doomsday - Часы Судного дня: сколько до полуночи (thebulletin.org).",
    },
    "/weathernow": {
        "icon": "🌤",
        "long": "🌤 /weathernow - шлёт дневной прогноз погоды в канал погоды.",
    },
    "/test": {
        "icon": "🔧",
        "long": "🔧 /test - служебная: проверка разбиения длинного ответа на части.",
    },
    "/test2": {
        "icon": "🔧",
        "long": "🔧 /test2 - служебная: резолв имени узла по ключу отправителя (личка).",
    },
    "/test3": {
        "icon": "🔧",
        "long": "🔧 /test3 - служебная: резолв имени узла по имени отправителя (канал).",
    },
}

# Алиасы команд -> основное имя, по которому ищется описание в HELP_INFO.
_HELP_ALIASES: dict[str, str] = {
    "/kurs": "/rate",
    "/version": "/ver",
    "/versions": "/ver",
    "/luna": "/moon",
    "/merc": "/mercury",
    "/retro": "/mercury",
}


# Команды, не показываемые в кратком "/help" (не влезают в лимит одного
# LoRa-сообщения или служебные), но доступные через "/help <команда>"
# и полностью - через "/helpex".
_HELP_SKIP_SHORT = {"/moon", "/mercury", "/help", "/doomsday", "/weathernow", "/test", "/test2", "/test3"}


async def _help(ctx: Context) -> str | None:
    query = ctx.args.strip()
    if not query:
        items = " ".join(f"{info['icon']}{name}" for name, info in HELP_INFO.items()
                          if name not in _HELP_SKIP_SHORT)
        return items + "\n/help COMMAND"

    name = query.split()[0].lower()
    if not name.startswith("/"):
        name = "/" + name
    name = _HELP_ALIASES.get(name, name)

    info = HELP_INFO.get(name)
    if info is None:
        return f"❓ Команда «{query}» не найдена. /help - список всех команд."
    return info["long"]


async def _helpex(ctx: Context) -> str | None:
    return "\n".join(info["long"] for info in HELP_INFO.values())


async def _traffic(ctx: Context) -> str | None:
    return await get_traffic_omsk()


async def _rate(ctx: Context) -> str | None:
    return await get_rates()


async def _ver(ctx: Context) -> str | None:
    return await get_latest_versions()


async def _moon(ctx: Context) -> str | None:
    moon = ctx.config.get("moon", {})
    return await get_moon(
        lat=moon.get("lat", OMSK_LAT),
        lon=moon.get("lon", OMSK_LON),
        tz_offset=moon.get("timezone_offset_hours", 6.0),
    )


async def _mercury(ctx: Context) -> str | None:
    mercury = ctx.config.get("mercury", {})
    return await get_mercury(
        tz_offset=mercury.get("timezone_offset_hours", 6.0),
    )


async def _doomsday(ctx: Context) -> str | None:
    return await get_doomsday()


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

async def _test3(ctx: Context) -> str | None:
    # В канале ключа отправителя нет в протоколе — резолвим по имени
    # (adv_name), которое отправитель сам вписал в текст "Имя: сообщение".
    # Это эвристика по нику, а не крипто-идентификация: при совпадении
    # имён у разных контактов результат может быть неверным.
    sender_name = ctx.sender_name
    if not sender_name:
        return "❌ Имя отправителя не найдено (команда рассчитана на канал)"
    contact = ctx.mc.get_contact_by_name(sender_name)
    if not contact:
        return f"❌ Контакт «{sender_name}» не найден"
    key = contact.get("public_key", "")
    key1, key2 = key[:2], key[:4]
    return f"{sender_name} → {key1} → {_resolve_node_name(ctx.mc, key1)}\n{key2} → {_resolve_node_name(ctx.mc, key2)}"


async def _test2(ctx: Context) -> str | None:
    key = ctx.sender_key
    if not key:
        return f"Key unknown, try in private\ntest resolve: b93a → {_resolve_node_name(ctx.mc, "b93a")}\nb9 → {_resolve_node_name(ctx.mc, "b9")}"
    key1 = key[:2]
    key2 = key[:4]
    key3 = key[:6]
    return f"{key1} → {_resolve_node_name(ctx.mc, key1)}\n{key2} → {_resolve_node_name(ctx.mc, key2)}\n{key3} → {_resolve_node_name(ctx.mc, key3)}"

COMMANDS: dict[str, Callable[..., Coroutine]] = {
    "/ping": _ping,
    "/pingn": _pingn,
    "/help": _help,
    "/helpex": _helpex,
    "/weather": _weather,
    "/weathernow": _weathernow,
    "/traffic": _traffic,
    "/rate": _rate,
    "/kurs": _rate,
    "/ver": _ver,
    "/version": _ver,
    "/versions": _ver,
    "/moon": _moon,
    "/luna": _moon,
    "/mercury": _mercury,
    "/merc": _mercury,
    "/retro": _mercury,
    "/doomsday": _doomsday,
    "/test": _test,
    "/test2": _test2,
    "/test3": _test3,
    "/weather2": _weather2,
}


async def dispatch(text: str, *, hops: int, route_data: dict | None,
                   weather_api_key: str, config: dict, mc: Any,
                   sender_key: str = "", sender_name: str = "") -> str | None:
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
        sender_key=sender_key, sender_name=sender_name,
    )

    result = await handler(ctx)

    return result
