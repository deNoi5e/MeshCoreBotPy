"""Контакты на ноде: автодобавление, переполнение памяти, чистка старых.

Контакты добавляет сама прошивка ноды по услышанным advert'ам, бот только
настраивает её и следит за результатом. Поведение прошивки (meshcore-dev/MeshCore:
examples/companion_radio/MyMesh.cpp, src/helpers/BaseChatMesh.cpp):

- manual_add_contacts=0 — нода сохраняет каждый новый узел; =1 — только типы,
  отмеченные битами autoadd_config, остальные уходят боту событием NEW_CONTACT
  и на ноде не сохраняются.
- Таблица полна: с битом AUTO_ADD_OVERWRITE_OLDEST нода вытесняет не избранный
  контакт с самым старым lastmod и шлёт CONTACT_DELETED; без него новый узел не
  сохраняется и приходит CONTACTS_FULL. Бывает одно из двух, не оба сразу.
- flags & 0x01 — «избранный»: прошивка такой контакт не вытесняет, чистка тоже.
"""
import asyncio
import logging
import time
from datetime import datetime

from meshcore import events

from core.versions import _humanize_interval

logger = logging.getLogger(__name__)

_FLAG_FAVOURITE = 0x01

_AUTO_ADD_OVERWRITE_OLDEST = 0x01
_AUTO_ADD_TYPE_BITS = (
    (0x02, "companion"),
    (0x04, "repeater"),
    (0x08, "room server"),
    (0x10, "sensor"),
)

_CONTACT_TYPES = {1: "companion", 2: "repeater", 3: "room server", 4: "sensor"}

# Часы ноды, расходящиеся с компьютером сильнее этого, отмечаем в логе.
_CLOCK_DRIFT_WARN_SECONDS = 3600

# Метка времени дальше этого в будущем — часы узла врут, такой метке не верим.
_FUTURE_TOLERANCE_SECONDS = 86400


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y.%m.%d %H:%M") if ts else "никогда"


def _describe(contact: dict) -> str:
    kind = _CONTACT_TYPES.get(contact.get("type"), f"тип {contact.get('type')}")
    return f"«{contact.get('adv_name') or '?'}» ({kind}, {contact.get('public_key', '')[:12]})"


def _last_heard(contact: dict, now: float) -> int | None:
    """Когда узел последний раз давал о себе знать; None — не определить.

    last_advert — метка из самого advert'а, то есть по часам отправителя;
    lastmod — по часам нашей ноды, когда контакт последний раз обновлялся
    (advert, сообщение, новый маршрут). Врать могут и те и другие: нода без RTC
    после перезагрузки стартует с прошлой даты, пока её время никто не выставит.
    Поэтому узел считается живым, если свежая хотя бы одна из двух меток.

    Метку дальше суток в будущем не учитываем вовсе: у узла со сбитыми часами
    last_advert бывает и 2037 годом, и по max() такой контакт считался бы живым
    вечно. Если в будущем обе метки — возраст не определить, вернётся None.
    """
    stamps = (contact.get("last_advert", 0), contact.get("lastmod", 0))
    valid = [ts for ts in stamps if ts <= now + _FUTURE_TOLERANCE_SECONDS]
    return max(valid) if valid else None


def _heard_text(contact: dict, now: float) -> str:
    """«последний раз слышен …» для лога, с пометкой о метках из будущего."""
    heard = _last_heard(contact, now)
    text = f"последний раз слышен {_fmt_ts(heard) if heard is not None else '?'}"
    future = [
        f"{name} {_fmt_ts(contact[key])}"
        for key, name in (("last_advert", "advert"), ("lastmod", "lastmod"))
        if contact.get(key, 0) > now + _FUTURE_TOLERANCE_SECONDS
    ]
    if future:
        text += f" (метка из будущего, не учтена: {', '.join(future)})"
    return text


def _is_error(result) -> bool:
    return result is None or result.type == events.EventType.ERROR


