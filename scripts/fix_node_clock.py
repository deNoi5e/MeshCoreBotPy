#!/usr/bin/env python3
"""Возвращает назад убежавшие вперёд часы ноды MeshCore (companion).

Нужен для плат без часового кварца (ProMicro nRF52840: USE_LFRC в прошивке) —
их часы спешат на минуты в сутки, а бот при старте умеет только переводить
время вперёд. Прошивка не даёт перевести часы назад (CMD_SET_DEVICE_TIME с
меньшим временем отвергается), а при загрузке сама ставит их на
max(lastmod) контактов + 1 с (bootstrapRTCfromContacts). Поэтому скрипт:

1. сохраняет резервную копию контактов в contacts_backup_*.json;
2. сдвигает назад на величину опережения lastmod у контактов, где он в будущем
   (CMD_ADD_UPDATE_CONTACT с явным lastmod в хвосте кадра);
3. перезагружает ноду (CMD_REBOOT сначала сохраняет контакты во flash);
4. после загрузки выставляет время компьютера.

Если между шагами 2 и 3 нода услышит advert, его lastmod снова уйдёт вперёд —
тогда попытка повторяется (до 3 раз).

Бот на время работы скрипта нужно остановить — порт занят. После исправления
около 2 ч (на величину опережения) соседи отбрасывают adverts ноды как повторы.

    python scripts/fix_node_clock.py                  — только показать, ничего не менять
    python scripts/fix_node_clock.py --apply          — исправить
    python scripts/fix_node_clock.py COM13 --apply    — порт явно (иначе MESHCORE_PORT из .env)
"""
import asyncio
import io
import json
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from meshcore import MeshCore, SerialConnection
from meshcore.events import EventType

load_dotenv()

ATTEMPTS = 3
# Опережение меньше этого не исправляем — это погрешность самого замера.
LEAD_TOLERANCE_SECONDS = 2


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y.%m.%d %H:%M:%S")


def is_error(ev):
    return ev is None or ev.type == EventType.ERROR


async def connect(port, wait_seconds=0):
    """Подключается к ноде; после перезагрузки ждёт, пока порт появится снова."""
    deadline = time.time() + wait_seconds
    while True:
        mc = MeshCore(SerialConnection(port, 115200, cx_dly=0.1))
        try:
            if await mc.connect() is not None:
                return mc
        except Exception:
            pass
        try:
            await mc.disconnect()
            await mc.connection_manager.connection.disconnect()
        except Exception:
            pass
        if time.time() >= deadline:
            raise ConnectionError(f"{port}: нода не отвечает (бот не остановлен?)")
        await asyncio.sleep(2)


async def device_lead(mc):
    ev = await mc.commands.get_time()
    if is_error(ev):
        raise RuntimeError(f"get_time: {ev.payload if ev else None}")
    dev = ev.payload["time"]
    return dev, dev - time.time()


async def set_lastmod(mc, contact, lastmod):
    """update_contact() библиотеки, но с явным lastmod в хвосте кадра.

    Библиотека lastmod не передаёт, и прошивка тогда ставит его по своим
    (спешащим) часам — ровно то, что нужно исправить.
    """
    orig_send = mc.commands.send

    async def send_with_lastmod(data, expected_events=None, timeout=None):
        return await orig_send(data + int(lastmod).to_bytes(4, "little"), expected_events, timeout)

    mc.commands.send = send_with_lastmod
    try:
        return await mc.commands.update_contact(contact)
    finally:
        mc.commands.send = orig_send


async def sync_time(mc):
    now = int(time.time())
    ev = await mc.commands.set_time(now)
    dev, lead = await device_lead(mc)
    status = "OK" if not is_error(ev) else f"отказ {ev.payload if ev else None}"
    print(f"set_time({fmt(now)}): {status}; часы ноды теперь {fmt(dev)} ({lead:+.1f} с)")
    return lead


async def attempt(port, apply):
    mc = await connect(port)
    dev, lead = await device_lead(mc)
    print(f"Часы ноды: {fmt(dev)}, компьютер: {fmt(time.time())}, опережение {lead:+.0f} с ({lead / 3600:+.2f} ч)")
    if lead <= LEAD_TOLERANCE_SECONDS:
        print("Часы не впереди — достаточно выставить время")
        if apply:
            await sync_time(mc)
        await mc.disconnect()
        return True

    ev = await mc.commands.get_contacts()
    if is_error(ev):
        raise RuntimeError(f"get_contacts: {ev.payload if ev else None}")
    contacts = list(ev.payload.values())
    now = time.time()
    future = [c for c in contacts if c["lastmod"] > now]
    print(f"Контактов: {len(contacts)}, с lastmod в будущем: {len(future)}")

    backup = f"contacts_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(contacts, f, ensure_ascii=False, indent=1)
    print(f"Резервная копия контактов: {os.path.abspath(backup)}")

    if not apply:
        for c in sorted(future, key=lambda c: -c["lastmod"])[:10]:
            new = min(c["lastmod"] - lead, now - 60)
            print(f"  {c['adv_name']!r}: lastmod {fmt(c['lastmod'])} -> {fmt(new)}")
        print("Режим просмотра: на ноде ничего не изменено (запустить с --apply)")
        await mc.disconnect()
        return True

    failed = 0
    for c in future:
        new = int(max(0, min(c["lastmod"] - lead, now - 60)))
        res = await set_lastmod(mc, c, new)
        if is_error(res):
            failed += 1
            print(f"  ❌ {c['adv_name']!r}: {res.payload if res else 'нет ответа'}")
    print(f"lastmod исправлен у {len(future) - failed} из {len(future)}, перезагружаю ноду")
    await mc.commands.reboot()
    try:
        await mc.disconnect()
        await mc.connection_manager.connection.disconnect()
    except Exception:
        pass

    await asyncio.sleep(5)
    mc = await connect(port, wait_seconds=60)
    dev, lead = await device_lead(mc)
    print(f"После перезагрузки часы ноды: {fmt(dev)} ({lead:+.0f} с)")
    lead = await sync_time(mc)
    await mc.disconnect()
    return abs(lead) <= LEAD_TOLERANCE_SECONDS


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = args[0] if args else os.environ.get("MESHCORE_PORT")
    if not port:
        sys.exit("Порт не задан: укажите аргументом или в MESHCORE_PORT (.env)")
    apply = "--apply" in sys.argv
    for n in range(1, ATTEMPTS + 1):
        print(f"--- попытка {n}, порт {port}")
        if await attempt(port, apply):
            if apply:
                print("Готово")
            return
        print("Часы всё ещё впереди (видимо, между правкой и перезагрузкой пришёл advert) — повторяю")
    print("Не удалось за отведённые попытки")


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    asyncio.run(main())
