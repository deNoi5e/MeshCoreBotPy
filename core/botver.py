"""Версия самого бота (не прошивки и не чужих проектов — для них core/versions.py).

Каждый коммит поднимает минорную версию: 1.0 → 1.1 → 1.2 …
"""
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BOT_VERSION = "1.5"

# Версия и момент её первого запуска на этой машине (две строки). Не в git.
_STATE_FILE = Path(__file__).resolve().parent.parent / "bot_version_deployed.txt"
_TIME_FORMAT = "%Y-%m-%d %H:%M"

_deployed_at: datetime | None = None


def deployed_at() -> datetime:
    """Когда текущая версия впервые запущена на этой машине (дата деплоя).

    Первый запуск новой версии записывает текущее время в файл состояния;
    последующие перезапуски той же версии читают его оттуда, так что дата
    остаётся датой деплоя, а не последнего рестарта. Файл не читается или не
    пишется — берётся текущее время (дата тогда «плавает» до починки файла).
    """
    global _deployed_at
    if _deployed_at is not None:
        return _deployed_at

    try:
        lines = _STATE_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 2 and lines[0].strip() == BOT_VERSION:
            _deployed_at = datetime.strptime(lines[1].strip(), _TIME_FORMAT)
            return _deployed_at
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        logger.warning(f"⚠️  Не прочитать {_STATE_FILE.name}: {e!r} — считаю версию задеплоенной сейчас")

    now = datetime.now().replace(second=0, microsecond=0)
    try:
        _STATE_FILE.write_text(f"{BOT_VERSION}\n{now.strftime(_TIME_FORMAT)}\n", encoding="utf-8")
    except OSError as e:
        logger.warning(f"⚠️  Не записать {_STATE_FILE.name}: {e!r}")
    _deployed_at = now
    return now
