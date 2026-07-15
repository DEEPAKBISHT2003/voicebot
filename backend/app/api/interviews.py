import os
import datetime
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends, UploadFile, File, Request
from fastapi.responses import FileResponse
from backend.app.parsers.factory import DocumentParserFactory
from pydantic import BaseModel
from loguru import logger

from backend.app.core.config import Settings
from backend.app.prompts.interview_prompt import InterviewPromptBuilder
from backend.app.pipeline.builder import LocalPipecatPipelineBuilder
from pipecat.workers.runner import WorkerRunner
from pipecat.frames.frames import LLMRunFrame

from backend.app.api.deps import (
    get_repo,
    get_active_sessions,
    get_repo_ws,
    get_active_sessions_ws
)

router = APIRouter(prefix="/api")

@router.post("/interviews/parse-resume")
async def parse_resume(file: UploadFile = File(...)):
    try:
        parser = DocumentParserFactory.get_parser(file.filename)
        file_bytes = await file.read()
        text = parser.parse(file_bytes, file.filename)
        return {"text": text, "filename": file.filename}
    except ValueError as ve:
        logger.error(f"Unsupported file format for parsing: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to parse resume: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse resume: {e}")

class StartSessionRequest(BaseModel):
    jd: str
    resume: str
    custom_prompt: str = ""
    resume_filename: str = "resume.txt"
    resume_base64: str = ""

@router.post("/interviews/start")
async def start_interview(
    req: StartSessionRequest,
    request: Request,
    repo=Depends(get_repo),
    active_sessions=Depends(get_active_sessions)
):
    try:
        session_id = await repo.create_session(
            jd=req.jd,
            resume=req.resume,
            custom_prompt=req.custom_prompt,
            resume_filename=req.resume_filename,
            resume_base64=req.resume_base64
        )
    except Exception as e:
        logger.error(f"Failed to create session folder: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create session folder: {e}")
        
    active_sessions[session_id] = {
        "status": "Connecting to audio stream...",
        "transcript": [],
        "timestamp": datetime.datetime.now().isoformat(),
        "jd": req.jd,
        "resume": req.resume,
        "custom_prompt": req.custom_prompt,
        "is_active": True,
        "worker": None
    }

    # Initialize the corresponding Copilot Session in background
    try:
        from backend.copilot.models.copilot import CopilotSessionModel
        from backend.copilot.engine.session import CopilotSessionEngine
        
        copilot_repo = request.app.state.copilot_repo
        copilot_sessions = request.app.state.copilot_sessions
        
        # Pre-initialize copilot session model in database
        await CopilotSessionModel.create(
            session_id=session_id,
            jd=req.jd,
            resume=req.resume,
            custom_prompt=req.custom_prompt or "",
            transcript=[]
        )
        
        engine = CopilotSessionEngine(session_id, copilot_repo, [], jd=req.jd, resume=req.resume)
        copilot_sessions[session_id] = {
            "engine": engine,
            "status": "Listening for audio stream...",
            "transcript": engine.get_transcript(),
            "timestamp": datetime.datetime.now().isoformat(),
            "jd": req.jd,
            "resume": req.resume,
            "custom_prompt": req.custom_prompt,
            "is_active": True,
            "websocket": None
        }
        logger.info(f"[CopilotObserver] Pre-initialized Copilot engine for interview: {session_id}")
    except Exception as e:
        logger.warning(f"[CopilotObserver] Could not pre-initialize copilot background engine: {e}")
    
    return {"session_id": session_id, "status": "Connecting to audio stream..."}

