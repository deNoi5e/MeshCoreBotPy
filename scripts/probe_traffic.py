#!/usr/bin/env python3
"""
Разведка: смотрим что отдаёт сайт пробок.
Запусти и скопируй вывод — разберём структуру и напишем парсер.
"""
import asyncio
import re
import ssl
import aiohttp
import certifi

URL = "https://омск.пробки-онлайн.рф/"

async def probe():
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    headers = {"User-Agent": "Mozilla/5.0 (compatible; bot)"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(URL, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"Status: {resp.status}")
                print(f"Content-Type: {resp.headers.get('Content-Type')}")
                html = await resp.text(errors="replace")
                print(f"HTML length: {len(html)}\n")

                # Ищем числа 0-10 рядом с ключевыми словами
                hits = re.findall(r'.{0,60}(?:балл|пробк|score|jam|traffic|point).{0,60}', html, re.IGNORECASE)
                print("=== Совпадения по ключевым словам ===")
                for h in hits[:20]:
                    print(repr(h))

                # Первые 3000 символов HTML
                print("\n=== Начало HTML ===")
                print(html[:3000])
    except Exception as e:
        print(f"Ошибка: {e}")

asyncio.run(probe())
