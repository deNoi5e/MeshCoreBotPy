#!/usr/bin/env python3
import asyncio
import os
from dotenv import load_dotenv
from meshcore import MeshCore

load_dotenv()

async def test():
    port = os.environ["MESHCORE_PORT"]

    mc = await MeshCore.create_serial(port=port)
    await mc.connect()

    print("Методы в mc.commands:")
    for attr in dir(mc.commands):
        if not attr.startswith('_') and callable(getattr(mc.commands, attr)):
            print(f"  - {attr}")

    await mc.disconnect()

asyncio.run(test())
