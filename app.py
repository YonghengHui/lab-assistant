"""Qwen 实时语音 — HTTP + WebSocket 一体服务（带自签 HTTPS）。

手机浏览器要用麦克风，**必须走 HTTPS**（浏览器强制要求安全上下文）。
所以本服务同时提供：
  - 静态页 /            → index.html
  - WebSocket /ws       → 转发到阿里实时语音

监听 QWEN_VOICE_HOST:QWEN_VOICE_PORT（手机要用麦克风 → 必须 HTTPS），自签证书。
"""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
import sys
from pathlib import Path

from aiohttp import web, WSMsgType, ClientSession, WSServerHandshakeError
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("qwen-voice")

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    log.error("缺少 DASHSCOPE_API_KEY"); sys.exit(1)

HERE = Path(__file__).parent
UPSTREAM = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
MODEL = os.environ.get("QWEN_VOICE_MODEL", "qwen3.5-omni-flash-realtime")
VOICE = os.environ.get("QWEN_VOICE_VOICE", "Serena")
HOST = os.environ.get("QWEN_VOICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_VOICE_PORT", "8900"))
CERT = HERE / "cert.pem"
KEY = HERE / "key.pem"


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(HERE / "index.html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "model": MODEL, "voice": VOICE})


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    peer = request.remote
    log.info("client connected: %s", peer)
    client = web.WebSocketResponse(max_msg_size=0, heartbeat=20)
    await client.prepare(request)

    url = f"{UPSTREAM}?model={MODEL}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        session = ClientSession()
        up = await session.ws_connect(url, headers=headers, max_msg_size=0, heartbeat=20)
    except Exception as e:
        log.error("upstream failed: %s", e)
        await client.close(code=1011, message=b"upstream failed")
        return client

    log.info("upstream ok for %s", peer)

    async def c2u():
        try:
            async for msg in client:
                if msg.type == WSMsgType.TEXT:
                    if '"session.update"' in msg.data or '"response.create"' in msg.data:
                        log.info("c->u %s", msg.data[:200])
                    await up.send_str(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await up.send_bytes(msg.data)
        except Exception as e:
            log.info("c->u end: %s", e)

    async def u2c():
        try:
            async for msg in up:
                if msg.type == WSMsgType.TEXT:
                    # 把非音频的事件都记下来，便于定位解析失败
                    if '"response.audio.delta"' not in msg.data:
                        log.info("u->c %s", msg.data[:260])
                    await client.send_str(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await client.send_bytes(msg.data)
                elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
        except Exception as e:
            log.info("u->c end: %s", e)

    try:
        await asyncio.gather(c2u(), u2c())
    finally:
        await up.close()
        await session.close()
        await client.close()
        log.info("client disconnected: %s", peer)
    return client


def make_ssl():
    if not (CERT.exists() and KEY.exists()):
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    return ctx


def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    ssl_ctx = make_ssl()
    scheme = "https" if ssl_ctx else "http"
    log.info("serving %s://%s:%d  model=%s voice=%s", scheme, HOST, PORT, MODEL, VOICE)
    web.run_app(app, host=HOST, port=PORT, ssl_context=ssl_ctx, print=None, access_log=None)


if __name__ == "__main__":
    main()
