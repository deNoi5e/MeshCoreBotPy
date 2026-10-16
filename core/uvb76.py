"""
УВБ-76 («Жужжалка», 4625 кГц) — последние голосовые сообщения станции.

Официального источника нет. Сообщения публикует Telegram-канал
«УВБ-76 логи» (@uvb76logs): он круглосуточно слушает частоту, на него же
ссылаются СМИ. Сам t.me из России напрямую не открывается (соединение висит
до таймаута), поэтому канал читается через зеркало — страницу канала на
агрегаторе telemetr.me (https://telemetr.me/content/uvb76logs): обычный HTML
без авторизации с текстами последних постов, разбор регулярками, как в
core/traffic.py (и так же хрупок к смене вёрстки). Со стандартным
User-Agent aiohttp сервер рвёт соединение — нужен явный.

Ограничения зеркала: без регистрации видны только 7 последних постов и у
постов нет номеров. Поэтому сообщение опознаётся по времени эфира из текста,
а уже виденные запоминаются списком; если между двумя проверками канал
выложит больше 7 постов, лишние не будут замечены. Насколько зеркало
отстаёт от канала, неизвестно — в лог пишется, через сколько после эфира
сообщение замечено.

Текст поста после снятия тегов:
    🔥 2-е сообщение за сегодня
    14.10.26 15:28 МСК
    НЖТИ 31899 ДРАПОТЫЛ 5029 0844 ВОЛОХЛЕВ 9232 4881
    #НЖТИ #двойное
    💙 УВБ-76 логи 🎥 стрим 💬 чат
До января 2026 время писали как `15.12.25 18:31 по мск`. Позывной в начале
сообщения уже менялся (МДЖБ → … → НЖТИ), поэтому на него разбор не
опирается: сообщение — строки после времени эфира до хэштегов. Посты без
времени эфира (трансляции, сбои передатчика, другие станции) пропускаются.

Канал публикует вручную и с задержкой: медиана 11 мин после эфира, четверть
постов — позже 48 мин, ночные — утром, пропущенное — пачкой (янв–окт 2026).
Поэтому в рассылке время эфира из текста поста, а не время публикации, и
опрос нечастый: днём раз в 15 мин, ночью раз в час — почти все сообщения
выходят в эфир с 7 до 21 МСК.
"""

import asyncio
import html
import logging
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
import certifi

from .msgsplit import split_msg
from .weather import to_lat

logger = logging.getLogger(__name__)

RADIO = "\U0001F4FB"  # 📻
PIN = "\U0001F4CC"  # 📌

_URL = "https://telemetr.me/content/uvb76logs"
# Ссылка для людей — на сам канал, а не на зеркало.
_SOURCE = "t.me/uvb76logs"
MSK = timezone(timedelta(hours=3))

# Текст поста; вложенных <div> внутри нет — только <br> и эмодзи.
_TEXT_RE = re.compile(r'<div class="show-more__content"[^>]*>(.*?)</div>', re.S)
_AIR_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s+(\d{1,2}):(\d{2})\s*(?:по\s+)?мск", re.IGNORECASE)
# Подпись канала в конце каждого поста.
_FOOTER = "УВБ-76 логи"

_CACHE_TTL = 5 * 60
_cache: tuple[float, "UvbMessage"] | None = None


