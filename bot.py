#!/usr/bin/env python3
import asyncio
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from meshcore import MeshCore, events

from core.commands import dispatch
from core.weather import to_lat, weather_broadcast_scheduler, weather_cache_updater
from core.yandex_weather import yandex_weather_cache_updater
from core.answers import load_answers, find_answer
from core.tg_relay import make_tg_relay_config, relay_to_telegram, tg_poll_loop

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def create_connection() -> MeshCore:
    """Создать подключение к устройству согласно настройкам .env.

    MESHCORE_CONNECTION=serial  — Serial/COM порт (MESHCORE_PORT)
    MESHCORE_CONNECTION=ble     — Bluetooth LE (MESHCORE_BLE_ADDRESS)
    MESHCORE_CONNECTION=tcp     — TCP/IP (MESHCORE_TCP_HOST, MESHCORE_TCP_PORT)

    Для BLE: create_ble() уже вызывает connect() внутри, не нужно вызывать повторно.
    PIN (MESHCORE_BLE_PIN) передаётся только если явно задан в .env.
    """
    conn_type = os.environ.get("MESHCORE_CONNECTION", "serial").lower().strip()

    if conn_type == "ble":
        address = os.environ.get("MESHCORE_BLE_ADDRESS", "")
        if not address:
            raise ValueError("MESHCORE_BLE_ADDRESS не задан в .env")
        pin = os.environ.get("MESHCORE_BLE_PIN", "").strip() or None
        logger.info(f"🔵 BLE подключение к {address}{' (с PIN)' if pin else ''}...")
        mc = await MeshCore.create_ble(address, pin=pin)
        if mc is None:
            raise RuntimeError("BLE подключение не удалось — устройство не ответило (create_ble вернул None)")
        logger.info("🔵 BLE подключение успешно")
        return mc

    elif conn_type == "tcp":
        host = os.environ.get("MESHCORE_TCP_HOST", "")
        port = int(os.environ.get("MESHCORE_TCP_PORT", "4000"))
        if not host:
            raise ValueError("MESHCORE_TCP_HOST не задан в .env")
        logger.info(f"🌐 Подключение по TCP к {host}:{port}")
        return await MeshCore.create_tcp(host, port)

    else:  # serial (по умолчанию)
        port = os.environ.get("MESHCORE_PORT", "")
        if not port:
            raise ValueError("MESHCORE_PORT не задан в .env")
        logger.info(f"🔌 Подключение по Serial к {port}")
        return await MeshCore.create_serial(port=port)


_mtproto_process: asyncio.subprocess.Process | None = None


