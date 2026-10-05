import asyncio
import json
from contextlib import asynccontextmanager
from typing import Dict
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError

from app.core.config import settings
from app.core.logging import setup_logging, logger
from app.core.security import decode_token
from app.api import auth, documents, scans, integrations, moysklad_vendor, products, acceptance, support, mark_control, inventory, organization_profiles, tsd, physical_counts
from app.api.deps import require_full_edition


class WebSocketManager:
    """Хранит активные WebSocket соединения и доставляет сообщения."""

    def __init__(self):
        self.connections: Dict[str, list[WebSocket]] = {}
        self.terminal_scopes = {}

    async def connect(self, user_id: str, ws: WebSocket, *, document_id=None, device_id=None):
        await ws.accept()
        self.connections.setdefault(user_id, []).append(ws)
        if document_id:
            self.terminal_scopes[ws] = (str(document_id), str(device_id))
        logger.info("ws.connected", user_id=user_id)

    def disconnect(self, user_id: str, ws: WebSocket):
        self.terminal_scopes.pop(ws, None)
        conns = self.connections.get(user_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self.connections.pop(user_id, None)
        logger.info("ws.disconnected", user_id=user_id)

    async def send(self, user_id: str, data: dict):
        conns = list(self.connections.get(user_id, []))
        dead = []
        for ws in conns:
            try:
                scope = self.terminal_scopes.get(ws)
                if scope:
                    if data.get("type") == "tsd_device_revoked" and data.get("device_id") == scope[1]:
                        await ws.close(code=1008)
                        dead.append(ws)
                        continue
                    if data.get("document_id") != scope[0]:
                        continue
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
app.include_router(organization_profiles.router)
app.include_router(tsd.router)
app.include_router(physical_counts.router)
app.include_router(moysklad_vendor.router)
app.include_router(products.router)
app.include_router(acceptance.router)
app.include_router(support.router)
# Инвентаризация и Контроль марок — только полная версия (см. require_full_edition).
app.include_router(mark_control.router, dependencies=[Depends(require_full_edition)])
app.include_router(inventory.router, dependencies=[Depends(require_full_edition)])


async def terminal_websocket_scope(token, document_id):
    from fastapi.security import HTTPAuthorizationCredentials
    from sqlalchemy import select
    from app.db.models import TsdDocumentSession
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        device = await tsd.get_tsd_device(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
        await tsd._owned_tsd_document(db, device, document_id)
        session = (await db.execute(select(TsdDocumentSession.id).where(
            TsdDocumentSession.document_id == document_id, TsdDocumentSession.device_id == device.id,
            TsdDocumentSession.status == "active"))).scalars().first()
        if session is None:
            raise HTTPException(403, "Сессия сборки завершена")
        await db.commit()
        return str(device.user_id), str(device.id)


@app.websocket("/ws/tsd/{document_id}")
async def terminal_websocket_endpoint(websocket: WebSocket, document_id: UUID):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return
    try:
        user_id, device_id = await terminal_websocket_scope(token, document_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await ws_manager.connect(user_id, websocket, document_id=document_id, device_id=device_id)
    try:
        while True:
            await websocket.receive_text()
            # Recheck revocation, warehouse/profile access and active session on each heartbeat.
            await terminal_websocket_scope(token, document_id)
    except HTTPException:
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(user_id, websocket)


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    # Аутентификация: JWT передаётся в query-параметре ?token= (браузер не даёт
    # слать заголовки при открытии WebSocket). Пускаем только валидный access-токен,
    # чей sub совпадает с user_id из пути — иначе любой мог бы слушать чужие сканы
    # (коды маркировки, ИНН владельца, движение документов).
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return
    try:
        payload = decode_token(token)
    except JWTError:
        await websocket.close(code=1008)
        return
    if payload.get("type") != "access" or payload.get("sub") != user_id:
        await websocket.close(code=1008)
        return

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
