"""
Последние доступные версии MeshCore — прошивки и мобильного приложения.

Речь именно про то, что опубликовано в интернете, а не про версию,
залитую в устройство бота (её отдаёт `mc.commands.send_device_query()`).

Источники:
  * прошивка — релизы GitHub `meshcore-dev/MeshCore` (репозиторий переехал
    с `ripplebiz/MeshCore`, старые ссылки редиректят на него). Релизы для
    разных ролей узла выкладываются отдельными тегами вида
    `companion-v1.17.1`, `repeater-v1.17.1`, `room-server-v1.17.1`;
  * прошивка EasySkyMesh (`IoTThinks/EasySkyMesh`) — сторонняя сборка
    MeshCore с упором на энергосбережение. Версионируется не семвером, а
    тегами вида `PowerSaving17.1`, поэтому версия берётся из тега как есть;
  * официальное приложение — MeshCore Liam Cottle. Версия берётся из его
    CHANGELOG (`app.meshcore.nz/assets/CHANGELOG.md`) — того же файла, что
    показывает веб-версия приложения, по заголовкам вида
    `## v1.50.0 - 25/September/2026`. Сборки для всех платформ нумеруются
    синхронно, поэтому одна версия описывает приложение в целом;
  * приложение Ommesh (`cm4ker/ommesh`, до сентября 2026 — `cm4ker/meshnet`)
    — альтернативный клиент. Все его релизы помечены prerelease, поэтому
    `releases/latest` отдаёт 404 и фильтр prerelease для него отключён. Список
    релизов GitHub отдаёт не в хронологическом порядке (сборки 100+ стоят
    ниже `dev.99`), поэтому свежайший релиз выбирается по `published_at` —
    это же правило действует и для EasySkyMesh.

Релизы MeshCore выходят раз в недели, поэтому результат кэшируется на
6 часов — у GitHub API без токена лимит 60 запросов в час на IP.
"""

import asyncio
import logging
import re
import ssl
import time
from datetime import datetime, timedelta

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_GITHUB_RELEASES = "https://api.github.com/repos/{repo}/releases"
_MESHCORE_REPO = "meshcore-dev/MeshCore"
_EASYSKYMESH_REPO = "IoTThinks/EasySkyMesh"
_OMMESH_REPO = "cm4ker/ommesh"
_APP_CHANGELOG = "https://app.meshcore.nz/assets/CHANGELOG.md"

# Префикс тега релиза -> подпись в ответе. Порядок задаёт порядок вывода.
_FW_KINDS = {
    "companion-v": "Companion",
    "repeater-v": "Repeater",
    "room-server-v": "Room Server",
}

_CACHE_TTL = 6 * 3600
_cache: tuple[float, str] | None = None


async def _fetch(url: str, params: dict | None = None, *, as_json: bool = True):
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    # GitHub API отвечает 403 на запросы без User-Agent.
    headers = {"User-Agent": "MeshCoreBotPy"}
    if as_json:
        headers["Accept"] = "application/json"
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, params=params, ssl=ssl_ctx,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{resp.status}")
            return await (resp.json() if as_json else resp.text())


async def _fetch_json(url: str, params: dict | None = None):
    return await _fetch(url, params)


def _short_date(iso: str) -> str:
    """`2026-08-14T13:32:31Z` -> `14.08`. При неожиданном формате — пусто."""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m")
    except (ValueError, TypeError):
        return ""


async def _releases(repo: str, per_page: int = 30) -> list:
    return await _fetch_json(_GITHUB_RELEASES.format(repo=repo),
                             {"per_page": str(per_page)})


async def _firmware_versions() -> dict[str, tuple[str, str]]:
    """Подпись роли -> (версия, дата). Релизы приходят от новых к старым."""
    releases = await _releases(_MESHCORE_REPO)
    found: dict[str, tuple[str, str]] = {}
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        for prefix, label in _FW_KINDS.items():
            if tag.startswith(prefix) and label not in found:
                found[label] = (tag[len(prefix):],
                                _short_date(release.get("published_at", "")))
    # Порядок вывода — как в _FW_KINDS, а не как в ответе GitHub.
    return {label: found[label] for label in _FW_KINDS.values() if label in found}


