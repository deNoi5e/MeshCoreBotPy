"""Мультиплексор компаньон-протокола MeshCore: одна нода по USB — несколько TCP-клиентов.

Нода (прошивка companion) рассчитана на одного клиента. Этот сервис — единственный
владелец COM-порта и сам выступает «нодой по WiFi»: слушает TCP и говорит тем же
кадровым протоколом (`<` + длина LE16 + payload от клиента, `>` + то же от ноды),
поэтому к нему подключаются и бот (`MESHCORE_PORT=tcp://127.0.0.1:5000`), и
официальное приложение (вкладка TCP).

Что делает мультиплексор:
- Команды клиентов идут в общую очередь и уходят на ноду по одной: ответы в протоколе
  не помечены номером запроса, различить их можно только по порядку. Ответ (в том числе
  многокадровый — список контактов) возвращается тому клиенту, чья команда в полёте.
- Push-кадры ноды (код >= 0x80: advert, ACK, RX_LOG…) рассылаются всем клиентам.
- Очередь сообщений на ноде одна, и чтение из неё разрушающее. Поэтому сообщения
  забирает сам мультиплексор (по push MESSAGES_WAITING и по таймеру) и раскладывает
  копиями по «слотам» клиентов: loopback-подключения — слот `bot`, остальные — `app`.
  Слот хранит сообщения и пока клиента нет (как офлайн-очередь ноды), поэтому
  приложение, подключившееся утром, получит накопленное за ночь.
- Агент ommesh подключается на отдельный порт (agent_port): слот `agent` определяется
  портом, а не адресом. Сообщений он не получает (только push-кадры и ответы на свои
  команды). Параметры его заданий приходят от сервера Ommesh, поэтому на ноду пускаются только
  нужные ему команды (белый список с потолками длины, лимиты частоты эфирных команд и advert,
  правка контактов — только репитеров, подпись — только кода сервера с префиксом агента);
  остальное отвечается ERROR и пишется в лог.
- Кадр клиента недопустимой длины отключает клиента (строгий разбор): иначе байты внутри
  слишком длинного кадра, ресинхронизацией, превратились бы во вложенную команду.
- DEVICE_QUERY мультиплексор отвечает сам из кэша, а ноду держит на протоколе v3
  (сообщения в формате *_V3, с SNR): версию в ноде хранит одно значение, и клиент
  с другой версией иначе переключил бы формат кадров всем.
- Обрыв связи с нодой (USB выдернули, нода перезагрузилась, команда REBOOT) закрывает
  все клиентские соединения — как перезагрузка настоящей ноды; клиенты переподключаются
  сами, мультиплексор переоткрывает порт и заново инициализирует ноду.
"""

import asyncio
import logging
import socket
from collections import deque
from typing import Awaitable, Callable, Optional, Protocol

from meshcore import SerialConnection
from meshcore.packets import CommandType

logger = logging.getLogger(__name__)

# Имена команд для лога команд агента (код → имя)
_CMD_NAMES = {c.value: c.name for c in CommandType}

# Команды (CommandType в meshcore/packets.py)
CMD_APP_START = 1
CMD_SEND_CHANNEL_TXT_MSG = 3
CMD_GET_CONTACTS = 4
CMD_GET_DEVICE_TIME = 5
CMD_SET_DEVICE_TIME = 6
CMD_SEND_SELF_ADVERT = 7
CMD_ADD_UPDATE_CONTACT = 9
CMD_SYNC_NEXT_MESSAGE = 10
CMD_REBOOT = 19
CMD_DEVICE_QUERY = 22
CMD_SEND_LOGIN = 26
CMD_SEND_STATUS_REQ = 27
CMD_SIGN_START = 33
CMD_SIGN_DATA = 34
CMD_SIGN_FINISH = 35
CMD_SEND_TRACE_PATH = 36
CMD_SEND_BINARY_REQ = 50
CMD_SEND_CONTROL_DATA = 55

