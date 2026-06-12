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

    print("=" * 60)
    print("Проверка set_flood_scope с разными типами:")

    # Попробуем разные значения
    test_values = [
        ("строка 'all'", "all"),
        ("строка 'flood'", "flood"),
        ("строка '255'", "255"),
        ("bytes", b"all"),
        ("dict", {"scope": "all"}),
    ]

    for desc, value in test_values:
        try:
            await mc.commands.set_flood_scope(value)
            print(f"✓ {desc}: Успех!")
            break
        except Exception as e:
            print(f"✗ {desc}: {e}")

    # Проверить default flood scope
    try:
        default = await mc.commands.get_default_flood_scope()
        print(f"\nТекущий default flood scope: {default}")
        print(f"Тип: {type(default)}")
    except Exception as e:
        print(f"Ошибка при получении default flood scope: {e}")

    print("=" * 60)
    await mc.disconnect()

asyncio.run(test())
