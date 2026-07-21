import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from loguru import logger
from typing import Dict, Any, List

from backend.copilot.api.deps import get_copilot_repo, get_copilot_sessions
from backend.copilot.services.repository import CopilotRepository
from backend.copilot.engine.session import CopilotSessionEngine

router = APIRouter(prefix="/api/copilot")

class StartCopilotRequest(BaseModel):
    jd: str
    resume: str
    custom_prompt: str = ""

@router.post("/start")
async def start_copilot(
    req: StartCopilotRequest,
    repo: CopilotRepository = Depends(get_copilot_repo),
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions)
):
    try:
        session_id = await repo.create_session(
            jd=req.jd,
            resume=req.resume,
            custom_prompt=req.custom_prompt
        )
        
        # Track session in active memory
        engine = CopilotSessionEngine(session_id, repo, [], jd=req.jd, resume=req.resume)
        active_sessions[session_id] = {
            "engine": engine,
            "status": "Connecting to audio stream...",
            "transcript": engine.get_transcript(),
            "timestamp": datetime.datetime.now().isoformat(),
            "jd": req.jd,
            "resume": req.resume,
            "custom_prompt": req.custom_prompt,
            "is_active": True,
            "websocket": None
        }
        
        logger.info(f"Initialized AI Copilot Session: {session_id}")
        return {"session_id": session_id, "status": "Connecting to audio stream..."}
    except Exception as e:
        logger.error(f"Failed to start copilot session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{session_id}/stop")
async def stop_copilot(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions)
):
    if session_id in active_sessions:
        # Mark inactive
        active_sessions[session_id]["is_active"] = False
        active_sessions[session_id]["status"] = "Session stopped."
        ws = active_sessions[session_id].get("websocket")
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        logger.info(f"Stopped AI Copilot Session: {session_id}")
        return {"status": "stopped"}
    else:
        # Check database fallback
        return {"status": "stopped"}

@router.get("")
async def list_copilot_sessions(
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    try:
        return await repo.list_sessions()
    except Exception as e:
        logger.error(f"Failed to list copilot sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{session_id}")
async def get_copilot_session(
    session_id: str,
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    try:
        return await repo.load_session(session_id)
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except Exception as e:
        logger.error(f"Failed to load copilot session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class AddTranscriptRequest(BaseModel):
    speaker: str
    text: str

@router.post("/{session_id}/transcript")
async def add_copilot_transcript(
    session_id: str,
    req: AddTranscriptRequest,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    if session_id in active_sessions:
        engine = active_sessions[session_id]["engine"]
        msg = await engine.add_message(req.speaker, req.text)
        active_sessions[session_id]["transcript"] = engine.get_transcript()
        return msg
    else:
        try:
            db_session = await repo.load_session(session_id)
            engine = CopilotSessionEngine(
                session_id, 
                repo, 
                db_session.get("transcript", []),
                jd=db_session.get("jd", ""),
                resume=db_session.get("resume", "")
            )
            msg = await engine.add_message(req.speaker, req.text)
            return msg
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")

@router.get("/{session_id}/status")
async def get_copilot_status(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        engine = sess["engine"]
        return {
            "session_id": session_id,
            "is_active": sess.get("is_active", True),
            "status": sess.get("status", "ready"),
            "transcript": engine.get_transcript(),
            "intelligence": engine.get_intelligence(),
            "assistance": engine.get_assistance()
        }
    else:
        # Fallback to database load
        try:
            db_session = await repo.load_session(session_id)
            return {
                "session_id": session_id,
                "is_active": False,
                "status": "Session completed.",
                "transcript": db_session.get("transcript", []),
                "intelligence": db_session.get("intelligence", {}),
                "assistance": db_session.get("assistance", {})
            }
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")

@router.post("/{session_id}/finalize")
async def finalize_copilot_report(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    if session_id in active_sessions:
        engine = active_sessions[session_id]["engine"]
        res = await engine.finalize_report()
        active_sessions[session_id]["is_active"] = False
        return res
    else:
        try:
            db_session = await repo.load_session(session_id)
            engine = CopilotSessionEngine(
                session_id,
                repo,
                db_session.get("transcript", []),
                jd=db_session.get("jd", ""),
                resume=db_session.get("resume", "")
            )
            res = await engine.finalize_report()
            return res
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")


