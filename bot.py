#!/usr/bin/env python3
import asyncio
import glob
import io
import logging
import os
import platform
import sys
import time
import traceback
from datetime import datetime

from dotenv import load_dotenv
from meshcore import MeshCore, events

from core.commands import dispatch
from core.moon import OMSK_LAT, OMSK_LON
from core.msgsplit import split_msg, str_byte_len
from core.traffic import traffic_broadcast_scheduler
from core.versions import SOURCES as versions_sources, versions_broadcast_scheduler
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

LOG_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def cleanup_old_logs(max_age_seconds: int = LOG_MAX_AGE_SECONDS) -> None:
    now = time.time()
    for path in glob.glob('bot_*.log'):
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                os.remove(path)
        except OSError:
            pass


cleanup_old_logs()

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


_INTERVAL_UNITS = {"m": 60, "h": 3600, "d": 86400}
_VERSIONS_INTERVAL_DEFAULT = "24h"


def _parse_interval_seconds(raw: str) -> int | None:
    """`30m`/`2h`/`3d` -> секунды. Голое число — часы (обратная совместимость).

    Возвращает None, если значение не разобрать — вызывающая сторона решает,
    что с этим делать.
    """
    text = raw.strip().lower()
    if not text:
        return None
    unit = _INTERVAL_UNITS.get(text[-1])
    number, multiplier = (text[:-1], unit) if unit else (text, 3600)
    try:
        value = int(number)
    except ValueError:
        return None
    if value < 0:
        return None
    return value * multiplier


