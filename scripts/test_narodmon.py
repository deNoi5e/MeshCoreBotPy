import asyncio
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, "E:\\mesh\\MeshCoreBotPy")

from core.narodmon import format_myweather, parse_narodmon_sensors

async def test():
    sensors_raw = 'D1291,"БЛПК",D5694,"22мкрн"'
    api_key = "T9PvkrrBPyawR"
    sensors = parse_narodmon_sensors(sensors_raw)
    print(f"Parsed sensors: {sensors}")
    result = await format_myweather(sensors, api_key)
    print(f"Result ({len(result.encode('utf-8'))} bytes): {result}")

asyncio.run(test())
