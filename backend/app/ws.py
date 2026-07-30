from collections import defaultdict

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, trial_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[trial_id].add(websocket)

    def disconnect(self, trial_id: int, websocket: WebSocket) -> None:
        group = self.connections.get(trial_id)
        if group is None:
            return
        group.discard(websocket)
        if not group:
            self.connections.pop(trial_id, None)

    async def broadcast(self, trial_id: int, event: dict[str, object]) -> None:
        stale: list[WebSocket] = []
        for websocket in tuple(self.connections.get(trial_id, ())):
            try:
                await websocket.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(trial_id, websocket)


manager = ConnectionManager()
