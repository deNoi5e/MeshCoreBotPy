#!/usr/bin/env python3
"""
Скрипт для отладки — показывает все события от устройства
"""

import asyncio
import logging
import os
from dotenv import load_dotenv
from meshcore import MeshCore, events

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s.%(msecs)03d] - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y.%m.%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


async def main():
    port = os.environ["MESHCORE_PORT"]
    baudrate = int(os.environ.get("MESHCORE_BAUDRATE", "115200"))

    logger.info(f"Подключение к {port}...")

    # Подключиться
    mc = await MeshCore.create_serial(
        port=port,
        baudrate=baudrate,
        auto_reconnect=True,
        debug=True  # Включить debug логирование
    )

    await mc.connect()
    logger.info("✓ Подключено\n")

    # Обработчик событий
    def on_event(event: events.Event):
        print("\n" + "="*60)
        print(f"📨 СОБЫТИЕ: {event.type}")
        print(f"   Атрибуты: {event.attributes}")
        # Выводим полный объект события для поиска информации о маршруте
        if hasattr(event, 'payload'):
            print(f"   Payload: {event.payload}")
        if hasattr(event, '__dict__'):
            print(f"   Все поля события: {event.__dict__}")
        print("="*60 + "\n")

    # Подписаться на ВСЕ события
    subscription = mc.subscribe(None, on_event)

    # Запустить загрузку сообщений
    await mc.start_auto_message_fetching()
    logger.info("🤖 Слушаю события...\n")
    logger.info("Отправьте сообщение в канал или напишите прямое сообщение\n")

    # Основной цикл
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n\nОстановка...")
    finally:
        await mc.stop_auto_message_fetching()
        mc.unsubscribe(subscription)
        await mc.disconnect()
        logger.info("✓ Отключено")


if __name__ == "__main__":
    asyncio.run(main())
