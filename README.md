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
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather | — |
| `TELEGRAM_CHAT_ID` | ID разрешённого Telegram-чата | — |
| `TELEGRAM_CHANNEL_IDX` | Индекс канала MeshCore, связанного с этим чатом | — |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Данные приложения с my.telegram.org (для user-аккаунта) | — |
| `TELEGRAM_USER_SESSION` | Путь к файлу сессии Telethon | `telegram_user.session` |
| `TELEGRAM_SOURCE_CHANNEL` | Публичный Telegram-канал для чтения (`@channel` или ссылка) | — |
| `TELEGRAM_USERFEED_CHANNEL_IDX` | Индекс канала MeshCore для сообщений из `TELEGRAM_SOURCE_CHANNEL` | — |

Примеры `MESHCORE_PORT`: macOS — `/dev/tty.usbmodem101`, Linux — `/dev/ttyACM0`, Windows — `COM3`.

Мост с Telegram (двусторонний, через Bot API) опционален: включается, если заданы `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`TELEGRAM_CHANNEL_IDX`.

Чтение чужого публичного канала (только приём, через user-аккаунт) — отдельная опция: включается, если заданы `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`/`TELEGRAM_SOURCE_CHANNEL`/`TELEGRAM_USERFEED_CHANNEL_IDX`. Перед первым запуском бота нужно один раз авторизоваться:

```bash
python scripts/telegram_login.py   # спросит номер телефона и код подтверждения
```

Это создаёт файл сессии (`TELEGRAM_USER_SESSION`), которым дальше пользуется бот без повторного ввода кода.

Пример — пересылка оповещений МЧС Омской области об угрозе БПЛА в отдельный канал тревог:

```
TELEGRAM_SOURCE_CHANNEL=@mchs_omsk
TELEGRAM_USERFEED_CHANNEL_IDX=<idx отдельного канала тревог>
```

Официального API у МЧС/РСЧС нет — единственный практичный способ автоматически получать эти оповещения — читать их публичный Telegram-канал.

## Команды бота

| Команда | Ответ |
|---|---|
| `/ping` | `pong (Direct 📡)` или `pong (N хопов: ...)` |
| `/pingn` | Как `/ping`, но с именами узлов из контактов вместо префиксов |
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
- **Мост с Telegram** — `telegram_bridge()` (`core/telegram_bridge.py`) работает параллельно через тот же `asyncio.gather()`. Сообщения из `TELEGRAM_CHAT_ID` идут через long polling Telegram Bot API, прогоняются через `dispatch()` (команды выполняются как обычно) и уходят в `TELEGRAM_CHANNEL_IDX`; сообщения из этого канала MeshCore пересылаются обратно в чат. Собственные сообщения бота не зацикливаются — они помечаются в общем `config["_own_channel_echoes"]`.
- **Чтение чужого канала** — `telegram_userfeed()` (`core/telegram_userfeed.py`) через user-аккаунт (Telethon) слушает публичный `TELEGRAM_SOURCE_CHANNEL`, куда бот не добавлен как участник, и пересылает сообщения в `TELEGRAM_USERFEED_CHANNEL_IDX`. Только приём — в исходный Telegram-канал бот не пишет.

## Дополнительно

- `docs/quickstart.md` — краткое руководство
- `docs/api.md` — справочник MeshCore API
- `docs/troubleshooting.md` — решение типовых проблем
- `scripts/` — отладочные скрипты (`debug_bot.py`) и примеры расширения (`examples.py`)
