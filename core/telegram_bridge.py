from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Any

import aiohttp
import certifi

from .commands import dispatch
from .msgsplit import split_msg
from .weather import to_lat

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/{method}"
_MAX_MESHCORE_LEN = 130
_POLL_TIMEOUT = 30


async def _get_updates(session: aiohttp.ClientSession, ssl_ctx: ssl.SSLContext,
                        token: str, offset: int | None) -> list[dict]:
    params = {"timeout": _POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    url = _API_URL.format(token=token, method="getUpdates")
    timeout = aiohttp.ClientTimeout(total=_POLL_TIMEOUT + 10)
    async with session.get(url, params=params, timeout=timeout, ssl=ssl_ctx) as resp:
        data = await resp.json()
        if not data.get("ok"):
            logger.error(f"   ❌ Telegram getUpdates: {data}")
            return []
        return data.get("result", [])


async def _send_telegram_message(session: aiohttp.ClientSession, ssl_ctx: ssl.SSLContext,
                                  token: str, chat_id: str, text: str) -> None:
    url = _API_URL.format(token=token, method="sendMessage")
    payload = {"chat_id": chat_id, "text": text}
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.post(url, json=payload, timeout=timeout, ssl=ssl_ctx) as resp:
        if resp.status != 200:
            body = await resp.text()
            logger.error(f"   ❌ Telegram sendMessage: код {resp.status}, {body}")


async def send_to_telegram(config: dict, text: str) -> None:
    tb = config.get("telegram_bridge") or {}
    token = tb.get("bot_token", "")
    chat_id = tb.get("chat_id", "")
    if not token or not chat_id:
        return
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession() as session:
            await _send_telegram_message(session, ssl_ctx, token, chat_id, text)
    except Exception as e:
        logger.error(f"   ❌ Ошибка отправки в Telegram: {e}")


async def telegram_bridge(mc: Any, config: dict) -> None:
    tb = config.get("telegram_bridge") or {}
    token = tb.get("bot_token", "")
    chat_id = tb.get("chat_id", "")
    channel_idx = tb.get("channel_idx")
    if not token or not chat_id or channel_idx is None:
        logger.info("ℹ️  Telegram-мост не настроен (нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID/TELEGRAM_CHANNEL_IDX)")
        return

    logger.info(f"🔗 Telegram-мост запущен: chat_id={chat_id} <-> channel_idx={channel_idx}")

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    offset: int | None = None

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                updates = await _get_updates(session, ssl_ctx, token, offset)
            except Exception as e:
                logger.error(f"   ❌ Ошибка опроса Telegram: {e}")
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1

                message = update.get("message")
                if not message:
                    continue
                if str(message.get("chat", {}).get("id")) != str(chat_id):
                    continue

                text = (message.get("text") or "").strip()
                if not text:
                    continue

                sender = message.get("from", {}).get("username") or \
                    message.get("from", {}).get("first_name", "")

                logger.info(f"📬 От Telegram ({sender}): '{text}'")

                try:
                    response = await dispatch(
                        text,
                        hops=0,
                        route_data=None,
                        weather_api_key=config.get("openweathermap_api_key", ""),
                        config=config,
                        mc=mc,
                    )
                except Exception as e:
                    logger.error(f"   ❌ Ошибка обработки команды из Telegram: {e}")
                    continue

                outgoing = response if response is not None else f"{sender}: {text}"
                outgoing = to_lat(outgoing)

                own_echoes = config.setdefault("_own_channel_echoes", {})
                for part in split_msg(outgoing, "", _MAX_MESHCORE_LEN):
                    try:
                        send_ts = int(time.time())
                        own_echoes[part] = send_ts
                        cutoff = send_ts - 60
                        for k in [k for k, v in own_echoes.items() if v < cutoff]:
                            del own_echoes[k]
                        await mc.commands.send_chan_msg(channel_idx, part)
                        logger.info(f"   📤 Telegram → MeshCore [{channel_idx}]: {part}")
                    except Exception as e:
                        logger.error(f"   ❌ Ошибка отправки в MeshCore: {e}")
                    time.sleep(2.0)
