import os
import datetime
import asyncio
import wave
import sys
import io
import struct
from typing import Dict, Any, Optional
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

def classify_speaker_role(text: str, raw_spk: Optional[str], speaker_map: dict) -> str:
    """Hybrid role classifier: combines high-speed linguistic patterns with Deepgram diarization pitch tags."""
    clean_text = text.strip().lower()
    
    # Candidate linguistic markers (Overrides pitch misclassifications)
    candidate_starters = (
        "sir", "my name", "i am", "i have", "we visited", "in my", "my nanaji",
        "i accompanied", "so basically", "i built", "i worked", "my project"
    )
    if any(clean_text.startswith(starter) for starter in candidate_starters):
        return "Candidate"
        
    # Interviewer question indicators
    question_starters = (
        "tell me", "can you", "could you", "will you", "what is", "how do", 
        "why did", "describe", "where", "who", "when", "please explain"
    )
    if clean_text.endswith("?") or any(clean_text.startswith(q) for q in question_starters):
        return "Interviewer"
        
    # Dynamic Speaker ID Diarization Mapping
    if raw_spk is not None:
        spk_key = str(raw_spk)
        if spk_key not in speaker_map:
            if len(speaker_map) == 0:
                speaker_map[spk_key] = "Candidate"
            elif len(speaker_map) == 1:
                speaker_map[spk_key] = "Interviewer"
            else:
                speaker_map[spk_key] = f"Speaker {len(speaker_map) + 1}"
        return speaker_map[spk_key]
        
    return "Candidate"

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
    meeting_url: str = ""

