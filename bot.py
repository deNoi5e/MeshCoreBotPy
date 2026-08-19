#!/usr/bin/env python3
import asyncio
import io
import logging
import os
import sys
import time
import traceback
from datetime import datetime

from dotenv import load_dotenv
from meshcore import MeshCore, events

from core.commands import dispatch
from core.msgsplit import split_msg, str_byte_len
from core.weather import to_lat, weather_broadcast_scheduler

load_dotenv()

# stdout/stderr при перенаправлении в файл на Windows наследуют системную
# кодировку (cp1251) вместо UTF-8, из-за чего логирование эмодзи роняет
# UnicodeEncodeError внутри logging (перехватывается, но строка теряется).
if isinstance(sys.stderr, io.TextIOWrapper):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')

log_filename = datetime.now().strftime('bot_%Y.%m.%d_%H-%M-%S.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s.%(msecs)03d] - %(message)s',
    datefmt='%Y.%m.%d %H:%M:%S',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ],
    force=True
)
logger = logging.getLogger(__name__)


def test_split():
    msg = "😀 😀 😀 test 1 test2 test3 testttt\ntt\ntt\n"
    result = split_msg(msg, "SenderName", 25)
    for part in result:
        print(f"{part}    ({str_byte_len(part)} bytes)")
    result = split_msg(msg, "", 25)
    for part in result:
        print(f"{part}    ({str_byte_len(part)} bytes)")


