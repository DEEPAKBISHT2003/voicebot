import os
import sys
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from loguru import logger
from typing import Dict, Any

app = FastAPI(title="Voicebot Browser Microservice", version="1.0.0")

# Session process tracker for active browser bots
active_bots: Dict[str, Any] = {}

class JoinMeetingRequest(BaseModel):
    session_id: str
    meeting_url: str

class StopMeetingRequest(BaseModel):
    session_id: str

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "browser-service", "active_bots": len(active_bots)}

@app.post("/join-meeting")
async def join_meeting(req: JoinMeetingRequest):
    session_id = req.session_id
    meeting_url = req.meeting_url
    
    if not meeting_url or not meeting_url.strip():
        raise HTTPException(status_code=400, detail="meeting_url is required")
        
    logger.info(f"[BrowserService] Spawning Playwright Bot for session {session_id} to meeting: {meeting_url}")
    
    # Check if a bot process is already running for this session
    if session_id in active_bots:
        proc = active_bots[session_id].get("process")
        if proc and proc.poll() is None:
            logger.info(f"[BrowserService] Bot process already active for session {session_id} (PID: {proc.pid})")
            return {"status": "already_running", "session_id": session_id, "pid": proc.pid}
            
    # Resolve path to teams_bot.py
    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(current_dir, "pipeline", "teams_bot.py")
    python_exe = sys.executable
    
    try:
        proc = subprocess.Popen(
            [python_exe, script_path, meeting_url, session_id],
            stdout=None,
            stderr=None
        )
        active_bots[session_id] = {
            "process": proc,
            "meeting_url": meeting_url,
            "pid": proc.pid
        }
        logger.info(f"[BrowserService] Successfully spawned bot process PID {proc.pid} for session {session_id}")
        return {"status": "bot_spawned", "session_id": session_id, "pid": proc.pid}
    except Exception as e:
        logger.error(f"[BrowserService] Failed to spawn bot process: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to spawn bot: {str(e)}")

@app.post("/stop-meeting")
async def stop_meeting(req: StopMeetingRequest):
    session_id = req.session_id
    if session_id in active_bots:
        bot_info = active_bots.pop(session_id)
        proc = bot_info.get("process")
        if proc and proc.poll() is None:
            logger.info(f"[BrowserService] Terminating bot process PID {proc.pid} for session {session_id}")
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    proc.kill()
            except Exception as pe:
                logger.warning(f"[BrowserService] Error terminating bot process: {pe}")
        return {"status": "stopped", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}
