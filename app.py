"""Qwen 实时语音 — HTTP + WebSocket 一体服务（带自签 HTTPS）。

手机浏览器要用麦克风，**必须走 HTTPS**（浏览器强制要求安全上下文）。
所以本服务同时提供：
  - 静态页 /            → index.html
  - WebSocket /ws       → 转发到阿里实时语音

监听 QWEN_VOICE_HOST:QWEN_VOICE_PORT（手机要用麦克风 → 必须 HTTPS），自签证书。
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import ssl
import sys
from pathlib import Path

from aiohttp import web, WSMsgType, ClientSession, WSServerHandshakeError
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("qwen-voice")

HERE = Path(__file__).parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取（和 lab_server.py 同一套）：已存在的环境变量优先。"""
    try:
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        log.warning("读取 %s 失败: %s", path, e)
        return
    n = 0
    for line in lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        else:
            v = re.split(r"\s+#", v, 1)[0].strip()
        if k not in os.environ:
            os.environ[k] = v
            n += 1
    if n:
        log.info("从 %s 读了 %d 个变量", path.name, n)


_load_dotenv(HERE / ".env")

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    log.error("缺少 DASHSCOPE_API_KEY"); sys.exit(1)

UPSTREAM = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
MODEL = os.environ.get("QWEN_VOICE_MODEL", "qwen3.5-omni-flash-realtime")
VOICE = os.environ.get("QWEN_VOICE_VOICE", "Serena")
HOST = os.environ.get("QWEN_VOICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("QWEN_VOICE_PORT", "8900"))
CERT = HERE / "cert.pem"
KEY = HERE / "key.pem"

# 可选鉴权：留空 = 不鉴权（默认）。设了就跟 lab_server.py 用同一个 LAB_TOKEN，
# 校验 ?token= / Authorization / cookie（cookie 不区分端口，主页面登录过这里也认）。
LAB_TOKEN = os.environ.get("LAB_TOKEN", "").strip()
LOG_CONTENT = os.environ.get("LAB_LOG_CONTENT", "0").strip().lower() in ("1", "true", "yes", "on")


def _token_ok(request: web.Request) -> bool:
    if not LAB_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    got = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    got = got or request.query.get("token", "") or request.cookies.get("lab_token", "")
    return bool(got) and hmac.compare_digest(got, LAB_TOKEN)


def _brief(s: str, n: int = 200) -> str:
    return s[:n] if LOG_CONTENT else f"<{len(s)} 字节>"


async def index(request: web.Request) -> web.Response:
    if not _token_ok(request):
        return web.Response(status=401, content_type="text/html",
                            text="<h3>需要访问口令</h3><p>在网址后加上 <code>?token=你的口令</code>，"
                                 "或先在主页面登录一次（cookie 同域共享）。</p>")
    return web.FileResponse(HERE / "index.html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "model": MODEL, "voice": VOICE})


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    if not _token_ok(request):
        raise web.HTTPUnauthorized(text="需要访问口令（?token= 或 cookie）")
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
                        log.info("c->u %s", _brief(msg.data))
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
                        log.info("u->c %s", _brief(msg.data, 260))
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
    log.info("serving %s://%s:%d  model=%s voice=%s 鉴权=%s", scheme, HOST, PORT, MODEL, VOICE,
             "开" if LAB_TOKEN else "关")
    web.run_app(app, host=HOST, port=PORT, ssl_context=ssl_ctx, print=None, access_log=None)


if __name__ == "__main__":
    main()
