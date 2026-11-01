"""Сквозная проверка мультиплексора (core/mcmux.py) без железа: `python scripts/test_mux.py`.

Вместо COM-порта — фальшивая нода (FakeNode), поднимается настоящий TCP-сервер мультиплексора,
к нему подключаются «бот» (клиент библиотеки meshcore, как в bot.py) и «приложение»
(сырой клиент, шлющий кадры вручную). Код возврата 1, если хоть одна проверка не прошла.
"""

import asyncio
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshcore import EventType, MeshCore  # noqa: E402

from core import mcmux  # noqa: E402
from core.mcmux import Mux, _FrameParser, _frame  # noqa: E402

CONTACTS = 40
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(name)


def _u32(n: int) -> bytes:
    return n.to_bytes(4, "little")


def contact_frame(i: int) -> bytes:
    return (bytes([3]) + i.to_bytes(32, "big") + bytes([1, 0, 255]) + bytes(64)
            + f"node{i}".encode().ljust(32, b"\0") + _u32(1000 + i) + _u32(0) + _u32(0) + _u32(2000 + i))


def msg_frame(text: str) -> bytes:
    # CHANNEL_MSG_RECV_V3: code, snr, 2 reserved, channel, path_len, txt_type, timestamp, text
    return bytes([17, 40, 0, 0, 0, 1, 0]) + _u32(1700000000) + text.encode()


def msg_text(frame: bytes) -> str:
    return frame[11:].decode()


class FakeNode:
    """Нода-компаньон на уровне кадров: ровно столько, сколько нужно для проверок."""

    def __init__(self):
        self.proto_ver = None
        self.queue: deque[bytes] = deque()
        self.sessions = 0
        self.count: dict[int, int] = {}
        self.silent_cmd = 99          # на эту команду нода молчит (проверка таймаута)
        self.up = None

    def make_upstream(self):
        return FakeUpstream(self)

    def inject(self, text: str) -> None:
        self.queue.append(msg_frame(text))
        self.up.emit(bytes([0x83]))


class FakeUpstream:
    def __init__(self, node: FakeNode):
        self.node = node
        self.on_frame = None
        self.on_lost = None

    async def connect(self, on_frame, on_lost):
        self.on_frame, self.on_lost = on_frame, on_lost
        self.node.sessions += 1
        self.node.up = self

    def emit(self, frame: bytes) -> None:
        asyncio.get_running_loop().call_soon(self.on_frame, frame)

    async def send(self, frame: bytes) -> None:
        asyncio.get_running_loop().call_later(0.005, self._handle, frame)

    async def close(self):
        pass

    def _handle(self, frame: bytes) -> None:
        node, cmd = self.node, frame[0]
        node.count[cmd] = node.count.get(cmd, 0) + 1
        if cmd == 1:
            node.proto_ver = frame[1]
            self.emit(bytes([5, 1, 20, 22]) + bytes(32) + bytes(8) + bytes([0, 0, 0, 0])
                      + _u32(869525) + _u32(250000) + bytes([11, 5]) + b"fake")
        elif cmd == 22:
            node.proto_ver = frame[1]
            self.emit(bytes([13, 3, 50, 40]) + _u32(123456) + b"fakefw".ljust(12, b"\0")
                      + b"FakeNode".ljust(40, b"\0") + b"1.0".ljust(20, b"\0"))
        elif cmd == 4:
            self.emit(bytes([2]) + _u32(CONTACTS))
            for i in range(CONTACTS):
                self.emit(contact_frame(i))
                if i == CONTACTS // 2:
                    self.emit(bytes([0x88, 1, 2, 3]))  # push посреди многокадрового ответа
            self.emit(bytes([4]) + _u32(3000))
        elif cmd == 10:
            self.emit(node.queue.popleft() if node.queue else bytes([10]))
        elif cmd == 5:
            self.emit(bytes([9]) + _u32(1700000000))
        elif cmd == 19:
            asyncio.get_running_loop().call_later(0.05, lambda: asyncio.create_task(self.on_lost("serial_disconnect")))
        elif cmd == node.silent_cmd:
            pass
        else:
            self.emit(bytes([0]))


