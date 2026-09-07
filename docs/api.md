# API MeshCore — Подробное руководство

## ⚠️ Важно: Асинхронный API

**Все методы MeshCore асинхронные!** Используйте `async/await` и `asyncio.run()`.

## Основы

### Инициализация подключения

```python
import asyncio
from meshcore import MeshCore

async def main():
    # Подключиться по USB (асинхронно!)
    mc = await MeshCore.create_serial(
        port="/dev/tty.usbmodem101",
        baudrate=115200,
        auto_reconnect=True
    )

    # Установить соединение (асинхронно!)
    await mc.connect()
    
    # ... использовать mc ...
    
    await mc.disconnect()

asyncio.run(main())
```

### События

MeshCore использует систему событий для уведомления о происходящих изменениях:

```python
from meshcore import events

def on_event(event: events.Event):
    print(f"Событие: {event.type}")
    print(f"Атрибуты: {event.attributes}")

# Подписаться на ВСЕ события
subscription = mc.subscribe(None, on_event)

# Или на конкретный тип события
subscription = mc.subscribe(events.EventType.CHANNEL_MSG_RECV, on_event)
```

## Основные типы событий

### Подключение

- **EventType.CONNECTED** — устройство подключено
- **EventType.DISCONNECTED** — устройство отключено
- **EventType.ERROR** — произошла ошибка

```python
def on_event(event):
    if event.type == events.EventType.CONNECTED:
        print("✓ Устройство подключено")
    elif event.type == events.EventType.DISCONNECTED:
        print("✗ Устройство отключено")
```

### Сообщения

- **EventType.CHANNEL_MSG_RECV** — сообщение в канале
  - Атрибуты: `channel` (публичный ключ), `message` (текст)

- **EventType.CONTACT_MSG_RECV** — прямое сообщение
  - Атрибуты: `contact` (публичный ключ), `message` (текст)

```python
def on_event(event):
    if event.type == events.EventType.CHANNEL_MSG_RECV:
        channel = event.attributes.get("channel")
        message = event.attributes.get("message")
        print(f"Сообщение в канале {channel[:8]}: {message}")
    
    elif event.type == events.EventType.CONTACT_MSG_RECV:
        contact = event.attributes.get("contact")
        message = event.attributes.get("message")
        print(f"Личное сообщение от {contact[:8]}: {message}")
```

### Контакты и каналы

- **EventType.NEW_CONTACT** — новый контакт
- **EventType.CONTACTS** — список контактов
- **EventType.CHANNEL_INFO** — информация о канале

```python
def on_event(event):
    if event.type == events.EventType.NEW_CONTACT:
        contact_info = event.attributes.get("contact")
        print(f"Новый контакт: {contact_info}")
```

## Отправка сообщений

### Отправка в канал

```python
# Отправить сообщение в канал (асинхронно!)
channel_public_key = "ваш_публичный_ключ_канала"
message_text = "Hello, World!"

await mc.send_text(message_text, channel_public_key)
```

### Асинхронная отправка из обработчика события

Обработчик события синхронный, но можно запустить асинхронную задачу:

```python
def on_event(self, event: events.Event):
    if event.type == events.EventType.CHANNEL_MSG_RECV:
        channel = event.attributes.get("channel")
        message = event.attributes.get("message")
        
        # Отправить асинхронно в фоне
        asyncio.create_task(self.mc.send_text(f"Got: {message}", channel))
```

## Автоматическая загрузка сообщений

```python
# Запустить автоматическую загрузку сообщений
mc.start_auto_message_fetching()

# Теперь события CHANNEL_MSG_RECV и CONTACT_MSG_RECV будут приходить автоматически

# Остановить загрузку
mc.stop_auto_message_fetching()
```

## Работа с контактами

