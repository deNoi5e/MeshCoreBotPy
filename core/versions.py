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
# Ссылка на скачивание приложения для рассылки: у него нет релизов на GitHub,
# а сборки для всех платформ нумеруются синхронно — даём Google Play.
_APP_DOWNLOAD_URL = "https://play.google.com/store/apps/details?id=com.liamcottle.meshcore.android"

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


def _release_url(release: dict, repo: str) -> str:
    """Страница релиза на GitHub; без `html_url` — список релизов репозитория."""
    return release.get("html_url") or f"https://github.com/{repo}/releases"


async def _firmware_versions() -> dict[str, tuple[str, str, str]]:
    """Подпись роли -> (версия, дата, ссылка). Релизы приходят от новых к старым."""
    releases = await _releases(_MESHCORE_REPO)
    found: dict[str, tuple[str, str, str]] = {}
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        for prefix, label in _FW_KINDS.items():
            if tag.startswith(prefix) and label not in found:
                found[label] = (tag[len(prefix):],
                                _short_date(release.get("published_at", "")),
                                _release_url(release, _MESHCORE_REPO))
    # Порядок вывода — как в _FW_KINDS, а не как в ответе GitHub.
    return {label: found[label] for label in _FW_KINDS.values() if label in found}


async def _latest_release(repo: str, *, allow_prerelease: bool = False) -> tuple[str, str, str]:
    """Тег, дата и ссылка самого свежего релиза репозитория — по `published_at`.

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
    return (newest.get("tag_name", ""), _short_date(newest["published_at"]),
            _release_url(newest, repo))


async def _easyskymesh_version() -> tuple[str, str, str]:
    # Теги вида `PowerSaving17.1` — не семвер, отдаём как есть, только
    # разделяя слово и номер, чтобы читалось как версия.
    tag, day, url = await _latest_release(_EASYSKYMESH_REPO)
    if tag.startswith("PowerSaving"):
        tag = f"PS {tag[len('PowerSaving'):]}"
    return tag, day, url


async def _ommesh_version() -> tuple[str, str, str]:
    # Стабильных релизов у репозитория нет вовсе — все prerelease.
    tag, day, url = await _latest_release(_OMMESH_REPO, allow_prerelease=True)
    # `dev-0.3.0-dev.105.1` -> `0.3.0-dev.105.1`: префикс ветки в версии лишний.
    if tag.startswith("dev-"):
        tag = tag[len("dev-"):]
    return tag, day, url


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


async def _app_version() -> tuple[str, str, str]:
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
    return version, date, _APP_DOWNLOAD_URL


def _format_firmware(versions: dict[str, tuple[str, str, str]]) -> list[str]:
    if not versions:
        return []
    unique = {v for v, _, _ in versions.values()}
    if len(unique) == 1:
        # Обычный случай: все роли выпускаются одной версией — не дублируем.
        version, day, _url = next(iter(versions.values()))
        suffix = f" {day}" if day else ""
        return [f"📟 MeshCore {version}{suffix}"]
    lines = ["📟 MeshCore:"]
    for label, (version, day, _url) in versions.items():
        suffix = f" {day}" if day else ""
        lines.append(f"{label} {version}{suffix}")
    return lines


def _format_one(label: str, value: tuple[str, str, str] | BaseException) -> tuple[str, bool]:
    """Строка ответа для одного источника и признак успеха."""
    if isinstance(value, BaseException):
        logger.warning(f"versions: {label} — не получено: {value}")
        return f"{label}: ошибка запроса", False
    version, day, _url = value
    suffix = f" {day}" if day else ""
    return f"{label} {version}{suffix}", True


async def _meshcore_version() -> tuple[str, str, str]:
    """MeshCore одной тройкой (версия, дата, ссылка) — для отслеживания изменений.

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


