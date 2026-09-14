import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from loguru import logger
from typing import Dict, Any, List

from services.copilot.src.api.deps import get_copilot_repo, get_copilot_sessions
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.engine.session import CopilotSessionEngine

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
        sess = active_sessions[session_id]
        sess["is_active"] = False
        sess["status"] = "Session stopped."

        # Broadcast termination frame to active dashboard subscribers
        dashboards = list(sess.get("dashboard_websockets", []))
        for dash_ws in dashboards:
            try:
                await dash_ws.send_json({
                    "type": "copilot_update",
                    "session_id": session_id,
                    "is_active": False,
                    "status": "Session stopped."
                })
            except Exception:
                pass

        # Terminate Playwright bot subprocess if active via browser-service
        browser_urls = [
            os.getenv("BROWSER_SERVICE_URL"),
            os.getenv("BROWSER_URL"),
            "http://localhost:8002",
            "http://127.0.0.1:8002",
            "http://browser-service:8002"
        ]
        browser_urls = [u for u in browser_urls if u]
        for b_url in browser_urls:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.post(f"{b_url}/stop-meeting", json={"session_id": session_id})
                    if resp.status_code == 200:
                        logger.info(f"[TeamsBot] Stopped meeting bot via browser-service at {b_url}")
                        break
            except Exception:
                continue

        # Terminate local bot process if present (using process-tree kill)
        bot_process = sess.get("bot_process")
        if bot_process and bot_process.poll() is None:
            logger.info(f"[TeamsBot] Terminating local bot subprocess for session {session_id} (PID: {bot_process.pid})")
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(bot_process.pid)], capture_output=True)
                else:
                    try:
                        import signal
                        os.killpg(os.getpgid(bot_process.pid), signal.SIGTERM)
                    except Exception:
                        bot_process.terminate()
                try:
                    bot_process.wait(timeout=3.0)
                except Exception:
                    bot_process.kill()
            except Exception as pe:
                logger.warning(f"[TeamsBot] Error terminating bot subprocess: {pe}")
        sess["bot_process"] = None

        worker = sess.get("worker")
        if worker:
            try:
                if hasattr(worker, "cancel"):
                    await worker.cancel()
            except Exception as we:
                logger.debug(f"[CopilotWS] Error cancelling worker on stop: {we}")

        ws = sess.get("websocket")
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
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        engine = sess["engine"]
        return {
            "session_id": session_id,
            "is_active": sess.get("is_active", True),
            "status": sess.get("status", "ready"),
            "transcript": engine.get_ui_transcript(),
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
                resume=db_session.get("resume", ""),
                custom_prompt=db_session.get("custom_prompt", "")
            )
            res = await engine.finalize_report()
            return res
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")


