#!/usr/bin/env python3
"""
Одноразовая авторизация user-аккаунта Telegram (Telethon) для чтения чужих
публичных каналов, куда бот не добавлен как участник/админ.

Требуется API ID/HASH с https://my.telegram.org/apps (раздел "API development
tools") — это НЕ токен бота, а данные обычного Telegram-приложения.

Запуск (из корня проекта, с активированным .venv):
    python scripts/telegram_login.py

Скрипт запросит номер телефона и код подтверждения (одноразово) и сохранит
сессию в файл, заданный TELEGRAM_USER_SESSION (по умолчанию telegram_user.session).
После этого core/telegram_userfeed.py сможет использовать сессию без
интерактивного ввода.
"""

import os

from dotenv import load_dotenv
from telethon.sync import TelegramClient

load_dotenv()


def main():
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session_path = os.environ.get("TELEGRAM_USER_SESSION", "telegram_user.session")

    if not api_id or not api_hash:
        print("❌ Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")
        print("   Получить можно на https://my.telegram.org/apps")
        return

    with TelegramClient(session_path, int(api_id), api_hash) as client:
        me = client.get_me()
        print(f"✓ Авторизовано как: {me.first_name} (@{me.username or me.id})")
        print(f"✓ Сессия сохранена в {session_path}")
        print("  Дальше бот сможет использовать её без повторного ввода кода.")


if __name__ == "__main__":
    main()
