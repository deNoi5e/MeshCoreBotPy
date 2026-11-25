"""Версия установленного агента Ommesh (`ommesh-agent`) — команда `/agentver`.

Агент ставится на ту же машину, где крутится бот (на M18 — службой
`ommesh-agent`, бинарь `/root/.ommesh-agent/ommesh-agent`), поэтому версию
спрашиваем у самого бинаря: `ommesh-agent --version` печатает
`ommesh-agent 0.7.2`. Это именно установленная версия, а не последняя
опубликованная (последний релиз приложения Ommesh — в `/ver`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

logger = logging.getLogger(__name__)

# Путь к бинарю агента: OMMESH_AGENT_PATH, иначе место, куда его кладёт install.sh.
_DEFAULT_PATH = os.path.join("~", ".ommesh-agent", "ommesh-agent")
_TIMEOUT_SECONDS = 5.0
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+\S*")


def _agent_path() -> str:
    return os.path.expanduser(os.environ.get("OMMESH_AGENT_PATH", "").strip() or _DEFAULT_PATH)


async def get_agent_version() -> str:
    path = _agent_path()
    if not os.path.isfile(path):
        logger.warning("/agentver: бинарь агента не найден: %s", path)
        return "🛰 Агент Ommesh не найден"
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), _TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("/agentver: %s --version не ответил за %s с", path, _TIMEOUT_SECONDS)
            return "❌ Агент Ommesh не ответил"
    except OSError as e:
        logger.warning("/agentver: не удалось запустить %s: %s", path, e)
        return "❌ Не удалось запустить агент Ommesh"

    text = out.decode("utf-8", errors="replace").strip()
    m = _VERSION_RE.search(text)
    if not m:
        logger.warning("/agentver: непонятный вывод --version: %r", text[:200])
        return "❌ Версия агента Ommesh не распознана"
    return f"🛰 Агент Ommesh {m.group(0)}"
