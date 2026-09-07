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


async def get_weather(city: str, api_key: str) -> str:
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
    country = data["sys"]["country"]
    temp = round(data["main"]["temp"])
    feels = round(data["main"]["feels_like"])
    humidity = data["main"]["humidity"]
    wind = round(data["wind"]["speed"])
    feels_str = f"({feels}°)" if feels != temp else ""
    return f"{icon} {data['name']} ({country}): {desc} {temp}°C{feels_str} 💧{humidity}% 💨{wind}м/с"


async def get_daily_forecast(city: str, api_key: str) -> str:
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
    descs = [i["weather"][0]["description"] for i in items]
    desc = max(set(descs), key=descs.count)
    icon = _weather_icon(items[len(items) // 2]["weather"][0]["id"])

    t_min, t_max = round(min(temps)), round(max(temps))
    f_min, f_max = round(min(feels)), round(max(feels))
    wind_max = round(max(winds))
    feels_str = f", ощущ. {f_min}..{f_max}°" if (f_min != t_min or f_max != t_max) else ""

    tz_offset = data["city"].get("timezone", 0) // 3600
    now = datetime.now(tz=timezone(timedelta(hours=tz_offset)))
    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    months_ru = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    date_str = f"{days_ru[now.weekday()]} {now.day} {months_ru[now.month - 1]}"

    return (
        f"{icon} Погода {city_name}, {date_str}: {desc}, "
        f"{t_min}..{t_max}°C{feels_str}, ветер {wind_max} м/с"
    )


async def weather_broadcast_scheduler(mc, config: dict) -> None:
    wb = config.get("weather_broadcast")
    if not wb:
        return
    api_key = config.get("openweathermap_api_key", "")
    city = wb.get("city", "Omsk")
    channel_idx = wb.get("channel_idx", 2)
    target_hour = wb.get("hour", 7)
    target_minute = wb.get("minute", 30)
    tz = timezone(timedelta(hours=wb.get("timezone_offset_hours", 6)))

    while True:
        now = datetime.now(tz=tz)
        target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        logger.info(f"⏰ Следующая рассылка погоды через {wait_sec/3600:.1f} ч ({target.strftime('%Y-%m-%d %H:%M')} по местному)")
        await asyncio.sleep(wait_sec)

        if not api_key:
            logger.warning("⚠️  weather_broadcast: OPENWEATHERMAP_API_KEY не задан")
            continue
        try:
            forecast = to_lat(await get_daily_forecast(city, api_key))
            await mc.commands.send_chan_msg(channel_idx, forecast)
            logger.info(f"📤 Погода отправлена в канал {channel_idx}: {forecast}")
        except Exception as e:
            logger.error(f"❌ Ошибка рассылки погоды: {e}")