async def ensure_auto_add_contacts(mc) -> None:
    """Логирует настройки автодобавления и выключает manual_add_contacts, если он включён."""
    manual = mc.self_info.get("manual_add_contacts")
    logger.info(f"📇 Автодобавление контактов: manual_add_contacts={manual}")

    try:
        autoadd = await mc.commands.get_autoadd_config()
    except Exception as e:
        autoadd = None
        logger.warning(f"   ⚠️  autoadd_config не получен: {e!r}")
    if _is_error(autoadd):
        if autoadd is not None:
            logger.warning(f"   ⚠️  autoadd_config не получен: {autoadd.payload}")
    else:
        flags = autoadd.payload.get("config", 0)
        types = [name for bit, name in _AUTO_ADD_TYPE_BITS if flags & bit]
        logger.info(
            f"   autoadd_config=0x{flags:02x}: типы для режима manual — "
            f"{', '.join(types) or 'никакие'}"
        )
        if flags & _AUTO_ADD_OVERWRITE_OLDEST:
            logger.info(
                "   При заполнении памяти нода вытесняет самый давно не слышанный "
                "не избранный контакт (overwrite_oldest вкл) — придёт CONTACT_DELETED"
            )
        else:
            logger.info(
                "   При заполнении памяти новые узлы не сохраняются "
                "(overwrite_oldest выкл) — придёт CONTACTS_FULL"
            )

    if not manual:
        logger.info("   ✅ Нода сама сохраняет каждый новый узел")
        return

    logger.warning("   ⚠️  manual_add_contacts включён — выключаю, чтобы нода сохраняла все новые узлы")
    try:
        result = await mc.commands.set_manual_add_contacts(False)
        if _is_error(result):
            logger.error(f"   ❌ Не удалось выключить manual_add_contacts: {result.payload if result else None}")
            return
        check = await mc.commands.send_appstart()
    except Exception as e:
        logger.error(f"   ❌ Не удалось выключить manual_add_contacts: {e!r}")
        return
    if _is_error(check):
        logger.warning(f"   ⚠️  manual_add_contacts выставлен, но перечитать не удалось: {check.payload if check else None}")
    elif check.payload.get("manual_add_contacts"):
        logger.error("   ❌ Нода приняла команду, но manual_add_contacts по-прежнему включён")
    else:
        logger.info("   ✅ manual_add_contacts=False — нода сохраняет каждый новый узел")


def subscribe_contact_events(mc, max_contacts: int | None) -> None:
    """Логирует переполнение памяти контактов на ноде."""
    # CONTACTS_FULL приходит без данных — какой узел не влез, видно только по
    # предшествующему NEW_CONTACT (прошивка шлёт его прямо перед CONTACTS_FULL).
    last_discovered: dict = {}

    def on_new_contact(event):
        last_discovered["contact"] = event.payload
        last_discovered["at"] = time.monotonic()

    def on_contacts_full(event):
        contact = last_discovered.get("contact")
        if contact and time.monotonic() - last_discovered["at"] < 5:
            who = f"новый узел {_describe(contact)} не сохранён"
        else:
            who = "новый узел не сохранён"
        logger.error(
            f"❌ CONTACTS_FULL: память контактов на ноде заполнена "
            f"({len(mc.contacts)}/{max_contacts or '?'}) — {who}"
        )

    def on_contact_deleted(event):
        pubkey = event.payload.get("pubkey", "")
        # Библиотека CONTACT_DELETED сама не обрабатывает, а инкрементальная
        # синхронизация (по lastmod) удалений не видит — без pop вытесненный
        # контакт висел бы в mc.contacts до конца сессии.
        contact = mc.contacts.pop(pubkey, None)
        if contact:
            logger.warning(
                f"♻️  Память контактов заполнена — нода вытеснила {_describe(contact)}, "
                f"{_heard_text(contact, time.time())}"
            )
        else:
            logger.warning(f"♻️  Память контактов заполнена — нода вытеснила контакт {pubkey[:12]}")

    mc.subscribe(events.EventType.NEW_CONTACT, on_new_contact)
    mc.subscribe(events.EventType.CONTACTS_FULL, on_contacts_full)
    mc.subscribe(events.EventType.CONTACT_DELETED, on_contact_deleted)