# Ответы
RESP_OK = 0
RESP_ERROR = 1
RESP_CONTACT_START = 2
RESP_CONTACT = 3
RESP_SELF_INFO = 5
RESP_NO_MORE_MSGS = 10
RESP_DEVICE_INFO = 13
RESP_CHANNEL_MSG_V3 = 17
# Имя узла в SELF_INFO — хвост кадра после 58 байт: код, 3 байта (тип, мощности),
# публичный ключ 32, широта и долгота 8, 4 байта настроек, частота и полоса 8, SF и CR 2
_SELF_INFO_NAME_OFFSET = 58
# CONTACT_MSG_RECV, CHANNEL_MSG_RECV, их V3-варианты и CHANNEL_DATA_RECV
MESSAGE_CODES = frozenset({7, 8, 16, 17, 27})

PUSH_FIRST = 0x80
PUSH_MESSAGES_WAITING = 0x83

# Протокол, который держим на ноде. 3 — последний формат кадров сообщений
# (CHANNEL_MSG_RECV_V3 несёт txt_hash, по которому бот подбирает маршрут).
NODE_PROTO_VER = 3

# Команды, на которые нода не отвечает (после REBOOT она перезагружается молча).
NO_RESPONSE_CMDS = frozenset({CMD_REBOOT})

_MAX_FRAME = 300             # как в библиотеке meshcore: больше — мусор
_RESPONSE_TIMEOUT = 8.0      # ожидание следующего кадра ответа от ноды, с
_INIT_TIMEOUT = 3.0          # то же для команд инициализации
_INIT_ATTEMPTS = 3           # на Linux первая команда после открытия порта бывает потеряна
_DRAIN_POLL_SECONDS = 10.0   # страховка на случай потерянного push MESSAGES_WAITING
_DRAIN_LIMIT = 200           # не больше сообщений за один проход (защита от зацикливания)
_SLOT_QUEUE_LIMIT = 200      # сообщений на слот, как у офлайн-очереди ноды
_JOBS_LIMIT = 64             # команд в очереди; больше — клиенту ERROR
_AGENT_REPORT_SECONDS = 1800  # как часто в лог идёт сводка команд агента
_MAX_WRITE_BUFFER = 1 << 20  # клиент, не читающий больше 1 МБ, отключается
_ERROR_FRAME = bytes([RESP_ERROR, 4])  # ERR_CODE_BAD_STATE
_DENIED_FRAME = bytes([RESP_ERROR, 1])  # ERR_CODE_UNSUPPORTED_CMD: мультиплексор команду не пропустил

# --- Что можно агенту ommesh ---
# Агент выполняет задания сервера Ommesh, а параметры заданий (пути, пароли, ключи контактов, длины)
# приходят от сервера и агентом почти не проверяются. Поэтому на ноду пропускаются только команды,
# которыми агент и пользуется (по логу мультиплексора), каждая — с потолком длины кадра.
_AGENT_ALLOWED: dict[int, int] = {
    CMD_APP_START: 64,
    CMD_GET_CONTACTS: 5,
    CMD_GET_DEVICE_TIME: 1,
    CMD_SET_DEVICE_TIME: 5,
    CMD_SEND_SELF_ADVERT: 2,
    CMD_ADD_UPDATE_CONTACT: 148,   # 144 байта + необязательные lastmod
    CMD_SYNC_NEXT_MESSAGE: 1,
    CMD_DEVICE_QUERY: 2,
    CMD_SEND_LOGIN: 65,            # ключ 32 + пароль
    CMD_SEND_STATUS_REQ: 33,
    CMD_SIGN_START: 1,
    CMD_SIGN_DATA: 31,             # префикс 14 + код 16, см. _PROOF_PREFIX
    CMD_SIGN_FINISH: 1,
    CMD_SEND_TRACE_PATH: 74,       # 10 байт заголовка + путь не длиннее 64 (MAX_PATH_SIZE)
    CMD_SEND_BINARY_REQ: 64,
    CMD_SEND_CONTROL_DATA: 24,
}
# Подпись агент даёт только над «OMMESH-AGENT\0\x01» + 16 байт кода сервера: это доказательство
# владения нодой, а не подпись произвольных данных ключом ноды.
_PROOF_PREFIX = b"OMMESH-AGENT\0\x01"
_ADV_TYPE_REPEATER = 2
_CONTACT_KIND_OFFSET = 33          # в ADD_UPDATE_CONTACT: код, ключ 32 байта, затем тип узла
_ADD_UPDATE_CONTACT_MIN = 144
# Команды, которые уходят в эфир по заданию сервера: их частота ограничена, чтобы сервер не мог
# забить эфир и очередь ноды трассировками и входами на репитеры.
_AGENT_AIR_CMDS = frozenset({CMD_SEND_LOGIN, CMD_SEND_STATUS_REQ, CMD_SEND_TRACE_PATH,
                             CMD_SEND_BINARY_REQ, CMD_SEND_CONTROL_DATA})