async def _latest_release(repo: str, *, allow_prerelease: bool = False) -> tuple[str, str]:
    """Версия и дата самого свежего релиза репозитория — по `published_at`.

    Не `releases/latest`: он отдаёт 404 у репозиториев, где все релизы
    помечены prerelease (случай `cm4ker/ommesh`). И не «первый в списке»:
    порядок списка GitHub не хронологический — у Ommesh сборки 100+ стоят
    ниже `dev.99`. Поэтому берётся страница максимального размера и из неё
    выбирается релиз с самой поздней датой публикации.
    """
    candidates = [
        r for r in await _releases(repo, per_page=100)
        # У черновика published_at пустой — он и так не участвует.
        if not r.get("draft") and r.get("published_at")
        and (allow_prerelease or not r.get("prerelease"))
    ]
    if not candidates:
        raise RuntimeError("подходящих релизов нет")
    # ISO 8601 в UTC (`2026-09-26T16:07:20Z`) сравнивается как строка.
    newest = max(candidates, key=lambda r: r["published_at"])
    return newest.get("tag_name", ""), _short_date(newest["published_at"])


async def _easyskymesh_version() -> tuple[str, str]:
    # Теги вида `PowerSaving17.1` — не семвер, отдаём как есть, только
    # разделяя слово и номер, чтобы читалось как версия.
    tag, day = await _latest_release(_EASYSKYMESH_REPO)
    if tag.startswith("PowerSaving"):
        tag = f"PS {tag[len('PowerSaving'):]}"
    return tag, day


async def _ommesh_version() -> tuple[str, str]:
    # Стабильных релизов у репозитория нет вовсе — все prerelease.
    tag, day = await _latest_release(_OMMESH_REPO, allow_prerelease=True)
    # `dev-0.3.0-dev.105.1` -> `0.3.0-dev.105.1`: префикс ветки в версии лишний.
    if tag.startswith("dev-"):
        tag = tag[len("dev-"):]
    return tag, day


# `## v1.50.0 - 25/September/2026` — заголовок версии в CHANGELOG приложения.
_CHANGELOG_HEADING = re.compile(
    r"^##\s+v(\d+(?:\.\d+)*)\s*-\s*(\d{1,2})/([A-Za-z]+)/(\d{4})\s*$",
    re.MULTILINE,
)

# Названия месяцев разбираем сами, а не через strptime("%B"): тот зависит от
# локали процесса и на русской сломался бы на «September».
_MONTHS = {name: i for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), start=1)}


async def _app_version() -> tuple[str, str]:
    """Официальное приложение — по CHANGELOG, который оно само показывает."""
    text = await _fetch(_APP_CHANGELOG, as_json=False)
    entries = []
    for version, day, month, _year in _CHANGELOG_HEADING.findall(text):
        month_num = _MONTHS.get(month.lower())
        date = f"{int(day):02d}.{month_num:02d}" if month_num else ""
        entries.append((tuple(int(p) for p in version.split(".")), version, date))
    if not entries:
        raise RuntimeError("в CHANGELOG нет заголовков версий")
    # Свежая запись сейчас идёт первой, но берём максимум по номеру версии,
    # а не первую строку — не зависим от порядка записей в файле.
    _key, version, date = max(entries)
    return version, date


def _format_firmware(versions: dict[str, tuple[str, str]]) -> list[str]:
    if not versions:
        return []
    unique = {v for v, _ in versions.values()}
    if len(unique) == 1:
        # Обычный случай: все роли выпускаются одной версией — не дублируем.
        version, day = next(iter(versions.values()))
        suffix = f" {day}" if day else ""
        return [f"📟 MeshCore {version}{suffix}"]
    lines = ["📟 MeshCore:"]
    for label, (version, day) in versions.items():
        suffix = f" {day}" if day else ""
        lines.append(f"{label} {version}{suffix}")
    return lines


def _format_one(label: str, value: tuple[str, str] | BaseException) -> tuple[str, bool]:
    """Строка ответа для одного источника и признак успеха."""
    if isinstance(value, BaseException):
        logger.warning(f"versions: {label} — не получено: {value}")
        return f"{label}: ошибка запроса", False
    version, day = value
    suffix = f" {day}" if day else ""
    return f"{label} {version}{suffix}", True


