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
  * официальное приложение — MeshCore Liam Cottle. Версия берётся из
    lookup-API App Store (отдаёт JSON без ключа и без скрапинга). Сборки
    для iOS и Android нумеруются синхронно, поэтому одна версия описывает
    приложение в целом;
  * приложение Meshnet (`cm4ker/meshnet`) — альтернативный клиент. Все его
    релизы помечены prerelease (`dev-0.3.0-dev.97.1`), стабильных нет
    вовсе, а `releases/latest` из-за этого отдаёт 404 — поэтому берётся
    просто самый свежий релиз, без фильтра по prerelease.

Релизы MeshCore выходят раз в недели, поэтому результат кэшируется на
6 часов — у GitHub API без токена лимит 60 запросов в час на IP.
"""

import asyncio
import logging
import ssl
import time
from datetime import datetime

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_GITHUB_RELEASES = "https://api.github.com/repos/{repo}/releases"
_MESHCORE_REPO = "meshcore-dev/MeshCore"
_EASYSKYMESH_REPO = "IoTThinks/EasySkyMesh"
_MESHNET_REPO = "cm4ker/meshnet"
_APPSTORE_LOOKUP = "https://itunes.apple.com/lookup"
_APP_BUNDLE_ID = "com.liamcottle.meshcore.ios"

# Префикс тега релиза -> подпись в ответе. Порядок задаёт порядок вывода.
_FW_KINDS = {
    "companion-v": "Companion",
    "repeater-v": "Repeater",
    "room-server-v": "Room Server",
}

_CACHE_TTL = 6 * 3600
_cache: tuple[float, str] | None = None


async def _fetch_json(url: str, params: dict | None = None):
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    # GitHub API отвечает 403 на запросы без User-Agent.
    headers = {"User-Agent": "MeshCoreBotPy", "Accept": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, params=params, ssl=ssl_ctx,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{resp.status}")
            # App Store отдаёт JSON с Content-Type text/javascript.
            return await resp.json(content_type=None)


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
    """Версия и дата самого свежего релиза репозитория.

    Не `releases/latest`: он отдаёт 404 у репозиториев, где все релизы
    помечены prerelease (случай `cm4ker/meshnet`).
    """
    for release in await _releases(repo, per_page=10):
        if release.get("draft"):
            continue
        if release.get("prerelease") and not allow_prerelease:
            continue
        tag = release.get("tag_name", "")
        return tag, _short_date(release.get("published_at", ""))
    raise RuntimeError("подходящих релизов нет")


async def _easyskymesh_version() -> tuple[str, str]:
    # Теги вида `PowerSaving17.1` — не семвер, отдаём как есть, только
    # разделяя слово и номер, чтобы читалось как версия.
    tag, day = await _latest_release(_EASYSKYMESH_REPO)
    if tag.startswith("PowerSaving"):
        tag = f"PS {tag[len('PowerSaving'):]}"
    return tag, day


async def _meshnet_version() -> tuple[str, str]:
    # Стабильных релизов у репозитория нет вовсе — все prerelease.
    tag, day = await _latest_release(_MESHNET_REPO, allow_prerelease=True)
    # `dev-0.3.0-dev.97.1` -> `0.3.0-dev.97.1`: префикс ветки в версии лишний.
    if tag.startswith("dev-"):
        tag = tag[len("dev-"):]
    return tag, day


async def _app_version() -> tuple[str, str]:
    data = await _fetch_json(_APPSTORE_LOOKUP, {"bundleId": _APP_BUNDLE_ID})
    results = data.get("results") or []
    if not results:
        raise RuntimeError("приложение не найдено в App Store")
    app = results[0]
    return app.get("version", "?"), _short_date(app.get("currentVersionReleaseDate", ""))


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


async def get_latest_versions() -> str:
    global _cache

    if _cache and time.time() - _cache[0] < _CACHE_TTL:
        return _cache[1]

    firmware, easysky, app, meshnet = await asyncio.gather(
        _firmware_versions(), _easyskymesh_version(),
        _app_version(), _meshnet_version(),
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
                         ("📱 Meshnet", meshnet)):
        line, ok = _format_one(label, value)
        lines.append(line)
        complete = complete and ok

    result = "\n".join(lines)
    if complete:
        _cache = (time.time(), result)
    return result
