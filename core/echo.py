"""Эхо собственных передач бота: какой ретранслятор услышал то, что отправил бот.

Своя передача в RX_LOG_DATA не попадает — нода не слышит сама себя. Поэтому
пакет бота, услышанный в RX_LOG, — это ретрансляция: пакет ушёл в эфир, и его
подхватила сеть. Обратное неверно: нет эха — не значит, что пакет не дошёл
(нода могла не услышать ретрансляцию или совпасть с ней по времени).

Ловятся все отправки бота, а не только ответы на команды: `track_own_sends()`
один раз за сессию оборачивает `send_chan_msg`, `send_msg` и `send_advert`
у `mc.commands`, так что модулям-отправителям знать об этом не нужно.

Как опознаётся свой пакет:
- канал (GRP_TXT): та же метка времени и текст `"ИмяБота: ..."`. Одной метки
  мало — бот отвечает в ту же секунду, что пришла команда, и эхо самой команды
  совпадает с ответом по метке. Путь должен быть непустым.
- личка (TEXT_MSG): не расшифровывается (шифр пары узлов), поэтому — по
  однобайтовым хэшам отправителя (бот) и получателя в начале payload. Путь
  может быть и пустым: у лички по прямому маршруту каждый ретранслятор
  вычёркивает себя из пути. Однобайтовый хэш бывает общим у разных узлов,
  а несколько сообщений одному получателю (пустой ACK и ответ) по эху не
  различить — засчитывается последнее отправленное.
- advert: по публичному ключу бота, только flood — обычный (zero-hop) advert
  ретрансляторы не пересылают.
"""
import logging
import time

from meshcore import events

logger = logging.getLogger(__name__)

_PAYLOAD_TXT_MSG = 2
_PAYLOAD_ADVERT = 4
_PAYLOAD_GRP_TXT = 5

# Сколько помнить отправленное: эхо приходит через секунды, от дальних — позже.
_TTL_SECONDS = 60

_PREVIEW_CHARS = 30


class OwnSends:
    """Недавние передачи бота и сопоставление с ними пакетов из RX_LOG_DATA."""

    def __init__(self, mc):
        self._mc = mc
        self._sent: list[dict] = []

    def match(self, rx_log: dict) -> dict | None:
        """Запись о передаче бота, ретрансляцией которой является пакет, или None."""
        now = time.time()
        self._sent = [s for s in self._sent if now - s['sent_at'] <= _TTL_SECONDS]
        payload_type = rx_log.get('payload_type')
        path = rx_log.get('path') or ''
        for sent in reversed(self._sent):
            if sent['kind'] == 'chan':
                if payload_type != _PAYLOAD_GRP_TXT or not path:
                    continue
                if rx_log.get('sender_timestamp') != sent['timestamp']:
                    continue
                prefix = f"{self._mc.self_info.get('name', '')}: "
                message = rx_log.get('message') or ''
                # startswith, а не равенство — на случай, если прошивка урезала текст.
                if message.startswith(prefix) and f"{prefix}{sent['text']}".startswith(message):
                    return sent
            elif sent['kind'] == 'dm':
                if payload_type != _PAYLOAD_TXT_MSG:
                    continue
                pkt_payload = rx_log.get('pkt_payload') or b''
                if (pkt_payload[0:1].hex() == sent['dst']
                        and pkt_payload[1:2].hex() == self._self_hash()):
                    return sent
            elif sent['kind'] == 'advert':
                if (payload_type == _PAYLOAD_ADVERT and path
                        and rx_log.get('adv_key') == self._mc.self_info.get('public_key')):
                    return sent
        return None

    def _self_hash(self) -> str:
        return (self._mc.self_info.get('public_key') or '')[:2].lower()

    def _remember(self, kind: str, label: str, **fields) -> None:
        self._sent.append({'kind': kind, 'label': label, 'sent_at': time.time(), **fields})

    def on_rx_log(self, event) -> None:
        rx_log = event.payload or {}
        sent = self.match(rx_log)
        if sent is None:
            return
        path = rx_log.get('path') or ''
        chars = (rx_log.get('path_hash_size') or 1) * 2
        addrs = []
        for i in range(0, len(path), chars):
            prefix = path[i:i + chars]
            contact = self._mc.get_contact_by_key_prefix(prefix)
            name = contact.get('adv_name') if contact else None
            addrs.append(f"{prefix} ({name})" if name else prefix)
        route = f"путь={' → '.join(addrs)}" if addrs else "путь пуст (прямой маршрут)"
        delay = time.time() - sent['sent_at']
        logger.info(f"   📡 Ретранслятор услышал {sent['label']} через {delay:.1f} с: {route}, "
                    f"SNR={rx_log.get('snr', '?')}, RSSI={rx_log.get('rssi', '?')}")

    def install(self) -> None:
        commands = self._mc.commands
        send_chan_msg = commands.send_chan_msg
        send_msg = commands.send_msg
        send_advert = commands.send_advert

        async def tracked_send_chan_msg(chan, msg, timestamp=None):
            # Метка нужна для сопоставления — задаём её сами, как сделала бы библиотека.
            if timestamp is None:
                timestamp = int(time.time())
            result = await send_chan_msg(chan, msg, timestamp=timestamp)
            if isinstance(timestamp, bytes):
                timestamp = int.from_bytes(timestamp, "little")
            self._remember('chan', f"сообщение в канале {chan} «{_preview(msg)}»",
                           timestamp=timestamp, text=msg)
            return result

        async def tracked_send_msg(dst, msg, *args, **kwargs):
            result = await send_msg(dst, msg, *args, **kwargs)
            dst_hash = _dst_hash(dst)
            if dst_hash:
                contact = self._mc.get_contact_by_key_prefix(dst_hash) if len(dst_hash) > 2 else None
                who = contact.get('adv_name') if contact else dst_hash
                text = f"«{_preview(msg)}»" if msg else "(пустой ACK)"
                self._remember('dm', f"личку для {who} {text}", dst=dst_hash[:2])
            return result

        async def tracked_send_advert(flood=False):
            result = await send_advert(flood=flood)
            if flood:
                self._remember('advert', "flood-advert бота")
            return result

        commands.send_chan_msg = tracked_send_chan_msg
        commands.send_msg = tracked_send_msg
        commands.send_advert = tracked_send_advert
        self._mc.subscribe(events.EventType.RX_LOG_DATA, self.on_rx_log)


def _preview(text: str) -> str:
    return text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else "")


def _dst_hash(dst) -> str:
    """Префикс ключа получателя в hex — сколько его есть в `dst` (не меньше байта)."""
    if isinstance(dst, dict):
        dst = dst.get('public_key') or ''
    if isinstance(dst, bytes):
        dst = dst.hex()
    return dst.lower() if isinstance(dst, str) and len(dst) >= 2 else ''


def track_own_sends(mc) -> OwnSends:
    """Включает логирование эха всех передач бота на время сессии с `mc`."""
    own_sends = OwnSends(mc)
    own_sends.install()
    return own_sends