def _version_interval_seconds(key: str) -> int:
    """Интервал проверки версии источника в секундах из env.

    Формат — число с суффиксом `m`/`h`/`d` (`30m`, `2h`, `3d`); число без
    суффикса понимается как часы, поэтому прежние `VERSIONS_*=24` работают
    как раньше. По умолчанию 24 часа. Нечитаемое и отрицательное значение —
    ошибка конфигурации, подменяется умолчанием с предупреждением. Ноль
    оставляем как есть: это осмысленное «не проверять этот источник».
    """
    name = f"VERSIONS_{key.upper()}_INTERVAL"
    raw = os.environ.get(name)
    if raw is None:
        # Прежнее имя переменной — чтобы не ломать уже настроенные .env.
        legacy = f"{name}_HOURS"
        raw = os.environ.get(legacy)
        if raw is not None:
            name = legacy
    if raw is None:
        raw = _VERSIONS_INTERVAL_DEFAULT

    seconds = _parse_interval_seconds(raw)
    if seconds is None:
        logger.warning(
            f"⚠️  {name}={raw!r} не разобрать (ожидается 30m/2h/3d), "
            f"использую {_VERSIONS_INTERVAL_DEFAULT}"
        )
        return _parse_interval_seconds(_VERSIONS_INTERVAL_DEFAULT)
    return seconds


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
        # Проверка балла пробок с интервалом (не чаще раза в 5 минут —
        # traffic_broadcast_scheduler это гарантирует сам, max(5, ...)),
        # в канал уходит только при изменении значения. TRAFFIC_INTERVAL_MINUTES=0
        # отключает проверку целиком.
        "traffic_broadcast": {
            "channel_idx": int(os.environ.get("TRAFFIC_CHANNEL_IDX", "3")),
            "interval_minutes": int(os.environ.get("TRAFFIC_INTERVAL_MINUTES", "60")),
            "hour_from": int(os.environ.get("TRAFFIC_HOUR_FROM", "7")),
            "hour_to": int(os.environ.get("TRAFFIC_HOUR_TO", "19")),
        },
        # Восход/заход Луны зависят от места, фаза — нет. Часовой пояс общий
        # с прогнозом погоды: узел стоит в одной точке.
        "moon": {
            "lat": float(os.environ.get("MOON_LAT", str(OMSK_LAT))),
            "lon": float(os.environ.get("MOON_LON", str(OMSK_LON))),
            "timezone_offset_hours": int(os.environ.get("WEATHER_TIMEZONE_OFFSET", "6")),
        },
        # Ретрограда — геоцентрическое явление, одинаковое для всей Земли,
        # поэтому от места не зависит: нужен только часовой пояс вывода.
        "mercury": {
            "timezone_offset_hours": int(os.environ.get("WEATHER_TIMEZONE_OFFSET", "6")),
        },
        # Слежение за новыми релизами прошивок и приложений: каждый источник
        # проверяется со своим интервалом (`30m`/`2h`/`3d`), в канал уходит
        # только при смене версии. Канал и окно тишины общие. Интервал 0
        # отключает проверку конкретного источника.
        "versions_broadcast": {
            "channel_idx": int(os.environ.get("VERSIONS_CHANNEL_IDX", "3")),
            "hour_from": int(os.environ.get("VERSIONS_HOUR_FROM", "7")),
            "hour_to": int(os.environ.get("VERSIONS_HOUR_TO", "19")),
            "interval_seconds": {
                key: _version_interval_seconds(key)
                for key in versions_sources
            },
        },
    }
    advert_interval_minutes = int(os.environ.get("ADVERT_INTERVAL_MINUTES", "30"))
    if advert_interval_minutes <= 0:
        logger.warning(
            f"⚠️  ADVERT_INTERVAL_MINUTES={advert_interval_minutes} некорректно, использую 30"
        )
        advert_interval_minutes = 30

    advert_flood_interval_hours = int(os.environ.get("ADVERT_FLOOD_INTERVAL_HOURS", "6"))
    if advert_flood_interval_hours <= 0:
        logger.warning(
            f"⚠️  ADVERT_FLOOD_INTERVAL_HOURS={advert_flood_interval_hours} некорректно, использую 6"
        )
        advert_flood_interval_hours = 6

    mc = await MeshCore.create_serial(port=port)
    if platform.system() == "Linux":
        await mc.connect()

    # DEVICE_QUERY с версией 3 переключает прошивку на CHANNEL_MSG_RECV_V3 — только
    # в нём библиотека отдаёт txt_hash для точного поиска маршрута. Прошивка держит
    # эту версию в RAM до перезагрузки, поэтому объявлять её надо при каждом старте.
    device_info = await mc.commands.send_device_query()
    if device_info is None or device_info.type == events.EventType.ERROR:
        logger.warning(f"⚠️  DEVICE_QUERY не удался ({device_info}) — поиск маршрута по msg_hash работать не будет")
    else:
        info = device_info.payload
        logger.info(
            f"📟 Устройство: {info.get('model', '?')}, прошивка {info.get('ver', '?')} "
            f"(сборка {info.get('fw_build', '?')}, протокол {info.get('fw ver', '?')})"
        )
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

    async def send_advert(flood: bool):
        kind = "широковещательный (flood)" if flood else "обычный"
        logger.info(f"📢 Отправляю {kind} advert...")
        try:
            result = await mc.commands.send_advert(flood=flood)
            logger.info(f"   ✅ Advert ({kind}) отправлен успешно: {result}")
        except Exception as e:
            logger.error(f"   ❌ Ошибка отправки advert ({kind}): {e}")

    async def advert_scheduler():
        await asyncio.sleep(5.0)
        await send_advert(flood=True)

        interval_seconds = advert_interval_minutes * 60
        flood_every_n_ticks = max(
            1, round((advert_flood_interval_hours * 3600) / interval_seconds)
        )
        tick = 0
        while True:
            await asyncio.sleep(interval_seconds)
            tick += 1
            await send_advert(flood=(tick % flood_every_n_ticks == 0))

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

            logger.info(f"  ----- rx_log payload = {rx_log}")

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

            #logger.info(f"  ----- payload = {payload}")
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
            traffic_broadcast_scheduler(mc, config),
            versions_broadcast_scheduler(mc, config),
            advert_scheduler(),
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
