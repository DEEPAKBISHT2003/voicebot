import os
import asyncio
import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from loguru import logger
from typing import Dict, Any, List, Literal, Optional

from services.copilot.src.api.deps import get_copilot_repo, get_copilot_sessions
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.core.config import Settings
from services.copilot.src.services.readiness import compute_readiness
from services.copilot.src.websocket.handler import stop_native_monitor

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
        
        # Phase 1: Pre-compile profile in background during initialization
        asyncio.create_task(engine.precompile())

        active_sessions[session_id] = {
            "engine": engine,
            "status": "Connecting to audio stream...",
            "session_state": "CONNECTED",
            "report_ready": False,
            "report_status": "not_ready",
            "report_error": None,
            "transcript": engine.get_transcript(),
            "timestamp": datetime.datetime.now().isoformat(),
            "jd": req.jd,
            "resume": req.resume,
            "custom_prompt": req.custom_prompt,
            "is_active": True,
            "service_off": False,
            "websocket": None,
            "bot_joined": False,
            "caption_socket_connected": False,
            "transcript_processor_initialized": True,
            "first_caption_received": False,
            "readiness_confirmed": False,
            "caption_count": 0,
            "unique_speakers": set(),
            "unique_speakers_detected": 0,
            "first_caption_timestamp": None,
            "last_caption_time": None,
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

async def _execute_service_off_finalization(session_id: str, sess: dict, repo: CopilotRepository):
    """
    Executes session finalization following service disconnect:
    1. FINALIZING: Flushes turn aggregators into transcript and finalizes QA FSM.
    2. GENERATING_REPORT: Invokes engine.finalize_report().
    3. REPORT_READY: Updates report_ready=True, report_status='ready', persists to DB.
    On error: Sets report_ready=False, report_status='failed', report_error=...
    """
    logger.info(f"[CopilotServiceOff] Background finalization started for session {session_id}")
    dashboards = list(sess.get("dashboard_websockets", set()))
    try:
        sess["session_state"] = "FINALIZING"
        sess["status"] = "Finalizing session..."

        # Notify dashboards of FINALIZING state
        for ws in dashboards:
            try:
                await ws.send_json({
                    "type": "copilot_update",
                    "session_id": session_id,
                    "status": "Finalizing session...",
                    "session_state": "FINALIZING",
                    "report_ready": False,
                    "report_status": "generating",
                    "service_off": True
                })
            except Exception:
                pass

        # Capture and commit any pending speech/turns from aggregators
        turn_agg = sess.get("turn_aggregator")
        logical_agg = sess.get("logical_aggregator")
        engine = sess.get("engine")
        if turn_agg and logical_agg and engine:
            try:
                flushed_seqs = turn_agg.finalize_all_pending()
                for s_list in flushed_seqs.values():
                    for fseq in s_list:
                        turns = logical_agg.process_finalized_sequence(fseq)
                        for t in turns:
                            await engine.add_message(
                                speaker=t.speaker_name,
                                text=t.text,
                                source="teams_native",
                                allow_merge=False,
                                turn_id=t.logical_turn_id,
                                is_final=True
                            )
                final_turns = logical_agg.flush()
                for t in final_turns:
                    await engine.add_message(
                        speaker=t.speaker_name,
                        text=t.text,
                        source="teams_native",
                        allow_merge=False,
                        turn_id=t.logical_turn_id,
                        is_final=True
                    )
                sess["transcript"] = engine.get_transcript()
            except Exception as flush_err:
                logger.warning(f"[CopilotServiceOff] Aggregator turn flush warning: {flush_err}")

        # Stop native monitor task as part of service off finalization
        stop_native_monitor(sess, session_id, reason="service_off_finalization")

        # Transition to GENERATING_REPORT
        sess["session_state"] = "GENERATING_REPORT"
        sess["status"] = "Generating final report..."
        for ws in dashboards:
            try:
                await ws.send_json({
                    "type": "copilot_update",
                    "session_id": session_id,
                    "status": "Generating final report...",
                    "session_state": "GENERATING_REPORT",
                    "report_ready": False,
                    "report_status": "generating",
                    "service_off": True
                })
            except Exception:
                pass

        if not engine:
            db_sess = await repo.load_session(session_id)
            engine = CopilotSessionEngine(
                session_id,
                repo,
                db_sess.get("transcript", []),
                jd=db_sess.get("jd", ""),
                resume=db_sess.get("resume", ""),
                custom_prompt=db_sess.get("custom_prompt", ""),
                confirmed_qa_pairs=db_sess.get("confirmed_qa_pairs", [])
            )
            sess["engine"] = engine

        res = await engine.finalize_report()
        if res and res.get("is_finalized"):
            sess["final_report"] = res
            sess["session_state"] = "REPORT_READY"
            sess["report_ready"] = True
            sess["report_status"] = "ready"
            sess["status"] = "Service Disconnected"
            sess["report_error"] = None

            # Persist finalized report and state to DB
            await repo.save_session(
                session_id,
                {
                    "session_id": session_id,
                    "status": "Service Disconnected",
                    "service_off": True,
                    "final_report": res,
                    "transcript": engine.get_transcript(),
                    "report_ready": True
                }
            )
            logger.info(f"[CopilotServiceOff] Final evaluation report successfully generated and saved for {session_id}")

            for ws in dashboards:
                try:
                    await ws.send_json({
                        "type": "copilot_update",
                        "session_id": session_id,
                        "status": "Service Disconnected",
                        "session_state": "REPORT_READY",
                        "report_ready": True,
                        "report_status": "ready",
                        "service_off": True,
                        "final_report": res
                    })
                    await ws.close(code=1000)
                except Exception:
                    pass
            sess.get("dashboard_websockets", set()).clear()

        else:
            raise RuntimeError("Final evaluation synthesis produced invalid or unconfirmed report")

    except Exception as fe:
        logger.error(f"[CopilotServiceOff] Final report generation failed for session {session_id}: {fe}")
        sess["session_state"] = "SERVICE_DISCONNECTED"
        sess["report_ready"] = False
        sess["report_status"] = "failed"
        sess["report_error"] = "Final report generation failed."
        sess["status"] = "Final report generation failed."

        for ws in dashboards:
            try:
                await ws.send_json({
                    "type": "copilot_update",
                    "session_id": session_id,
                    "status": "Final report generation failed.",
                    "session_state": "SERVICE_DISCONNECTED",
                    "report_ready": False,
                    "report_status": "failed",
                    "report_error": "Final report generation failed.",
                    "service_off": True
                })
                await ws.close(code=1000)
            except Exception:
                pass
        sess.get("dashboard_websockets", set()).clear()


@router.post("/{session_id}/service-off")
async def service_off_copilot(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    """
    Dedicated SERVICE OFF endpoint:
    - Sets session_state to DISCONNECT_REQUESTED and initiates background finalization
    - Requests the browser service to make the Teams bot leave and cleanly shut down
    - Transitions session through FINALIZING -> GENERATING_REPORT -> SERVICE_DISCONNECTED -> REPORT_READY
    - Sets report_ready=True ONLY once report is finalized and persisted
    - Strictly idempotent
    """
    logger.info(f"[CopilotServiceOff] Received SERVICE OFF request for session: {session_id}")

    # 1. Update in-memory session state
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        sess["service_off"] = True
        sess["is_active"] = False
        sess["session_state"] = "DISCONNECT_REQUESTED"
        sess["report_ready"] = False
        sess["report_status"] = "generating"
        sess["report_error"] = None
        sess["status"] = "Disconnecting..."
    else:
        active_sessions[session_id] = {
            "service_off": True,
            "is_active": False,
            "session_state": "DISCONNECT_REQUESTED",
            "report_ready": False,
            "report_status": "generating",
            "report_error": None,
            "status": "Disconnecting...",
            "transcript": [],
            "dashboard_websockets": set()
        }
        sess = active_sessions[session_id]

    # 2. Persist flag to session directory
    try:
        session_dir = os.path.join("interviews", session_id)
        os.makedirs(session_dir, exist_ok=True)
        flag_path = os.path.join(session_dir, "service_off.flag")
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write("service_off")
    except Exception as fe:
        logger.warning(f"[CopilotServiceOff] Error creating local flag: {fe}")

    # 3. Request Browser Service to execute Service Off (gracefully clicking Leave)
    browser_url = os.getenv("BROWSER_SERVICE_URL", os.getenv("BROWSER_URL", "http://browser-service:8002"))
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(f"{browser_url}/service-off", json={"session_id": session_id})
            logger.info(f"[CopilotServiceOff] Browser service response: {resp.status_code}")
    except Exception as be:
        logger.warning(f"[CopilotServiceOff] Notification to browser-service failed or skipped ({be}); checking local bot process...")

    # 4. If bot process was spawned locally, wait bounded time or terminate
    bot_process = sess.get("bot_process")
    if bot_process and bot_process.poll() is None:
        logger.info(f"[CopilotServiceOff] Waiting for local bot process PID {bot_process.pid} to exit gracefully...")
        start_t = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_t) < 6.0:
            if bot_process.poll() is not None:
                break
            await asyncio.sleep(0.5)

        if bot_process.poll() is None:
            logger.warning(f"[CopilotServiceOff] Local bot PID {bot_process.pid} did not exit within timeout, terminating...")
            try:
                bot_process.terminate()
                try:
                    bot_process.wait(timeout=2.0)
                except Exception:
                    bot_process.kill()
            except Exception as pe:
                logger.warning(f"[CopilotServiceOff] Error terminating local bot: {pe}")
        sess["bot_process"] = None

    # 4b. Explicitly save audio recording if buffered, stop worker/runner, and close audio producer WebSocket
    audio_buf = sess.get("audio_buffer")
    if audio_buf:
        rec_path = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id, "recording.wav")
        if not os.path.exists(rec_path):
            try:
                import wave
                user_audio = bytes(audio_buf._user_audio_buffer) if hasattr(audio_buf, "_user_audio_buffer") else b""
                directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
                os.makedirs(directory, exist_ok=True)
                frames_to_write = user_audio if len(user_audio) > 0 else (b"\x00" * 32000)
                with wave.open(rec_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(frames_to_write)
                logger.info(f"[CopilotServiceOff] Directly saved recording.wav for session {session_id} ({len(frames_to_write)} bytes)")
            except Exception as fe:
                logger.warning(f"[CopilotServiceOff] Error directly saving recording: {fe}")

    audio_ws = sess.get("audio_websocket")
    if audio_ws:
        try:
            await audio_ws.close(code=1000)
            logger.info(f"[CopilotServiceOff] Closed audio producer WebSocket for session {session_id}")
        except Exception as awe:
            logger.warning(f"[CopilotServiceOff] Error closing audio producer WebSocket: {awe}")
        sess["audio_websocket"] = None

    worker = sess.get("worker")
    if worker:
        try:
            if hasattr(worker, "cancel"):
                await worker.cancel()
            elif hasattr(worker, "stop"):
                await worker.stop()
        except Exception as we:
            logger.warning(f"[CopilotServiceOff] Error stopping worker: {we}")

    runner = sess.get("runner")
    if runner:
        try:
            if hasattr(runner, "stop"):
                await runner.stop()
            elif hasattr(runner, "cancel"):
                await runner.cancel()
        except Exception as re:
            logger.warning(f"[CopilotServiceOff] Error stopping runner: {re}")

    # 5. Broadcast DISCONNECT_REQUESTED to dashboards
    dashboards = list(sess.get("dashboard_websockets", set()))
    for ws in dashboards:
        try:
            await ws.send_json({
                "type": "copilot_update",
                "session_id": session_id,
                "status": "Disconnecting...",
                "session_state": "DISCONNECT_REQUESTED",
                "service_off": True,
                "is_active": False,
                "report_ready": False,
                "report_status": "generating"
            })
        except Exception:
            pass

    # 6. Save intermediate session state
    try:
        await repo.save_session(
            session_id,
            {
                "session_id": session_id,
                "status": "Service Disconnected",
                "service_off": True,
                "transcript": sess.get("transcript", [])
            }
        )
    except Exception as se:
        logger.debug(f"[CopilotServiceOff] Session save notice: {se}")

    # 7. Spawn background finalization task to transition FINALIZING -> GENERATING_REPORT -> REPORT_READY
    asyncio.create_task(_execute_service_off_finalization(session_id, sess, repo))

    logger.info(f"[CopilotServiceOff] Service Off initiated for {session_id}; finalization dispatched in background")
    return {
        "status": "Service Disconnected",
        "session_id": session_id,
        "session_state": "DISCONNECT_REQUESTED",
        "report_ready": False,
        "report_status": "generating"
    }

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
        engine = sess.get("engine")
        final_rep = sess.get("final_report")
        if not final_rep:
            try:
                db_sess = await repo.load_session(session_id)
                if db_sess.get("final_report"):
                    final_rep = db_sess.get("final_report")
                    sess["final_report"] = final_rep
                    sess["is_active"] = False
                    sess["status"] = "Service Disconnected" if db_sess.get("service_off") else "Session completed."
            except Exception:
                pass

        intelligence = engine.get_intelligence() if engine else {}
        assistance = engine.get_assistance() if engine else {}
        if isinstance(final_rep, dict):
            if not intelligence or intelligence.get("current_topic") == "":
                intelligence = final_rep.get("intelligence", intelligence)
            if not assistance or not assistance.get("suggested_follow_up_questions"):
                assistance = final_rep.get("assistance", assistance)

        raw_transcript = engine.get_transcript() if engine else sess.get("transcript", [])
        normalized_transcript = []
        for idx, entry in enumerate(raw_transcript):
            e_copy = dict(entry)
            t_id = e_copy.get("turn_id") if e_copy.get("turn_id") is not None else (idx + 1)
            e_copy.setdefault("turn_id", t_id)
            e_copy.setdefault("id", f"{session_id}-turn-{t_id}")
            e_copy.setdefault("source", "teams_native")
            normalized_transcript.append(e_copy)

        has_ready_report = bool(final_rep and isinstance(final_rep, dict) and final_rep.get("is_finalized") is True)
        report_ready = sess.get("report_ready", has_ready_report) or has_ready_report

        session_state = sess.get("session_state")
        if not session_state:
            if report_ready:
                session_state = "REPORT_READY"
            elif sess.get("service_off"):
                session_state = "SERVICE_DISCONNECTED"
            elif sess.get("is_active"):
                session_state = "IN_MEETING" if raw_transcript else "CONNECTED"
            else:
                session_state = "CONNECTED"

        report_status = sess.get("report_status")
        if not report_status:
            if report_ready:
                report_status = "ready"
            elif sess.get("service_off") and not report_ready:
                report_status = "generating" if not sess.get("report_error") else "failed"
            else:
                report_status = "not_ready"

        is_act = False if (report_ready or sess.get("service_off")) else sess.get("is_active", True)
        status_text = sess.get("status", "ready")
        if sess.get("service_off") and report_ready:
            status_text = "Service Disconnected"

        readiness = compute_readiness(sess)

        return {
            "session_id": session_id,
            "is_active": is_act,
            "service_off": sess.get("service_off", False),
            "status": status_text,
            "session_state": session_state,
            "report_ready": report_ready,
            "report_status": report_status,
            "report_error": sess.get("report_error"),
            "readiness": readiness,
            "interview_ready": readiness["interview_ready"],
            "readiness_confirmed": readiness["readiness_confirmed"],
            "readiness_state": readiness["state"],
            "bot_joined": readiness["bot_joined"],
            "caption_socket_connected": readiness["caption_socket_connected"],
            "transcript_processor_initialized": readiness["transcript_processor_initialized"],
            "first_caption_received": readiness["first_caption_received"],
            "has_proven_transcript": readiness.get("has_proven_transcript", False),
            "caption_count": readiness["caption_count"],
            "unique_speakers_detected": readiness["unique_speakers_detected"],
            "first_caption_timestamp": readiness["first_caption_timestamp"],
            "transcript": normalized_transcript,
            "intelligence": intelligence,
            "assistance": assistance,
            "confirmed_qa_pairs": getattr(engine, "confirmed_qa_pairs", []) if engine else sess.get("confirmed_qa_pairs", []),
            "final_report": final_rep,
            "custom_prompt": sess.get("custom_prompt", "")
        }
    else:
        # Fallback to database load
        try:
            db_session = await repo.load_session(session_id)
            is_service_off = db_session.get("service_off", False)
            final_report = db_session.get("final_report")
            has_ready_report = bool(final_report and isinstance(final_report, dict) and final_report.get("is_finalized") is True)

            intelligence = {}
            assistance = {}
            if isinstance(final_report, dict):
                intelligence = final_report.get("intelligence", {})
                assistance = final_report.get("assistance", {})
            elif db_session.get("intelligence"):
                intelligence = db_session.get("intelligence", {})
                assistance = db_session.get("assistance", {})

            raw_transcript = db_session.get("transcript", [])
            normalized_transcript = []
            for idx, entry in enumerate(raw_transcript):
                e_copy = dict(entry)
                t_id = e_copy.get("turn_id") if e_copy.get("turn_id") is not None else (idx + 1)
                e_copy.setdefault("turn_id", t_id)
                e_copy.setdefault("id", f"{session_id}-turn-{t_id}")
                e_copy.setdefault("source", "teams_native")
                normalized_transcript.append(e_copy)

            session_state = "REPORT_READY" if has_ready_report else ("SERVICE_DISCONNECTED" if is_service_off else "CONNECTED")
            report_status = "ready" if has_ready_report else ("not_ready" if not is_service_off else "failed")
            status_text = "Service Disconnected" if is_service_off else "Session completed."

            readiness = compute_readiness(db_session)

            return {
                "session_id": session_id,
                "is_active": False,
                "service_off": is_service_off,
                "status": status_text,
                "session_state": session_state,
                "report_ready": has_ready_report,
                "report_status": report_status,
                "report_error": None if has_ready_report else ("Final report not found" if is_service_off else None),
                "readiness": readiness,
                "interview_ready": readiness["interview_ready"],
                "readiness_confirmed": readiness["readiness_confirmed"],
                "readiness_state": readiness["state"],
                "bot_joined": readiness["bot_joined"],
                "caption_socket_connected": readiness["caption_socket_connected"],
                "transcript_processor_initialized": readiness["transcript_processor_initialized"],
                "first_caption_received": readiness["first_caption_received"],
                "caption_count": readiness["caption_count"],
                "unique_speakers_detected": readiness["unique_speakers_detected"],
                "first_caption_timestamp": readiness["first_caption_timestamp"],
                "transcript": normalized_transcript,
                "intelligence": intelligence,
                "assistance": assistance,
                "confirmed_qa_pairs": db_session.get("confirmed_qa_pairs", []),
                "final_report": final_report,
                "custom_prompt": db_session.get("custom_prompt", "")
            }
        except FileNotFoundError:
            # Session not yet initialized in copilot — return a default waiting state
            readiness = compute_readiness({})
            return {
                "session_id": session_id,
                "is_active": False,
                "status": "Copilot session initializing...",
                "session_state": "CONNECTED",
                "report_ready": False,
                "report_status": "not_ready",
                "report_error": None,
                "readiness": readiness,
                "interview_ready": readiness["interview_ready"],
                "readiness_confirmed": readiness["readiness_confirmed"],
                "readiness_state": readiness["state"],
                "bot_joined": readiness["bot_joined"],
                "caption_socket_connected": readiness["caption_socket_connected"],
                "transcript_processor_initialized": readiness["transcript_processor_initialized"],
                "first_caption_received": readiness["first_caption_received"],
                "caption_count": readiness["caption_count"],
                "unique_speakers_detected": readiness["unique_speakers_detected"],
                "first_caption_timestamp": readiness["first_caption_timestamp"],
                "transcript": [],
                "intelligence": {},
                "assistance": {},
                "final_report": None,
                "custom_prompt": ""
            }

@router.post("/{session_id}/finalize")
async def finalize_copilot_report(
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
    repo: CopilotRepository = Depends(get_copilot_repo)
):
    # 1. Always check PostgreSQL first for an already persisted final_report
    db_session = None
    try:
        db_session = await repo.load_session(session_id)
        if db_session.get("final_report"):
            logger.info(f"Returning already finalized report from DB for session {session_id} (zero LLM calls)")
            if session_id in active_sessions:
                active_sessions[session_id]["is_active"] = False
                active_sessions[session_id]["final_report"] = db_session["final_report"]
                active_sessions[session_id]["report_ready"] = True
                active_sessions[session_id]["report_status"] = "ready"
                active_sessions[session_id]["session_state"] = "REPORT_READY"
                active_sessions[session_id]["status"] = "Service Disconnected"
            return db_session["final_report"]
    except FileNotFoundError:
        pass

    # 2. Check active memory
    if session_id in active_sessions:
        sess = active_sessions[session_id]

        # Idempotency check: if report already finalized for active session, return it
        if sess.get("final_report"):
            logger.info(f"Returning already finalized report for active session {session_id}")
            sess["is_active"] = False
            sess["report_ready"] = True
            sess["report_status"] = "ready"
            sess["session_state"] = "REPORT_READY"
            sess["status"] = "Service Disconnected"
            return sess["final_report"]

        engine = sess["engine"]

        # Capture and commit any pending speech/turns from aggregators before terminating bot
        try:
            turn_agg = sess.get("turn_aggregator")
            logical_agg = sess.get("logical_aggregator")
            if turn_agg and logical_agg:
                flushed_seqs = turn_agg.finalize_all_pending()
                for s_list in flushed_seqs.values():
                    for fseq in s_list:
                        turns = logical_agg.process_finalized_sequence(fseq)
                        for t in turns:
                            await engine.add_message(
                                speaker=t.speaker_name,
                                text=t.text,
                                source="teams_native",
                                allow_merge=False,
                                turn_id=t.logical_turn_id,
                                is_final=True
                            )
                final_turns = logical_agg.flush()
                for t in final_turns:
                    await engine.add_message(
                        speaker=t.speaker_name,
                        text=t.text,
                        source="teams_native",
                        allow_merge=False,
                        turn_id=t.logical_turn_id,
                        is_final=True
                    )
                sess["transcript"] = engine.get_transcript()
        except Exception as flush_err:
            logger.warning(f"[Finalize] Error flushing pending turns before bot termination: {flush_err}")

        # Stop native monitor task as part of finalize endpoint execution
        stop_native_monitor(sess, session_id, reason="session_finalize_endpoint")

        # Safely terminate bot process now that caption buffers are flushed
        bot_process = sess.get("bot_process")
        if bot_process and bot_process.poll() is None:
            logger.info(f"[TeamsBot] Terminating bot process for session {session_id} on finalize (PID: {bot_process.pid})")
            try:
                bot_process.terminate()
            except Exception:
                pass
        sess["bot_process"] = None

        sess["session_state"] = "GENERATING_REPORT"
        sess["report_status"] = "generating"
        res = await engine.finalize_report()
        if res and res.get("is_finalized"):
            sess["is_active"] = False
            sess["final_report"] = res
            sess["report_ready"] = True
            sess["report_status"] = "ready"
            sess["session_state"] = "REPORT_READY"
            sess["status"] = "Service Disconnected"
            sess["report_error"] = None
            try:
                await repo.save_session(session_id, {
                    "final_report": res,
                    "status": "Service Disconnected",
                    "service_off": True,
                    "transcript": engine.get_transcript(),
                    "report_ready": True
                })
            except Exception as se:
                logger.debug(f"[Finalize] Save report notice: {se}")
        else:
            sess["report_ready"] = False
            sess["report_status"] = "failed"
            sess["report_error"] = "Final report generation failed."
        return res
    else:
        if not db_session:
            raise HTTPException(status_code=404, detail="Session not found")

        engine = CopilotSessionEngine(
            session_id,
            repo,
            db_session.get("transcript", []),
            jd=db_session.get("jd", ""),
            resume=db_session.get("resume", ""),
            custom_prompt=db_session.get("custom_prompt", ""),
            confirmed_qa_pairs=db_session.get("confirmed_qa_pairs", [])
        )
        res = await engine.finalize_report()
        return res



class JoinMeetingRequest(BaseModel):
    meeting_url: str
    bot_role: Literal["observer", "interviewer"] = "observer"
    bot_name: str = "Appzlogic Observer"

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
        env = os.environ.copy()
        env["BOT_ROLE"] = req.bot_role
        env["BOT_DISPLAY_NAME"] = req.bot_name

        process = subprocess.Popen(
            [python_exe, script_path, req.meeting_url, session_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout for real-time interleaved logs
            cwd=workspace_root,
            bufsize=1,  # Line-buffered
            env=env,
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
                        if any(term in decoded for term in ("IN_MEETING", "Meeting admission confirmed", "Real in-meeting state", "Pre-join name input submitted", "Clicking Join now", "Live Captions successfully enabled")):
                            if session_id in active_sessions:
                                active_sessions[session_id]["bot_joined"] = True
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


class NativeCaptionEventRequest(BaseModel):
    event_type: str = "native_caption"
    session_id: Optional[str] = None
    event_id: Optional[str] = None
    speaker_name: Optional[str] = "Unknown"
    text: str
    detected_at: Optional[str] = None
    source: str = "teams_native"
    is_final: Optional[bool] = None
    finality: Optional[str] = "unknown"
    caption_sequence: Optional[int] = None
    dom_action: Optional[str] = "updated"


@router.post("/{session_id}/native-captions")
async def post_native_caption(
    session_id: str,
    req: NativeCaptionEventRequest,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions)
):
    """
    HTTP REST fallback endpoint for Teams Native Live Caption transport.
    Validates the session, schema, logs the event, and appends to in-memory runtime and disk artifacts.
    CRITICAL: Does NOT invoke engine.add_message() and does NOT trigger evaluation or candidate scoring.
    """
    import uuid
    import json

    # Guard against inactive, completed, or Service Off sessions
    sess = active_sessions.get(session_id)
    is_service_off = os.path.exists(os.path.join("interviews", session_id, "service_off.flag"))
    if sess:
        is_service_off = is_service_off or sess.get("service_off", False)
        is_completed = bool(sess.get("final_report")) or (sess.get("is_active") is False)
    else:
        is_completed = is_service_off

    if is_service_off or is_completed:
        raise HTTPException(
            status_code=403 if is_service_off else 400,
            detail="Session is inactive or marked Service Off. Native caption rejected."
        )

    text = req.text.strip() if req.text else ""
    if not text:
        return {"status": "skipped", "reason": "empty_text"}

    raw_speaker = (req.speaker_name or "").strip() or "Unknown"
    detected_at_str = req.detected_at
    received_at_dt = datetime.datetime.now(datetime.timezone.utc)
    received_at_str = received_at_dt.isoformat()

    latency_ms = None
    if detected_at_str:
        try:
            detected_dt = datetime.datetime.fromisoformat(detected_at_str.replace("Z", "+00:00"))
            latency_ms = max(0.0, (received_at_dt - detected_dt).total_seconds() * 1000.0)
        except Exception:
            pass

    event_record = {
        "event_type": "native_caption",
        "session_id": session_id,
        "event_id": req.event_id or str(uuid.uuid4()),
        "speaker_name": raw_speaker,
        "text": text,
        "detected_at": detected_at_str or received_at_str,
        "received_at": received_at_str,
        "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
        "source": "teams_native",
        "is_final": req.is_final,
        "finality": req.finality or "unknown",
        "caption_sequence": req.caption_sequence,
        "dom_action": req.dom_action or "updated",
    }

    if sess:
        sess["bot_joined"] = True
        sess["caption_socket_connected"] = True
        sess["first_caption_received"] = True
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        sess["last_caption_time"] = now_ts
        if "first_caption_timestamp" not in sess or sess["first_caption_timestamp"] is None:
            sess["first_caption_timestamp"] = now_ts
        sess["caption_count"] = sess.get("caption_count", 0) + 1
        if not isinstance(sess.get("unique_speakers"), set):
            sess["unique_speakers"] = set(sess.get("unique_speakers") or [])
        if raw_speaker and raw_speaker.lower() != "unknown":
            sess["unique_speakers"].add(raw_speaker)
        sess["unique_speakers_detected"] = len(sess["unique_speakers"])
        sess.setdefault("native_captions", []).append(event_record)
        logical_agg = sess.get("logical_aggregator")
        eng = sess.get("engine")
        if logical_agg and eng:
            active_prog = logical_agg.update_interim_text(
                sequence_id=req.caption_sequence,
                speaker_name=raw_speaker,
                text=text,
                current_time=detected_dt if detected_at_str else received_at_dt
            )
            if active_prog:
                logger.info(
                    f"[Transcript] Progressive Turn Emitted: session_id={session_id}, speaker='{active_prog.speaker_name}', "
                    f"sequence_id={req.caption_sequence}, turn_id={active_prog.logical_turn_id}, timestamp='{received_at_str}'"
                )
                async def _dispatch_prog(t=active_prog):
                    prog_msg = await eng.add_message(
                        speaker=t.speaker_name,
                        text=t.text,
                        source="teams_native",
                        allow_merge=False,
                        turn_id=t.logical_turn_id,
                        is_final=False
                    )
                    sess["transcript"] = eng.get_transcript()
                    sess["has_proven_transcript"] = True
                    if sess.get("engine") and sess["engine"].on_update_callback:
                        await sess["engine"].on_update_callback(prog_msg, is_final=False)
                    logger.info(
                        f"[Transcript] Interim Broadcast Sent: session_id={session_id}, speaker='{t.speaker_name}', "
                        f"turn_id={t.logical_turn_id}, text='{t.text}'"
                    )
                import asyncio
                asyncio.create_task(_dispatch_prog())

        readiness = compute_readiness(sess)
        if readiness["readiness_confirmed"]:
            sess["readiness_confirmed"] = True

        aggregator = sess.get("turn_aggregator")
        if aggregator:
            try:
                newly_fin = aggregator.process_raw_event(event_record)
                disp_ids = sess.get("dispatched_seq_ids")
                if logical_agg and eng and disp_ids is not None:
                    for strat in ("strategy_c_quiescence_1500ms", "strategy_b_dom_removal"):
                        for fseq in newly_fin.get(strat, []):
                            if fseq.sequence_id not in disp_ids:
                                disp_ids.add(fseq.sequence_id)
                                completed_turns = logical_agg.process_finalized_sequence(fseq)
                                for turn in completed_turns:
                                    import asyncio
                                    import time as _time
                                    logger.info(
                                        f"[Transcript] Final Turn Replaced Interim: session_id={session_id}, speaker='{turn.speaker_name}', "
                                        f"turn_id={turn.logical_turn_id}, text='{turn.text}'"
                                    )
                                    async def _dispatch_turn(t=turn):
                                        last_msg = await eng.add_message(
                                            speaker=t.speaker_name,
                                            text=t.text,
                                            source="teams_native",
                                            allow_merge=False,
                                            turn_id=t.logical_turn_id,
                                            is_final=True
                                        )
                                        sess["transcript"] = eng.get_transcript()
                                        sess["last_speech_time"] = _time.time()
                                        sess["has_proven_transcript"] = True
                                        if sess.get("engine") and sess["engine"].on_update_callback:
                                            await sess["engine"].on_update_callback(last_msg, is_final=True)
                                    asyncio.create_task(_dispatch_turn())
            except Exception as agg_err:
                logger.warning(f"[NativeHTTP] Error processing event in turn_aggregator: {agg_err}")

        for sub_ws in list(sess.get("native_caption_websockets", set())):
            try:
                import asyncio
                asyncio.create_task(sub_ws.send_text(json.dumps(event_record)))
            except Exception:
                pass

    session_dir = os.path.join("interviews", session_id)
    os.makedirs(session_dir, exist_ok=True)
    captions_file_path = os.path.join(session_dir, "native_captions.jsonl")
    with open(captions_file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event_record) + "\n")

    poc_dir = os.path.join("interviews", "phase2i_poc")
    os.makedirs(poc_dir, exist_ok=True)
    with open(os.path.join(poc_dir, "native_captions.jsonl"), "a", encoding="utf-8") as pf:
        pf.write(json.dumps(event_record) + "\n")

    lat_disp = f"{latency_ms:.1f}ms" if latency_ms is not None else "N/A"
    logger.info(
        f"[TEAMS_NATIVE_HTTP] speaker='{raw_speaker}' text='{text}' "
        f"seq={req.caption_sequence} detected_at='{detected_at_str}' received_at='{received_at_str}' latency={lat_disp}"
    )

    return {"status": "recorded", "event_id": event_record["event_id"]}