async def main():
    port = os.environ["MESHCORE_PORT"]
    weather_api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "")
    config = {
        "openweathermap_api_key": weather_api_key,
        "weather_broadcast": {
            "city": os.environ.get("WEATHER_CITY", "Omsk"),
            "channel_idx": int(os.environ.get("WEATHER_CHANNEL_IDX", "3")),
            "hour": int(os.environ.get("WEATHER_HOUR", "7")),
            "minute": int(os.environ.get("WEATHER_MINUTE", "30")),
            "timezone_offset_hours": int(os.environ.get("WEATHER_TIMEZONE_OFFSET", "6")),
        },
    }

    mc = await MeshCore.create_serial(port=port)
    #await mc.connect()
    await mc.commands.set_flood_scope(None)
    mc.set_decrypt_channel_logs(True)

    # decrypt_channels в RX_LOG_DATA (msg_hash/pkt_hash для точного сопоставления
    # маршрута с сообщением) работает только для каналов, чей секрет библиотека
    # уже знает — а узнаёт она его только через ответ на get_channel().
    max_channel_idx = int(os.environ.get("MAX_CHANNEL_IDX", "10"))
    for channel_idx in range(max_channel_idx + 1):
        try:
            event = await mc.commands.get_channel(channel_idx)
            channel_name = event.payload.get("channel_name", "") if event else ""
            if channel_name:
                logger.info(f"   ✅ Канал {channel_idx} получен: name='{channel_name}'")
            else:
                logger.info(f"   Канал {channel_idx} пуст, пропускаю")
        except Exception as e:
            logger.debug(f"   Канал {channel_idx} недоступен: {e}")

    logger.info("=" * 50)
    logger.info("🎉 MeshCore Bot запущен!")
    logger.info(f"📡 Подключено к {port}")
    logger.info("=" * 50 + "\n")

    await mc.ensure_contacts()
    mc.auto_update_contacts = True
    logger.info(f"📇 Контактов синхронизировано: {len(mc.contacts)}")
    for contact in mc.contacts.values():
        logger.info(f"   Контакт: {contact}")

    async def listen():
        await mc.start_auto_message_fetching()
        logger.info("🤖 Бот готов! Ожидаю входящие сообщения...\n")

        processed_messages: set = set()
        route_cache: dict = {}
        route_by_hash: dict = {}
        pending_bot_sends: dict = {}

        def on_rx_log(event):
            if event.type != events.EventType.RX_LOG_DATA:
                return
            rx_log = event.payload

            #logger.info(f"  ----- rx_log payload = {rx_log}")

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
            msg_hash = rx_log.get('msg_hash')
            current_time = int(datetime.now().timestamp())
            if msg_hash is not None and path:
                route_by_hash[msg_hash] = {
                    'path': path,
                    'path_len': path_len,
                    'stored_at': current_time,
                }
                logger.info(f"   🔍 RX_LOG сохранена по msg_hash={msg_hash}: path={path}, path_len={path_len}")
                for k in [k for k, v in route_by_hash.items() if current_time - v['stored_at'] > 30]:
                    del route_by_hash[k]
            if recv_time and path:
                route_cache[recv_time] = {'path': path, 'path_len': path_len}
                logger.info(f"   🔍 RX_LOG сохранена: recv_time={recv_time}, path={path}, path_len={path_len}")
                for k in [k for k in route_cache if current_time - k > 30]:
                    del route_cache[k]

        mc.subscribe(events.EventType.RX_LOG_DATA, on_rx_log)

        async def process_message(payload, is_channel=False, route_data=None):

            logger.info(f"  ----- payload = {payload}")
            sender = ""

            weather_channel_idx = config.get("weather_broadcast", {}).get("channel_idx", 3)
            if is_channel:
                channel_idx = payload.get('channel_idx', '?')
                #if channel_idx == weather_channel_idx:
                #    return
                full_text = payload.get('text', '').strip()
                sender_timestamp = payload.get('sender_timestamp', 0)
                path_len = payload.get('path_len', 0)
                if ':' in full_text:
                    parts = full_text.split(':', 1)
                    text = parts[1].strip()
                    sender = parts[0].strip()
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

            if not is_channel:
                try:
                    await mc.commands.send_msg(source_key, "")
                    logger.info("   ✅ Подтверждение отправлено")
                except Exception as e:
                    logger.error(f"   ⚠️  Ошибка отправки ACK: {e}")

            hops = 0 if path_len == 255 else path_len
            response_all = await dispatch(
                text,
                hops=hops,
                route_data=route_data,
                weather_api_key=weather_api_key,
                config=config,
                mc=mc,
                sender_key="" if is_channel else source_key,
                sender_name=sender if is_channel else "",
            )

            if response_all is not None:
                response_all = to_lat(response_all)

                responses = split_msg(response_all, sender, 130 if is_channel else 150)

                for response in responses:
                    try:
                        logger.info(f"   📤 Отправляю ответ... {response}")
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
                    time.sleep(2.0)

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
                        logger.info(f"   is_channel = {is_channel}   event.type = {event.type}")
                        sender_timestamp = event.payload.get('sender_timestamp')
                        txt_hash = event.payload.get('txt_hash')
                        route_data = None
                        if txt_hash is not None:
                            # RX_LOG (с точным msg_hash) обычно приходит чуть позже самого
                            # сообщения — недолго подождём его, прежде чем откатываться
                            # на менее точный подбор по времени.
                            for _ in range(10):
                                if txt_hash in route_by_hash:
                                    route_data = route_by_hash[txt_hash]
                                    logger.info(f"   🔍 Маршрут найден точно по msg_hash={txt_hash}: path={route_data['path']}")
                                    break
                                await asyncio.sleep(0.2)
                        if route_data is None and sender_timestamp:
                            best_recv_time = None
                            best_diff = None
                            for recv_time, data in route_cache.items():
                                diff = abs(sender_timestamp - recv_time)
                                if diff <= 7 and (best_diff is None or diff < best_diff):
                                    best_diff = diff
                                    best_recv_time = recv_time
                                    route_data = data
                            if route_data:
                                logger.info(f"   🔍 Маршрут найден: sender_ts={sender_timestamp}, recv_time={best_recv_time}, diff={best_diff}s")
                            else:
                                logger.info(f"   🔍 Маршрут не найден для sender_ts={sender_timestamp}, доступно recv_times: {list(route_cache.keys())}")
                        await process_message(event.payload, is_channel=is_channel, route_data=route_data)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Ошибка обработки события: {e}")
                    traceback.print_exc()

    try:
        await asyncio.gather(
            listen(),
            weather_broadcast_scheduler(mc, config),
        )
    except KeyboardInterrupt:
        logger.info("\n" + "=" * 50)
        logger.info("🛑 Бот остановлен пользователем")
        logger.info("=" * 50)
    except Exception as e:
        logger.error(f"Ошибка в listen(): {e}")
    finally:
        await mc.stop_auto_message_fetching()
        await mc.disconnect()
        logger.info("👋 Отключено от устройства")


#test_split()
if __name__ == "__main__":
    asyncio.run(main())
