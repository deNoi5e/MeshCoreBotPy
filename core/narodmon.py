import asyncio
import logging
import os
import ssl
import uuid as uuid_module

import aiohttp
import certifi

logger = logging.getLogger(__name__)

# Правильный endpoint согласно документации народмон
_API_URL = "https://api.narodmon.ru"

# Название приложения для заголовка User-Agent
_APP_NAME = "MeshCoreWeatherBTK"

# Файл для хранения постоянного UUID клиента
_UUID_FILE = os.path.join(os.path.dirname(__file__), ".narodmon_uuid")


def _get_client_uuid() -> str:
    """Вернуть постоянный UUID клиента (генерируется один раз и сохраняется)."""
    if os.path.exists(_UUID_FILE):
        try:
            with open(_UUID_FILE, "r") as f:
                uid = f.read().strip()
                if uid:
                    return uid
        except Exception:
            pass
    uid = str(uuid_module.uuid4())
    try:
        with open(_UUID_FILE, "w") as f:
            f.write(uid)
        logger.info(f"🆔 Создан UUID клиента народмон: {uid}")
    except Exception as e:
        logger.warning(f"⚠️  Не удалось сохранить UUID народмон: {e}")
    return uid


def _sensor_icon(type_id: int, unit: str, name: str) -> str:
    """Иконка по типу датчика (type из API) или единице измерения."""
    # Типы из API народмон: 1=температура, 2=влажность, 3=давление, 4=освещённость,
    # 5=яркость, 6=UV, 7=радиация, 8=осадки, 9=пыль, 10=скорость ветра, 11=направление
    icons = {
        1: "🌡",   # температура
        2: "💧",   # влажность
        3: "📊",   # давление
        4: "☀",    # освещённость
        5: "☀",    # яркость
        6: "🌞",   # UV
        7: "☢",    # радиация
        8: "🌧",   # осадки
        9: "🏭",   # пыль
        10: "💨",  # скорость ветра
        11: "🧭",  # направление ветра
    }
    if type_id in icons:
        return icons[type_id]
    # Fallback по единице/названию
    t = (unit + " " + name).lower()
    if "°" in t or "temp" in t:
        return "🌡"
    if "%" in t:
        return "💧"
    if "mmhg" in t or "hpa" in t or "мм" in t:
        return "📊"
    if "м/с" in t or "wind" in t:
        return "💨"
    return "📡"


def _format_value(value, unit: str) -> str:
    """Форматировать значение с единицей."""
    if value is None:
        return "—"
    # Народмон возвращает температуру с единицей "°" — добавляем C
    if unit == "°":
        unit = "°C"
    try:
        v = float(value)
        if "°" in unit or unit.lower() in ("c", "f", "к"):
            return f"{v:+.1f}{unit}" if v != int(v) else f"{v:+.0f}{unit}"
        if v == int(v):
            return f"{int(v)}{unit}"
        return f"{v:.1f}{unit}"
    except (TypeError, ValueError):
        return f"{value}{unit}"


def parse_narodmon_sensors(env_value: str) -> list[tuple[str, str]]:
    """Разобрать строку вида: D1291,"БЛПК",D5694,"22мкрн"

    Возвращает список [(device_id, label), ...]
    """
    sensors = []
    parts = [p.strip() for p in env_value.split(",")]
    i = 0
    while i < len(parts):
        device_id = parts[i].strip().strip('"')
        label = ""
        if i + 1 < len(parts):
            next_part = parts[i + 1].strip()
            # Метка: в кавычках или не начинается с D+цифры
            stripped = next_part.strip('"')
            if next_part.startswith('"') or not (stripped.upper().startswith("D") and stripped[1:].isdigit()):
                label = stripped
                i += 2
            else:
                i += 1
        else:
            i += 1
        if device_id:
            sensors.append((device_id, label))
    return sensors


