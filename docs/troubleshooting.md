# Решение проблем

## Ошибки подключения

### "No module named 'dotenv'" / "ModuleNotFoundError"

Виртуальное окружение не активировано или зависимости не установлены:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### "KeyError: 'MESHCORE_PORT'"

Переменная не задана. Создайте `.env` из шаблона:

```bash
cp .env.example .env
# задайте MESHCORE_PORT в .env
```

### "Permission denied" на USB-порт

**macOS:** обычно не требует дополнительных прав — проверьте, что кабель подключён.

**Linux:** добавьте пользователя в группу `dialout`:

```bash
sudo usermod -a -G dialout $USER
newgrp dialout
```

### "Cannot import name 'MeshCore'"

Устаревшая версия библиотеки:

```bash
pip install -r requirements.txt --upgrade
```

## Проблемы с сообщениями

### Бот подключился, но не реагирует на команды

1. Убедитесь, что устройство находится в **режиме компаньона**
2. Запустите `scripts/debug_bot.py` чтобы увидеть поток событий:
   ```bash
   python scripts/debug_bot.py
   ```
3. Проверьте, что команда отправляется в нужный канал (не в канал погоды)

### Сообщения приходят дважды

Встроенная дедупликация работает по `"{source_key}:{sender_timestamp}:{text}"`. Если видите дубли в логах — это разные `sender_timestamp`.

### `/ping` показывает неверное число хопов

`path_len=255` означает прямую видимость (Direct). Маршрут берётся из `RX_LOG_DATA` с допуском ±5 с по `sender_timestamp`. Если маршрут не найден в кэше — покажет только число хопов без пути.

## Отладка

### Все события устройства

```bash
python scripts/debug_bot.py
```

### Повышение уровня логов

В `bot.py` замените `logging.INFO` на `logging.DEBUG`.

## Частые проблемы

| Проблема | Решение |
|---|---|
| Бот не находит `.env` | Запускайте из корня проекта: `python bot.py` |
| Устройство отключается | Включите `auto_reconnect=True` в `MeshCore.create_serial()` |
| Погода не отправляется | Проверьте `OPENWEATHERMAP_API_KEY` и `WEATHER_CHANNEL_IDX` |
| Кириллица в ответах бота | Норма — `to_lat()` заменяет визуальные омоглифы на ASCII для экономии байт |