async def _meshcore_version() -> tuple[str, str]:
    """MeshCore одной парой (версия, дата) — для отслеживания изменений.

    `_firmware_versions()` отдаёт версии по ролям узла; здесь берётся
    Companion (роль самого бота), а при его отсутствии — любая доступная.
    """
    versions = await _firmware_versions()
    if not versions:
        raise RuntimeError("в релизах GitHub нет известных тегов прошивки")
    return versions.get("Companion") or next(iter(versions.values()))


# Ключ источника -> (подпись в сообщениях, функция получения версии).
# Ключ попадает в имя env-переменной интервала и в имя файла состояния,
# поэтому менять его — значит сбросить сохранённое состояние рассылки.
SOURCES: dict[str, tuple[str, object]] = {
    "meshcore": ("📟 MeshCore", _meshcore_version),
    "easyskymesh": ("📟 EasySky", _easyskymesh_version),
    "app": ("📱 App", _app_version),
    "ommesh": ("📱 Ommesh", _ommesh_version),
}


async def get_source_version(key: str) -> tuple[str, str]:
    """Версия и дата одного источника. Бросает исключение при ошибке."""
    _label, fetch = SOURCES[key]
    return await fetch()


async def get_latest_versions() -> str:
    global _cache

    if _cache and time.time() - _cache[0] < _CACHE_TTL:
        return _cache[1]

    firmware, easysky, app, ommesh = await asyncio.gather(
        _firmware_versions(), _easyskymesh_version(),
        _app_version(), _ommesh_version(),
        return_exceptions=True,
    )

    # Без заголовка и с короткими подписями: четыре строки с датами иначе
    # не влезают в одно сообщение (лимит 130 байт в каналах).
    lines: list[str] = []
    complete = True

    if isinstance(firmware, BaseException):
        logger.warning(f"versions: прошивка не получена: {firmware}")
        lines.append("📟 MeshCore: ошибка запроса")
        complete = False
    else:
        fw_lines = _format_firmware(firmware)
        if fw_lines:
            lines.extend(fw_lines)
        else:
            logger.warning("versions: в релизах GitHub нет известных тегов прошивки")
            lines.append("📟 MeshCore: не найдена")
            complete = False

    for label, value in (("📟 EasySky", easysky),
                         ("📱 App", app),
                         ("📱 Ommesh", ommesh)):
        line, ok = _format_one(label, value)
        lines.append(line)
        complete = complete and ok

    result = "\n".join(lines)
    if complete:
        _cache = (time.time(), result)
    return result


# --- Автоматическая рассылка изменившихся версий ------------------------------
#
# По образцу `core/traffic.py::traffic_broadcast_scheduler()`: последнее
# разосланное значение хранится и в памяти, и в файле, чтобы рестарт бота не
# принял текущую версию за «изменение с нуля» и не слал её в канал без повода.
# Отличия от пробок два, оба из-за редкости события (релиз раз в недели):
#   * интервал задаётся в часах, а не в минутах, и свой для каждого источника;
#   * изменение, случившееся вне окна тишины, не теряется, а откладывается до
#     ближайшего окна — файл состояния обновляется только после отправки.

_STATE_FILE_TEMPLATE = "versions_last_{key}.txt"


def _state_file(key: str) -> str:
    return _STATE_FILE_TEMPLATE.format(key=key)


