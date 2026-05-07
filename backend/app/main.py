import asyncio
import json
from contextlib import asynccontextmanager
from typing import Dict

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging, logger
from app.api import auth, documents, scans, integrations


class WebSocketManager:
    """Хранит активные WebSocket соединения и доставляет сообщения."""

    def __init__(self):
        self.connections: Dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        self.connections.setdefault(user_id, []).append(ws)
        logger.info("ws.connected", user_id=user_id)

    def disconnect(self, user_id: str, ws: WebSocket):
        conns = self.connections.get(user_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self.connections.pop(user_id, None)
        logger.info("ws.disconnected", user_id=user_id)

    async def send(self, user_id: str, data: dict):
        conns = self.connections.get(user_id, [])
        dead = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)


ws_manager = WebSocketManager()


async def redis_subscriber():
    """Слушает Redis pub/sub и доставляет сообщения через WebSocket."""
    r = aioredis.from_url(settings.REDIS_URL)
    pubsub = r.pubsub()
    await pubsub.psubscribe("ws:*")
    logger.info("redis.subscriber.started")

    async for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        channel: str = message["channel"].decode()
        user_id = channel.removeprefix("ws:")
        try:
            data = json.loads(message["data"])
            await ws_manager.send(user_id, data)
        except Exception as e:
            logger.error("redis.subscriber.error", error=str(e))

    await r.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    task = asyncio.create_task(redis_subscriber())
    logger.info("app.started")
    yield
    task.cancel()
    logger.info("app.stopped")


app = FastAPI(
    title="МС-Сканер API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(scans.router)
app.include_router(integrations.router)


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # Держим соединение открытым, принимаем ping
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)


@app.get("/health")
async def health():
    return {"status": "ok"}