async def _get_device_data(device_id: str, api_key: str) -> dict | None:
    """Получить данные устройства через sensorsOnDevice.

    Возвращает dict с полями id, name, sensors:[{id,type,name,value,unit,time}]
    или None при ошибке.
    """
    numeric = device_id.lstrip("Dd")
    try:
        dev_id = int(numeric)
    except ValueError:
        logger.warning(f"⚠️  Некорректный ID устройства народмон: {device_id}")
        return None

    payload = {
        "cmd": "sensorsOnDevice",
        "uuid": _get_client_uuid(),
        "id": dev_id,
    }
    headers = {
        "Content-Type": "application/json",
        "Narodmon-Api-Key": api_key,
        "User-Agent": _APP_NAME,
    }
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ssl_ctx,
            ) as resp:
                if resp.status == 429:
                    raise RuntimeError("Превышен лимит запросов (HTTP 429)")
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        raise RuntimeError("Сервис народмон не отвечает")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Ошибка запроса: {e}")

    if "error" in data:
        raise RuntimeError(f"API ошибка {data.get('errno', '')}: {data['error']}")

    return data


def _best_sensor(sensors: list[dict]) -> dict | None:
    """Выбрать наиболее интересный датчик из устройства.

    Приоритет: температура (type=1) > влажность (type=2) > давление (type=3) > первый.
    """
    if not sensors:
        return None
    for priority_type in (1, 2, 3):
        for s in sensors:
            if s.get("type") == priority_type and s.get("pub", 1):
                return s
    # Вернуть первый публичный
    for s in sensors:
        if s.get("pub", 1):
            return s
    return sensors[0]


async def format_myweather(sensors_config: list[tuple[str, str]], api_key: str) -> str:
    """Получить данные с народмон и сформировать строку для /myweather.

    Для каждого устройства выводит: температура, влажность, давление, ветер.
    После данных первого датчика — перенос строки.

    Пример:
      🌡БЛПК:+15°C 💧82% 📊723мм 💨2м/с
      🌡22мкрн:+16°C 💧72% 📊723мм
    """
    if not sensors_config:
        return "❌ Датчики народмон не настроены"
    if not api_key:
        return "❌ NARODMON_API_KEY не настроен"

    # Запрашиваем все устройства параллельно
    tasks = [_get_device_data(sid, api_key) for sid, _ in sensors_config]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    parts = []
    for (sid, label), result in zip(sensors_config, results):
        tag = label if label else sid
        if isinstance(result, Exception):
            logger.error(f"❌ Народмон {sid}: {result}")
            parts.append(f"{tag}:—")
            continue
        if result is None:
            parts.append(f"{tag}:—")
            continue

        dev_sensors = result.get("sensors", [])
        if not dev_sensors:
            parts.append(f"{tag}:—")
            continue

        def get_s(type_id):
            return next((s for s in dev_sensors if s.get("type") == type_id and s.get("pub", 1)), None)

        def get_wind():
            """Ищем скорость ветра по type=10 или по unit/name."""
            # Сначала по стандартному type
            s = get_s(10)
            if s:
                return s
            # Fallback: ищем по единице или названию
            for s in dev_sensors:
                if not s.get("pub", 1):
                    continue
                unit = s.get("unit", "").lower()
                name = s.get("name", "").lower()
                if unit in ("m/s", "м/с", "km/h", "км/ч") or "скорость" in name or "ветер" in name or "wind" in name:
                    return s
            return None

        temp_s  = get_s(1)   # температура
        hum_s   = get_s(2)   # влажность
        press_s = get_s(3)   # давление
        wind_s  = get_wind() # скорость ветра

        if temp_s:
            icon = _sensor_icon(1, temp_s.get("unit", ""), temp_s.get("name", ""))
            val = _format_value(temp_s.get("value"), temp_s.get("unit", "°C"))
            entry = f"{tag}:\n{icon}{val}"
        else:
            best = _best_sensor(dev_sensors)
            if not best:
                parts.append(f"{tag}:—")
                continue
            icon = _sensor_icon(best.get("type", 0), best.get("unit", ""), best.get("name", ""))
            val = _format_value(best.get("value"), best.get("unit", ""))
            entry = f"{tag}:\n{icon}{val}"

        if hum_s:
            entry += f" 💧{_format_value(hum_s.get('value'), hum_s.get('unit', '%'))}"
        if press_s:
            entry += f" 📊{_format_value(press_s.get('value'), press_s.get('unit', 'mmHg'))}"
        if wind_s:
            entry += f" 💨{_format_value(wind_s.get('value'), wind_s.get('unit', 'm/s'))}"

        parts.append(entry)

    # Каждый датчик на новой строке
    result = "\n".join(parts)

    # Обрезаем по байтам (143 байта — лимит MeshCore)
    encoded = result.encode("utf-8")
    if len(encoded) > 143:
        result = encoded[:143].decode("utf-8", errors="ignore")
    return result