@router.websocket("/ws/interview/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    repo=Depends(get_repo_ws),
    active_sessions=Depends(get_active_sessions_ws)
):
    await websocket.accept()
    logger.info(f"WebSocket client connected for session: {session_id}")
    
    if session_id not in active_sessions:
        active_sessions[session_id] = {
            "status": "Initializing...",
            "transcript": [],
            "timestamp": datetime.datetime.now().isoformat(),
            "jd": "",
            "resume": "",
            "custom_prompt": "",
            "is_active": True,
            "worker": None
        }
        
    sess = active_sessions[session_id]
    sess["is_active"] = True
    sess["status"] = "Microphone online! Say 'Hello' to start."
    
    prompt_builder = InterviewPromptBuilder()
    system_instruction = prompt_builder.build_system_instruction(
        sess["jd"], 
        sess["resume"], 
        sess["custom_prompt"]
    )
    
    def make_transcript_callback(sid):
        async def callback(entry):
            if sid in active_sessions:
                active_sessions[sid]["transcript"].append(entry)
                await repo.save_session(
                    sid,
                    {
                        "session_id": sid,
                        "timestamp": active_sessions[sid]["timestamp"],
                        "jd": active_sessions[sid]["jd"],
                        "resume": active_sessions[sid]["resume"],
                        "custom_prompt": active_sessions[sid]["custom_prompt"],
                        "transcript": active_sessions[sid]["transcript"]
                    }
                )
                
                # Dynamic AI Copilot Observer Pipeline Integration
                try:
                    copilot_sessions = getattr(websocket.app.state, "copilot_sessions", None)
                    if copilot_sessions and sid in copilot_sessions:
                        copilot_sess = copilot_sessions[sid]
                        engine = copilot_sess.get("engine")
                        if engine:
                            speaker = "Candidate" if entry.get("role") == "user" else "Interviewer"
                            logger.info(f"[CopilotObserver] Forwarding segment to Copilot: {entry.get('text')}")
                            
                            # Add statement to active Copilot engine memory and run evaluations
                            last_msg = await engine.add_message(speaker, entry.get("text", ""))
                            
                            # Broadcast real-time suggestions updates to dashboard WS client
                            copilot_ws = copilot_sess.get("websocket")
                            if copilot_ws:
                                await copilot_ws.send_json({
                                    "type": "copilot_update",
                                    "session_id": sid,
                                    "last_message": last_msg,
                                    "transcript": engine.get_transcript(),
                                    "intelligence": engine.get_intelligence(),
                                    "assistance": engine.get_assistance()
                                })
                except Exception as e:
                    logger.error(f"[CopilotObserver] Failed to forward segment to Copilot engine: {e}")
        return callback
        
    pipeline_builder = LocalPipecatPipelineBuilder(Settings.DEEPGRAM_API_KEY, Settings.GROQ_API_KEY)
    _, _, worker = pipeline_builder.build_pipeline(
        system_instruction=system_instruction,
        session_id=session_id,
        transcript_callback=make_transcript_callback(session_id),
        websocket=websocket
    )
    
    sess["worker"] = worker
    
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    await worker.queue_frames([LLMRunFrame()])
    
    try:
        sess["status"] = "Interview started! Say hello to the interviewer."
        await runner.run()
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from session {session_id}.")
    except Exception as e:
        logger.error(f"Error in WebSocket voice session: {e}", exc_info=True)
        sess["status"] = f"Error: {e}"
    finally:
        sess["is_active"] = False
        sess["status"] = "Mock Interview Stopped."
        await repo.save_session(
            session_id,
            {
                "session_id": session_id,
                "timestamp": sess["timestamp"],
                "jd": sess["jd"],
                "resume": sess["resume"],
                "custom_prompt": sess["custom_prompt"],
                "transcript": sess["transcript"]
            }
        )
        logger.info(f"Session {session_id} voice execution finished.")

@router.post("/interviews/{session_id}/stop")
async def stop_interview(
    session_id: str,
    repo=Depends(get_repo),
    active_sessions=Depends(get_active_sessions)
):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
        
    sess = active_sessions[session_id]
    sess["is_active"] = False
    sess["status"] = "Interview Completed and Saved."
    
    if sess["worker"] is not None:
        try:
            await sess["worker"].cancel()
        except Exception as e:
            logger.warning(f"Failed to cancel pipeline worker: {e}")
            
    await repo.save_session(
        session_id,
        {
            "session_id": session_id,
            "timestamp": sess["timestamp"],
            "jd": sess["jd"],
            "resume": sess["resume"],
            "custom_prompt": sess["custom_prompt"],
            "transcript": sess["transcript"]
        }
    )
    
    return {"status": "Stopped"}

@router.get("/interviews/{session_id}/status")
async def get_session_status(
    session_id: str,
    repo=Depends(get_repo),
    active_sessions=Depends(get_active_sessions)
):
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        return {
            "session_id": session_id,
            "is_active": sess["is_active"],
            "status": sess["status"],
            "transcript": sess["transcript"]
        }
    try:
        data = await repo.load_session(session_id)
        return {
            "session_id": session_id,
            "is_active": False,
            "status": "Mock Interview Stopped.",
            "transcript": data.get("transcript", [])
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")

@router.get("/interviews")
async def list_interviews(
    repo=Depends(get_repo),
    active_sessions=Depends(get_active_sessions)
):
    try:
        session_ids = await repo.list_sessions()
        detailed_records = []
        for sid in session_ids:
            try:
                if sid in active_sessions:
                    sess = active_sessions[sid]
                    detailed_records.append({
                        "session_id": sid,
                        "timestamp": sess["timestamp"],
                        "jd": sess["jd"],
                        "resume": sess["resume"],
                        "custom_prompt": sess["custom_prompt"],
                        "transcript": sess["transcript"]
                    })
                else:
                    rec = await repo.load_session(sid)
                    detailed_records.append(rec)
            except Exception:
                pass
        detailed_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return detailed_records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/interviews/{session_id}")
async def get_interview(
    session_id: str,
    repo=Depends(get_repo),
    active_sessions=Depends(get_active_sessions)
):
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        return {
            "session_id": session_id,
            "timestamp": sess["timestamp"],
            "jd": sess["jd"],
            "resume": sess["resume"],
            "custom_prompt": sess["custom_prompt"],
            "transcript": sess["transcript"]
        }
    try:
        return await repo.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")

@router.get("/interviews/{session_id}/recording")
def get_recording(session_id: str):
    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    recording_path = os.path.join(directory, "recording.wav")
    if not os.path.exists(recording_path):
        raise HTTPException(status_code=404, detail="Recording audio not found.")
    return FileResponse(recording_path, media_type="audio/wav", filename="recording.wav")

@router.get("/interviews/{session_id}/resume")
def get_resume(session_id: str):
    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    pdf_path = os.path.join(directory, "resume.pdf")
    txt_path = os.path.join(directory, "resume.txt")
    
    if os.path.exists(pdf_path):
        return FileResponse(pdf_path, media_type="application/pdf", filename="resume.pdf")
    elif os.path.exists(txt_path):
        return FileResponse(txt_path, media_type="text/plain", filename="resume.txt")
    else:
        raise HTTPException(status_code=404, detail="Resume file not found.")