async def _start_mtproto_converter() -> None:
    """Запустить mtProto_to_socks5.py как фоновый subprocess."""
    global _mtproto_process
    script = os.path.join(os.path.dirname(__file__), "mtProto_to_socks5.py")
    if not os.path.exists(script):
        logger.error(f"❌ MTProto конвертер не найден: {script}")
        return
    try:
        import sys
        _mtproto_process = await asyncio.create_subprocess_exec(
            sys.executable, script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        # Ждём немного чтобы конвертер успел стартовать
        await asyncio.sleep(1.5)
        logger.info(f"🔌 MTProto→SOCKS5 конвертер запущен (PID {_mtproto_process.pid})")
    except Exception as e:
        logger.error(f"❌ Не удалось запустить MTProto конвертер: {e}")


async def main():
    weather_api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "")
    config = {
        "openweathermap_api_key": weather_api_key,
        "weather_broadcast": {
            "city": os.environ.get("WEATHER_CITY", "Omsk"),
            "channel_idx": int(os.environ.get("WEATHER_CHANNEL_IDX", "3")),
            "hour": int(os.environ.get("WEATHER_HOUR", "7")),
            "minute": int(os.environ.get("WEATHER_MINUTE", "30")),
            "timezone_offset_hours": int(os.environ.get("WEATHER_TIMEZONE_OFFSET", "6")),
            "broadcast_period": os.environ.get("WEATHER_BROADCAST_PERIOD", "1d"),
            "weather_source": os.environ.get("WEATHER_SOURCE", "owm").lower().strip(),
        },
        "narodmon": {
            "api_key": os.environ.get("NARODMON_API_KEY", ""),
            "sensors_raw": os.environ.get("NARODMON_SENSORS", ""),
        },
        "yandex_weather": {
            "api_key": os.environ.get("YANDEX_WEATHER_API_KEY", ""),
            "lat": float(os.environ.get("YANDEX_WEATHER_LAT", "0") or "0"),
            "lon": float(os.environ.get("YANDEX_WEATHER_LON", "0") or "0"),
        },
    }

    mc = await create_connection()
    # Для serial/tcp нужен явный connect(), для BLE он уже вызван внутри create_ble()
    conn_type = os.environ.get("MESHCORE_CONNECTION", "serial").lower().strip()
    if conn_type != "ble":
        await mc.connect()
    await mc.commands.set_flood_scope(None)
    mc.set_decrypt_channel_logs(True)

    # Конфиг Telegram relay
    tg_cfg = make_tg_relay_config(dict(os.environ))
    if tg_cfg.enabled:
        logger.info(f"📨 Telegram relay включён: chat={tg_cfg.chat_id}, topic={tg_cfg.topic_id}, канал={tg_cfg.channel_idx}")
        # Автозапуск MTProto→SOCKS5 конвертера если нужен
        if tg_cfg.proxy.proxy_type == "mtproto":
            await _start_mtproto_converter()
    else:
        logger.info("📨 Telegram relay отключён (TG_RELAY_ENABLED=false)")

    # Загружаем правила автоответов
    answers_enabled = os.environ.get("ANSWERS_ENABLED", "true").strip().lower() in ("true", "1", "yes")
    answer_rules = load_answers("answers.txt") if answers_enabled else []
    if not answers_enabled:
        logger.info("📭 Автоответы отключены (ANSWERS_ENABLED=false)")

    logger.info("=" * 50)
    logger.info("🎉 MeshCore Bot запущен!")
    logger.info("=" * 50 + "\n")

    async def listen():
        await mc.start_auto_message_fetching()
        logger.info("🤖 Бот готов! Ожидаю входящие сообщения...\n")

        processed_messages: set = set()
        route_cache: dict = {}
        pending_bot_sends: dict = {}

        def on_rx_log(event):
            if event.type != events.EventType.RX_LOG_DATA:
                return
            rx_log = event.payload
            payload_type = rx_log.get('payload_type')
            sender_timestamp = rx_log.get('sender_timestamp')

            if payload_type == 5 and sender_timestamp in pending_bot_sends:
                snr = rx_log.get('snr', '?')
                rssi = rx_log.get('rssi', '?')
                path = rx_log.get('path', '')
                path_len = rx_log.get('path_len', 0)
                path_hash_size = rx_log.get('path_hash_size', 1)
                preview = pending_bot_sends[sender_timestamp]
                if path and path_len > 0:
                    chars = path_hash_size * 2
                    addrs = [path[i:i+chars] for i in range(0, len(path), chars)]
                    logger.info(f"   📡 Ретранслятор услышал ответ «{preview}»: путь={' → '.join(addrs)}, SNR={snr}, RSSI={rssi}")
                else:
                    logger.info(f"   📡 Ответ «{preview}» получен напрямую узлом: SNR={snr}, RSSI={rssi}")
                return

            recv_time = rx_log.get('recv_time')
            path = rx_log.get('path')
            path_len = rx_log.get('path_len')
            if recv_time and path:
                route_cache[recv_time] = {'path': path, 'path_len': path_len}
                logger.info(f"   🔍 RX_LOG сохранена: recv_time={recv_time}, path={path}, path_len={path_len}")
                current_time = int(datetime.now().timestamp())
                for k in [k for k in route_cache if current_time - k > 30]:
                    del route_cache[k]

        mc.subscribe(events.EventType.RX_LOG_DATA, on_rx_log)

        async def process_message(payload, is_channel=False, route_data=None):
            weather_channel_idx = config.get("weather_broadcast", {}).get("channel_idx", 3)
            if is_channel:
                channel_idx = payload.get('channel_idx', '?')
                if channel_idx == weather_channel_idx:
                    return
                full_text = payload.get('text', '').strip()
                sender_timestamp = payload.get('sender_timestamp', 0)
                path_len = payload.get('path_len', 0)
                if ':' in full_text:
                    parts = full_text.split(':', 1)
                    text = parts[1].strip()
                else:
                    text = full_text
                source_key = f"channel_{channel_idx}"
                source_name = f"канал {channel_idx}"
                dest_key = f"channel_{channel_idx}"
            else:
                source_key = payload.get('pubkey_prefix', '?')
                text = payload.get('text', '').strip()
                sender_timestamp = payload.get('sender_timestamp', 0)
                path_len = payload.get('path_len', 0)
                source_name = f"контакт {source_key[:12]}"
                dest_key = source_key

            msg_id = f"{source_key}:{sender_timestamp}:{text}"
            if msg_id in processed_messages:
                logger.debug(f"Дубликат от {source_name}, пропускаю")
                return
            processed_messages.add(msg_id)

            logger.info(f"📬 От {source_name}: '{text}'")

            # Пересылка в Telegram (только для нужного канала)
            if is_channel and tg_cfg.enabled and channel_idx == tg_cfg.channel_idx:
                # Имя отправителя: часть до ':' из полного текста канала
                if ':' in full_text:
                    sender_name = full_text.split(':', 1)[0].strip()
                else:
                    sender_name = f"ch{channel_idx}"
                _hops = 0 if path_len == 255 else path_len
                await relay_to_telegram(
                    sender_name=sender_name,
                    text=text,
                    hops=_hops,
                    route_data=route_data,
                    cfg=tg_cfg,
                )

            if not is_channel:
                try:
                    await mc.commands.send_msg(source_key, "")
                    logger.info("   ✅ Подтверждение отправлено")
                except Exception as e:
                    logger.error(f"   ⚠️  Ошибка отправки ACK: {e}")

            hops = 0 if path_len == 255 else path_len
            response = await dispatch(
                text,
                hops=hops,
                route_data=route_data,
                weather_api_key=weather_api_key,
                config=config,
                mc=mc,
            )

            # Если команда не распознана и это канал — проверяем автоответы
            if response is None and is_channel:
                response = find_answer(text, channel_idx, answer_rules)

            if response is not None:
                response = to_lat(response)
                # Обрезаем по байтам — MeshCore лимит 143 байта
                encoded = response.encode("utf-8")
                if len(encoded) > 143:
                    response = encoded[:143].decode("utf-8", errors="ignore")
                try:
                    logger.info("   📤 Отправляю ответ...")
                    if is_channel:
                        send_ts = int(time.time())
                        preview = response[:30] + ("…" if len(response) > 30 else "")
                        pending_bot_sends[send_ts] = preview
                        cutoff = send_ts - 60
                        for k in [k for k in pending_bot_sends if k < cutoff]:
                            del pending_bot_sends[k]
                        channel_idx = payload.get('channel_idx', 0)
                        await mc.commands.send_chan_msg(channel_idx, response, timestamp=send_ts)
                    else:
                        await mc.commands.send_msg(dest_key, response)
                    logger.info("   ✨ Ответ успешно отправлен!")
                except Exception as e:
                    logger.error(f"   ❌ Ошибка отправки ответа: {e}")

        while True:
            contact_event = asyncio.create_task(
                mc.wait_for_event(events.EventType.CONTACT_MSG_RECV, timeout=60)
            )
            channel_event = asyncio.create_task(
                mc.wait_for_event(events.EventType.CHANNEL_MSG_RECV, timeout=60)
            )

            done, pending = await asyncio.wait(
                [contact_event, channel_event],
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()

            for task in done:
                try:
                    event = task.result()
                    if event:
                        is_channel = event.type == events.EventType.CHANNEL_MSG_RECV
                        sender_timestamp = event.payload.get('sender_timestamp')
                        route_data = None
                        if sender_timestamp:
                            for recv_time, data in route_cache.items():
                                if abs(sender_timestamp - recv_time) <= 5:
                                    route_data = data
                                    logger.info(f"   🔍 Маршрут найден: sender_ts={sender_timestamp}, recv_time={recv_time}, diff={abs(sender_timestamp - recv_time)}s")
                                    break
                            if not route_data:
                                logger.info(f"   🔍 Маршрут не найден для sender_ts={sender_timestamp}, доступно recv_times: {list(route_cache.keys())}")
                        await process_message(event.payload, is_channel=is_channel, route_data=route_data)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Ошибка обработки события: {e}")

    try:
        await asyncio.gather(
            listen(),
            weather_broadcast_scheduler(mc, config),
            weather_cache_updater(config),
            yandex_weather_cache_updater(config),
            tg_poll_loop(tg_cfg, mc),
        )
    except KeyboardInterrupt:
        logger.info("\n" + "=" * 50)
        logger.info("🛑 Бот остановлен пользователем")
        logger.info("=" * 50)
    except Exception as e:
        logger.error(f"Ошибка в main(): {e}")
    finally:
        await mc.stop_auto_message_fetching()
        await mc.disconnect()
        logger.info("👋 Отключено от устройства")
        # Останавливаем MTProto конвертер если он был запущен
        if _mtproto_process is not None:
            try:
                _mtproto_process.terminate()
                await _mtproto_process.wait()
                logger.info("🔌 MTProto конвертер остановлен")
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