async def _log_device_clock(mc) -> None:
    try:
        result = await mc.commands.get_time()
    except Exception as e:
        logger.warning(f"   ⚠️  Время ноды не получено: {e!r}")
        return
    if _is_error(result):
        logger.warning(f"   ⚠️  Время ноды не получено: {result.payload if result else None}")
        return
    device_time = result.payload.get("time", 0)
    drift = device_time - time.time()
    if abs(drift) > _CLOCK_DRIFT_WARN_SECONDS:
        drift_text = f"{drift / 86400:+.1f} сут" if abs(drift) >= 2 * 86400 else f"{drift / 3600:+.1f} ч"
        logger.warning(
            f"   ⚠️  Часы ноды ({_fmt_ts(device_time)}) расходятся с компьютером на "
            f"{drift_text} — lastmod контактов неточен, решает last_advert"
        )


async def cleanup_stale_contacts(mc, max_age_days: int) -> None:
    """Удаляет с ноды контакты, не слышанные дольше max_age_days (кроме избранных)."""
    logger.info(f"🧹 Чистка контактов: ищу не слышанные дольше {max_age_days} сут")
    await _log_device_clock(mc)

    # Список берём с ноды заново, а не из mc.contacts: туда не доходят удаления,
    # сделанные вне бота (например, из приложения).
    result = await mc.commands.get_contacts()
    if _is_error(result):
        logger.error(
            f"   ❌ Список контактов с ноды не получен: {result.payload if result else None} — чистка пропущена"
        )
        return
    contacts = list(result.payload.values())

    now = time.time()
    cutoff = now - max_age_days * 86400
    # Обе метки в будущем — возраст не определить, такой контакт не трогаем.
    heard_at = {c["public_key"]: _last_heard(c, now) for c in contacts}
    unknown = [c for c in contacts if heard_at[c["public_key"]] is None]
    stale = [
        c for c in contacts
        if heard_at[c["public_key"]] is not None and heard_at[c["public_key"]] < cutoff
    ]
    favourites = [c for c in stale if c.get("flags", 0) & _FLAG_FAVOURITE]
    to_remove = [c for c in stale if not c.get("flags", 0) & _FLAG_FAVOURITE]
    logger.info(
        f"   Контактов на ноде: {len(contacts)}, давно не слышно: {len(stale)}, "
        f"из них избранных (не трогаю): {len(favourites)}"
    )
    for contact in unknown:
        logger.warning(
            f"   ⏭️  Пропускаю {_describe(contact)}: обе метки времени в будущем, "
            f"возраст не определить ({_heard_text(contact, now)})"
        )
    for contact in favourites:
        logger.info(f"   ⭐ Пропускаю избранный {_describe(contact)}, {_heard_text(contact, now)}")

    removed = 0
    for contact in to_remove:
        heard = _heard_text(contact, now)
        try:
            res = await mc.commands.remove_contact(contact)
            error = None if not _is_error(res) else (res.payload if res else "нет ответа")
        except Exception as e:
            error = repr(e)
        if error is None:
            removed += 1
            mc.contacts.pop(contact["public_key"], None)
            logger.info(f"   🗑️  Удалён {_describe(contact)}, {heard}")
        else:
            logger.error(f"   ❌ Не удалось удалить {_describe(contact)}: {error}")

    logger.info(
        f"🧹 Чистка контактов завершена: удалено {removed} из {len(to_remove)}, "
        f"осталось {len(contacts) - removed}"
    )


async def contacts_cleanup_scheduler(mc, config: dict) -> None:
    cfg = config["contacts_cleanup"]
    max_age_days = cfg["max_age_days"]
    interval_seconds = cfg["interval_seconds"]
    if max_age_days <= 0 or interval_seconds <= 0:
        logger.info("🧹 Автоудаление старых контактов отключено")
        return

    logger.info(
        f"🧹 Автоудаление контактов, не слышанных дольше {max_age_days} сут: "
        f"при старте и каждые {_humanize_interval(interval_seconds)}"
    )
    await asyncio.sleep(5.0)
    while True:
        try:
            await cleanup_stale_contacts(mc, max_age_days)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка чистки контактов: {e!r}")
        await asyncio.sleep(interval_seconds)
