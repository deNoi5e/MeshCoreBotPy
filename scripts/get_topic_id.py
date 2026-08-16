import asyncio
import ssl
import sys
sys.stdout.reconfigure(encoding='utf-8')
import aiohttp
import certifi

TOKEN = ""  # вставьте ваш токен сюда

async def get_topic_id():
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params={"limit": 20}, ssl=ssl_ctx) as r:
            data = await r.json()
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post")
        if not msg:
            continue
        thread_id = msg.get("message_thread_id")
        chat = msg.get("chat", {})
        text = msg.get("text", "")[:40]
        frm = (msg.get("from") or {}).get("first_name", "")
        if thread_id:
            print(f"chat_id={chat.get('id')}  topic_id={thread_id}  от={frm}  текст={text!r}")

asyncio.run(get_topic_id())
