#!/usr/bin/env python3
import asyncio, ssl, aiohttp, certifi, json

KEY = "f9064d4e-869b-4715-acca-c4caf3906a4d"

# Несколько маршрутов по Омску (lon,lat~lon,lat)
ROUTES = [
    ("Центр→Левый берег", "73.369,54.989~73.286,54.963"),
    ("Центр→Север",       "73.369,54.989~73.382,55.066"),
    ("Центр→Аэропорт",    "73.369,54.989~73.313,54.957"),
]

BASE = "https://api-maps.yandex.ru/services/route/2.0/"

async def probe():
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        for name, rll in ROUTES:
            for traffic in ("true", "false"):
                url = f"{BASE}?apikey={KEY}&lang=ru_RU&rll={rll}&routingMode=driving&traffic={traffic}"
                print(f"\n→ {name} traffic={traffic}")
                try:
                    async with session.get(url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        text = await r.text()
                        print(f"  status={r.status}")
                        if r.status == 200:
                            data = json.loads(text)
                            # Ищем duration в ответе
                            print(f"  keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
                            print(f"  body={text[:600]}")
                        else:
                            print(f"  body={text[:300]}")
                except Exception as e:
                    print(f"  ошибка: {e}")

asyncio.run(probe())
