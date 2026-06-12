# MeshCore Bot

Бот для мессенджера [MeshCore](https://github.com/deeplay-io/meshcore). Подключается к устройству по USB в режиме компаньона, слушает сообщения в каналах и прямые сообщения, отвечает на команды.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируйте .env: задайте MESHCORE_PORT и прочие параметры

python bot.py
```

Для автоопределения USB-порта:

```bash
python find_device.py
```

## Конфигурация

Все настройки — через `.env` (см. `.env.example`):

| Переменная | Описание | Умолчание |
|---|---|---|
| `MESHCORE_PORT` | USB-порт устройства | — (обязательно) |
| `OPENWEATHERMAP_API_KEY` | Ключ OpenWeatherMap | — |
| `WEATHER_CITY` | Город для рассылки погоды | `Omsk` |
| `WEATHER_CHANNEL_IDX` | Индекс канала для погоды | `3` |
| `WEATHER_HOUR` | Час рассылки (местное время) | `7` |
| `WEATHER_MINUTE` | Минута рассылки | `30` |
| `WEATHER_TIMEZONE_OFFSET` | Смещение часового пояса (ч) | `6` |

Примеры `MESHCORE_PORT`: macOS — `/dev/tty.usbmodem101`, Linux — `/dev/ttyACM0`, Windows — `COM3`.

## Команды бота

| Команда | Ответ |
|---|---|
| `/ping` | `pong (Direct 📡)` или `pong (N хопов: ...)` |
| `/help` | Список команд |
| `/weather <город>` | Текущая погода |
| `/weathernow` | Дневной прогноз в канал погоды |

## Docker

```bash
# отредактируйте .env, затем:
docker-compose up -d
```

## Архитектура

`bot.py` — весь бот, одна `async def main()`. Ключевые детали:

- **Дедупликация** — `processed_messages: set`, ключ `"{source_key}:{sender_timestamp}:{text}"`
- **Маршруты** — `route_cache: dict`, заполняется событиями `RX_LOG_DATA`, используется в `/ping`; записи старше 30 с вычищаются
- **Главный цикл** — `asyncio.wait(FIRST_COMPLETED)` на `CONTACT_MSG_RECV` и `CHANNEL_MSG_RECV`
- **Каналы** — сообщение приходит как `"Имя: /команда"`, парсится по первому `:`
- **Прямые сообщения** — перед ответом отправляется пустой ACK
- **Рассылка погоды** — `weather_broadcast_scheduler()` работает параллельно с `listen()` через `asyncio.gather()`

## Дополнительно

- `docs/quickstart.md` — краткое руководство
- `docs/api.md` — справочник MeshCore API
- `docs/troubleshooting.md` — решение типовых проблем
- `scripts/` — отладочные скрипты (`debug_bot.py`) и примеры расширения (`examples.py`)
