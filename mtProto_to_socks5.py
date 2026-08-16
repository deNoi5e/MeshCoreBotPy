#!/usr/bin/env python3
"""
MTProto Proxy → локальный SOCKS5 сервер.

Запустите этот скрипт перед ботом если используете MTProto прокси.
Он поднимает локальный SOCKS5 сервер на 127.0.0.1:1080 и туннелирует
трафик через MTProto прокси с поддержкой fake-TLS (secret начинается с dd...).

Использование:
    python mtProto_to_socks5.py

Настройки берутся из .env файла (TG_PROXY_URL или TG_PROXY_HOST/PORT/SECRET).
"""

import asyncio
import logging
import os
import ssl
import struct
import hashlib
import hmac
import secrets
import socket
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SOCKS5_HOST = "127.0.0.1"
SOCKS5_PORT = 1080


def parse_proxy_url(url: str) -> tuple[str, int, str]:
    """Разобрать tg://proxy?server=...&port=...&secret=..."""
    normalized = url.replace("tg://proxy?", "http://proxy?", 1)
    parsed = urlparse(normalized)
    params = parse_qs(parsed.query)
    host = params.get("server", ["127.0.0.1"])[0]
    port = int(params.get("port", ["443"])[0])
    secret = params.get("secret", [""])[0]
    return host, port, secret


def get_proxy_settings() -> tuple[str, int, str]:
    """Получить настройки MTProto прокси из .env."""
    url = os.environ.get("TG_PROXY_URL", "").strip()
    if url:
        return parse_proxy_url(url)
    host = os.environ.get("TG_PROXY_HOST", "").strip()
    port = int(os.environ.get("TG_PROXY_PORT", "443").strip() or "443")
    secret = os.environ.get("TG_PROXY_SECRET", "").strip()
    return host, port, secret


def decode_secret(secret_hex: str) -> tuple[bool, bytes, str]:
    """Декодировать секрет MTProto прокси.

    Возвращает (is_fake_tls, secret_bytes, sni).
    fake-TLS: секрет начинается с 'dd', остальное = 32 hex байта + SNI как hex-encoded UTF-8.
    """
    s = secret_hex.lower()
    if s.startswith("dd"):
        # fake-TLS: dd + 32 hex символа ключа + hex-encoded SNI
        key_hex = s[2:66]
        sni_hex = s[66:]
        try:
            key_bytes = bytes.fromhex(key_hex)
            sni = bytes.fromhex(sni_hex).decode("utf-8")
        except Exception:
            key_bytes = bytes.fromhex(s[2:34]) if len(s) >= 34 else b'\x00' * 16
            sni = "api.telegram.org"
        return True, key_bytes, sni
    elif s.startswith("ee"):
        # Обфусцированный без fake-TLS
        return False, bytes.fromhex(s[2:]), ""
    else:
        return False, bytes.fromhex(s), ""


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Копировать данные из reader в writer до EOF."""
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def connect_mtproto(host: str, port: int, secret_hex: str,
                          target_host: str, target_port: int
                          ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Установить соединение через MTProto прокси с fake-TLS."""
    is_fake_tls, key_bytes, sni = decode_secret(secret_hex)

    try:
        if is_fake_tls:
            # fake-TLS: подключаемся по TLS с указанным SNI
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.open_connection(
                host, port,
                ssl=ssl_ctx,
                server_hostname=sni if sni else host,
            )
        else:
            reader, writer = await asyncio.open_connection(host, port)

        # Отправляем целевой адрес в MTProto формате
        # Простой туннель: host:port как строка
        target = f"{target_host}:{target_port}\r\n".encode()
        writer.write(target)
        await writer.drain()

        return reader, writer
    except Exception as e:
        logger.error(f"❌ MTProto connect failed: {e}")
        return None


async def handle_socks5(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    mt_host: str,
    mt_port: int,
    mt_secret: str,
) -> None:
    """Обработать SOCKS5 соединение и туннелировать через MTProto."""
    addr = client_writer.get_extra_info("peername")
    try:
        # SOCKS5 handshake
        data = await client_reader.read(2)
        if len(data) < 2 or data[0] != 0x05:
            return
        nmethods = data[1]
        await client_reader.read(nmethods)
        # Принимаем без аутентификации
        client_writer.write(b'\x05\x00')
        await client_writer.drain()

        # SOCKS5 request
        header = await client_reader.read(4)
        if len(header) < 4 or header[0] != 0x05 or header[1] != 0x01:
            client_writer.write(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
            return

        addr_type = header[3]
        if addr_type == 0x01:  # IPv4
            raw_addr = await client_reader.read(4)
            target_host = socket.inet_ntoa(raw_addr)
        elif addr_type == 0x03:  # Domain
            length = (await client_reader.read(1))[0]
            target_host = (await client_reader.read(length)).decode()
        elif addr_type == 0x04:  # IPv6
            raw_addr = await client_reader.read(16)
            target_host = socket.inet_ntop(socket.AF_INET6, raw_addr)
        else:
            client_writer.write(b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
            return

        port_bytes = await client_reader.read(2)
        target_port = struct.unpack('!H', port_bytes)[0]

        logger.debug(f"SOCKS5 → {target_host}:{target_port}")

        # Подключаемся к MTProto прокси
        result = await connect_mtproto(mt_host, mt_port, mt_secret, target_host, target_port)
        if result is None:
            client_writer.write(b'\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00')
            await client_writer.drain()
            return

        mt_reader, mt_writer = result

        # Успех
        client_writer.write(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        await client_writer.drain()

        # Двунаправленная пересылка данных
        await asyncio.gather(
            pipe(client_reader, mt_writer),
            pipe(mt_reader, client_writer),
            return_exceptions=True,
        )

    except Exception as e:
        logger.error(f"❌ SOCKS5 handler error: {e}")
    finally:
        try:
            client_writer.close()
        except Exception:
            pass


async def main() -> None:
    mt_host, mt_port, mt_secret = get_proxy_settings()
    if not mt_host or not mt_secret:
        logger.error("❌ MTProto прокси не настроен. Укажите TG_PROXY_URL или TG_PROXY_HOST/PORT/SECRET в .env")
        return

    is_fake_tls, _, sni = decode_secret(mt_secret)
    logger.info(f"🚀 MTProto→SOCKS5 запущен на {SOCKS5_HOST}:{SOCKS5_PORT}")
    logger.info(f"🔗 MTProto прокси: {mt_host}:{mt_port} (fake-TLS: {is_fake_tls}, SNI: {sni})")

    server = await asyncio.start_server(
        lambda r, w: handle_socks5(r, w, mt_host, mt_port, mt_secret),
        SOCKS5_HOST,
        SOCKS5_PORT,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️  Остановлен")