```python
# Получить контакт по имени
contact = mc.get_contact_by_name("Alice")
print(contact)

# Получить контакт по префиксу публичного ключа
contact = mc.get_contact_by_key_prefix("abc123")
print(contact)

# Получить все контакты
all_contacts = mc.contacts
for contact in all_contacts:
    print(f"Контакт: {contact}")
```

## Полный пример: Echo бот

```python
import asyncio
from meshcore import MeshCore, events

class EchoBot:
    def __init__(self, port: str):
        self.mc = None
        self.port = port
    
    def on_event(self, event: events.Event):
        if event.type == events.EventType.CHANNEL_MSG_RECV:
            channel = event.attributes.get("channel")
            message = event.attributes.get("message")
            
            # Echo сообщение обратно в канал (асинхронно)
            asyncio.create_task(self._echo(message, channel))
    
    async def _echo(self, message: str, channel: str):
        await self.mc.send_text(f"Echo: {message}", channel)
    
    async def run(self):
        self.mc = await MeshCore.create_serial(
            port=self.port,
            auto_reconnect=True
        )
        await self.mc.connect()
        
        self.mc.subscribe(None, self.on_event)
        self.mc.start_auto_message_fetching()
        
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.mc.stop_auto_message_fetching()
            await self.mc.disconnect()

async def main():
    bot = EchoBot("/dev/tty.usbmodem101")
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
```

## Обработка ошибок

```python
from meshcore import events

def on_event(event):
    if event.type == events.EventType.ERROR:
        error_info = event.attributes
        print(f"❌ Ошибка: {error_info}")
    
    elif event.type == events.EventType.DISCONNECTED:
        print("⚠️ Соединение потеряно. Переподключение...")

# Бот с auto_reconnect=True будет автоматически переподключаться
```

## Отладка

### Включение debug режима

```python
mc = await MeshCore.create_serial(
    port="/dev/tty.usbmodem101",
    debug=True,  # Включить debug логи
    only_error=False  # Показывать все логи, не только ошибки
)
```

### Логирование

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("meshcore")
```

### Посмотреть все события от устройства

Создайте скрипт `debug_events.py`:

```python
import asyncio
from meshcore import MeshCore, events
import sys

async def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/tty.usbmodem101"
    
    mc = await MeshCore.create_serial(port=port, debug=True)
    await mc.connect()
    
    def on_event(event):
        print(f"📨 {event.type}: {event.attributes}")
    
    mc.subscribe(None, on_event)
    mc.start_auto_message_fetching()
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        mc.stop_auto_message_fetching()
        await mc.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
```

Запустите:
```bash
python debug_events.py /dev/tty.usbmodem101
```

### Проверить соединение

```python
import asyncio
from meshcore import MeshCore

async def check():
    mc = await MeshCore.create_serial("/dev/tty.usbmodem101")
    await mc.connect()
    
    if mc.is_connected:
        print("✓ Соединение установлено")
        print(f"Информация устройства: {mc.self_info}")
    else:
        print("✗ Соединение не установлено")
    
    await mc.disconnect()

asyncio.run(check())
```

## Полезные методы MeshCore

```python
# Состояние подключения
if mc.is_connected:
    print("Подключено")

# Информация об устройстве
print(mc.self_info)

# Текущее время устройства
print(mc.time)

# Отключиться (асинхронно!)
await mc.disconnect()

# Мягко остановить бота
mc.stop()
```

## Ограничения и особенности

1. **Обработчик события синхронный** — используйте `asyncio.create_task()` для асинхронных операций
2. **Прямые сообщения** — API для отправки прямых сообщений требует дополнительного исследования
3. **Таймауты** — можно установить `default_timeout` при создании подключения
4. **Максимум переподключений** — установить через `max_reconnect_attempts`

## Дополнительные ресурсы

- Документация meshcore: https://pypi.org/project/meshcore
- Исходный код библиотеки: https://github.com/fdlamotte/meshcore_py
- Проект MeshCore (прошивка): https://github.com/meshcore-dev/MeshCore