def _load_last_version(key: str) -> str | None:
    path = _state_file(key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            version = f.read().strip()
        if not version:
            return None
        logger.info(f"💾 Загружена последняя разосланная версия {key} из {path}: {version}")
        return version
    except FileNotFoundError:
        logger.info(f"💾 Файл {path} не найден, последняя версия {key} неизвестна")
        return None
    except Exception as e:
        logger.warning(f"💾 Не удалось прочитать {path}: {e}")
        return None


def _save_last_version(key: str, version: str) -> None:
    path = _state_file(key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(version)
        logger.info(f"💾 Сохранена последняя разосланная версия {key} в {path}: {version}")
    except Exception as e:
        logger.warning(f"💾 Не удалось сохранить {path}: {e}")


def _in_broadcast_window(now: datetime, hour_from: int, hour_to: int) -> bool:
    return hour_from <= now.hour < hour_to


async def _check_version_change(mc, key: str, channel_idx: int,
                                hour_from: int, hour_to: int,
                                last_version: str | None) -> str | None:
    """Опрашивает источник и при изменении версии шлёт сообщение в канал.

    Возвращает версию, которую считаем разосланной. При ошибке запроса и при
    попадании вне окна тишины возвращает прежнюю — тогда на следующей проверке
    изменение будет обнаружено снова и уйдёт в канал, когда окно откроется.
    """
    label, _fetch = SOURCES[key]
    try:
        version, day = await get_source_version(key)
    except Exception as e:
        logger.error(f"📭 Версия {key} не проверена: {e}")
        return last_version

    if version == last_version:
        logger.info(f"📭 Версия {key} не изменилась: {version}")
        return last_version

    if last_version is None:
        # Первый запуск без файла состояния: запоминаем текущую версию молча,
        # иначе бот разошлёт «новинку», которая вышла задолго до него.
        _save_last_version(key, version)
        logger.info(f"📭 Версия {key} запомнена без рассылки (первый запуск): {version}")
        return version

    if not _in_broadcast_window(datetime.now(), hour_from, hour_to):
        logger.info(
            f"📭 Версия {key} изменилась ({last_version} → {version}), но не время "
            f"({hour_from}:00–{hour_to}:00) — рассылка отложена до окна"
        )
        return last_version

    suffix = f" {day}" if day else ""
    report = f"🆕 {label} {version}{suffix} (было {last_version})"
    try:
        await mc.commands.send_chan_msg(channel_idx, report)
        logger.info(f"📤 Версия {key} изменилась ({last_version} → {version}), отправлено в канал {channel_idx}: {report}")
    except Exception as e:
        logger.error(f"📭 Рассылка версии {key} в канал {channel_idx} не отправлена: ошибка {e}")
        return last_version

    _save_last_version(key, version)
    return version


def _humanize_interval(seconds: int) -> str:
    """600 -> `10 мин`, 7200 -> `2 ч`, 259200 -> `3 сут` — только для логов."""
    for unit_seconds, unit in ((86400, "сут"), (3600, "ч"), (60, "мин")):
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds} {unit}"
    return f"{seconds} с"


async def _source_scheduler(mc, key: str, channel_idx: int, interval_seconds: int,
                            hour_from: int, hour_to: int) -> None:
    """Цикл проверки одного источника — свой интервал у каждого."""
    last_version = _load_last_version(key)

    await asyncio.sleep(5.0)
    last_version = await _check_version_change(
        mc, key, channel_idx, hour_from, hour_to, last_version
    )

    human = _humanize_interval(interval_seconds)
    while True:
        next_run = datetime.now() + timedelta(seconds=interval_seconds)
        logger.info(
            f"⏰ Следующая проверка версии {key} через {human} "
            f"({next_run.strftime('%Y-%m-%d %H:%M')} по местному)"
        )
        await asyncio.sleep(interval_seconds)
        last_version = await _check_version_change(
            mc, key, channel_idx, hour_from, hour_to, last_version
        )


async def versions_broadcast_scheduler(mc, config: dict) -> None:
    vb = config.get("versions_broadcast")
    if not vb:
        return
    channel_idx = vb.get("channel_idx", 3)
    hour_from = vb.get("hour_from", 7)
    hour_to = vb.get("hour_to", 19)
    intervals = vb.get("interval_seconds", {})

    tasks = []
    for key in SOURCES:
        interval_seconds = intervals.get(key, 24 * 3600)
        if interval_seconds <= 0:
            logger.info(f"⏰ Проверка версии {key} отключена (интервал {interval_seconds})")
            continue
        tasks.append(_source_scheduler(mc, key, channel_idx, interval_seconds,
                                       hour_from, hour_to))

    if not tasks:
        logger.info("⏰ Рассылка версий отключена: все интервалы нулевые")
        return

    await asyncio.gather(*tasks)
