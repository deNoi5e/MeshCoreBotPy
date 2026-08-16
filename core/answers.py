from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_ANSWERS_FILE = "answers.txt"


@dataclass
class AnswerRule:
    """Одно правило автоответа."""
    channels: set[int] | None   # None означает все каналы (*)
    triggers: list[str]         # триггерные фразы (в нижнем регистре)
    response: str               # текст ответа


def _parse_quoted_list(s: str) -> list[str]:
    """Извлечь список строк в кавычках: "aaa","bbb" → ['aaa', 'bbb']"""
    return re.findall(r'"([^"]*)"', s)


def load_answers(path: str = _ANSWERS_FILE) -> list[AnswerRule]:
    """Загрузить правила автоответов из файла.

    Формат строки:
        [0,1,3]"триггер1","триггер2"="ответ"
        [*]"триггер"="ответ"
    """
    rules: list[AnswerRule] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        logger.info(f"📋 Файл автоответов {path} не найден, автоответы отключены")
        return rules
    except Exception as e:
        logger.error(f"❌ Ошибка чтения {path}: {e}")
        return rules

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        # Пропускаем комментарии и пустые строки
        if not line or line.startswith("#"):
            continue

        # Парсим [каналы]
        m = re.match(r'^\[([^\]]*)\](.+)$', line)
        if not m:
            logger.warning(f"⚠️  answers.txt строка {lineno}: неверный формат (нет [каналов]): {line!r}")
            continue

        channels_str = m.group(1).strip()
        rest = m.group(2).strip()

        # Каналы
        if channels_str == "*":
            channels = None  # все каналы
        else:
            try:
                channels = {int(c.strip()) for c in channels_str.split(",") if c.strip()}
            except ValueError:
                logger.warning(f"⚠️  answers.txt строка {lineno}: неверные номера каналов: {channels_str!r}")
                continue

        # Разделяем триггеры и ответ по знаку = между кавычками
        # Ищем паттерн: "...","..."=  "..."
        eq_match = re.search(r'"\s*=\s*"', rest)
        if not eq_match:
            logger.warning(f"⚠️  answers.txt строка {lineno}: не найден разделитель =\": {rest!r}")
            continue

        triggers_part = rest[:eq_match.start() + 1]   # включая закрывающую кавычку триггера
        response_part = rest[eq_match.end() - 1:]     # начиная с открывающей кавычки ответа

        triggers = [t.lower() for t in _parse_quoted_list(triggers_part) if t]
        responses = _parse_quoted_list(response_part)

        if not triggers:
            logger.warning(f"⚠️  answers.txt строка {lineno}: нет триггеров")
            continue
        if not responses:
            logger.warning(f"⚠️  answers.txt строка {lineno}: нет ответа")
            continue

        response = responses[0]
        rules.append(AnswerRule(channels=channels, triggers=triggers, response=response))
        logger.debug(f"📋 Правило загружено: каналы={channels_str}, триггеры={triggers}, ответ={response!r}")

    logger.info(f"📋 Загружено {len(rules)} правил автоответов из {path}")
    return rules


def find_answer(text: str, channel_idx: int, rules: list[AnswerRule]) -> str | None:
    """Найти ответ для текста сообщения из указанного канала.

    Возвращает текст ответа или None если правило не найдено.
    Сравнение регистронезависимое, триггер ищется в любой части сообщения.
    """
    text_lower = text.lower()
    for rule in rules:
        # Проверяем канал
        if rule.channels is not None and channel_idx not in rule.channels:
            continue
        # Проверяем триггеры
        for trigger in rule.triggers:
            if trigger in text_lower:
                logger.info(f"📋 Автоответ: триггер={trigger!r}, канал={channel_idx}, ответ={rule.response!r}")
                return rule.response
    return None
