from __future__ import annotations

import asyncio
import logging
import re
import ssl
import traceback
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs

import aiohttp
import certifi

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org/bot{token}"
_TG_SEND = _TG_API_BASE + "/sendMessage"
_TG_GET_UPDATES = _TG_API_BASE + "/getUpdates"

# Локальный порт для MTProto→SOCKS5 конвертера
_MTPROTO_LOCAL_SOCKS5_PORT = 1080


@dataclass
class ProxyConfig:
    """Настройки прокси для Telegram."""
    proxy_type: str        # none | socks5 | mtproto
    host: str
    port: int
    user: str              # логин для SOCKS5 (пустой если не нужен)
    password: str          # пароль для SOCKS5 (пустой если не нужен)


@dataclass
class TgRelayConfig:
    enabled: bool
    token: str
    chat_id: str            # ID группы
    topic_id: int | None    # message_thread_id топика (None — без топика)
    channel_idx: int        # канал MeshCore → Telegram
    to_mesh_channel: int    # канал MeshCore ← Telegram (сообщения со *)
    proxy: ProxyConfig      # настройки прокси


def _parse_mtproto_url(url: str) -> tuple[str, int, str]:
    """Разобрать tg://proxy?server=...&port=...&secret=...
    Возвращает (host, port, secret).
    """
    # Парсим как обычный URL заменяя tg:// на http://
    normalized = url.replace("tg://proxy?", "http://proxy?", 1)
    parsed = urlparse(normalized)
    params = parse_qs(parsed.query)
    host = params.get("server", ["127.0.0.1"])[0]
    port = int(params.get("port", ["443"])[0])
    secret = params.get("secret", [""])[0]
    return host, port, secret