async def get_source_version(key: str) -> tuple[str, str, str]:
    """Версия, дата и ссылка на скачивание одного источника. Бросает исключение при ошибке."""
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
#     ближайшего окна.
#
# Просто «отложить до окна» недостаточно: если планировщик спит ровно
# interval_seconds, а окно короче суток, момент проверки при каждом заходе
# приходится на одно и то же время суток — если это время вне окна, оно
# остаётся вне окна навсегда, и отложенная версия никогда не уйдёт (баг,
# найденный на интервале 24ч при окне, скажем, 7–23: проверка в 2 ночи так и
# останется проверкой в 2 ночи). Поэтому дополнительно к обычному сну есть
# будильник на каждое открытие окна (`hour_from:00`): если с последней
# фактической отправки прошло больше interval_seconds, он форсирует проверку
# незамедлительно, не дожидаясь обычного таймера. Смысл при этом смещается —
# это уже не «опрашивать источник раз в N», а «не давать окну без рассылки
# растянуться дольше N».

_STATE_FILE_TEMPLATE = "versions_last_{key}.txt"

# Пауза перед сообщением в канал ссылок — чтобы не слать пакеты в эфир подряд.
_LINK_PAUSE_SECONDS = 5.0

# Сокращатели ссылок для канала ссылок — пробуются по порядку до первого
# успешного: (имя, адрес API, параметр формата ответа, префикс короткой ссылки).
# Кэш — на время работы процесса: анонс при старте и повторы шлют ту же
# ссылку, лишний раз сокращатели не дёргаем.
_SHORTENERS = (
    ("v.gd", "https://v.gd/create.php", {"format": "simple"}, "https://v.gd/"),
    ("da.gd", "https://da.gd/s", {}, "https://da.gd/"),
)
_short_urls: dict[str, str] = {}


def _state_file(key: str) -> str:
    return _STATE_FILE_TEMPLATE.format(key=key)


