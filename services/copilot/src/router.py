import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from loguru import logger
from typing import Dict, Any, List

from services.copilot.src.api.deps import get_copilot_repo, get_copilot_sessions
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.engine.session import CopilotSessionEngine

router = APIRouter()

class StartCopilotRequest(BaseModel):
    jd: str
    resume: str
    custom_prompt: str = ""
    session_id: str = ""  # Optional: use existing interview session_id

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
            custom_prompt=req.custom_prompt,
            session_id=req.session_id or None
        )
        
        # Track session in active memory
        engine = CopilotSessionEngine(session_id, repo, [], jd=req.jd, resume=req.resume, custom_prompt=req.custom_prompt)
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
        
        # Terminate Playwright bot subprocess if active locally or via browser-service
        browser_url = os.getenv("BROWSER_SERVICE_URL", os.getenv("BROWSER_URL", "http://browser-service:8002"))
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{browser_url}/stop-meeting", json={"session_id": session_id})
        except Exception:
            pass

        bot_process = active_sessions[session_id].get("bot_process")
        if bot_process and bot_process.poll() is None:
            logger.info(f"[TeamsBot] Terminating local bot subprocess for session {session_id} (PID: {bot_process.pid})")
            try:
                bot_process.terminate()
                try:
                    bot_process.wait(timeout=3.0)
                except Exception:
                    logger.warning(f"[TeamsBot] Process {bot_process.pid} did not exit gracefully, killing...")
                    bot_process.kill()
            except Exception as pe:
                logger.warning(f"[TeamsBot] Error terminating bot subprocess: {pe}")
        active_sessions[session_id]["bot_process"] = None

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
                resume=db_session.get("resume", ""),
                custom_prompt=db_session.get("custom_prompt", "")
            )
            msg = await engine.add_message(req.speaker, req.text)
            return msg
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")

class UpdateCopilotPromptRequest(BaseModel):
    custom_prompt: str

@router.patch("/{session_id}/prompt")
async def update_copilot_prompt(
    session_id: str,
    req: UpdateCopilotPromptRequest,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    if session_id in active_sessions:
        active_sessions[session_id]["custom_prompt"] = req.custom_prompt
        try:
            db_session = await repo.load_session(session_id)
            db_session["custom_prompt"] = req.custom_prompt
            await repo.save_session(session_id, db_session)
        except Exception as e:
            logger.warning(f"Could not update custom_prompt file for {session_id}: {e}")
        return {"status": "success", "session_id": session_id, "custom_prompt": req.custom_prompt}
    try:
        db_session = await repo.load_session(session_id)
        db_session["custom_prompt"] = req.custom_prompt
        await repo.save_session(session_id, db_session)
        return {"status": "success", "session_id": session_id, "custom_prompt": req.custom_prompt}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

@router.get("/{session_id}/status")
async def get_copilot_status(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    # Guard against non-UUID session IDs (e.g. browser prefetch hitting /start/status)
    import uuid as _uuid
    try:
        _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Invalid session id: {session_id}")
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        engine = sess["engine"]
        return {
            "session_id": session_id,
            "is_active": sess.get("is_active", True),
            "status": sess.get("status", "ready"),
            "transcript": engine.get_transcript(),
            "intelligence": engine.get_intelligence(),
            "assistance": engine.get_assistance(),
            "custom_prompt": sess.get("custom_prompt", "")
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
                "assistance": db_session.get("assistance", {}),
                "custom_prompt": db_session.get("custom_prompt", "")
            }
        except FileNotFoundError:
            # Session not yet initialized in copilot — return a default waiting state
            # instead of 404 so the frontend doesn't break during startup
            return {
                "session_id": session_id,
                "is_active": False,
                "status": "Copilot session initializing...",
                "transcript": [],
                "intelligence": {},
                "assistance": {},
                "custom_prompt": ""
            }

@router.post("/{session_id}/finalize")
async def finalize_copilot_report(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    if session_id in active_sessions:
        bot_process = active_sessions[session_id].get("bot_process")
        if bot_process and bot_process.poll() is None:
            logger.info(f"[TeamsBot] Terminating bot process for session {session_id} on finalize (PID: {bot_process.pid})")
            try:
                bot_process.terminate()
            except Exception:
                pass
        active_sessions[session_id]["bot_process"] = None

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
                resume=db_session.get("resume", ""),
                custom_prompt=db_session.get("custom_prompt", "")
            )
            res = await engine.finalize_report()
            return res
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")



class JoinMeetingRequest(BaseModel):
    meeting_url: str
    bot_role: str = "interviewer"
    bot_name: str = "Mia - AI Interviewer"

@router.post("/{session_id}/join-meeting")
async def join_meeting(
    session_id: str,
    req: JoinMeetingRequest,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions)
):
    """
    Spawns the Playwright Teams Bot from the Copilot Service.
    The bot joins the Teams meeting and streams audio to the copilot WebSocket.
    """
    import os
    import sys
    import subprocess
    import threading
    import httpx

    browser_url = os.getenv("BROWSER_SERVICE_URL", os.getenv("BROWSER_URL", "http://browser-service:8002"))
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{browser_url}/join-meeting",
                json={
                    "session_id": session_id,
                    "meeting_url": req.meeting_url,
                    "bot_role": req.bot_role,
                    "bot_name": req.bot_name
                }
            )
            if resp.status_code == 200:
                logger.info(f"[TeamsBot] Successfully delegated bot spawning to browser-service at {browser_url}")
                return resp.json()
    except Exception as err:
        logger.warning(f"[TeamsBot] Could not contact browser-service at {browser_url} ({err}). Falling back to local subprocess...")

    python_exe = os.path.abspath(sys.executable)
    script_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "browser", "src", "pipeline", "teams_bot.py")
    )
    workspace_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )

    logger.info(f"[TeamsBot] python_exe  : {python_exe}")
    logger.info(f"[TeamsBot] script_path : {script_path}")
    logger.info(f"[TeamsBot] cwd         : {workspace_root}")

    if not os.path.exists(script_path):
        logger.error(f"[TeamsBot] Script not found at: {script_path}")
        raise HTTPException(status_code=500, detail=f"teams_bot.py not found at {script_path}")

    try:
        process = subprocess.Popen(
            [python_exe, script_path, req.meeting_url, session_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout for real-time interleaved logs
            cwd=workspace_root,
            bufsize=1,  # Line-buffered
        )
        logger.info(f"[TeamsBot] Subprocess spawned with PID: {process.pid}")

        if session_id in active_sessions:
            existing_proc = active_sessions[session_id].get("bot_process")
            if existing_proc and existing_proc.poll() is None:
                try:
                    existing_proc.terminate()
                except Exception:
                    pass
            active_sessions[session_id]["bot_process"] = process

        def _stream_logs():
            """Stream bot logs line by line in real-time as they are produced."""
            try:
                for line in iter(process.stdout.readline, b''):
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        logger.info(f"[TeamsBot] {decoded}")
                process.stdout.close()
                process.wait()
                logger.info(f"[TeamsBot] Bot process exited with code: {process.returncode}")
            except Exception as e:
                logger.warning(f"[TeamsBot] Log streaming error: {e}")

        t = threading.Thread(target=_stream_logs, daemon=True)
        t.start()

        return {"status": "bot_spawned", "pid": process.pid, "session_id": session_id}

    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"Executable not found: {e}")
    except Exception as e:
        logger.error(f"[TeamsBot] Failed to spawn: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
