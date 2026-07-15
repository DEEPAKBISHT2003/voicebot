from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from loguru import logger
from typing import Dict, Any
import json

from backend.copilot.api.deps import get_copilot_sessions, get_copilot_repo
from backend.copilot.services.repository import CopilotRepository
from backend.copilot.engine.session import CopilotSessionEngine

router = APIRouter()

@router.websocket("/api/ws/copilot/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    await websocket.accept()
    logger.info(f"Copilot WebSocket client connected: {session_id}")
    
    # Initialize active session state if not already started
    if session_id not in active_sessions:
        try:
            db_session = await repo.load_session(session_id)
            jd = db_session.get("jd", "")
            resume = db_session.get("resume", "")
            engine = CopilotSessionEngine(session_id, repo, db_session.get("transcript", []), jd=jd, resume=resume)
            active_sessions[session_id] = {
                "engine": engine,
                "status": "Ready",
                "transcript": engine.get_transcript(),
                "timestamp": db_session.get("timestamp"),
                "jd": jd,
                "resume": resume,
                "custom_prompt": db_session.get("custom_prompt", ""),
                "is_active": True,
                "websocket": None
            }
        except Exception:
            engine = CopilotSessionEngine(session_id, repo, [], jd="", resume="")
            active_sessions[session_id] = {
                "engine": engine,
                "status": "Ready",
                "transcript": engine.get_transcript(),
                "timestamp": None,
                "jd": "",
                "resume": "",
                "custom_prompt": "",
                "is_active": True,
                "websocket": None
            }
            
    sess = active_sessions[session_id]
    if "engine" not in sess:
        try:
            db_session = await repo.load_session(session_id)
            sess["engine"] = CopilotSessionEngine(
                session_id, 
                repo, 
                db_session.get("transcript", []),
                jd=db_session.get("jd", ""),
                resume=db_session.get("resume", "")
            )
        except Exception:
            sess["engine"] = CopilotSessionEngine(session_id, repo, [], jd="", resume="")

    sess["is_active"] = True
    sess["websocket"] = websocket
    sess["status"] = "Listening for audio stream..."

    try:
        while True:
            msg = await websocket.receive()
            if "text" in msg:
                try:
                    payload = json.loads(msg["text"])
                    speaker = payload.get("speaker")
                    text = payload.get("text")
                    if speaker and text:
                        engine = sess["engine"]
                        last_msg = await engine.add_message(speaker, text)
                        sess["transcript"] = engine.get_transcript()
                        await websocket.send_json({
                            "type": "copilot_update",
                            "session_id": session_id,
                            "last_message": last_msg,
                            "transcript": engine.get_transcript(),
                            "intelligence": engine.get_intelligence(),
                            "assistance": engine.get_assistance()
                        })
                except Exception as e:
                    logger.error(f"Error parsing websocket text payload: {e}")
            elif "bytes" in msg:
                # Echo bytes back to client
                await websocket.send_bytes(msg["bytes"])
    except WebSocketDisconnect:
        logger.info(f"Copilot WebSocket client disconnected: {session_id}")
    finally:
        # Cleanup connection mapping
        if session_id in active_sessions:
            active_sessions[session_id]["websocket"] = None
            active_sessions[session_id]["status"] = "Connection disconnected."
