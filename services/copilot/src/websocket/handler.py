from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from loguru import logger
from typing import Dict, Any, Set, Optional
import json
import asyncio

from services.copilot.src.api.deps import get_copilot_sessions_ws, get_copilot_repo_ws
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.pipeline.builder import CopilotPipelineBuilder
from services.copilot.src.core.config import Settings
from pipecat.workers.runner import WorkerRunner

router = APIRouter()

@router.websocket("/api/ws/copilot/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions_ws),
    repo: CopilotRepository = Depends(get_copilot_repo_ws)
):
    await websocket.accept()
    mode = websocket.query_params.get("mode", "")
    is_audio_producer = (mode == "audio_stream")
    logger.info(f"[CopilotWS] WebSocket client connected (session={session_id}, is_audio_producer={is_audio_producer})")
    
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
                "dashboard_websockets": set(),
                "speaker_map": {}
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
                "dashboard_websockets": set(),
                "speaker_map": {}
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
    sess.setdefault("dashboard_websockets", set())
    speaker_map = sess.setdefault("speaker_map", {})

    # Helper function to broadcast updated session state to all connected dashboard clients
    async def broadcast_update(last_message: Optional[dict] = None):
        eng = sess.get("engine")
        if not eng:
            return
        payload = {
            "type": "copilot_update",
            "session_id": session_id,
            "last_message": last_message,
            "transcript": eng.get_transcript(),
            "intelligence": eng.get_intelligence(),
            "assistance": eng.get_assistance()
        }
        
        dead_sockets = set()
        dashboards = set(sess.get("dashboard_websockets", set()))
        for dash_ws in dashboards:
            try:
                await dash_ws.send_json(payload)
            except Exception as ws_err:
                logger.debug(f"[CopilotWS] Failed to broadcast update to dashboard client: {ws_err}")
                dead_sockets.add(dash_ws)
        
        if dead_sockets:
            sess["dashboard_websockets"].difference_update(dead_sockets)

    # Audio Producer Branch (Teams Bot / Raw Audio Stream)
    if is_audio_producer:
        sess["status"] = "Listening to audio stream..."
        
        async def on_transcript_entry(entry: dict):
            raw_spk = entry.get("speaker")
            text_content = entry.get("text", "").strip()
            if not text_content:
                return

            if raw_spk is not None:
                spk_key = str(raw_spk)
                if spk_key not in speaker_map:
                    if len(speaker_map) == 0:
                        speaker_map[spk_key] = "Candidate"
                    elif len(speaker_map) == 1:
                        speaker_map[spk_key] = "Interviewer"
                    else:
                        speaker_map[spk_key] = f"Speaker {len(speaker_map) + 1}"
                speaker = speaker_map[spk_key]
            else:
                role_val = entry.get("role", "user")
                speaker = "Candidate" if role_val == "user" else ("Interviewer" if role_val == "assistant" else "System")

            logger.info(f"[CopilotWS] Audio STT segment ({speaker}): {text_content}")

            eng = sess.get("engine")
            if eng:
                last_msg = await eng.add_message(speaker, text_content)
                sess["transcript"] = eng.get_transcript()
                await broadcast_update(last_msg)

        builder = CopilotPipelineBuilder()
        pipeline_res = builder.build_observer_pipeline(websocket, session_id, on_transcript_entry)

        if pipeline_res is not None:
            pipeline, worker = pipeline_res
            sess["worker"] = worker
            runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
            await runner.add_workers(worker)
            logger.info(f"[CopilotWS] Pipecat audio observer runner active for audio producer session {session_id}")
            try:
                await runner.run()
            except WebSocketDisconnect:
                logger.info(f"[CopilotWS] Audio producer disconnected: {session_id}")
            except Exception as err:
                logger.error(f"[CopilotWS] Audio pipeline error: {err}")
        else:
            logger.warning(f"[CopilotWS] Could not build observer pipeline for audio producer. Falling back to byte echo.")
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if "bytes" in msg:
                        await websocket.send_bytes(msg["bytes"])
            except WebSocketDisconnect:
                pass

    # Dashboard Subscriber Branch (Browser UI Window)
    else:
        sess["dashboard_websockets"].add(websocket)
        logger.info(f"[CopilotWS] Registered dashboard subscriber. Total active dashboards: {len(sess['dashboard_websockets'])}")
        
        # Send initial state frame to newly connected dashboard
        try:
            eng = sess["engine"]
            await websocket.send_json({
                "type": "copilot_update",
                "session_id": session_id,
                "last_message": eng.get_transcript()[-1] if eng.get_transcript() else None,
                "transcript": eng.get_transcript(),
                "intelligence": eng.get_intelligence(),
                "assistance": eng.get_assistance()
            })
        except Exception as initial_err:
            logger.warning(f"[CopilotWS] Could not send initial state frame to dashboard: {initial_err}")

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(code=msg.get("code", 1000))
                    
                if "text" in msg:
                    try:
                        payload = json.loads(msg["text"])
                        speaker = payload.get("speaker")
                        text = payload.get("text")
                        
                        if not speaker:
                            role = payload.get("role")
                            if role == "user":
                                speaker = "Candidate"
                            elif role == "assistant":
                                speaker = "Interviewer"
                            elif role == "system":
                                speaker = "System"

                        if speaker and text:
                            engine = sess["engine"]
                            last_msg = await engine.add_message(speaker, text)
                            sess["transcript"] = engine.get_transcript()
                            await broadcast_update(last_msg)
                    except Exception as parse_err:
                        logger.error(f"[CopilotWS] Error parsing dashboard text payload: {parse_err}")
        except WebSocketDisconnect:
            logger.info(f"[CopilotWS] Dashboard subscriber disconnected: {session_id}")
        finally:
            if session_id in active_sessions:
                active_sessions[session_id].get("dashboard_websockets", set()).discard(websocket)
