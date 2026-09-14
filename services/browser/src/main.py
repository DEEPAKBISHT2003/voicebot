import os
import sys
import subprocess
from dotenv import load_dotenv

# Load .env file with override to ensure configuration takes precedence over terminal variables
load_dotenv(override=True)

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
    bot_role: str = "interviewer"
    bot_name: str = "Mia - AI Interviewer"

class StopMeetingRequest(BaseModel):
    session_id: str

@app.get("/health")
async def health_check():
    dead = [sid for sid, info in active_bots.items() if info.get("process") and info.get("process").poll() is not None]
    for sid in dead:
        active_bots.pop(sid, None)
    return {"status": "ok", "service": "browser-service", "active_bots": len(active_bots)}

@app.post("/join-meeting")
async def join_meeting(req: JoinMeetingRequest):
    session_id = req.session_id
    meeting_url = req.meeting_url
    bot_role = req.bot_role or "interviewer"
    bot_name = req.bot_name or "Mia - AI Interviewer"
    
    if not meeting_url or not meeting_url.strip():
        raise HTTPException(status_code=400, detail="meeting_url is required")
        
    logger.info(f"[BrowserService] Spawning Playwright Bot ({bot_name}, role={bot_role}) for session {session_id} to meeting: {meeting_url}")
    
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
    
    env = os.environ.copy()
    env["BOT_ROLE"] = bot_role
    env["BOT_DISPLAY_NAME"] = bot_name

    session_dir = os.path.join("interviews", session_id)
    os.makedirs(session_dir, exist_ok=True)
    log_file_path = os.path.join(session_dir, "teams_bot.log")
    log_file = open(log_file_path, "a", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            [python_exe, script_path, meeting_url, session_id],
            stdout=log_file,
            stderr=log_file,
            env=env
        )
        active_bots[session_id] = {
            "process": proc,
            "meeting_url": meeting_url,
            "bot_role": bot_role,
            "bot_name": bot_name,
            "pid": proc.pid
        }
        logger.info(f"[BrowserService] Successfully spawned bot process PID {proc.pid} ({bot_name}) for session {session_id}")
        return {"status": "bot_spawned", "session_id": session_id, "pid": proc.pid, "bot_name": bot_name, "bot_role": bot_role}
    except Exception as e:
        logger.error(f"[BrowserService] Failed to spawn bot process: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to spawn bot: {str(e)}")

def terminate_process_tree(pid: int):
    """Forcefully terminates the process and all of its descendants across Windows and POSIX."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5.0)
        else:
            import signal
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception as e:
        logger.warning(f"[BrowserService] Error terminating process tree {pid}: {e}")


def find_running_bot_pids(session_id: str) -> list[int]:
    """Finds PIDs of any running teams_bot.py processes for this session (reload recovery)."""
    pids = []
    try:
        if sys.platform == "win32":
            cmd = f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*teams_bot.py*' -and $_.CommandLine -like '*{session_id}*' }} | Select-Object -ExpandProperty ProcessId"
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", cmd], text=True, timeout=5.0)
            for line in out.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        else:
            out = subprocess.check_output(["pgrep", "-f", f"teams_bot.py.*{session_id}"], text=True, timeout=5.0)
            for line in out.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
    except Exception as e:
        logger.debug(f"[BrowserService] Process scan for session {session_id}: {e}")
    return pids


@app.post("/stop-meeting")
async def stop_meeting(req: StopMeetingRequest):
    session_id = req.session_id
    stopped = False

    # 1. Terminate tracked in-memory bot process tree
    if session_id in active_bots:
        bot_info = active_bots.pop(session_id)
        proc = bot_info.get("process")
        if proc:
            logger.info(f"[BrowserService] Terminating bot process tree for PID {proc.pid} (session {session_id})")
            terminate_process_tree(proc.pid)
            stopped = True

    # 2. Reload recovery: detect and terminate any orphaned bot process trees matching this session
    recovered_pids = find_running_bot_pids(session_id)
    for r_pid in recovered_pids:
        logger.info(f"[BrowserService] Terminating recovered bot process PID {r_pid} for session {session_id}")
        terminate_process_tree(r_pid)
        stopped = True

    if stopped:
        return {"status": "stopped", "session_id": session_id}
    return {"status": "not_found", "session_id": session_id}
