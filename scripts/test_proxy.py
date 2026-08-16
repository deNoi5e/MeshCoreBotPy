import asyncio
import sys
import ssl
sys.stdout.reconfigure(encoding='utf-8')
import aiohttp
import certifi
from aiohttp_socks import ProxyConnector

PROXIES = [
    ("158.46.145.49",  62258, "RcNzGTtM", "Gx8pJmyr"),
    ("158.46.145.49",  62259, "RcNzGTtM", "Gx8pJmyr"),
    ("154.219.20.71",  63388, "H1Bm52zF", "UfEt49de"),
    ("154.219.20.71",  63389, "H1Bm52zF", "UfEt49de"),
    ("45.207.149.66",  64980, "iyAJUwwQ", "QKGhUh2q"),
    ("45.207.149.66",  64981, "iyAJUwwQ", "QKGhUh2q"),
]

async def test(host, port, user, pwd):
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    proxy_url = f"socks5://{user}:{pwd}@{host}:{port}"
    try:
        connector = ProxyConnector.from_url(proxy_url)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                "https://api.telegram.org",
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ssl_ctx,
            ) as resp:
                print(f"✅ {host}:{port} — HTTP {resp.status} — РАБОТАЕТ!")
    except Exception as e:
        print(f"❌ {host}:{port} — {type(e).__name__}: {str(e)[:80]}")

async def main():
    tasks = [test(h, p, u, pw) for h, p, u, pw in PROXIES]
    await asyncio.gather(*tasks)

asyncio.run(main())