@dataclass(frozen=True)
class UvbMessage:
    aired: datetime  # время эфира, МСК
    text: str        # `НЖТИ 31899 ДРАПОТЫЛ 5029 0844`

    @property
    def key(self) -> str:
        """Опознание сообщения: номеров постов зеркало не даёт, а два
        сообщения в одну минуту станция не передаёт."""
        return f"{self.aired:%Y-%m-%dT%H:%M}"


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _parse_text(text: str) -> UvbMessage | None:
    lines = [line.strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        m = _AIR_RE.search(line)
        if m:
            break
    else:
        return None

    day, month, year, hour, minute = map(int, m.groups())
    try:
        aired = datetime(2000 + year, month, day, hour, minute, tzinfo=MSK)
    except ValueError:
        return None

    words = []
    for line in [line[m.end():].strip(), *lines[i + 1:]]:
        if not line:
            continue
        if line.startswith("#") or _FOOTER in line:
            break
        words.append(line.split(" #", 1)[0])
    body = " ".join(" ".join(words).split())
    if not body:
        return None
    return UvbMessage(aired, body)


def _parse_posts(page: str) -> list[UvbMessage]:
    """Сообщения станции со страницы, по возрастанию времени эфира."""
    messages = {}
    for fragment in _TEXT_RE.findall(page):
        message = _parse_text(_strip_html(fragment))
        if message:
            messages[message.key] = message
    return sorted(messages.values(), key=lambda msg: msg.aired)


async def fetch_messages() -> list[UvbMessage]:
    """Последние сообщения канала. Бросает исключение при ошибке запроса."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    # Со стандартным User-Agent aiohttp сервер рвёт соединение.
    headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(_URL, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"telemetr.me ответил {resp.status}")
            return _parse_posts(await resp.text())


async def _latest_message() -> UvbMessage:
    messages = await fetch_messages()
    if not messages:
        raise RuntimeError("на странице нет сообщений станции — сменилась вёрстка?")
    return messages[-1]


def _describe_error(e: Exception) -> str:
    # У таймаута текст пустой.
    return str(e) or type(e).__name__


def _format(message: UvbMessage, prefix: str) -> str:
    return f"{prefix} {message.aired:%d.%m %H:%M}мск: {message.text}"


async def get_uvb76() -> str:
    """Готовый текст для команды /uvb — последнее сообщение станции."""
    global _cache

    if not (_cache and time.time() - _cache[0] < _CACHE_TTL):
        try:
            _cache = (time.time(), await _latest_message())
        except Exception as e:
            logger.warning(f"uvb76: последнее сообщение не получено: {_describe_error(e)}")
            return f"❌ Не удалось получить данные, см. {_SOURCE}"
    return _format(_cache[1], f"{RADIO} УВБ-76")


# --- Рассылка ---------------------------------------------------------------
#
# Новое сообщение уходит в канал сразу, как только появилось на странице
# зеркала; при старте бота — последнее известное, если в канал уходило не
# оно; раз в сутки в заданное время — последнее, если за прошедшие сутки
# нового не было. Окна тишины нет: сообщения станции редкие, и интересны
# сразу.
#
# Состояние — уже виденные сообщения, последнее отправленное в канал и
# момент рассылки нового — в файле, чтобы рестарт не разослал уже
# отправленное повторно и не повторил ежедневное напоминание сразу после
# свежего сообщения.

_STATE_FILE = "uvb76_last.txt"
# Строка файла состояния с ключом последнего отправленного в канал сообщения.
# Префикс отличает её от ключей виденных: в файлах, записанных до её
# появления, за первой строкой сразу идут виденные.
_SENT_PREFIX = "sent:"

# Сколько виденных сообщений помнить. На странице их 7; запас — на случай,
# если канал удалит пост и из-под него на страницу вернётся старый.
_SEEN_KEEP = 50

# Днём (по Москве, как и вещание станции) опрос чаще, ночью — реже.
_DAY_HOURS_MSK = (7, 22)

# Не виденное раньше рассылается, только если вышло в эфир не раньше
# стольких часов назад: канал сам выкладывает с задержкой до 12 ч, а более
# старое — уже не новость (например, после долгого простоя бота). Последнее
# всё равно покажет сообщение при старте.
_MAX_AGE_HOURS = 12

# Сколько новых сообщений рассылать за одну проверку: при всплеске (бывало
# 25 за день, канал выкладывает пропущенное пачкой) — только последние,
# остальные одной строкой.
_MAX_PER_CHECK = 5

_SEND_PAUSE_SECONDS = 5.0

# Сообщение при старте — один раз за процесс, а не за каждое
# переподключение к ноде: иначе каждый обрыв USB даёт повтор в канале.
_announced_on_start = False


@dataclass
class _State:
    seen: list[str]               # ключи виденных сообщений в порядке обнаружения
    last_new_at: datetime | None  # когда последний раз разослано новое сообщение
    last_sent: str | None = None  # ключ последнего сообщения, ушедшего в канал (любого вида)


def _load_state() -> _State:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        last_new_at = None
        if lines and lines[0].strip():
            try:
                last_new_at = datetime.fromisoformat(lines[0].strip())
            except ValueError:
                pass
        last_sent = None
        seen = []
        for line in (line.strip() for line in lines[1:]):
            if line.startswith(_SENT_PREFIX):
                last_sent = line[len(_SENT_PREFIX):] or None
            elif line:
                seen.append(line)
        logger.info(f"💾 Загружено из {_STATE_FILE}: виденных сообщений УВБ-76 {len(seen)}, "
                    f"последнее {max(seen, default='—')}, в канал ушло {last_sent or '—'}")
        return _State(seen, last_new_at, last_sent)
    except FileNotFoundError:
        logger.info(f"💾 Файл {_STATE_FILE} не найден, виденные сообщения УВБ-76 неизвестны")
    except Exception as e:
        logger.warning(f"💾 Не удалось прочитать {_STATE_FILE}: {e}")
    return _State([], None)


def _save_state(state: _State) -> None:
    state.seen = state.seen[-_SEEN_KEEP:]
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            last_new_at = state.last_new_at.isoformat() if state.last_new_at else ""
            f.write("\n".join([last_new_at, f"{_SENT_PREFIX}{state.last_sent or ''}",
                               *state.seen]) + "\n")
        logger.info(f"💾 Сохранено в {_STATE_FILE}: последнее виденное сообщение УВБ-76 "
                    f"{max(state.seen, default='—')}")
    except Exception as e:
        logger.warning(f"💾 Не удалось сохранить {_STATE_FILE}: {e}")


async def _send(mc, channel_idx: int, text: str) -> bool:
    for n, part in enumerate(split_msg(to_lat(text), "", 130)):
        if n:
            await asyncio.sleep(_SEND_PAUSE_SECONDS)
        try:
            await mc.commands.send_chan_msg(channel_idx, part)
            logger.info(f"📤 УВБ-76 отправлено в канал {channel_idx}: {part}")
        except Exception as e:
            logger.error(f"📭 УВБ-76 в канал {channel_idx} не отправлено: ошибка {e}")
            return False
    return True


async def _check_new(mc, channel_idx: int, state: _State) -> bool:
    """Рассылает ещё не виденные сообщения. Возвращает True, если что-то ушло."""
    try:
        messages = await fetch_messages()
    except Exception as e:
        logger.error(f"📻 УВБ-76 не проверено: {_describe_error(e)}")
        return False
    if not messages:
        logger.warning("📻 УВБ-76: на странице нет сообщений станции — сменилась вёрстка?")
        return False

    if not state.seen:
        # Первый запуск без файла состояния: запоминаем молча, иначе бот
        # разошлёт как новое то, что вышло задолго до него. Последнее
        # покажет сообщение при старте.
        state.seen = [msg.key for msg in messages]
        _save_state(state)
        logger.info(f"📻 УВБ-76: {len(messages)} сообщ. запомнены без рассылки (первый запуск)")
        return False

    seen = set(state.seen)
    new = [msg for msg in messages if msg.key not in seen]
    if not new:
        logger.info("📻 УВБ-76: новых сообщений нет")
        return False

    now = datetime.now(timezone.utc)
    for msg in new:
        delay = int((now - msg.aired).total_seconds() // 60)
        logger.info(f"📻 УВБ-76: новое сообщение (эфир {msg.aired:%d.%m %H:%M} МСК, "
                    f"замечено через {delay} мин): {msg.text}")
    fresh = [msg for msg in new if now - msg.aired <= timedelta(hours=_MAX_AGE_HOURS)]
    if len(fresh) < len(new):
        logger.info(f"📻 УВБ-76: {len(new) - len(fresh)} сообщ. вышли в эфир раньше "
                    f"{_MAX_AGE_HOURS} ч назад — пропущены без рассылки")

    # (текст, ключ сообщения); у строки «ещё N» ключа нет.
    texts: list[tuple[str, str | None]] = []
    if len(fresh) > _MAX_PER_CHECK:
        texts.append((f"{RADIO} УВБ-76: ещё {len(fresh) - _MAX_PER_CHECK} сообщ. до этих, все — {_SOURCE}",
                      None))
        fresh = fresh[-_MAX_PER_CHECK:]
    texts.extend((_format(msg, f"{RADIO} УВБ-76"), msg.key) for msg in fresh)

    sent = False
    for n, (text, key) in enumerate(texts):
        if n:
            await asyncio.sleep(_SEND_PAUSE_SECONDS)
        if await _send(mc, channel_idx, text):
            sent = True
            if key:
                state.last_sent = key
        elif not sent:
            # Не ушло ни одно — состояние не трогаем, повторим на следующей проверке.
            return False

    state.seen.extend(msg.key for msg in new)
    if fresh:
        state.last_new_at = now
    _save_state(state)
    return bool(fresh)


async def _announce(mc, channel_idx: int, state: _State, prefix: str, reason: str,
                    *, only_if_changed: bool = False) -> bool:
    """Шлёт в канал последнее сообщение станции. С `only_if_changed` — только
    если в канал последним уходило не оно. Возвращает False при ошибке."""
    try:
        message = await _latest_message()
    except Exception as e:
        logger.error(f"📻 УВБ-76 ({reason}) не отправлено: {_describe_error(e)}")
        return False
    if only_if_changed and message.key == state.last_sent:
        logger.info(f"📻 УВБ-76 ({reason}): последнее сообщение (эфир {message.aired:%d.%m %H:%M} МСК) "
                    f"уже уходило в канал — не отправляю")
        return True
    logger.info(f"📻 УВБ-76 ({reason}): эфир {message.aired:%d.%m %H:%M} МСК")
    if not await _send(mc, channel_idx, _format(message, prefix)):
        return False
    state.last_sent = message.key
    _save_state(state)
    return True


def _is_day(now_msk: datetime) -> bool:
    hour_from, hour_to = _DAY_HOURS_MSK
    return hour_from <= now_msk.hour < hour_to


async def _poll_loop(mc, channel_idx: int, state: _State,
                     day_seconds: int, night_seconds: int) -> None:
    global _announced_on_start

    await asyncio.sleep(5.0)
    while True:
        sent_new = await _check_new(mc, channel_idx, state)
        if not _announced_on_start:
            # Только что разосланное новое и есть последнее — не дублируем;
            # и последнее не шлём, если в канал оно уже уходило (до рестарта).
            if sent_new or await _announce(mc, channel_idx, state, f"{PIN} УВБ-76 последнее",
                                           "при старте", only_if_changed=True):
                _announced_on_start = True

        seconds = day_seconds if _is_day(datetime.now(MSK)) else night_seconds
        next_run = datetime.now() + timedelta(seconds=seconds)
        logger.info(f"⏰ Следующая проверка УВБ-76 через {seconds // 60} мин ({next_run.strftime('%Y-%m-%d %H:%M')})")
        await asyncio.sleep(seconds)


async def _daily_loop(mc, channel_idx: int, state: _State,
                      hour: int, minute: int, tz: timezone) -> None:
    while True:
        now = datetime.now(tz=tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        logger.info(f"⏰ Ежедневное напоминание УВБ-76 — {target.strftime('%Y-%m-%d %H:%M')}")
        await asyncio.sleep((target - now).total_seconds())

        if state.last_new_at and datetime.now(timezone.utc) - state.last_new_at < timedelta(days=1):
            logger.info("📻 УВБ-76: за сутки было новое сообщение — ежедневное напоминание пропущено")
            continue
        await _announce(mc, channel_idx, state, f"{RADIO} УВБ-76 новых нет, последнее", "ежедневно")


async def uvb76_broadcast_scheduler(mc, config: dict) -> None:
    ub = config.get("uvb76") or {}
    channel_idx = ub.get("channel_idx")
    if channel_idx is None:
        logger.info("⏰ Рассылка УВБ-76 отключена: не задан UVB76_CHANNEL_IDX")
        return
    day_seconds = ub.get("day_interval_seconds", 15 * 60)
    night_seconds = ub.get("night_interval_seconds", 60 * 60)
    if day_seconds <= 0 or night_seconds <= 0:
        logger.info("⏰ Рассылка УВБ-76 отключена: нулевой интервал проверки")
        return
    daily_time = ub.get("daily_time")
    tz = timezone(timedelta(hours=ub.get("timezone_offset_hours", 6)))

    hour_from, hour_to = _DAY_HOURS_MSK
    logger.info(
        f"⏰ УВБ-76 → канал {channel_idx}: проверка раз в {day_seconds // 60} мин "
        f"с {hour_from}:00 до {hour_to}:00 МСК, иначе раз в {night_seconds // 60} мин; "
        + (f"напоминание в {daily_time[0]}:{daily_time[1]:02d}" if daily_time else "без напоминания")
    )

    state = _load_state()
    tasks = [_poll_loop(mc, channel_idx, state, day_seconds, night_seconds)]
    if daily_time:
        tasks.append(_daily_loop(mc, channel_idx, state, *daily_time, tz))
    await asyncio.gather(*tasks)
