# Быстрый старт

## 1. Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Конфигурация

```bash
cp .env.example .env
```

Минимум — только `MESHCORE_PORT`. Найти порт автоматически:

```bash
python find_device.py
```

Или задать вручную в `.env`:

```
MESHCORE_PORT=/dev/tty.usbmodem101   # macOS
MESHCORE_PORT=/dev/ttyACM0           # Linux
MESHCORE_PORT=COM3                   # Windows
```

## 3. Запуск

```bash
python bot.py
```

## 4. Команды

Отправьте в любой канал или прямое сообщение:

```
/ping               → pong (Direct 📡)
/help               → список команд
/weather Москва     → текущая погода
```

## 5. Погодная рассылка (опционально)

Добавьте в `.env`:

```
OPENWEATHERMAP_API_KEY=ваш_ключ
WEATHER_CITY=Омск
WEATHER_CHANNEL_IDX=3
WEATHER_HOUR=7
WEATHER_MINUTE=30
WEATHER_TIMEZONE_OFFSET=6
```

Бот будет отправлять дневной прогноз каждый день в указанное время.

## 6. Docker (Linux)

```bash
docker-compose up -d
```

Порт устройства задаётся через `MESHCORE_PORT` в `.env`.