class RawClient:
    """«Приложение»: кадры вручную, как по TCP к ноде с WiFi-прошивкой."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.responses: asyncio.Queue = asyncio.Queue()
        self.pushes: list[bytes] = []
        self.closed = asyncio.Event()
        self._task = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, port: int) -> "RawClient":
        return cls(*await asyncio.open_connection("127.0.0.1", port))

    async def _read(self):
        parser = _FrameParser(b">")
        try:
            while data := await self.reader.read(4096):
                for payload in parser.feed(data):
                    if payload[0] >= 0x80:
                        self.pushes.append(payload)
                    else:
                        self.responses.put_nowait(payload)
        except (ConnectionError, OSError):
            pass
        self.closed.set()

    def send(self, payload: bytes) -> None:
        self.writer.write(_frame(b"<", payload))

    async def request(self, payload: bytes, timeout: float = 3.0) -> bytes:
        self.send(payload)
        return await asyncio.wait_for(self.responses.get(), timeout)

    async def close(self) -> None:
        self.writer.close()
        await asyncio.wait_for(self.closed.wait(), 2)


async def wait_for(cond, timeout: float = 3.0) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def pull_all(app: RawClient) -> list[str]:
    texts = []
    while True:
        frame = await app.request(bytes([10]))
        if frame[0] == 10:
            return texts
        texts.append(msg_text(frame))


async def main() -> None:
    node = FakeNode()
    mux = Mux(node.make_upstream, "127.0.0.1", 0, reconnect_delay=0.3)
    slot = ["bot"]
    mux.slot_for = lambda host: slot[0]
    mux_task = asyncio.create_task(mux.run())
    await wait_for(lambda: mux._ready)
    port = mux.server.sockets[0].getsockname()[1]
    print(f"мультиплексор на порту {port}")

    print("1. Бот (библиотека meshcore) и версия протокола")
    slot[0] = "bot"
    bot = await MeshCore.create_tcp("127.0.0.1", port)
    check("бот подключился и получил SELF_INFO", bot is not None and bool(bot.self_info))
    r = await bot.commands.send_device_query()
    check("DEVICE_QUERY отвечен", r.type == EventType.DEVICE_INFO, str(r))
    disconnected = asyncio.Event()
    bot.subscribe(EventType.DISCONNECTED, lambda e: disconnected.set())

    print("2. Приложение с другой версией протокола не переключает ноду")
    slot[0] = "app"
    app = await RawClient.open(port)
    f = await app.request(bytes([1, 9]) + b"      app")
    check("APP_START отвечен SELF_INFO", f[0] == 5)
    await asyncio.sleep(0.1)
    check("нода осталась на протоколе 3", node.proto_ver == 3, f"proto_ver={node.proto_ver}")
    before = node.count.get(22, 0)
    f = await app.request(bytes([22, 9]))
    check("DEVICE_QUERY приложения отвечен из кэша", f[0] == 13 and node.count.get(22, 0) == before)
    check("нода всё ещё на протоколе 3", node.proto_ver == 3)

    print("3. Сообщения достаются обоим клиентам")
    got: list[str] = []
    bot.subscribe(EventType.CHANNEL_MSG_RECV, lambda e: got.append(e.payload["text"]))
    await bot.start_auto_message_fetching()
    for t in ("m1", "m2", "m3"):
        node.inject(t)
    await wait_for(lambda: len(got) == 3)
    check("бот получил m1..m3", got == ["m1", "m2", "m3"], str(got))
    check("приложение получило MESSAGES_WAITING на каждое", sum(1 for p in app.pushes if p[0] == 0x83) == 3)
    texts = await pull_all(app)
    check("приложение вытянуло те же m1..m3", texts == ["m1", "m2", "m3"], str(texts))
    check("на ноде очередь пуста", len(node.queue) == 0)

    print("4. Офлайн-очередь приложения")
    await app.close()
    await asyncio.sleep(0.1)
    node.inject("m4")
    node.inject("m5")
    await wait_for(lambda: len(got) == 5)
    app = await RawClient.open(port)
    await app.request(bytes([1, 3]) + b"      app")
    texts = await pull_all(app)
    check("накопленное без приложения получено", texts == ["m4", "m5"], str(texts))

    print("5. Параллельные команды и многокадровый ответ")
    app.pushes.clear()
    app.send(bytes([4]))
    contacts_task = asyncio.create_task(bot.commands.get_contacts())
    chan_task = asyncio.create_task(bot.commands.send_chan_msg(0, "hello"))
    frames = []
    while True:
        fr = await asyncio.wait_for(app.responses.get(), 3)
        frames.append(fr)
        if fr[0] == 4:
            break
    contacts = await asyncio.wait_for(contacts_task, 5)
    sent = await asyncio.wait_for(chan_task, 5)
    check("приложение: START + все контакты + END", [f[0] for f in frames] == [2] + [3] * CONTACTS + [4],
          str([f[0] for f in frames][:5]))
    check("бот: все контакты", contacts.type == EventType.CONTACTS and len(contacts.payload) == CONTACTS,
          str(contacts.type))
    check("бот: send_chan_msg получил OK", sent.type == EventType.OK, str(sent))
    check("push посреди списка дошёл до приложения", any(p[0] == 0x88 for p in app.pushes))

    print("6. Нода промолчала — клиент получает ERROR, очередь не сбивается")
    mcmux._RESPONSE_TIMEOUT = 0.5
    f = await app.request(bytes([node.silent_cmd]), timeout=3)
    check("ответ ERROR на молчание ноды", f[0] == 1, str(f))
    f = await app.request(bytes([5]))
    check("следующая команда получает свой ответ (CURRENT_TIME)", f[0] == 9, str(f))

    print("7. Новое подключение вытесняет прежнее в слоте")
    app2 = await RawClient.open(port)
    check("прежнее приложение отключено", await wait_for(app.closed.is_set))
    f = await app2.request(bytes([5]))
    check("новое работает", f[0] == 9)
    node.inject("m_keep")
    await wait_for(lambda: len(got) == 6)

    print("8. REBOOT: клиенты отключены, нода переинициализирована, очередь слота сохранена")
    sessions = node.sessions
    app2.send(bytes([19]) + b"reboot")
    check("приложение отключено", await wait_for(app2.closed.is_set))
    check("бот получил DISCONNECTED", await wait_for(disconnected.is_set))
    check("мультиплексор открыл новую сессию с нодой", await wait_for(lambda: node.sessions == sessions + 1 and mux._ready, 5))
    app3 = await RawClient.open(port)
    await app3.request(bytes([1, 3]) + b"      app")
    texts = await pull_all(app3)
    check("сообщение, не забранное до перезагрузки, на месте", texts == ["m_keep"], str(texts))
    slot[0] = "bot"
    bot2 = await MeshCore.create_tcp("127.0.0.1", port)
    check("бот переподключился", bot2 is not None)

    await app3.close()
    await bot2.disconnect()
    mux_task.cancel()
    await asyncio.gather(mux_task, return_exceptions=True)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="      [mux] %(message)s")
    asyncio.run(asyncio.wait_for(main(), 60))
    print()
    if failures:
        print(f"ПРОВАЛЕНО: {len(failures)}: {', '.join(failures)}")
        sys.exit(1)
    print("Все проверки прошли")