_AGENT_AIR_LIMIT = 30              # не больше стольких за окно (опрос одного репитера — до ~9)
_AGENT_AIR_WINDOW = 60.0           # окно, с
_AGENT_ADVERT_MIN_SECONDS = 300.0  # flood-advert ноды — не чаще раза в 5 минут


def _frame(marker: bytes, payload: bytes) -> bytes:
    return marker + len(payload).to_bytes(2, "little") + payload


class _FrameParser:
    """Разбор потока кадров `<marker> + длина LE16 + payload` с ресинхронизацией.

    strict — для клиентов: кадр недопустимой длины не пропускается с поиском следующего маркера,
    а останавливает разбор (`error`), и клиента отключают. Иначе тот, кто может подбирать байты
    внутри слишком длинного кадра (путь трассировки агента приходит от сервера Ommesh), вложил
    бы туда кадр любой команды: после ресинхронизации он разобрался бы как отдельный.
    """

    def __init__(self, marker: bytes, strict: bool = False):
        self._marker = marker
        self._strict = strict
        self._buf = bytearray()
        self.error: Optional[str] = None

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out: list[bytes] = []
        buf = self._buf
        while self.error is None:
            start = buf.find(self._marker)
            if start < 0:
                buf.clear()
                break
            if start:
                del buf[:start]
            if len(buf) < 3:
                break
            size = int.from_bytes(buf[1:3], "little")
            if size == 0 or size > _MAX_FRAME:
                if self._strict:
                    self.error = f"недопустимая длина кадра {size}"
                    buf.clear()
                    break
                del buf[0]
                continue
            if len(buf) < 3 + size:
                break
            out.append(bytes(buf[3:3 + size]))
            del buf[:3 + size]
        return out


class Upstream(Protocol):
    """Канал до ноды. Подменяется в тестах (scripts/test_mux.py)."""

    async def connect(self, on_frame: Callable[[bytes], None],
                      on_lost: Callable[[str], Awaitable[None]]) -> None: ...

    async def send(self, frame: bytes) -> None: ...

    async def close(self) -> None: ...


class SerialUpstream:
    """Нода по USB: кадровый разбор и открытие порта берём из библиотеки meshcore."""

    def __init__(self, port: str):
        self._cx = SerialConnection(port, 115200, cx_dly=0.1)

    async def connect(self, on_frame, on_lost) -> None:
        class _Reader:
            async def handle_rx(self, data):
                on_frame(bytes(data))

        self._cx.set_reader(_Reader())
        self._cx.set_disconnect_callback(on_lost)
        await self._cx.connect()

    async def send(self, frame: bytes) -> None:
        await self._cx.send(frame)

    async def close(self) -> None:
        # Свой disconnect_callback при штатном закрытии не нужен.
        self._cx.set_disconnect_callback(None)
        await self._cx.disconnect()


class _Job:
    """Одна команда в очереди на ноду. client=None — внутренняя (инициализация, чтение сообщений)."""

    def __init__(self, client: Optional["_Client"], frame: bytes, timeout: Optional[float] = None):
        self.client = client
        self.frame = frame
        self.timeout = _RESPONSE_TIMEOUT if timeout is None else timeout
        self.frames: list[bytes] = []
        self.done: asyncio.Future = asyncio.get_running_loop().create_future()


