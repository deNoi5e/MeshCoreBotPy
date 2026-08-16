"""Пересылка сообщений из канала MeshCore в Telegram группу."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    _AIOGRAM_AVAILABLE = True
except ImportError:
    _AIOGRAM_AVAILABLE = False
    logger.warning("⚠️  aiogram не установлен — Telegram relay недоступен (pip install aiogram)")


def _format_route(hops: int, route_data: dict | None) -> str:
    """Форматировать трассировку маршрута (как в /ping)."""
    if hops == 0:
        return "Direct 📡"
    if route_data:
        path = route_data.get("path", "")
        path_len = route_data.get("path_len", hops)
        path_hash_size = route_data.get("path_hash_size", 1)
        chars = path_hash_size * 2
        addrs = [path[i:i+chars] for i in range(0, len(path), chars)] if path else []
        if addrs:
            return f"{path_len} hops: {' → '.join(addrs)}"
    return f"{hops} hops"


class TelegramRelay:
    """Отправляет сообщения из MeshCore канала в Telegram топик."""

    def __init__(self, bot_token: str, group_id: int, topic_id: int | None,
                 meshcore_channel: int = 0):
        if not _AIOGRAM_AVAILABLE:
            raise RuntimeError("aiogram не установлен")
        self.bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.group_id = group_id
        self.topic_id = topic_id
        self.meshcore_channel = meshcore_channel
        logger.info(
            f"📨 Telegram relay инициализирован: группа={group_id}, "
            f"топик={topic_id}, MeshCore канал={meshcore_channel}"
        )

    async def relay(self, sender_name: str, text: str,
                    hops: int, route_data: dict | None) -> None:
        """Переслать сообщение в Telegram.

        Формат: <b>ИМЯ</b>: текст\n<i>via маршрут</i>
        """
        route_str = _format_route(hops, route_data)
        # Экранируем HTML спецсимволы в пользовательском тексте
        safe_name = _escape_html(sender_name)
        safe_text = _escape_html(text)
        message = f"<b>{safe_name}</b>: {safe_text}\n<i>via {route_str}</i>"

        kwargs: dict[str, Any] = {
            "chat_id": self.group_id,
            "text": message,
        }
        if self.topic_id:
            kwargs["message_thread_id"] = self.topic_id

        try:
            await self.bot.send_message(**kwargs)
            logger.info(f"📨 Telegram: переслано от {sender_name!r}: {text[:50]}")
        except Exception as e:
            logger.error(f"❌ Telegram relay ошибка: {e}")

    async def close(self) -> None:
        """Закрыть сессию бота."""
        try:
            await self.bot.session.close()
        except Exception:
            pass


def _escape_html(text: str) -> str:
    """Экранировать HTML спецсимволы для Telegram HTML parse mode."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def create_relay_from_config(config: dict) -> TelegramRelay | None:
    """Создать TelegramRelay из config или вернуть None если отключено/не настроено."""
    tg = config.get("telegram", {})
    if not tg.get("enabled", False):
        return None
    token = tg.get("bot_token", "")
    group_id = tg.get("group_id")
    if not token or not group_id:
        logger.warning("⚠️  Telegram relay: TELEGRAM_BOT_TOKEN или TELEGRAM_GROUP_ID не заданы")
        return None
    if not _AIOGRAM_AVAILABLE:
        logger.warning("⚠️  Telegram relay: aiogram не установлен")
        return None
    topic_id = tg.get("topic_id")
    meshcore_channel = tg.get("meshcore_channel", 0)
    return TelegramRelay(
        bot_token=token,
        group_id=group_id,
        topic_id=topic_id,
        meshcore_channel=meshcore_channel,
    )
