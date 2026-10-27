import asyncio
import json
import logging
import os
import ssl
import time
from datetime import datetime, timedelta

import aiohttp
import certifi

logger = logging.getLogger(__name__)

# АЗС Газпромнефть на Маршала Жукова, 154 (Омск). GPNAZSID — ключ станции в API
# сайта gpnbonus.ru (тот же, по которому карта АЗС на сайте запрашивает карточку
# станции). Закрыли станцию или сменили ей ID — запрос вернёт 404, см. _download().
_STATION_ID = 3259
_URL = f"https://gpnbonus.ru/api/stations/{_STATION_ID}"

# Сервер отдаёт только свой сертификат, без промежуточных, — проверка по одному
# certifi падает. Недостающая цепочка лежит в репозитории (см. заголовок файла).
_CHAIN_FILE = os.path.join(os.path.dirname(__file__), "certs", "gpnbonus_chain.pem")

# Сайт опрашивается не чаще раза в час (как часто он обновляет цены — не проверено:
# в единственном снимке у всех видов топлива одна метка 12:00 UTC): всё
# остальное время ответ берётся из кэша. Кэш лежит и в памяти, и в файле — чтобы
# рестарт бота не заставлял сразу идти на сайт (файл в корне проекта, не в git).
_CACHE_TTL_SECONDS = 60 * 60
_CACHE_FILE = "fuel_cache.json"
# После неудачного запроса новый не раньше чем через столько секунд: пока сайт не
# отвечает, каждая команда иначе тратила бы три попытки и лишний раз стучалась в него.
_FAIL_COOLDOWN_SECONDS = 20 * 60

# Как у /traffic: короткий connect-таймаут и несколько попыток — сервер gpnbonus.ru
# иногда сбрасывает соединение на рукопожатии, и одна попытка на 10 с слишком дорога.
_ATTEMPTS = 3
_RETRY_PAUSE_SECONDS = 1.0
_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=3)

_ssl_ctx: ssl.SSLContext | None = None
# (time.time() получения, топливо) — время по настенным часам, а не monotonic:
# оно сохраняется в файл и должно пережить перезапуск.
_cache: tuple[float, list[dict]] | None = None
_cache_file_loaded = False
_last_failure: tuple[float, str] | None = None  # (когда, текст ошибки)
_lock = asyncio.Lock()


def _get_ssl_ctx() -> ssl.SSLContext:
    global _ssl_ctx
    if _ssl_ctx is None:
        _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        _ssl_ctx.load_verify_locations(cafile=_CHAIN_FILE)
    return _ssl_ctx


def _parse(payload: dict) -> list[dict]:
    """Из ответа API — список {title, type, price, since, avail}; без цены топливо пропускается."""
    fuels = []
    for item in payload.get("data", []):
        price = (item.get("price") or {}).get("price")
        if price is None:
            continue
        product = item.get("product") or {}
        fuels.append({
            "title": product.get("shortTitle") or product.get("title") or "?",
            "type": product.get("type"),
            "price": float(price),
            "since": item["price"].get("since"),
            "avail": (item.get("rest") or {}).get("avail", True),
        })
    # Бензин по возрастанию цены (92, 95, G-95), дизель после него.
    fuels.sort(key=lambda f: (f["type"] == "dizel", f["price"]))
    return fuels


def _load_cache_file() -> tuple[float, list[dict]] | None:
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        fetched_at, fuels = float(data["fetched_at"]), data["fuels"]
        if not fuels or not all({"title", "price"} <= f.keys() for f in fuels):
            raise ValueError("неожиданная структура")
        logger.info(f"💾 Загружен кэш цен на топливо из {_CACHE_FILE}: возраст {(time.time() - fetched_at) / 60:.0f} мин")
        return fetched_at, fuels
    except FileNotFoundError:
        logger.info(f"💾 Файл {_CACHE_FILE} не найден, кэш цен на топливо пуст")
    except Exception as e:
        logger.warning(f"💾 Не удалось прочитать {_CACHE_FILE}: {e}")
    return None


def _save_cache_file(fetched_at: float, fuels: list[dict]) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": fetched_at, "fuels": fuels}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"💾 Не удалось сохранить {_CACHE_FILE}: {e}")


