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
import asyncio
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

# Сколько ждать, что ретранслятор услышит сообщение (нода слышит его ретрансляцию;
# по логам эхо приходит через 2.6–3.9 с), и сколько раз переотправить, если не услышал.
_ECHO_WAIT_SECONDS = 6.0
_ECHO_RETRIES = 1

# Минимальный промежуток между отправками бота (канал, advert) от конца предыдущей.
_SEND_GAP_SECONDS = 3.0


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

        # Все отправки бота — по одной и с паузой: шедулеры шлют независимо и при
        # старте попадали в эфир пачкой (ретрансляторы слышали пакеты внахлёст, часть
        # терялась), а параллельные команды библиотека не разводит — ответ ноды на одну
        # засчитывался другой.
        lock = asyncio.Lock()
        last_sent_at = 0.0

        async def paced(send, *args, gap=True, **kwargs):
            nonlocal last_sent_at
            async with lock:
                wait = last_sent_at + _SEND_GAP_SECONDS - time.monotonic()
                if gap and wait > 0:
                    await asyncio.sleep(wait)
                try:
                    return await send(*args, **kwargs)
                finally:
                    last_sent_at = time.monotonic()

        async def tracked_send_chan_msg(chan, msg, timestamp=None):
            # Метка нужна для сопоставления — задаём её сами, как сделала бы библиотека.
            if timestamp is None:
                timestamp = int(time.time())
            result = await paced(send_chan_msg, chan, msg, timestamp=timestamp)
            if isinstance(timestamp, bytes):
                timestamp = int.from_bytes(timestamp, "little")
            self._remember('chan', f"сообщение в канале {chan} «{_preview(msg)}»",
                           timestamp=timestamp, text=msg)
            return result

        async def tracked_send_msg(dst, msg, *args, **kwargs):
            # Личка — без паузы: ACK и ответ идут подряд, как и раньше.
            result = await paced(send_msg, dst, msg, *args, gap=False, **kwargs)
            dst_hash = _dst_hash(dst)
            if dst_hash:
                contact = self._mc.get_contact_by_key_prefix(dst_hash) if len(dst_hash) > 2 else None
                who = contact.get('adv_name') if contact else dst_hash
                text = f"«{_preview(msg)}»" if msg else "(пустой ACK)"
                self._remember('dm', f"личку для {who} {text}", dst=dst_hash[:2])
            return result

        async def tracked_send_advert(flood=False):
            result = await paced(send_advert, flood=flood)
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


async def send_until_heard(mc, channel_idx: int, message: str, *, label: str,
                           target: str) -> bool:
    """Отправка в канал с проверкой, что её услышал ретранслятор.

    Признак — нода слышит ретрансляцию своего же пакета (RX_LOG_DATA с тем же
    текстом и меткой времени и с непустым путём). Своя передача в RX_LOG не
    попадает, так что эхо означает: пакет ушёл в эфир и его подхватила сеть.
    Нет эха за `_ECHO_WAIT_SECONDS` — переотправка, не больше `_ECHO_RETRIES`
    раз. Отсутствие эха не значит, что пакет не дошёл (нода могла не услышать
    ретрансляцию), поэтому повторов немного — иначе в канале будут дубли.

    Повтор — с новой меткой времени: пакет с прежней дал бы тот же хэш, и
    ретрансляторы отбросили бы его как уже пересланный. Эхо любой из попыток
    засчитывается, даже если пришло уже после повтора.

    `label` и `target` — только для логов («Версия ommesh», «канал ссылок 4»).
    Само эхо (путь, SNR) пишет в лог `OwnSends`, здесь — только номер попытки,
    которую оно подтвердило.

    Возвращает False, если нода не приняла сообщение или отправка не удалась.
    """
    expected = f"{mc.self_info.get('name', '')}: {message}"
    attempts: dict[int, int] = {}  # sender_timestamp -> номер попытки
    heard = asyncio.Event()

    def on_rx_log(event):
        rx_log = event.payload
        attempt = attempts.get(rx_log.get('sender_timestamp'))
        if (attempt is None or rx_log.get('payload_type') != _PAYLOAD_GRP_TXT
                or rx_log.get('message') != expected or not rx_log.get('path')):
            return
        if not heard.is_set():
            logger.info(f"📡 {label}: сообщение в {target} услышано ретранслятором "
                        f"(попытка {attempt})")
        heard.set()

    subscription = mc.subscribe(events.EventType.RX_LOG_DATA, on_rx_log)
    try:
        for attempt in range(1, _ECHO_RETRIES + 2):
            timestamp = max(int(time.time()), max(attempts, default=0) + 1)
            attempts[timestamp] = attempt
            try:
                result = await mc.commands.send_chan_msg(channel_idx, message, timestamp=timestamp)
            except Exception as e:
                logger.error(f"📭 {label}: в {target} не отправлено (попытка {attempt}): ошибка {e}")
                return False
            if result is None or result.type == events.EventType.ERROR:
                logger.error(f"📭 {label}: нода не приняла сообщение в {target} (попытка "
                             f"{attempt}): {result.payload if result else 'нет ответа'}")
                return False
            logger.info(f"📤 {label}: отправлено в {target} (попытка {attempt}): {message}")
            try:
                await asyncio.wait_for(heard.wait(), _ECHO_WAIT_SECONDS)
                return True
            except asyncio.TimeoutError:
                pass
            if attempt <= _ECHO_RETRIES:
                logger.warning(f"📡 {label}: за {_ECHO_WAIT_SECONDS:g} с ни один ретранслятор "
                               f"не услышал сообщение в {target} (попытка {attempt}) — "
                               f"отправляю повторно")
            else:
                logger.warning(f"📡 {label}: ретранслятор не услышал сообщение в {target} "
                               f"и после {attempt} попыток — больше не повторяю")
        return True
    finally:
        mc.unsubscribe(subscription)


def track_own_sends(mc) -> OwnSends:
    """Включает логирование эха всех передач бота на время сессии с `mc`."""
    own_sends = OwnSends(mc)
    own_sends.install()
    return own_sends
