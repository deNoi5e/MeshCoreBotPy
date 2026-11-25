"""Запуск мультиплексора ноды: `python mux.py`.

Владеет COM-портом ноды (MESHCORE_PORT) и раздаёт её по TCP (MUX_HOST:MUX_PORT) боту
и официальному приложению MeshCore. Бот в этом режиме запускается с
MESHCORE_PORT=tcp://127.0.0.1:<MUX_PORT>. Устройство описано в core/mcmux.py.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from core.mcmux import Mux, SerialUpstream

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s.%(msecs)03d] - %(message)s',
    datefmt='%Y.%m.%d %H:%M:%S',
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger(__name__)


def main() -> None:
    serial_port = os.environ.get("MUX_SERIAL_PORT") or os.environ.get("MESHCORE_PORT", "")
    if not serial_port or serial_port.startswith("tcp://"):
        logger.error("❌ Задайте MUX_SERIAL_PORT — COM-порт ноды (MESHCORE_PORT у бота указывает на сам мультиплексор)")
        sys.exit(1)
    # По умолчанию только localhost: по TCP без пароля можно всё, вплоть до выгрузки
    # приватного ключа ноды. Для телефона в сети явно задать MUX_HOST=0.0.0.0.
    host = os.environ.get("MUX_HOST", "127.0.0.1")
    port = int(os.environ.get("MUX_PORT", "5000"))
    delay = float(os.environ.get("RECONNECT_DELAY_SECONDS", "15"))
    # Отдельный порт для агента ommesh (его запускают с --radio 127.0.0.1:<порт>). Не задан — выключен.
    agent_port_raw = os.environ.get("MUX_AGENT_PORT", "").strip()
    agent_port = int(agent_port_raw) if agent_port_raw else None
    agent_host = os.environ.get("MUX_AGENT_HOST", "127.0.0.1")
    logger.info(f"🚀 Мультиплексор: нода {serial_port} → TCP {host}:{port}")
    if host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning("⚠️  Мультиплексор открыт в сеть без авторизации: любой, кто доберётся до порта, управляет нодой")
    if agent_port is not None and agent_host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning("⚠️  Порт агента открыт в сеть без авторизации: любой, кто доберётся до него, управляет нодой")
    mux = Mux(lambda: SerialUpstream(serial_port), host, port, reconnect_delay=delay,
              agent_host=agent_host, agent_port=agent_port)
    try:
        asyncio.run(mux.run())
    except KeyboardInterrupt:
        logger.info("👋 Остановлен")


if __name__ == "__main__":
    main()
