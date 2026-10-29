"""Check public WebSocket upgrade with a short-lived, non-user diagnostic JWT."""
import asyncio
from datetime import datetime, timedelta, timezone
import sys
import uuid

from jose import jwt
import websockets

from app.core.config import settings


async def main():
    subject = str(uuid.uuid4())
    token = jwt.encode({'sub': subject, 'type': 'access',
                        'exp': datetime.now(timezone.utc) + timedelta(seconds=30)},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    # No real user, no received events, no database writes. Never print the URL.
    try:
        async with websockets.connect(
            f'wss://skandata.ru/ws/{subject}?token={token}', open_timeout=10,
        ) as connection:
            await connection.send('ping')
            pong = await connection.ping()
            await asyncio.wait_for(pong, timeout=5)
        print('Public authenticated WebSocket upgrade and ping: OK')
    except Exception as exc:
        print('WebSocket check failed: ' + type(exc).__name__)
        sys.exit(1)


asyncio.run(main())
