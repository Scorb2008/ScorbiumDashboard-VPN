import asyncio
from datetime import datetime, timezone
from typing import Set

from fastapi import WebSocket

from app.utils.log import log


class NotificationManager:
    """In-memory pub/sub for real-time WebSocket notifications."""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        log.info(f"[ws] Client connected. Total: {len(self._connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        log.info(f"[ws] Client disconnected. Total: {len(self._connections)}")

    async def broadcast(self, event: dict) -> None:
        """Broadcast an event to all connected clients."""
        if not self._connections:
            return
        payload = {
            **event,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        dead = set()
        for ws in list(self._connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                self._connections -= dead


notification_manager = NotificationManager()