def _is_fresh(fetched_at: float, now: float) -> bool:
    # Метка из будущего (часы переводили назад) свежей не считается.
    return 0 <= now - fetched_at < _CACHE_TTL_SECONDS


async def _get_prices() -> list[dict] | str:
    """Возвращает список топлива (из кэша или с сайта) или строку с текстом ошибки.
    Если сайт не отвечает, а в кэше что-то есть, отдаёт устаревший кэш: время обновления
    цен печатается в ответе, так что дату видно."""
    global _cache, _cache_file_loaded, _last_failure
    if not _cache_file_loaded:
        _cache_file_loaded = True
        _cache = _load_cache_file()
    if _cache and _is_fresh(_cache[0], time.time()):
        return _cache[1]

    # Замок: две команды подряд не должны обе пойти на сайт; вторая дождётся первой
    # и возьмёт свежий кэш.
    async with _lock:
        now = time.time()
        if _cache and _is_fresh(_cache[0], now):
            return _cache[1]
        if _last_failure and 0 <= now - _last_failure[0] < _FAIL_COOLDOWN_SECONDS:
            error = _last_failure[1]
        else:
            result = await _download()
            if not isinstance(result, str):
                _cache = (time.time(), result)
                _last_failure = None
                _save_cache_file(*_cache)
                return result
            error = result
            _last_failure = (time.time(), error)

    if _cache:
        logger.warning(f"fuel: {error}; отдаю устаревший кэш (возраст {(time.time() - _cache[0]) / 60:.0f} мин)")
        return _cache[1]
    return error


async def _download() -> list[dict] | str:
    """Запрос к сайту: список топлива при успехе или строка с текстом ошибки."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible)", "Accept": "application/json"}
    payload = None
    reason = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            # Без прокси сознательно: trust_env=False не даёт aiohttp читать HTTPS_PROXY
            # из окружения, а proxy=None — подставить прокси из конфига.
            async with aiohttp.ClientSession(headers=headers, trust_env=False) as session:
                async with session.get(_URL, ssl=_get_ssl_ctx(), timeout=_TIMEOUT, proxy=None) as resp:
                    if resp.status == 404:
                        logger.error(f"fuel: станция {_STATION_ID} не найдена (404) — закрыта или сменила GPNAZSID?")
                        return "❌ АЗС не найдена на gpnbonus.ru"
                    if resp.status != 200:
                        return f"❌ gpnbonus.ru недоступен (код {resp.status})"
                    payload = await resp.json(content_type=None)
            break
        except Exception as e:
            # У asyncio.TimeoutError str(e) пустой — в ответе и логе была бы «Ошибка запроса: ».
            reason = "таймаут" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
            logger.warning(f"fuel: попытка {attempt}/{_ATTEMPTS} не удалась: {reason}: {e}")
            if attempt < _ATTEMPTS:
                await asyncio.sleep(_RETRY_PAUSE_SECONDS)
    if payload is None:
        return f"❌ gpnbonus.ru не отвечает ({reason})"

    fuels = _parse(payload)
    if not fuels:
        logger.warning(f"fuel: в ответе нет цен: {str(payload)[:300]!r}")
        return "❌ Нет данных о ценах"
    return fuels


def _format(fuels: list[dict], tz_offset: float) -> str:
    # Пары «вид цена» — по строке на вид, первая строка начинается со значка.
    pairs = "\n".join(f"{f['title']} {f['price']:.2f}" if f["avail"] else f"{f['title']} нет"
                      for f in fuels)
    text = f"⛽ {pairs}"
    # since — время обновления цены по UTC; у разных видов топлива может отличаться на секунды,
    # берём самое свежее.
    sinces = [f["since"] for f in fuels if f["since"]]
    if not sinces:
        return text
    updated = datetime.fromisoformat(max(sinces).replace("Z", "+00:00")) + timedelta(hours=tz_offset)
    return f"{text}\n({updated:%d.%m %H:%M})"


async def get_fuel(tz_offset: float = 6.0) -> str:
    result = await _get_prices()
    if isinstance(result, str):
        return result
    return _format(result, tz_offset)
