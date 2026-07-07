import os
import datetime
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from loguru import logger

from backend.app.core.config import Settings
from backend.app.repositories.json_repository import JSONFileInterviewRepository
from backend.app.runner.bot_runner_impl import LocalBotRunner

# Load dotenv
load_dotenv(override=True)
Settings.validate()

app = FastAPI(title="AI Mock Interviewer Backend")

# Initialize SOLID components
repo = JSONFileInterviewRepository()

# Track active sessions and state in-memory
active_sessions: Dict[str, Dict[str, Any]] = {}

class StartSessionRequest(BaseModel):
    jd: str
    resume: str
    custom_prompt: str = ""

@app.post("/api/interviews/start")
def start_interview(req: StartSessionRequest):
    # Check if there is an active session running on hardware
    for sid, sess in active_sessions.items():
        if sess["runner"].is_running():
            raise HTTPException(status_code=400, detail="An interview session is already active.")
            
    # Generate UUID and create initial folder
    try:
        session_id = repo.create_session(
            jd=req.jd,
            resume=req.resume,
            custom_prompt=req.custom_prompt
        )
    except Exception as e:
        logger.error(f"Failed to create session folder: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create session folder: {e}")
        
    # Start bot runner
    bot_runner = LocalBotRunner(Settings.DEEPGRAM_API_KEY, Settings.GROQ_API_KEY)
    
    session_data = {
        "runner": bot_runner,
        "status": "Initializing...",
        "transcript": [],
        "timestamp": datetime.datetime.now().isoformat(),
        "jd": req.jd,
        "resume": req.resume,
        "custom_prompt": req.custom_prompt
    }
    
    # Callback to update the session JSON in the background as transcript grows
    def make_transcript_callback(sid):
        def callback(entry):
            if sid in active_sessions:
                active_sessions[sid]["transcript"].append(entry)
                # Persist state
                repo.save_session(
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
        return callback
        
    def make_status_callback(sid):
        def callback(status_str):
            if sid in active_sessions:
                active_sessions[sid]["status"] = status_str
        return callback

    try:
        bot_runner.start(
            jd=req.jd,
            resume=req.resume,
            session_id=session_id,
            status_callback=make_status_callback(session_id),
            transcript_callback=make_transcript_callback(session_id),
            custom_prompt=req.custom_prompt
        )
    except Exception as e:
        logger.error(f"Failed to start bot runner: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start bot runner: {e}")
        
    active_sessions[session_id] = session_data
    
    return {"session_id": session_id, "status": "Initializing..."}

@app.post("/api/interviews/{session_id}/stop")
def stop_interview(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found or not active.")
        
    sess = active_sessions[session_id]
    sess["runner"].stop()
    sess["status"] = "Interview Completed and Saved."
    
    # Save one last time
    repo.save_session(
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

@app.get("/api/interviews/{session_id}/status")
def get_session_status(session_id: str):
    # Check if active in memory
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        return {
            "session_id": session_id,
            "is_active": sess["runner"].is_running(),
            "status": sess["status"],
            "transcript": sess["transcript"]
        }
    # Otherwise check database
    try:
        data = repo.load_session(session_id)
        return {
            "session_id": session_id,
            "is_active": False,
            "status": "Mock Interview Stopped.",
            "transcript": data.get("transcript", [])
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")

@app.get("/api/interviews")
def list_interviews():
    try:
        session_ids = repo.list_sessions()
        detailed_records = []
        for sid in session_ids:
            try:
                # If active in memory, get current transcript, otherwise load from disk
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
                    rec = repo.load_session(sid)
                    detailed_records.append(rec)
            except Exception:
                pass
        # Sort by timestamp descending
        detailed_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return detailed_records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/interviews/{session_id}")
def get_interview(session_id: str):
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
        return repo.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found.")

@app.get("/api/interviews/{session_id}/recording")
def get_recording(session_id: str):
    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    recording_path = os.path.join(directory, "recording.wav")
    if not os.path.exists(recording_path):
        raise HTTPException(status_code=404, detail="Recording audio not found.")
    return FileResponse(recording_path, media_type="audio/wav", filename="recording.wav")
