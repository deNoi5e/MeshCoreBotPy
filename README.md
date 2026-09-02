# MeshCore Bot

Бот для мессенджера [MeshCore](https://github.com/meshcore-dev/MeshCore). Подключается к устройству по USB в режиме компаньона, слушает сообщения в каналах и прямые сообщения, отвечает на команды.

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
| `TRAFFIC_CHANNEL_IDX` | Индекс канала для рассылки пробок | `3` |
| `TRAFFIC_INTERVAL_MINUTES` | Интервал рассылки пробок (мин, не чаще 60; `0` — отключить) | `60` |
| `TRAFFIC_HOUR_FROM` | Начало окна рассылки пробок (местное время) | `7` |
| `TRAFFIC_HOUR_TO` | Конец окна рассылки пробок (местное время) | `19` |
| `MOON_LAT` | Широта для восхода/захода Луны | `54.9914` (Омск) |
| `MOON_LON` | Долгота для восхода/захода Луны | `73.3645` (Омск) |

Примеры `MESHCORE_PORT`: macOS — `/dev/tty.usbmodem101`, Linux — `/dev/ttyACM0`, Windows — `COM3`.

## Команды бота

| Команда | Ответ |
|---|---|
| `/ping` | `pong (Direct 📡)` или `pong (N хопов: ...)` |
| `/pingn` | Как `/ping`, но с именами узлов из контактов вместо префиксов |
| `/help` | Список команд |
| `/weather <город>` | Текущая погода |
| `/weathernow` | Дневной прогноз в канал погоды |
| `/rate` (`/kurs`) | Курсы USD/EUR/CNY к рублю по ЦБ РФ с изменением за день |
| `/ver` (`/version`) | Последние опубликованные версии прошивки MeshCore и мобильного приложения |
| `/moon` (`/luna`) | Фаза Луны, освещённость, возраст, восход/заход, ближайшие полнолуние и новолуние |
| `/mercury` (`/merc`, `/retro`) | Ретрограден ли Меркурий сейчас, даты ближайшей ретрограды и сколько до неё осталось |

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
