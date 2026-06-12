"""
Примеры расширения функциональности бота
"""

import asyncio
from bot import MeshCoreBot
from meshcore import events
from datetime import datetime


class AdvancedMeshCoreBot(MeshCoreBot):
    """Расширенная версия бота с дополнительными командами"""

    def handle_message(self, message: str, source_key: str, is_direct: bool = False):
        """Обработать входящее сообщение с поддержкой дополнительных команд"""
        message = message.strip()

        if message == "/ping":
            response = "pong"
            self.send_response(response, source_key, is_direct)

        elif message == "/help":
            response = self.get_help()
            self.send_response(response, source_key, is_direct)

        elif message == "/time":
            response = f"⏰ Текущее время: {datetime.now().strftime('%H:%M:%S')}"
            self.send_response(response, source_key, is_direct)

        elif message == "/date":
            response = f"📅 Сегодняшняя дата: {datetime.now().strftime('%d.%m.%Y')}"
            self.send_response(response, source_key, is_direct)

        elif message.startswith("/echo "):
            # Повторить сообщение пользователя
            text = message[6:]
            response = f"🔊 {text}"
            self.send_response(response, source_key, is_direct)

        elif message.startswith("/upper "):
            # Преобразовать в заглавные буквы
            text = message[7:]
            response = text.upper()
            self.send_response(response, source_key, is_direct)

        elif message.startswith("/lower "):
            # Преобразовать в строчные буквы
            text = message[7:]
            response = text.lower()
            self.send_response(response, source_key, is_direct)

        elif message == "/status":
            response = self.get_bot_status()
            self.send_response(response, source_key, is_direct)

        else:
            # Для неизвестных команд в прямых сообщениях
            if is_direct and message.startswith("/"):
                response = f"❌ Неизвестная команда: {message}\nВведите /help для справки"
                self.send_response(response, source_key, is_direct)

    def get_help(self) -> str:
        """Вернуть расширенную справку"""
        return """📋 Доступные команды:
/ping - Проверить статус бота
/help - Показать это сообщение
/time - Показать текущее время
/date - Показать текущую дату
/echo <текст> - Повторить текст
/upper <текст> - Преобразовать в ЗАГЛАВНЫЕ БУКВЫ
/lower <текст> - Преобразовать в строчные буквы
/status - Показать статус бота

Пример: /echo Hello World"""

    def get_bot_status(self) -> str:
        """Вернуть статус бота"""
        channels_list = ", ".join(self.channel_keys.values()) if self.channel_keys else "нет"
        return f"""🤖 Статус бота:
✓ Бот активен
📡 Подключённые каналы: {channels_list}
⏱️ Время: {datetime.now().strftime('%H:%M:%S')}"""


# Пример 2: Бот с автоответчиком
class AutoReplyBot(MeshCoreBot):
    """Бот с функцией автоответа"""

    def __init__(self):
        super().__init__()
        self.auto_replies = {
            "привет": "👋 Привет! Как дела?",
            "как дела": "✨ Отлично, спасибо за внимание!",
            "помощь": "Введите /help",
        }

    def handle_message(self, message: str, source_key: str, is_direct: bool = False):
        """Обработать сообщение с учетом автоответов"""
        message_lower = message.lower().strip()

        # Проверить автоответы
        for trigger, response in self.auto_replies.items():
            if trigger in message_lower:
                self.send_response(response, source_key, is_direct)
                return

        # Затем проверить стандартные команды
        super().handle_message(message, source_key, is_direct)


# Пример 3: Бот с подсчётом статистики
class StatsBot(MeshCoreBot):
    """Бот, собирающий статистику сообщений"""

    def __init__(self):
        super().__init__()
        self.message_count = 0
        self.command_count = 0
        self.user_messages = {}

    def handle_message(self, message: str, source_key: str, is_direct: bool = False):
        """Обработать сообщение и собрать статистику"""
        self.message_count += 1

        if source_key not in self.user_messages:
            self.user_messages[source_key] = 0
        self.user_messages[source_key] += 1

        if message.strip().startswith("/"):
            self.command_count += 1

        if message.strip() == "/stats":
            response = self.get_stats()
            self.send_response(response, source_key, is_direct)
            return

        super().handle_message(message, source_key, is_direct)

    def get_stats(self) -> str:
        """Вернуть статистику"""
        if not self.user_messages:
            return "📊 Пока нет сообщений"

        top_users = sorted(self.user_messages.items(), key=lambda x: x[1], reverse=True)[:5]
        users_str = "\n".join([
            f"  {self.channel_keys.get(k, f'user:{k[:8]}')}: {count}"
            for k, count in top_users
        ])

        return f"""📊 Статистика бота:
Всего сообщений: {self.message_count}
Всего команд: {self.command_count}

Топ пользователей:
{users_str}"""


async def main():
    """Пример использования расширенного бота"""
    # Используйте одну из трёх версий:

    # Вариант 1: Расширенный бот с дополнительными командами
    # bot = AdvancedMeshCoreBot()

    # Вариант 2: Бот с автоответчиком
    # bot = AutoReplyBot()

    # Вариант 3: Бот со статистикой
    bot = StatsBot()

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
