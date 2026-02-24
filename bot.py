from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# 兼容两种结构：
# 1) /opt/tgbot/bot.py + /opt/tgbot/core
# 2) /opt/tgbot/tgbot/bot.py + /opt/tgbot/tgbot/core
try:
    from tgbot.core.auth import AuthService
    from tgbot.core.config import get_settings
    from tgbot.core.storage import StorageEngine
    from tgbot.core.telegram import TelegramAdapter
    from tgbot.core.ws import WsHub
except ImportError:
    from core.auth import AuthService
    from core.config import get_settings
    from core.storage import StorageEngine
    from core.telegram import TelegramAdapter
    from core.ws import WsHub


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)

    storage = StorageEngine(settings)
    await storage.start()

    auth = AuthService(settings)
    ws_hub = WsHub(settings, auth, storage)
    tg = TelegramAdapter(settings, storage, ws_hub)
    ws_hub.set_export_callback(tg.send_export_file)
    ws_hub.set_clear_callback(tg.send_clear_notice)
    await tg.start()

    app.state.settings = settings
    app.state.storage = storage
    app.state.ws_hub = ws_hub
    app.state.telegram = tg

    try:
        yield
    finally:
        await tg.stop()
        await storage.stop()
        await asyncio.sleep(0)


app = FastAPI(
    title="tgbot IM Console",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.websocket("/ws")
async def websocket_entry(ws: WebSocket) -> None:
    hub: WsHub = app.state.ws_hub
    await hub.handle(ws)


# 自动识别 miniapp/dist 路径
BOT_DIR = Path(__file__).resolve().parent
DIST_CANDIDATES = [
    BOT_DIR / "miniapp" / "dist",        # 平铺: /opt/tgbot/miniapp/dist
    BOT_DIR / "tgbot" / "miniapp" / "dist",  # 兜底
]
miniapp_dist = next((p for p in DIST_CANDIDATES if p.exists()), None)

if miniapp_dist:
    app.mount("/", StaticFiles(directory=miniapp_dist, html=True), name="miniapp")
else:
    @app.get("/{full_path:path}")
    async def miniapp_not_built(full_path: str) -> HTMLResponse:
        return HTMLResponse(
            "<h3>tgbot miniapp dist not found. Build frontend first.</h3>",
            status_code=503,
        )


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        app,  # 直接传 app，避免模块路径问题
        host=settings.app_host,
        port=settings.app_port,
        proxy_headers=settings.trust_proxy_headers,
        forwarded_allow_ips="127.0.0.1,::1",
    )


if __name__ == "__main__":
    main()