def _load_last_version(key: str) -> tuple[str | None, datetime | None]:
    """Версия и момент её отправки. Второй строки может не быть — файлы,
    сохранённые до появления будильника окна, читаются как (версия, None)."""
    path = _state_file(key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        version = lines[0].strip() if lines else ""
        if not version:
            return None, None
        sent_at = None
        if len(lines) > 1 and lines[1].strip():
            try:
                sent_at = datetime.fromisoformat(lines[1].strip())
            except ValueError:
                pass
        logger.info(f"💾 Загружена последняя разосланная версия {key} из {path}: {version}")
        return version, sent_at
    except FileNotFoundError:
        logger.info(f"💾 Файл {path} не найден, последняя версия {key} неизвестна")
        return None, None
    except Exception as e:
        logger.warning(f"💾 Не удалось прочитать {path}: {e}")
        return None, None


def _save_last_version(key: str, version: str, sent_at: datetime) -> None:
    path = _state_file(key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{version}\n{sent_at.isoformat()}\n")
        logger.info(f"💾 Сохранена последняя разосланная версия {key} в {path}: {version}")
    except Exception as e:
        logger.warning(f"💾 Не удалось сохранить {path}: {e}")


def _in_broadcast_window(now: datetime, hour_from: int, hour_to: int) -> bool:
    if hour_from <= hour_to:
        return hour_from <= now.hour < hour_to
    # Окно переходит через полночь (напр. 22–6 или 7–0): "с hour_from до
    # конца суток" ИЛИ "с начала суток до hour_to".
    return now.hour >= hour_from or now.hour < hour_to


async def _check_version_change(mc, key: str, channel_idx: int,
                                hour_from: int, hour_to: int,
                                last_version: str | None,
                                last_sent_at: datetime | None,
                                *, force: bool = False, announce: bool = False,
                                link_channel_idx: int | None = None) -> tuple[str | None, datetime | None]:
    """Опрашивает источник и при изменении версии шлёт сообщение в канал.

    Если задан `link_channel_idx`, туда вдобавок через паузу уходит версия
    и ссылка на скачивание (см. `_link_messages()`).

    `announce=True` (первая проверка сессии для источников из
    `VERSIONS_ANNOUNCE_ON_START`) вдобавок шлёт текущую версию со ссылкой
    в канал ссылок — безусловно, даже если она не изменилась, сейчас вне
    окна тишины или это первый запуск. Только туда: основной канал и
    состояние рассылки это не трогает, так что настоящая новая версия
    потом уйдёт в основной канал как обычно. Нужно для проверки, что
    приходит в канал ссылок.

    Возвращает (версию, момент отправки), которые считаем актуальным
    состоянием. При ошибке запроса и при попадании вне окна тишины (без
    `force`) возвращает прежние — тогда на следующей проверке изменение
    будет обнаружено снова и уйдёт в канал, когда окно откроется.

    `force=True` (вызывается будильником открытия окна, см.
    `versions_broadcast_scheduler()`) обходит проверку окна: используется,
    когда рассылка и так уже подзадержалась дольше интервала проверки, и
    ждать обычного планового захода незачем.
    """
    label, _fetch = SOURCES[key]
    try:
        version, day, url = await get_source_version(key)
    except Exception as e:
        logger.error(f"📭 Версия {key} не проверена: {e}")
        return last_version, last_sent_at

    state, link_sent = await _apply_version(
        mc, key, label, version, day, url, channel_idx, hour_from, hour_to,
        last_version, last_sent_at, force=force, link_channel_idx=link_channel_idx,
    )

    if announce and not link_sent:
        if link_channel_idx is None:
            logger.warning(f"📢 VERSIONS_ANNOUNCE_ON_START: версия {key} не отправлена — "
                           f"не задан VERSIONS_LINK_CHANNEL_IDX")
        else:
            suffix = f" {day}" if day else ""
            logger.info(f"📢 Версия {key} при старте (VERSIONS_ANNOUNCE_ON_START) — "
                        f"в канал {link_channel_idx}: {version}")
            await _send_messages(mc, key, link_channel_idx,
                                 await _link_messages(f"📌 {label} {version}{suffix}", url))
    return state


async def _apply_version(mc, key: str, label: str, version: str, day: str, url: str,
                         channel_idx: int, hour_from: int, hour_to: int,
                         last_version: str | None, last_sent_at: datetime | None,
                         *, force: bool, link_channel_idx: int | None,
                         ) -> tuple[tuple[str | None, datetime | None], bool]:
    """Обычная рассылка изменившейся версии (см. `_check_version_change()`).

    Возвращает новое состояние и признак, ушла ли версия в канал ссылок.
    """
    if version == last_version:
        logger.info(f"📭 Версия {key} не изменилась: {version}")
        return (last_version, last_sent_at), False

    now = datetime.now()

    if last_version is None:
        # Первый запуск без файла состояния: запоминаем текущую версию молча,
        # иначе бот разошлёт «новинку», которая вышла задолго до него.
        _save_last_version(key, version, now)
        logger.info(f"📭 Версия {key} запомнена без рассылки (первый запуск): {version}")
        return (version, now), False

    if not force and not _in_broadcast_window(now, hour_from, hour_to):
        logger.info(
            f"📭 Версия {key} изменилась ({last_version} → {version}), но не время "
            f"({hour_from}:00–{hour_to}:00) — рассылка отложена до окна"
        )
        return (last_version, last_sent_at), False

    suffix = f" {day}" if day else ""
    same_channel = link_channel_idx == channel_idx
    tail: list[str] = []
    if same_channel:
        # Канал ссылок совпадает с основным — туда только сообщения со ссылкой.
        report, *tail = await _link_messages(f"🆕 {label} {version}{suffix}", url)
    else:
        report = f"🆕 {label} {version}{suffix} (было {last_version})"
    try:
        await mc.commands.send_chan_msg(channel_idx, report)
        logger.info(f"📤 Версия {key} изменилась ({last_version} → {version}), отправлено в канал {channel_idx}: {report}")
    except Exception as e:
        logger.error(f"📭 Рассылка версии {key} в канал {channel_idx} не отправлена: ошибка {e}")
        return (last_version, last_sent_at), False

    # Состояние сохраняем сразу после основного канала: сбой в канале ссылок
    # не должен приводить к повтору, иначе основной канал получит дубль.
    _save_last_version(key, version, now)

    if link_channel_idx is None:
        return (version, now), False
    if same_channel:
        await _send_messages(mc, key, channel_idx, tail)
    else:
        await _send_messages(mc, key, link_channel_idx,
                             await _link_messages(f"🆕 {label} {version}{suffix}", url))
    return (version, now), True


async def _send_messages(mc, key: str, channel_idx: int, messages: list[str]) -> None:
    """Сообщения в канал ссылок — каждое после паузы, чтобы не слать пакеты
    в эфир вплотную друг к другу и к основному каналу."""
    for message in messages:
        try:
            await asyncio.sleep(_LINK_PAUSE_SECONDS)
            await mc.commands.send_chan_msg(channel_idx, message)
            logger.info(f"📤 Версия {key}: отправлено в канал ссылок {channel_idx}: {message}")
        except Exception as e:
            logger.error(f"📭 Версия {key} в канал ссылок {channel_idx} не отправлена: ошибка {e}")
            return


async def _link_messages(text: str, url: str) -> list[str]:
    """Сообщения для канала ссылок: версия и ссылка на скачивание.

    С короткой ссылкой — одним сообщением: второе подряд в эфир доходит не
    всегда. Если сократить не удалось — полная ссылка вторым сообщением:
    вместе с текстом она занимает почти весь лимит канала.
    Без «было»: в канале ссылок важно, что качать, а не с чего обновляться.
    """
    short = await _shorten_url(url)
    if short:
        return [f"{text} {short}"]
    return [text, url]


async def _shorten_url(url: str) -> str | None:
    """Короткая ссылка — первым сработавшим из `_SHORTENERS`; не сработал
    ни один — None.

    Об ошибке v.gd (как и is.gd того же автора) сообщает текстом `Error...`
    — бывает и с HTTP 200 («database insert failed»), поэтому проверяется
    сам ответ по префиксу короткой ссылки, а не только статус.
    """
    if url in _short_urls:
        return _short_urls[url]
    for name, api, params, prefix in _SHORTENERS:
        try:
            short = (await _fetch(api, {**params, "url": url}, as_json=False)).strip()
        except Exception as e:
            logger.warning(f"🔗 {name} не сократил {url}: {e}")
            continue
        if not short.startswith(prefix):
            logger.warning(f"🔗 {name} не сократил {url}: {short[:100]}")
            continue
        _short_urls[url] = short
        return short
    return None


def _humanize_interval(seconds: int) -> str:
    """600 -> `10 мин`, 7200 -> `2 ч`, 259200 -> `3 сут` — только для логов."""
    for unit_seconds, unit in ((86400, "сут"), (3600, "ч"), (60, "мин")):
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds} {unit}"
    return f"{seconds} с"


def _humanize_duration(seconds: int) -> str:
    """26243 -> `7 ч 17 мин` — произвольная (не обязательно круглая) длительность."""
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if seconds or not parts:
        parts.append(f"{seconds} с")
    return " ".join(parts)


def _seconds_until_hour(now: datetime, hour: int) -> float:
    """Секунды до ближайшего наступления `hour:00` (сегодня или завтра)."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


async def _source_scheduler(mc, key: str, channel_idx: int, interval_seconds: int,
                            hour_from: int, hour_to: int,
                            link_channel_idx: int | None = None,
                            announce_on_start: bool = False) -> None:
    """Цикл проверки одного источника — свой интервал у каждого.

    Спит до того, что наступит раньше: обычный интервал или ближайшее
    открытие окна (`hour_from:00`, каждые сутки). Будильник открытия окна
    сам по себе источник не опрашивает — опрос (HTTP-запрос) случается,
    только когда до этого с последней отправки уже прошло не меньше
    interval_seconds, то есть версия либо подзадержалась в отложенном
    состоянии, либо штатная проверка давно не случалась (бот долго не
    работал). Иначе будильник просто пересчитывает сон заново (`continue`)
    без обращения к источнику — иначе источник опрашивался бы лишний раз
    при каждом открытии окна, даже если интервал — несколько суток. Без
    этого будильника отложенная вне окна версия могла бы застрять навсегда
    — см. комментарий выше файла.
    """
    last_version, last_sent_at = _load_last_version(key)

    await asyncio.sleep(5.0)
    last_version, last_sent_at = await _check_version_change(
        mc, key, channel_idx, hour_from, hour_to, last_version, last_sent_at,
        announce=announce_on_start, link_channel_idx=link_channel_idx,
    )

    human = _humanize_interval(interval_seconds)
    while True:
        now = datetime.now()
        window_wait = _seconds_until_hour(now, hour_from)
        next_run = now + timedelta(seconds=interval_seconds)
        window_note = ""
        if not _in_broadcast_window(now, hour_from, hour_to):
            window_note = f"; до рассылки {_humanize_duration(window_wait)}"
        logger.info(
            f"⏰ Следующая проверка версии {key} не позже {next_run.strftime('%Y-%m-%d %H:%M')} "
            f"(интервал {human}{window_note})"
        )

        interval_sleep = asyncio.ensure_future(asyncio.sleep(interval_seconds))
        window_sleep = asyncio.ensure_future(asyncio.sleep(window_wait))
        done, pending = await asyncio.wait(
            {interval_sleep, window_sleep}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

        force = False
        if window_sleep in done:
            # Проснулись (в том числе) по будильнику открытия окна — не
            # обязательно эксклюзивно: если interval_seconds кратен суткам,
            # оба будильника с какого-то момента срабатывают ОДНОВРЕМЕННО
            # (окно и обычный таймер совпадают по фазе), и `in done` тогда
            # истинно для обоих сразу — раньше здесь стояло дополнительное
            # `and interval_sleep not in done`, из-за которого именно этот,
            # самый частый случай (интервал вида `Nd`/`24h`) никогда не
            # форсировался, и отложенная версия могла зависнуть навсегда.
            # Форсируем, только если рассылка и так подзадержалась.
            stale = (last_sent_at is None or
                    (datetime.now() - last_sent_at).total_seconds() >= interval_seconds)
            if stale:
                force = True
                logger.info(f"⏰ Окно версии {key} открылось, рассылка задержалась — проверяю вне очереди")
            elif interval_sleep not in done:
                # Разбудил только будильник окна, и рассылка не подзадержалась —
                # источник в очередной раз опрашивать незачем, дальше подождём
                # до штатного интервала или следующего окна. Без этого источник
                # опрашивался бы лишний раз при каждом открытии окна (например,
                # раз в сутки даже при интервале в несколько суток).
                continue

        last_version, last_sent_at = await _check_version_change(
            mc, key, channel_idx, hour_from, hour_to, last_version, last_sent_at,
            force=force, link_channel_idx=link_channel_idx,
        )


async def versions_broadcast_scheduler(mc, config: dict) -> None:
    vb = config.get("versions_broadcast")
    if not vb:
        return
    channel_idx = vb.get("channel_idx", 3)
    hour_from = vb.get("hour_from", 7)
    hour_to = vb.get("hour_to", 19)
    intervals = vb.get("interval_seconds", {})
    link_channel_idx = vb.get("link_channel_idx")
    announce_on_start = vb.get("announce_on_start", set())
    if announce_on_start:
        logger.info(f"📢 При старте сессии без проверки изменения отправляются: "
                    f"{', '.join(sorted(announce_on_start))}")
    if link_channel_idx is None:
        logger.info("⏰ Канал ссылок на новые версии не задан — ссылки не рассылаются")
    else:
        logger.info(f"⏰ Новые версии со ссылкой на скачивание — ещё и в канал {link_channel_idx}")

    tasks = []
    for key in SOURCES:
        interval_seconds = intervals.get(key, 24 * 3600)
        if interval_seconds <= 0:
            logger.info(f"⏰ Проверка версии {key} отключена (интервал {interval_seconds})")
            continue
        tasks.append(_source_scheduler(mc, key, channel_idx, interval_seconds,
                                       hour_from, hour_to, link_channel_idx,
                                       key in announce_on_start))

    if not tasks:
        logger.info("⏰ Рассылка версий отключена: все интервалы нулевые")
        return

    await asyncio.gather(*tasks)
