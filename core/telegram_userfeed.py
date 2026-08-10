from __future__ import annotations

import logging
import time
from typing import Any

from telethon import TelegramClient, events as tg_events

from .msgsplit import split_msg
from .weather import to_lat

logger = logging.getLogger(__name__)

_MAX_MESHCORE_LEN = 130


async def telegram_userfeed(mc: Any, config: dict) -> None:
    """
    Читает сообщения из чужого публичного Telegram-канала через
    user-аккаунт (Telethon) и пересылает их в канал MeshCore.

    Только чтение: бот не пишет в исходный Telegram-канал, только
    ретранслирует в MeshCore. Не путать с core/telegram_bridge.py —
    тот работает через Bot API и требует, чтобы бот был участником чата.
    """
    uf = config.get("telegram_userfeed") or {}
    api_id = uf.get("api_id")
    api_hash = uf.get("api_hash")
    session_path = uf.get("session_path", "telegram_user.session")
    source_channel = uf.get("source_channel", "")
    channel_idx = uf.get("channel_idx")

    if not api_id or not api_hash or not source_channel or channel_idx is None:
        logger.info("ℹ️  Telegram-userfeed не настроен (нет TELEGRAM_API_ID/TELEGRAM_API_HASH/"
                    "TELEGRAM_SOURCE_CHANNEL/TELEGRAM_USERFEED_CHANNEL_IDX)")
        return

    client = TelegramClient(session_path, int(api_id), api_hash)

    @client.on(tg_events.NewMessage(chats=source_channel))
    async def _on_message(event: tg_events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return

        sender = await event.get_sender()
        sender_name = getattr(sender, "username", None) or getattr(sender, "title", None) or "?"

        logger.info(f"📬 От Telegram-канала «{source_channel}» ({sender_name}): '{text}'")

        outgoing = to_lat(f"{sender_name}: {text}")
        for part in split_msg(outgoing, "", _MAX_MESHCORE_LEN):
            try:
                await mc.commands.send_chan_msg(channel_idx, part)
                logger.info(f"   📤 Telegram-канал → MeshCore [{channel_idx}]: {part}")
            except Exception as e:
                logger.error(f"   ❌ Ошибка отправки в MeshCore: {e}")
            time.sleep(2.0)

    await client.start()
    logger.info(f"🔗 Telegram-userfeed запущен: канал «{source_channel}» -> channel_idx={channel_idx}")
    await client.run_until_disconnected()
