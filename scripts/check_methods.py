#!/usr/bin/env python3
import asyncio
import inspect
import os
from dotenv import load_dotenv
from meshcore import MeshCore

load_dotenv()

async def test():
    port = os.environ["MESHCORE_PORT"]

    mc = await MeshCore.create_serial(port=port)
    await mc.connect()

    print("=" * 60)
    print("set_flood_scope сигнатура:")
    sig = inspect.signature(mc.commands.set_flood_scope)
    print(f"  {sig}")

    print("\nsend_advert сигнатура:")
    sig_advert = inspect.signature(mc.commands.send_advert)
    print(f"  {sig_advert}")

    print("\nsend_chan_msg сигнатура:")
    sig_chan = inspect.signature(mc.commands.send_chan_msg)
    print(f"  {sig_chan}")

    print("=" * 60)

    await mc.disconnect()

asyncio.run(test())