class _Client:
    def __init__(self, mux: "Mux", reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 slot: str, peer: str):
        self.mux = mux
        self.reader = reader
        self.writer = writer
        self.slot = slot
        self.peer = peer

    @property
    def closed(self) -> bool:
        return self.writer.transport.is_closing()

    def send_frame(self, payload: bytes) -> None:
        transport = self.writer.transport
        if transport.is_closing():
            return
        if transport.get_write_buffer_size() > _MAX_WRITE_BUFFER:
            logger.warning(f"🔌 Клиент {self.peer} не читает ответы — отключаю")
            self.close()
            return
        self.writer.write(_frame(b">", payload))

    def close(self) -> None:
        if not self.writer.transport.is_closing():
            self.writer.close()

    async def serve(self) -> None:
        parser = _FrameParser(b"<", strict=True)
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    break
                for payload in parser.feed(data):
                    self.mux.on_client_frame(self, payload)
                if parser.error:
                    logger.warning(f"🔌 Клиент {self.peer} ({self.slot}): {parser.error} — отключаю")
                    break
        except (ConnectionError, OSError) as e:
            logger.info(f"🔌 Клиент {self.peer}: обрыв соединения ({e!r})")
        finally:
            self.close()


class Mux:
    def __init__(self, upstream_factory: Callable[[], Upstream], host: str, port: int,
                 reconnect_delay: float = 15.0, agent_host: str = "127.0.0.1",
                 agent_port: Optional[int] = None):
        self._upstream_factory = upstream_factory
        self._host = host
        self._port = port
        # Отдельный порт для агента ommesh (None — выключен). Слот агента определяется портом,
        # а не адресом: на одной машине с ботом он тоже приходит с loopback.
        self._agent_host = agent_host
        self._agent_port = agent_port
        self.agent_server: Optional[asyncio.AbstractServer] = None
        self._agent_cmds: dict[int, int] = {}
        # Что агенту не разрешено: счётчик по коду команды и причины, о которых уже писали в лог.
        self._agent_denied: dict[int, int] = {}
        self._agent_denied_logged: set[tuple[int, str]] = set()
        self._agent_air: deque = deque()          # моменты эфирных команд агента в текущем окне
        self._agent_last_advert = float("-inf")   # когда агенту последний раз разрешили advert
        self._reconnect_delay = reconnect_delay
        self._clients: list[_Client] = []
        # Очереди сообщений живут дольше сессии с нодой: перезагрузка ноды не должна
        # стирать то, что приложение ещё не забрало. Слота у агента нет: сообщений ему не отдаём
        # (SYNC_NEXT_MESSAGE → «пусто», MESSAGES_WAITING не шлём) — ему нужен эфир, а не переписка.
        self._slots: dict[str, deque] = {"bot": deque(), "app": deque()}
        # Сколько сообщений слот вытеснил в текущем переполнении: в лог — одна строка
        # в начале и одна в конце, а не по строке на каждое сообщение.
        self._slot_lost: dict[str, int] = {}
        # Состояние текущей сессии с нодой (None/пусто между сессиями)
        self._upstream: Optional[Upstream] = None
        self._ready = False
        self._jobs: Optional[asyncio.Queue] = None
        self._responses: Optional[asyncio.Queue] = None
        self._lost: Optional[asyncio.Event] = None
        self._drain_event: Optional[asyncio.Event] = None
        self._device_info: Optional[bytes] = None
        self._node_name: Optional[bytes] = None
        self.server: Optional[asyncio.AbstractServer] = None

    # ---- жизненный цикл ----

    async def run(self) -> None:
        self.server = await asyncio.start_server(self._on_connect, self._host, self._port)
        addrs = ", ".join(str(s.getsockname()) for s in self.server.sockets)
        logger.info(f"🔀 Мультиплексор слушает {addrs}")
        report_task = None
        if self._agent_port is not None:
            self.agent_server = await asyncio.start_server(
                lambda r, w: self._on_connect(r, w, "agent"), self._agent_host, self._agent_port)
            addrs = ", ".join(str(s.getsockname()) for s in self.agent_server.sockets)
            logger.info(f"🛰 Порт агента слушает {addrs}")
            report_task = asyncio.create_task(self._agent_report_loop())
        try:
            while True:
                try:
                    await self._session()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"❌ Сессия с нодой завершилась: {e!r}")
                logger.info(f"⏳ Переподключение к ноде через {self._reconnect_delay:.0f} с")
                await asyncio.sleep(self._reconnect_delay)
        finally:
            self.server.close()
            if self.agent_server is not None:
                self.agent_server.close()
            if report_task is not None:
                report_task.cancel()

    async def _session(self) -> None:
        upstream = self._upstream_factory()
        self._upstream = upstream
        self._lost = asyncio.Event()
        self._jobs = asyncio.Queue()
        self._responses = None
        self._drain_event = asyncio.Event()
        tasks: list[asyncio.Task] = []
        try:
            await upstream.connect(self._on_upstream_frame, self._on_upstream_lost)
            logger.info("🔌 Нода подключена")
            tasks.append(asyncio.create_task(self._worker()))
            await self._init_node()
            tasks.append(asyncio.create_task(self._drainer()))
            self._drain_event.set()
            self._ready = True
            logger.info("✅ Нода готова, принимаю клиентов")
            lost_task = asyncio.create_task(self._lost.wait())
            tasks.append(lost_task)
            await lost_task
        finally:
            self._ready = False
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._fail_pending_jobs()
            for c in list(self._clients):
                c.close()
            self._device_info = None
            self._upstream = None
            try:
                await upstream.close()
            except Exception as e:
                logger.warning(f"⚠️  Ошибка при закрытии порта ноды: {e!r}")

    async def _init_node(self) -> None:
        # Сессию на ноде открывает APP_START; сразу после него фиксируем протокол v3.
        start = bytes([CMD_APP_START, NODE_PROTO_VER]) + b"      mcmux"
        for attempt in range(1, _INIT_ATTEMPTS + 1):
            frames = await self._request_internal(start, _INIT_TIMEOUT)
            if frames and frames[-1][0] == RESP_SELF_INFO:
                break
            logger.warning(f"⚠️  Нода не ответила на APP_START (попытка {attempt}/{_INIT_ATTEMPTS})")
        else:
            raise ConnectionError("нода не ответила на APP_START")
        if not await self._refresh_device_info():
            raise ConnectionError("нода не ответила на DEVICE_QUERY")

    async def _refresh_device_info(self, inline: bool = False) -> bool:
        """Спросить ноду DEVICE_QUERY(v3) и обновить кэш. inline=True — из самого воркера:
        через очередь он бы ждал сам себя."""
        query = bytes([CMD_DEVICE_QUERY, NODE_PROTO_VER])
        if inline:
            job = _Job(None, query, _INIT_TIMEOUT)
            frames = job.frames if await self._exchange(job) else None
        else:
            frames = await self._request_internal(query, _INIT_TIMEOUT)
        if frames and frames[-1][0] == RESP_DEVICE_INFO:
            self._device_info = frames[-1]
            return True
        return False

    async def _on_upstream_lost(self, reason: str) -> None:
        if self._lost is not None and not self._lost.is_set():
            logger.warning(f"🔌 Связь с нодой потеряна: {reason}")
            self._lost.set()

    def _fail_pending_jobs(self) -> None:
        # Команды, не дошедшие до ноды, освобождаем: внутренние ждут future, клиентов закрываем отдельно.
        if self._jobs is None:
            return
        while not self._jobs.empty():
            job = self._jobs.get_nowait()
            if not job.done.done():
                job.done.set_result(None)

    # ---- кадры от ноды ----

    def _on_upstream_frame(self, frame: bytes) -> None:
        # Синхронно, без await: библиотека порождает по задаче на кадр, а порядок кадров важен.
        if not frame:
            return
        code = frame[0]
        if code >= PUSH_FIRST:
            if code == PUSH_MESSAGES_WAITING:
                # Клиентам скажем сами, когда разложим сообщение по слотам.
                if self._drain_event is not None:
                    self._drain_event.set()
                return
            for client in self._clients:
                client.send_frame(frame)
            return
        if self._responses is None:
            logger.warning(f"⚠️  Кадр ноды без запроса (код {code}), отбрасываю")
            return
        self._responses.put_nowait(frame)

    # ---- очередь команд ----

    async def _worker(self) -> None:
        while True:
            job = await self._jobs.get()
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"❌ Команда {job.frame[0]} не выполнена: {e!r}")
                self._finish_job(job, ok=False)

    async def _run_job(self, job: _Job) -> None:
        cmd = job.frame[0]
        client = job.client
        if client is not None and client.closed:
            # Отключился (или вытеснен) пока команда стояла в очереди: выполнять нечему,
            # а вынутое из слота сообщение уйти уже никому не сможет.
            self._finish_job(job, ok=True)
            return
        if client is not None and cmd == CMD_SYNC_NEXT_MESSAGE:
            queue = self._slots.get(client.slot)  # у агента слота нет — всегда «пусто»
            client.send_frame(queue.popleft() if queue else bytes([RESP_NO_MORE_MSGS]))
            lost = self._slot_lost.get(client.slot)
            if lost and queue is not None and len(queue) < _SLOT_QUEUE_LIMIT:
                del self._slot_lost[client.slot]
                logger.info(f"📭 Слот {client.slot}: за время переполнения вытеснено сообщений: {lost}")
            self._finish_job(job, ok=True)
            return
        if client is not None and cmd == CMD_DEVICE_QUERY and self._device_info is not None:
            client.send_frame(self._device_info)
            self._finish_job(job, ok=True)
            return

        ok = await self._exchange(job)
        if cmd == CMD_APP_START and ok:
            # Нода могла взять версию протокола из APP_START клиента — возвращаем v3.
            await self._refresh_device_info(inline=True)
        if (ok and cmd == CMD_SEND_CHANNEL_TXT_MSG and client is not None and client.slot == "bot"
                and job.frames and job.frames[0][0] == RESP_OK):
            self._mirror_bot_channel_message(job.frame)
        self._finish_job(job, ok)

    async def _exchange(self, job: _Job) -> bool:
        """Отправить команду на ноду и раздать кадры ответа. False — нода не ответила."""
        cmd = job.frame[0]
        self._responses = asyncio.Queue()
        try:
            await self._upstream.send(job.frame)
            if cmd in NO_RESPONSE_CMDS:
                return True
            while True:
                try:
                    frame = await asyncio.wait_for(self._responses.get(), job.timeout)
                except asyncio.TimeoutError:
                    logger.warning(f"⚠️  Нода не ответила на команду {cmd} за {job.timeout:.0f} с")
                    return False
                job.frames.append(frame)
                if frame[0] == RESP_SELF_INFO and len(frame) > _SELF_INFO_NAME_OFFSET:
                    self._node_name = frame[_SELF_INFO_NAME_OFFSET:].rstrip(b"\0")
                if frame[0] == RESP_ERROR and job.client is not None and job.client.slot == "agent":
                    logger.info(f"🛰 Агент: нода ответила ERROR на команду {cmd} "
                                f"({_CMD_NAMES.get(cmd, '?')}), ответ {frame.hex()}")
                if job.client is not None:
                    job.client.send_frame(frame)
                # Список контактов — START, N × CONTACT, END; остальные команды отвечают одним кадром.
                if not (cmd == CMD_GET_CONTACTS and frame[0] in (RESP_CONTACT_START, RESP_CONTACT)):
                    return True
        finally:
            self._responses = None

    def _finish_job(self, job: _Job, ok: bool) -> None:
        if not ok and job.client is not None:
            job.client.send_frame(_ERROR_FRAME)
        if not job.done.done():
            job.done.set_result(job.frames if ok else None)

    async def _request_internal(self, frame: bytes, timeout: Optional[float] = None) -> Optional[list[bytes]]:
        job = _Job(None, frame, timeout)
        self._jobs.put_nowait(job)
        return await job.done

    # ---- разбор очереди сообщений ноды ----

    async def _drainer(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._drain_event.wait(), _DRAIN_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._drain_event.clear()
            await self._drain()

    async def _drain(self) -> None:
        for _ in range(_DRAIN_LIMIT):
            frames = await self._request_internal(bytes([CMD_SYNC_NEXT_MESSAGE]))
            if not frames:
                return
            frame = frames[-1]
            if frame[0] == RESP_NO_MORE_MSGS:
                return
            if frame[0] not in MESSAGE_CODES:
                logger.warning(f"⚠️  На SYNC_NEXT_MESSAGE пришёл кадр с кодом {frame[0]}")
                return
            self._store_message(frame)

    def _mirror_bot_channel_message(self, command: bytes) -> None:
        """Показать приложению сообщение в канал, которое отправил бот.

        Нода входящими считает только чужие сообщения, а своё отправившему клиенту не возвращает:
        приложение рисует то, что отправило само, но ответы бота (другой клиент той же ноды)
        в нём иначе не видны. Поэтому по принятой нодой команде отправки собираем кадр
        CHANNEL_MSG_RECV_V3 и кладём в слот приложения. Боту копию не отдаём: он принял бы
        собственный ответ за входящее сообщение и мог бы обработать его как команду.
        """
        # Команда: код, тип текста, канал, метка времени (4), текст
        if len(command) < 8 or not self._node_name:
            return
        # Нода при передаче сама приписывает перед текстом «ИмяУзла: » — так его увидят и все остальные.
        text = self._node_name + b": " + command[7:]
        # SNR 0, 2 зарезервированных байта, канал, путь 0 хопов, тип текста, метка времени
        frame = (bytes([RESP_CHANNEL_MSG_V3, 0, 0, 0, command[2], 0, command[1]])
                 + command[3:7] + text)
        self._store_message(frame, only_slots=("app",))

    def _store_message(self, frame: bytes, only_slots: Optional[tuple] = None) -> None:
        for slot, queue in self._slots.items():
            if only_slots is not None and slot not in only_slots:
                continue
            if len(queue) >= _SLOT_QUEUE_LIMIT:
                queue.popleft()
                if slot not in self._slot_lost:
                    logger.warning(f"⚠️  Очередь слота {slot} переполнена ({_SLOT_QUEUE_LIMIT}) — "
                                   f"самые старые сообщения вытесняются")
                self._slot_lost[slot] = self._slot_lost.get(slot, 0) + 1
            queue.append(frame)
        for client in self._clients:
            if client.slot in self._slots and (only_slots is None or client.slot in only_slots):
                client.send_frame(bytes([PUSH_MESSAGES_WAITING]))

    # ---- клиенты ----

    def slot_for(self, host: str) -> str:
        """Слот клиента по адресу: бот ходит с этой же машины, всё остальное — приложение."""
        return "bot" if host in ("127.0.0.1", "::1") else "app"

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          slot: Optional[str] = None) -> None:
        """slot задан у порта агента; у основного порта слот определяется по адресу клиента."""
        peer_addr = writer.get_extra_info("peername") or ("?", 0)
        peer = f"{peer_addr[0]}:{peer_addr[1]}"
        if not self._ready:
            logger.info(f"🔌 Клиент {peer} отклонён: нода не готова")
            writer.close()
            return
        if slot is None:
            slot = self.slot_for(peer_addr[0])
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        client = _Client(self, reader, writer, slot, peer)
        # Как у ноды по WiFi: новое подключение вытесняет прежнее. Иначе у «зависшего»
        # телефона (сменил сеть) осталось бы мёртвое соединение, ждущее таймаута TCP.
        for old in [c for c in self._clients if c.slot == slot]:
            logger.info(f"🔌 Клиент {old.peer} ({slot}) вытеснен новым {peer}")
            old.close()
            self._clients.remove(old)
        self._clients.append(client)
        queued = f", в очереди {len(self._slots[slot])} сообщ." if slot in self._slots else ""
        logger.info(f"🔌 Клиент {peer} подключился (слот {slot}{queued})")
        try:
            await client.serve()
        finally:
            if client in self._clients:
                self._clients.remove(client)
            logger.info(f"🔌 Клиент {peer} ({slot}) отключился")
            if slot == "agent":
                self._log_agent_summary()

    def _note_agent_command(self, payload: bytes) -> None:
        """Агент ommesh — чёрный ящик: какие команды он шлёт, заранее неизвестно. Первую команду
        каждого кода пишем в лог с началом кадра, остальные считаем — по сводке потом решим,
        что ему разрешать (белый список), а что закрыть."""
        cmd = payload[0]
        count = self._agent_cmds[cmd] = self._agent_cmds.get(cmd, 0) + 1
        if count == 1:
            tail = "…" if len(payload) > 24 else ""
            logger.info(f"🛰 Агент: первая команда {cmd} ({_CMD_NAMES.get(cmd, '?')}), "
                        f"{len(payload)} байт: {payload[:24].hex()}{tail}")

    def _agent_denied_reason(self, payload: bytes) -> Optional[str]:
        """Почему команду агента на ноду не пускаем (None — пускаем). Вызывается один раз на кадр:
        разрешённая эфирная команда сразу занимает место в окне частоты."""
        cmd = payload[0]
        limit = _AGENT_ALLOWED.get(cmd)
        if limit is None:
            return "команда агенту не разрешена"
        if len(payload) > limit:
            return f"кадр длиннее {limit} байт"
        if cmd == CMD_ADD_UPDATE_CONTACT and (len(payload) < _ADD_UPDATE_CONTACT_MIN
                                              or payload[_CONTACT_KIND_OFFSET] != _ADV_TYPE_REPEATER):
            return "менять можно только репитеры (полный кадр контакта)"
        if cmd == CMD_SIGN_DATA and (len(payload) != _AGENT_ALLOWED[CMD_SIGN_DATA]
                                     or payload[1:1 + len(_PROOF_PREFIX)] != _PROOF_PREFIX):
            return "подписывать можно только код сервера с префиксом OMMESH-AGENT"
        now = asyncio.get_running_loop().time()
        if cmd == CMD_SEND_SELF_ADVERT:
            if now - self._agent_last_advert < _AGENT_ADVERT_MIN_SECONDS:
                return f"advert чаще раза в {_AGENT_ADVERT_MIN_SECONDS:.0f} с"
            self._agent_last_advert = now
        elif cmd in _AGENT_AIR_CMDS:
            air = self._agent_air
            while air and now - air[0] > _AGENT_AIR_WINDOW:
                air.popleft()
            if len(air) >= _AGENT_AIR_LIMIT:
                return f"больше {_AGENT_AIR_LIMIT} эфирных команд за {_AGENT_AIR_WINDOW:.0f} с"
            air.append(now)
        return None

    def _note_agent_denied(self, cmd: int, reason: str) -> None:
        self._agent_denied[cmd] = self._agent_denied.get(cmd, 0) + 1
        # Одна строка на пару «команда — причина»: остальные только в сводке, иначе лог зальёт заданием в цикле.
        if (cmd, reason) not in self._agent_denied_logged:
            self._agent_denied_logged.add((cmd, reason))
            logger.warning(f"🛰 Агенту отказано: команда {cmd} ({_CMD_NAMES.get(cmd, '?')}) — {reason}")

    def _log_agent_summary(self) -> None:
        if not self._agent_cmds:
            return
        parts = ", ".join(f"{_CMD_NAMES.get(c, c)}={n}" for c, n in sorted(self._agent_cmds.items()))
        logger.info(f"🛰 Агент, команд с начала работы мультиплексора: {parts}")
        if self._agent_denied:
            denied = ", ".join(f"{_CMD_NAMES.get(c, c)}={n}" for c, n in sorted(self._agent_denied.items()))
            logger.info(f"🛰 Агенту отказано (из них): {denied}")

    async def _agent_report_loop(self) -> None:
        while True:
            await asyncio.sleep(_AGENT_REPORT_SECONDS)
            self._log_agent_summary()

    def on_client_frame(self, client: _Client, payload: bytes) -> None:
        cmd = payload[0]
        if client.slot == "agent":
            self._note_agent_command(payload)
            reason = self._agent_denied_reason(payload)
            if reason is not None:
                self._note_agent_denied(cmd, reason)
                client.send_frame(_DENIED_FRAME)
                return
        if cmd in (CMD_APP_START, CMD_DEVICE_QUERY) and len(payload) > 1:
            logger.info(f"📱 {client.peer}: команда {cmd}, версия протокола клиента {payload[1]}")
        if self._jobs is None or self._jobs.qsize() >= _JOBS_LIMIT:
            client.send_frame(_ERROR_FRAME)
            return
        self._jobs.put_nowait(_Job(client, payload))