async def spawn_teams_bot(session_id: str, meeting_url: str):
    logger.info(f"[TeamsBot] Spawning Teams Playwright Observer Bot for session {session_id} to meeting: {meeting_url}")
    python_exe = sys.executable
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pipeline", "teams_bot.py")
    
    try:
        # Launch the teams_bot.py script as an independent background process
        process = await asyncio.create_subprocess_exec(
            python_exe, script_path, meeting_url, session_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        logger.info(f"[TeamsBot] Subprocess spawned successfully with PID: {process.pid}")
        
        # Helper task to print bot output inside server log console
        async def log_stream(stream, prefix):
            while True:
                try:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded_line = line.decode("utf-8", errors="replace").strip()
                    logger.info(f"[{prefix}] {decoded_line}")
                except Exception as le:
                    logger.warning(f"[{prefix}] Failed to read/decode subprocess line: {le}")
                    break
                
        asyncio.create_task(log_stream(process.stdout, "TeamsBot-STDOUT"))
        asyncio.create_task(log_stream(process.stderr, "TeamsBot-STDERR"))
        
    except Exception as e:
        logger.error(f"[TeamsBot] Failed to spawn Teams observer bot process: {e}")

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
        "worker": None,
        "meeting_url": req.meeting_url
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
    
    # If meeting URL is provided, spawn Teams bot background task
    if req.meeting_url and req.meeting_url.strip():
        asyncio.create_task(spawn_teams_bot(session_id, req.meeting_url))
        active_sessions[session_id]["status"] = "Teams Bot joining meeting... Check dashboard suggestions."

    return {"session_id": session_id, "status": active_sessions[session_id]["status"]}

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
        db_sess = await repo.get_session(session_id) or {}
        active_sessions[session_id] = {
            "status": "Initializing...",
            "transcript": db_sess.get("transcript", []),
            "timestamp": db_sess.get("timestamp", datetime.datetime.now().isoformat()),
            "jd": db_sess.get("jd", ""),
            "resume": db_sess.get("resume", ""),
            "custom_prompt": db_sess.get("custom_prompt", ""),
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
                            speaker_map = copilot_sess.setdefault("speaker_map", {})
                            raw_spk = entry.get("speaker")
                            text_content = entry.get("text", "")
                            
                            speaker = classify_speaker_role(text_content, raw_spk, speaker_map)
                            logger.info(f"[CopilotObserver] Forwarding segment ({speaker}): {text_content}")
                            
                            copilot_ws = copilot_sess.get("websocket")
                            
                            # Add statement to active Copilot engine memory (returns INSTANTLY <5ms)
                            last_msg = await engine.add_message(speaker, text_content, websocket=copilot_ws)
                            
                            # Broadcast instant transcript update frame to dashboard WS client (<5ms)
                            if copilot_ws:
                                try:
                                    await copilot_ws.send_json({
                                        "type": "copilot_update",
                                        "session_id": sid,
                                        "last_message": last_msg,
                                        "transcript": engine.get_transcript(),
                                        "intelligence": engine.get_intelligence(),
                                        "assistance": engine.get_assistance()
                                    })
                                except Exception as ws_err:
                                    logger.debug(f"Instant WS broadcast error: {ws_err}")
                except Exception as e:
                    logger.error(f"[CopilotObserver] Failed to forward segment to Copilot engine: {e}")
        return callback
        
    mode = websocket.query_params.get("mode")
    is_observer = (mode == "observer")
    simulate = websocket.query_params.get("simulate")
    is_simulation = (simulate == "true")
    
    pipeline_builder = LocalPipecatPipelineBuilder(Settings.DEEPGRAM_API_KEY, Settings.DEEPSEEK_API_KEY)
    _, _, worker = pipeline_builder.build_pipeline(
        system_instruction=system_instruction,
        session_id=session_id,
        transcript_callback=make_transcript_callback(session_id),
        websocket=websocket,
        is_observer=is_observer,
        is_simulation=is_simulation
    )
    
    sess["worker"] = worker
    
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    if not is_observer:
        await worker.queue_frames([LLMRunFrame()])
    
    simulation_task = None
    if is_simulation:
        simulation_task = asyncio.create_task(simulate_audio_playback(session_id, worker, websocket))
    
    try:
        if is_observer:
            sess["status"] = "Teams Observer Copilot connected and listening..."
        else:
            sess["status"] = "Interview started! Say hello to the interviewer."
        await runner.run()
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from session {session_id}.")
    except Exception as e:
        logger.error(f"Error in WebSocket voice session: {e}", exc_info=True)
        sess["status"] = f"Error: {e}"
    finally:
        if simulation_task:
            simulation_task.cancel()
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

async def simulate_audio_playback(session_id: str, worker, websocket: WebSocket = None):
    # Find the wav file in the session directory
    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    file_path = os.path.join(directory, "uploaded_audio.wav")
    if not os.path.exists(file_path):
        logger.error(f"Simulation failed: file not found at {file_path}")
        return
    
    # Ensure audio file is normalized to 16kHz Mono PCM on the fly
    try:
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        normalized_bytes = normalize_wav_to_16k_mono(raw_bytes)
        if normalized_bytes != raw_bytes:
            with open(file_path, "wb") as f:
                f.write(normalized_bytes)
            logger.info(f"On-the-fly resampled and updated audio file at {file_path} to 16kHz Mono PCM.")
    except Exception as ne:
        logger.warning(f"Could not pre-check audio normalization: {ne}")

    logger.info(f"Starting audio simulation from {file_path}...")
    try:
        # Give some initial delay for websocket stabilization
        await asyncio.sleep(1.0)
        
        with wave.open(file_path, "rb") as wf:
            # Read 100ms chunks: 16000 * 0.1 = 1600 samples
            # 1600 samples * 2 bytes = 3200 bytes per chunk
            chunk_size = 1600
            
            while True:
                data = wf.readframes(chunk_size)
                if not data:
                    break
                
                from pipecat.frames.frames import InputAudioRawFrame
                frame = InputAudioRawFrame(
                    audio=data,
                    sample_rate=16000,
                    num_channels=1
                )
                await worker.queue_frames([frame])

                # Send raw binary audio bytes to client WebSocket so browser can hear it
                if websocket:
                    try:
                        await websocket.send_bytes(data)
                    except Exception as we:
                        logger.info(f"WebSocket client disconnected, ending audio simulation stream.")
                        break

                await asyncio.sleep(0.1) # 100ms interval
                
        logger.info("Audio simulation complete.")

        # Notify observer client that simulation has finished
        if websocket:
            try:
                await websocket.send_json({
                    "type": "simulation_complete",
                    "session_id": session_id
                })
            except Exception:
                pass

        # Update copilot session state if active
        copilot_sessions = getattr(websocket.app.state, "copilot_sessions", None) if websocket else None
        if copilot_sessions and session_id in copilot_sessions:
            copilot_sess = copilot_sessions[session_id]
            copilot_sess["is_active"] = False
            copilot_sess["status"] = "Recording finished. Click View Final Results to generate report."
            copilot_ws = copilot_sess.get("websocket")
            if copilot_ws:
                try:
                    await copilot_ws.send_json({
                        "type": "simulation_complete",
                        "session_id": session_id
                    })
                except Exception:
                    pass

    except asyncio.CancelledError:
        logger.info("Audio simulation task cancelled.")
    except Exception as e:
        logger.error(f"Error during audio simulation: {e}")

def normalize_wav_to_16k_mono(file_bytes: bytes) -> bytes:
    """
    Normalizes any WAV audio file bytes to strict 16000Hz 16-bit Mono PCM format.
    Supports 8-bit, 16-bit, 24-bit, 32-bit int and float PCM WAV inputs with arbitrary sample rates and channel counts.
    """
    try:
        in_io = io.BytesIO(file_bytes)
        with wave.open(in_io, "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            raw_data = wf.readframes(nframes)

        logger.info(f"Normalizing uploaded WAV file: {framerate}Hz, {nchannels}-channel, {sampwidth*8}-bit -> 16000Hz Mono 16-bit PCM...")

        # If already 16kHz mono 16-bit PCM, return as-is
        if nchannels == 1 and sampwidth == 2 and framerate == 16000:
            logger.info("Uploaded WAV is already 16kHz Mono 16-bit PCM.")
            return file_bytes

        # Extract samples as integer list
        total_samples = nframes * nchannels
        if sampwidth == 2:
            samples = list(struct.unpack(f"<{total_samples}h", raw_data))
        elif sampwidth == 1:
            # 8-bit unsigned
            raw_samples = struct.unpack(f"<{total_samples}B", raw_data)
            samples = [(s - 128) * 256 for s in raw_samples]
        elif sampwidth == 4:
            # 32-bit int or float
            try:
                raw_samples = struct.unpack(f"<{total_samples}i", raw_data)
                samples = [int(s / 65536) for s in raw_samples]
            except Exception:
                raw_samples = struct.unpack(f"<{total_samples}f", raw_data)
                samples = [int(s * 32767) for s in raw_samples]
        else:
            # Fallback for unknown widths
            try:
                import audioop
                converted_data, _ = audioop.ratecv(raw_data, sampwidth, nchannels, framerate, 16000, None)
                if nchannels > 1:
                    converted_data = audioop.tomono(converted_data, sampwidth, 0.5, 0.5)
                if sampwidth != 2:
                    converted_data = audioop.lin2lin(converted_data, sampwidth, 2)
                
                out_io = io.BytesIO()
                with wave.open(out_io, "wb") as out_wf:
                    out_wf.setnchannels(1)
                    out_wf.setsampwidth(2)
                    out_wf.setframerate(16000)
                    out_wf.writeframes(converted_data)
                return out_io.getvalue()
            except Exception as ae:
                logger.warning(f"Audioop fallback conversion warning: {ae}")
                return file_bytes

        # Step 1: Convert multi-channel (stereo) to mono
        if nchannels > 1:
            mono_samples = []
            for i in range(0, len(samples), nchannels):
                frame_chunk = samples[i:i + nchannels]
                avg_sample = sum(frame_chunk) // nchannels
                mono_samples.append(avg_sample)
            samples = mono_samples

        # Step 2: Resample to 16000 Hz if needed
        if framerate != 16000:
            target_length = int(len(samples) * 16000 / framerate)
            if target_length > 0:
                resampled = []
                step = (len(samples) - 1) / (target_length - 1) if target_length > 1 else 0
                for i in range(target_length):
                    pos = i * step
                    idx = int(pos)
                    frac = pos - idx
                    if idx + 1 < len(samples):
                        sample_val = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
                    else:
                        sample_val = samples[idx] if idx < len(samples) else 0
                    resampled.append(sample_val)
                samples = resampled

        # Clamp 16-bit values (-32768 to 32767)
        clamped_samples = [max(-32768, min(32767, s)) for s in samples]
        pcm_bytes = struct.pack(f"<{len(clamped_samples)}h", *clamped_samples)

        out_io = io.BytesIO()
        with wave.open(out_io, "wb") as out_wf:
            out_wf.setnchannels(1)
            out_wf.setsampwidth(2)
            out_wf.setframerate(16000)
            out_wf.writeframes(pcm_bytes)

        logger.info(f"Successfully normalized audio to 16kHz Mono 16-bit PCM ({len(clamped_samples)/16000:.1f}s).")
        return out_io.getvalue()
    except Exception as e:
        logger.error(f"Failed to normalize WAV file, using raw uploaded bytes: {e}")
        return file_bytes

@router.post("/interviews/{session_id}/upload-audio")
async def upload_audio_file(session_id: str, file: UploadFile = File(...)):
    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    os.makedirs(directory, exist_ok=True)
    
    # Save the file as uploaded_audio.wav after normalizing to 16kHz Mono PCM
    file_path = os.path.join(directory, "uploaded_audio.wav")
    
    try:
        content = await file.read()
        normalized_content = normalize_wav_to_16k_mono(content)
        with open(file_path, "wb") as buffer:
            buffer.write(normalized_content)
        logger.info(f"Successfully uploaded and normalized test audio file to {file_path}")
        return {"status": "success", "file_path": file_path}
    except Exception as e:
        logger.error(f"Failed to save uploaded audio file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded audio: {e}")

