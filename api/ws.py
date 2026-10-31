from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

ws_router = APIRouter()

_connections: dict[str, list[WebSocket]] = {}


async def broadcast(project: str, event: dict[str, Any]) -> None:
    """Send an event to all WebSocket clients for a project."""
    conns = _connections.get(project, [])
    dead = []
    for ws in conns:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.remove(ws)


def emit(project: str, event: dict[str, Any]) -> None:
    """Synchronous wrapper — schedules broadcast on the event loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast(project, event))
    except RuntimeError:
        pass


@ws_router.websocket("/ws/{project}")
async def websocket_endpoint(websocket: WebSocket, project: str):
    await websocket.accept()
    _connections.setdefault(project, []).append(websocket)
    logger.info("WebSocket connected: %s", project)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        _connections.get(project, []).remove(websocket)
        logger.info("WebSocket disconnected: %s", project)
