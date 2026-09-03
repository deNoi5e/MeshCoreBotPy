import asyncio
import re
import ssl
import logging
from datetime import datetime, timedelta

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_URL = "https://ngs55.ru/maps-traffic/"
_STATE_FILE = "traffic_last_score.txt"

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


async def _fetch_traffic_score() -> tuple[int, str] | str:
    """Возвращает (score, report) при успехе или строку с текстом ошибки."""
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

    return score, f"{icon} Омск: {score} {word} — {desc}"


async def get_traffic_omsk() -> str:
    result = await _fetch_traffic_score()
    if isinstance(result, str):
        return result
    _score, report = result
    return report


def _in_broadcast_window(now: datetime, hour_from: int, hour_to: int) -> bool:
    return hour_from <= now.hour < hour_to


def _load_last_score() -> int | None:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            score = int(f.read().strip())
        logger.info(f"💾 Загружено последнее известное значение пробок из {_STATE_FILE}: {score}")
        return score
    except FileNotFoundError:
        logger.info(f"💾 Файл {_STATE_FILE} не найден, последнее значение пробок неизвестно")
        return None
    except Exception as e:
        logger.warning(f"💾 Не удалось прочитать {_STATE_FILE}: {e}")
        return None


def _save_last_score(score: int) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(str(score))
        logger.info(f"💾 Сохранено последнее значение пробок в {_STATE_FILE}: {score}")
    except Exception as e:
        logger.warning(f"💾 Не удалось сохранить {_STATE_FILE}: {e}")


async def _check_traffic_change(mc, channel_idx: int, hour_from: int, hour_to: int, last_score: int | None) -> int | None:
    """Опрашивает балл пробок и при изменении шлёт отчёт в канал (только внутри окна часов).
    Возвращает актуальный последний известный балл (или last_score без изменений при ошибке)."""
    result = await _fetch_traffic_score()
    if isinstance(result, str):
        logger.error(f"📭 Пробки не проверены: {result}")
        return last_score

    score, report = result
    if score == last_score:
        logger.info(f"📭 Пробки не изменились: {score}")
        return last_score

    _save_last_score(score)

    if not _in_broadcast_window(datetime.now(), hour_from, hour_to):
        logger.info(f"📭 Пробки изменились ({last_score} → {score}), но не время ({hour_from}:00–{hour_to}:00) — рассылка в канал {channel_idx} пропущена")
        return score

    try:
        await mc.commands.send_chan_msg(channel_idx, report)
        logger.info(f"📤 Пробки изменились ({last_score} → {score}), отправлено в канал {channel_idx}: {report}")
    except Exception as e:
        logger.error(f"📭 Рассылка пробок в канал {channel_idx} не отправлена: ошибка {e}")
    return score


async def traffic_broadcast_scheduler(mc, config: dict) -> None:
    tb = config.get("traffic_broadcast")
    if not tb:
        return
    channel_idx = tb.get("channel_idx", 3)
    interval_minutes = tb.get("interval_minutes", 60)
    if interval_minutes == 0:
        logger.info("⏰ Проверка пробок отключена (TRAFFIC_INTERVAL_MINUTES=0)")
        return
    interval_minutes = max(5, interval_minutes)
    hour_from = tb.get("hour_from", 7)
    hour_to = tb.get("hour_to", 19)

    last_score = _load_last_score()

    await asyncio.sleep(5.0)
    last_score = await _check_traffic_change(mc, channel_idx, hour_from, hour_to, last_score)

    while True:
        next_run = datetime.now() + timedelta(minutes=interval_minutes)
        logger.info(f"⏰ Следующая проверка пробок через {interval_minutes} мин ({next_run.strftime('%Y-%m-%d %H:%M')} по местному)")
        await asyncio.sleep(interval_minutes * 60)
        last_score = await _check_traffic_change(mc, channel_idx, hour_from, hour_to, last_score)