def make_tg_relay_config(env: dict) -> TgRelayConfig:
    """Собрать конфиг из переменных окружения."""
    enabled = env.get("TG_RELAY_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    token = env.get("TG_BOT_TOKEN", "").strip()
    chat_id = env.get("TG_CHAT_ID", "").strip()
    topic_raw = env.get("TG_TOPIC_ID", "").strip()
    topic_id = int(topic_raw) if topic_raw.lstrip("-").isdigit() else None
    try:
        channel_idx = int(env.get("TG_RELAY_CHANNEL", "0").strip())
    except ValueError:
        channel_idx = 0
    try:
        to_mesh_channel = int(env.get("TG_TO_MESH_CHANNEL", "0").strip())
    except ValueError:
        to_mesh_channel = 0

    # Прокси
    proxy_type = env.get("TG_PROXY_TYPE", "none").strip().lower()
    proxy_host = env.get("TG_PROXY_HOST", "").strip()
    proxy_port_raw = env.get("TG_PROXY_PORT", "0").strip()

    try:
        proxy_port = int(proxy_port_raw) if proxy_port_raw else 0
    except ValueError:
        proxy_port = 0

    if proxy_type == "mtproto":
        url = env.get("TG_PROXY_URL", "").strip()
        if url:
            proxy_host, proxy_port, _ = _parse_mtproto_url(url)
        proxy = ProxyConfig(proxy_type="mtproto", host=proxy_host, port=proxy_port, user="", password="")
    elif proxy_type == "socks5":
        user = env.get("TG_PROXY_USER", "").strip()
        password = env.get("TG_PROXY_PASS", "").strip()
        proxy = ProxyConfig(proxy_type="socks5", host=proxy_host, port=proxy_port, user=user, password=password)
    else:
        proxy = ProxyConfig(proxy_type="none", host="", port=0, user="", password="")

    return TgRelayConfig(
        enabled=enabled,
        token=token,
        chat_id=chat_id,
        topic_id=topic_id,
        channel_idx=channel_idx,
        to_mesh_channel=to_mesh_channel,
        proxy=proxy,
    )


def _make_connector(cfg: TgRelayConfig):
    """Создать aiohttp коннектор с учётом прокси."""
    proxy_type = cfg.proxy.proxy_type

    if proxy_type == "socks5":
        try:
            from aiohttp_socks import ProxyConnector
            if cfg.proxy.user and cfg.proxy.password:
                proxy_url = f"socks5://{cfg.proxy.user}:{cfg.proxy.password}@{cfg.proxy.host}:{cfg.proxy.port}"
            else:
                proxy_url = f"socks5://{cfg.proxy.host}:{cfg.proxy.port}"
            logger.debug(f"🔌 TG proxy: SOCKS5 {cfg.proxy.host}:{cfg.proxy.port}")
            return ProxyConnector.from_url(proxy_url), None

        except ImportError:
            logger.error("❌ aiohttp-socks не установлен. Запустите: pip install aiohttp-socks")
            return aiohttp.TCPConnector(), None

    elif proxy_type == "socks4":
        try:
            from aiohttp_socks import ProxyConnector
            if cfg.proxy.user:
                proxy_url = f"socks4://{cfg.proxy.user}@{cfg.proxy.host}:{cfg.proxy.port}"
            else:
                proxy_url = f"socks4://{cfg.proxy.host}:{cfg.proxy.port}"
            logger.debug(f"🔌 TG proxy: SOCKS4 {cfg.proxy.host}:{cfg.proxy.port}")
            return ProxyConnector.from_url(proxy_url), None
        except ImportError:
            logger.error("❌ aiohttp-socks не установлен.")
            return aiohttp.TCPConnector(), None

    elif proxy_type == "http":
        # aiohttp нативно поддерживает HTTP прокси через параметр proxy=
        if cfg.proxy.user and cfg.proxy.password:
            proxy_url = f"http://{cfg.proxy.user}:{cfg.proxy.password}@{cfg.proxy.host}:{cfg.proxy.port}"
        else:
            proxy_url = f"http://{cfg.proxy.host}:{cfg.proxy.port}"
        logger.debug(f"🔌 TG proxy: HTTP {cfg.proxy.host}:{cfg.proxy.port}")
        return aiohttp.TCPConnector(), proxy_url

    elif proxy_type == "mtproto":
        try:
            from aiohttp_socks import ProxyConnector
            proxy_url = f"socks5://127.0.0.1:{_MTPROTO_LOCAL_SOCKS5_PORT}"
            logger.debug(f"🔌 TG proxy: MTProto→SOCKS5 127.0.0.1:{_MTPROTO_LOCAL_SOCKS5_PORT}")
            return ProxyConnector.from_url(proxy_url), None
        except ImportError:
            logger.error("❌ aiohttp-socks не установлен. Запустите: pip install aiohttp-socks")
            return aiohttp.TCPConnector(), None

    return aiohttp.TCPConnector(), None


def format_route(hops: int, route_data: dict | None) -> str:
    """Сформировать строку трассировки как в /ping."""
    if hops == 0:
        return "Direct"
    if route_data:
        path = route_data.get("path", "")
        path_len = route_data.get("path_len", hops)
        path_hash_size = route_data.get("path_hash_size", 1)
        chars = path_hash_size * 2
        addrs = [path[i:i+chars] for i in range(0, len(path), chars)] if path else []
        if addrs:
            return f"{path_len} hops: {' → '.join(addrs)}"
        return f"{path_len} hops"
    return f"{hops} hops"


def _get_sender_name(msg: dict) -> str:
    """Получить имя отправителя из Telegram сообщения."""
    user = msg.get("from") or {}
    first = user.get("first_name", "")
    last = user.get("last_name", "")
    username = user.get("username", "")
    name = f"{first} {last}".strip()
    return name if name else username if username else "TG"


async def _tg_post(session: aiohttp.ClientSession, url: str, payload: dict,
                   ssl_ctx, cfg: TgRelayConfig, proxy_url: str | None = None) -> dict | None:
    """Выполнить POST к Telegram API с обработкой миграции группы."""
    for attempt in range(2):
        try:
            post_kwargs = dict(
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ssl_ctx,
            )
            if proxy_url:
                post_kwargs["proxy"] = proxy_url
            async with session.post(url, **post_kwargs) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 200:
                    return data
                new_id = data.get("parameters", {}).get("migrate_to_chat_id")
                if resp.status == 400 and new_id:
                    logger.warning(f"⚠️  TG relay: группа мигрировала, новый ID: {new_id}")
                    cfg.chat_id = str(new_id)
                    payload["chat_id"] = cfg.chat_id
                    continue
                logger.error(
                    f"❌ TG API ошибка: HTTP {resp.status}\n"
                    f"   ответ: {data}\n"
                    f"   chat_id: {cfg.chat_id}, topic_id: {cfg.topic_id}"
                )
                return None
        except aiohttp.ClientConnectorError as e:
            logger.error(
                f"❌ TG relay: не удалось подключиться к Telegram API [{type(e).__name__}]: {e}\n"
                f"   прокси: {cfg.proxy.proxy_type} "
                f"{cfg.proxy.host+':'+str(cfg.proxy.port) if cfg.proxy.proxy_type == 'socks5' else '127.0.0.1:1080'}\n"
                f"   Проверьте настройки прокси в .env"
            )
            return None
        except asyncio.TimeoutError:
            logger.error(
                f"❌ TG relay: таймаут подключения к Telegram API (попытка {attempt+1}/2)\n"
                f"   прокси: {cfg.proxy.proxy_type} "
                f"{cfg.proxy.host+':'+str(cfg.proxy.port) if cfg.proxy.proxy_type == 'socks5' else ''}"
            )
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            return None
    return None


async def relay_to_telegram(
    sender_name: str,
    text: str,
    hops: int,
    route_data: dict | None,
    cfg: TgRelayConfig,
) -> None:
    """Переслать сообщение из MeshCore канала в Telegram группу/топик."""
    if not cfg.enabled:
        return
    if not cfg.token or not cfg.chat_id:
        logger.warning("⚠️  TG relay: TG_BOT_TOKEN или TG_CHAT_ID не заданы")
        return

    route_str = format_route(hops, route_data)

    import re as _re

    def fix_reply_mentions(s: str) -> str:
        """Заменить \\\\Name\\\\ (reply-упоминание MeshCore) на @[Name]."""
        return _re.sub(r'\\\\(.+?)\\\\', lambda m: f'@[{m.group(1)}]', s)

    def escape_md(s: str) -> str:
        """Экранировать спецсимволы для MarkdownV2.
        Обратный слеш обрабатывается первым, чтобы не экранировать
        уже добавленные escape-последовательности повторно.
        """
        # '\' должен идти первым
        s = s.replace('\\', '\\\\')
        for ch in r'_*[]()~`>#+-=|{}.!':
            s = s.replace(ch, f'\\{ch}')
        return s

    # Обработать reply-упоминания до экранирования
    text = fix_reply_mentions(text)

    sender_esc = escape_md(sender_name)
    text_esc = escape_md(text)
    route_esc = escape_md(route_str)
    message = f"*{sender_esc}*\n{text_esc}\n_via \\[{route_esc}\\]_"
    # Plain text версия для логов
    message_plain = f"{sender_name}\n{text}\nvia [{route_str}]"

    payload: dict = {
        "chat_id": cfg.chat_id,
        "text": message,
        "parse_mode": "MarkdownV2",
        "disable_notification": False,
    }
    if cfg.topic_id is not None:
        payload["message_thread_id"] = cfg.topic_id

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector, proxy_url = _make_connector(cfg)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            result = await _tg_post(session, _TG_SEND.format(token=cfg.token), payload, ssl_ctx, cfg, proxy_url)
            if result:
                logger.info(f"📨 MeshCore→TG: «{message_plain[:60]}»")
    except Exception as e:
        logger.error(
            f"❌ TG relay ошибка [{type(e).__name__}]: {e}\n"
            f"   прокси: {cfg.proxy.proxy_type}, chat: {cfg.chat_id}\n"
            f"   сообщение: {message_plain[:60]}\n"
            f"   {traceback.format_exc().strip()}"
        )


async def tg_poll_loop(cfg: TgRelayConfig, mc) -> None:
    """Фоновый long-polling loop: читает updates из Telegram и пересылает
    сообщения начинающиеся с '*' в MeshCore канал cfg.to_mesh_channel.
    """
    if not cfg.enabled:
        return
    if not cfg.token:
        logger.warning("⚠️  TG poll: TG_BOT_TOKEN не задан")
        return

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    offset = 0
    _retry_delay = 5
    proxy_info = f" (прокси: {cfg.proxy.proxy_type})" if cfg.proxy.proxy_type != "none" else ""
    logger.info(f"📩 TG poll loop запущен{proxy_info} (канал MeshCore ← TG: {cfg.to_mesh_channel})")

    # Валидация конфига
    if cfg.proxy.proxy_type in ("socks5", "socks4", "http"):
        if not cfg.proxy.host or not cfg.proxy.port:
            logger.error(f"❌ TG poll: TG_PROXY_HOST или TG_PROXY_PORT не заданы для {cfg.proxy.proxy_type}")
            return
        auth = f" (user: {cfg.proxy.user})" if cfg.proxy.user else ""
        logger.info(f"🔌 TG {cfg.proxy.proxy_type.upper()} прокси: {cfg.proxy.host}:{cfg.proxy.port}{auth}")
    elif cfg.proxy.proxy_type == "mtproto":
        logger.info(f"🔌 TG MTProto прокси через локальный SOCKS5 127.0.0.1:{_MTPROTO_LOCAL_SOCKS5_PORT}")
    if cfg.chat_id and not cfg.chat_id.lstrip("-").isdigit():
        logger.warning(f"⚠️  TG chat_id={cfg.chat_id!r} выглядит неверно (должно быть числом)")
    elif cfg.chat_id and not cfg.chat_id.startswith("-"):
        logger.warning(f"⚠️  TG chat_id={cfg.chat_id} — положительное число. Для групп обычно отрицательное")

    while True:
        connector, proxy_url = _make_connector(cfg)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                url = _TG_GET_UPDATES.format(token=cfg.token)
                params = {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }
                get_kwargs = dict(
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=40),
                    ssl=ssl_ctx,
                )
                if proxy_url:
                    get_kwargs["proxy"] = proxy_url
                async with session.get(url, **get_kwargs) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            f"❌ TG poll: HTTP {resp.status}\n"
                            f"   ответ: {body[:200]}\n"
                            f"   прокси: {cfg.proxy.proxy_type}"
                        )
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json(content_type=None)

            if not data.get("ok"):
                logger.error(
                    f"❌ TG poll: API вернул ошибку\n"
                    f"   ответ: {data}"
                )
                await asyncio.sleep(_retry_delay)
                continue

            # Успешный ответ — сбрасываем задержку
            _retry_delay = 5

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue

                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id != cfg.chat_id:
                    continue

                text = msg.get("text", "").strip()
                if not text.startswith("*"):
                    continue

                mesh_text = text[1:].strip()
                if not mesh_text:
                    continue

                sender_name = _get_sender_name(msg)
                mesh_msg = f"[{sender_name}]\n{mesh_text}"

                encoded = mesh_msg.encode("utf-8")
                if len(encoded) > 143:
                    mesh_msg = encoded[:143].decode("utf-8", errors="ignore")

                logger.info(f"📩 TG→MeshCore ch{cfg.to_mesh_channel}: «{mesh_msg}»")
                try:
                    await mc.commands.send_chan_msg(cfg.to_mesh_channel, mesh_msg)
                except Exception as e:
                    logger.error(f"❌ TG→MeshCore ошибка отправки: {e}")

        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            logger.info("📩 TG poll loop остановлен")
            return
        except aiohttp.ClientOSError as e:
            # WinError 121 — SOCKS5 прокси не отвечает или сеть недоступна
            proxy_addr = f"{cfg.proxy.host}:{cfg.proxy.port}" if cfg.proxy.proxy_type == "socks5" else "127.0.0.1:1080"
            logger.warning(
                f"⚠️  TG poll: ошибка сети [{type(e).__name__}]: {e}\n"
                f"   прокси: {cfg.proxy.proxy_type} {proxy_addr}\n"
                f"   Повтор через {_retry_delay:.0f} сек..."
            )
            await asyncio.sleep(_retry_delay)
            _retry_delay = min(_retry_delay * 2, 60)  # экспоненциальный backoff до 60 сек
            continue
        except aiohttp.ClientConnectorError as e:
            proxy_addr = f"{cfg.proxy.host}:{cfg.proxy.port}" if cfg.proxy.proxy_type == "socks5" else "127.0.0.1:1080"
            logger.warning(
                f"⚠️  TG poll: не удалось подключиться [{type(e).__name__}]: {e}\n"
                f"   прокси: {cfg.proxy.proxy_type} {proxy_addr}\n"
                f"   Повтор через {_retry_delay:.0f} сек..."
            )
            await asyncio.sleep(_retry_delay)
            _retry_delay = min(_retry_delay * 2, 60)
            continue
        except Exception as e:
            logger.error(
                f"❌ TG poll ошибка [{type(e).__name__}]: {e}\n"
                f"   прокси: {cfg.proxy.proxy_type}\n"
                f"   {traceback.format_exc().strip()}"
            )
            await asyncio.sleep(_retry_delay)
            _retry_delay = min(_retry_delay * 2, 60)
            continue
        # Успешный цикл — сбрасываем задержку
        _retry_delay = 5
