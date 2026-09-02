"""
Последние доступные версии MeshCore — прошивки и мобильного приложения.

Речь именно про то, что опубликовано в интернете, а не про версию,
залитую в устройство бота (её отдаёт `mc.commands.send_device_query()`).

Источники:
  * прошивка — релизы GitHub `meshcore-dev/MeshCore` (репозиторий переехал
    с `ripplebiz/MeshCore`, старые ссылки редиректят на него). Релизы для
    разных ролей узла выкладываются отдельными тегами вида
    `companion-v1.17.1`, `repeater-v1.17.1`, `room-server-v1.17.1`;
  * приложение — официальный MeshCore Liam Cottle. Версия берётся из
    lookup-API App Store (отдаёт JSON без ключа и без скрапинга). Сборки
    для iOS и Android нумеруются синхронно, поэтому одна версия описывает
    приложение в целом.

Релизы выходят раз в недели, поэтому результат кэшируется на 6 часов —
у GitHub API без токена лимит 60 запросов в час на IP.
"""

import asyncio
import logging
import ssl
import time
from datetime import datetime

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_GITHUB_RELEASES = "https://api.github.com/repos/meshcore-dev/MeshCore/releases"
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


async def _firmware_versions() -> dict[str, tuple[str, str]]:
    """Подпись роли -> (версия, дата). Релизы приходят от новых к старым."""
    releases = await _fetch_json(_GITHUB_RELEASES, {"per_page": "30"})
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
        suffix = f" ({day})" if day else ""
        return [f"📟 Прошивка {version}{suffix}"]
    lines = ["📟 Прошивка:"]
    for label, (version, day) in versions.items():
        suffix = f" ({day})" if day else ""
        lines.append(f"{label} {version}{suffix}")
    return lines


async def get_latest_versions() -> str:
    global _cache

    if _cache and time.time() - _cache[0] < _CACHE_TTL:
        return _cache[1]

    firmware, app = await asyncio.gather(
        _firmware_versions(), _app_version(), return_exceptions=True
    )

    lines = ["🆕 Последние версии:"]
    complete = True

    if isinstance(firmware, BaseException):
        logger.warning(f"versions: прошивка не получена: {firmware}")
        lines.append("📟 Прошивка: ошибка запроса")
        complete = False
    else:
        fw_lines = _format_firmware(firmware)
        if fw_lines:
            lines.extend(fw_lines)
        else:
            logger.warning("versions: в релизах GitHub нет известных тегов прошивки")
            lines.append("📟 Прошивка: не найдена")
            complete = False

    if isinstance(app, BaseException):
        logger.warning(f"versions: версия приложения не получена: {app}")
        lines.append("📱 Приложение: ошибка запроса")
        complete = False
    else:
        version, day = app
        suffix = f" ({day})" if day else ""
        lines.append(f"📱 Приложение {version}{suffix}")

    result = "\n".join(lines)
    if complete:
        _cache = (time.time(), result)
    return result
