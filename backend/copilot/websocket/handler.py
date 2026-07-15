from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter(prefix="/api/copilot")

@router.websocket("/ws/session/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info(f"Copilot WebSocket connected for session: {session_id}")
    try:
        while True:
            data = await websocket.receive_bytes()
            # Echo back bytes for testing stub
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        logger.info(f"Copilot WebSocket disconnected: {session_id}")
