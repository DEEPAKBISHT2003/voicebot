from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from loguru import logger
from typing import Dict, Any, Set, Optional
import json
import asyncio
import time
import os
import uuid
import datetime

from services.copilot.src.api.deps import get_copilot_sessions_ws, get_copilot_repo_ws
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.pipeline.builder import CopilotPipelineBuilder
from services.copilot.src.pipeline.native_turn_finalizer import NativeTurnAggregator
from services.copilot.src.pipeline.native_turn_aligner import NativeLogicalTurnAggregator
try:
    from pipecat.pipeline.runner import PipelineRunner as WorkerRunner
except ImportError:
    try:
        from pipecat.workers.runner import WorkerRunner
    except ImportError:
        try:
            from pipecat.runner.runner import PipelineRunner as WorkerRunner
        except ImportError:
            class WorkerRunner:
                def __init__(self, **kwargs): pass
                async def add_workers(self, w): pass
                async def run(self): pass

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
    is_native_captions = (mode == "native_captions")
    logger.info(f"[CopilotWS] WebSocket client connected (session={session_id}, is_audio_producer={is_audio_producer}, is_native_captions={is_native_captions})")
    
    # Initialize active session state if not already started
    if session_id not in active_sessions:
        try:
            db_session = await repo.load_session(session_id)
            is_service_off = db_session.get("service_off", False) or os.path.exists(os.path.join("interviews", session_id, "service_off.flag"))
            final_report = db_session.get("final_report")
            is_completed = bool(final_report) or is_service_off
            jd = db_session.get("jd", "")
            resume = db_session.get("resume", "")
            engine = CopilotSessionEngine(session_id, repo, db_session.get("transcript", []), jd=jd, resume=resume)
            if isinstance(final_report, dict):
                if "intelligence" in final_report and isinstance(final_report["intelligence"], dict):
                    engine.intelligence = final_report["intelligence"]
                if "assistance" in final_report and isinstance(final_report["assistance"], dict):
                    engine.assistance = final_report["assistance"]
            active_sessions[session_id] = {
                "engine": engine,
                "status": "Service Off" if is_service_off else ("Session completed." if is_completed else "Ready"),
                "service_off": is_service_off,
                "transcript": engine.get_transcript(),
                "timestamp": db_session.get("timestamp"),
                "jd": jd,
                "resume": resume,
                "custom_prompt": db_session.get("custom_prompt", ""),
                "is_active": False if is_completed else True,
                "final_report": final_report,
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
            final_report = db_session.get("final_report")
            is_completed = bool(final_report) or db_session.get("service_off", False)
            sess["engine"] = CopilotSessionEngine(
                session_id, 
                repo, 
                db_session.get("transcript", []),
                jd=db_session.get("jd", ""),
                resume=db_session.get("resume", "")
            )
            sess["final_report"] = final_report
            if db_session.get("service_off", False):
                sess["service_off"] = True
                sess["is_active"] = False
                sess["status"] = "Service Off"
            elif is_completed:
                sess["is_active"] = False
                sess["status"] = "Session completed."
        except Exception:
            sess["engine"] = CopilotSessionEngine(session_id, repo, [], jd="", resume="")

    # Guard against reactivating completed or permanently stopped Service Off sessions
    has_final_report = bool(sess.get("final_report"))
    is_service_off = sess.get("service_off", False) or os.path.exists(os.path.join("interviews", session_id, "service_off.flag"))
    if has_final_report or is_service_off or sess.get("is_active") is False:
        sess["service_off"] = is_service_off
        sess["is_active"] = False
        sess["status"] = "Service Off" if is_service_off else "Session completed."
        logger.info(f"[CopilotWS] Rejecting live WebSocket connection for completed session: {session_id}")
        try:
            eng = sess.get("engine")
            intel = eng.get_intelligence() if eng else {}
            assist = eng.get_assistance() if eng else {}
            rep = sess.get("final_report")
            if isinstance(rep, dict):
                intel = rep.get("intelligence", intel)
                assist = rep.get("assistance", assist)
            await websocket.send_json({
                "type": "copilot_update",
                "session_id": session_id,
                "status": sess["status"],
                "service_off": is_service_off,
                "is_active": False,
                "transcript": sess.get("transcript", []),
                "intelligence": intel,
                "assistance": assist,
                "final_report": rep
            })
            await websocket.close(code=1000)
        except Exception:
            pass
        return

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
        
        # Also broadcast dedicated single-turn transcript event frame if last_message is provided
        if last_message and dashboards:
            turn_id_val = last_message.get("turn_id")
            id_val = last_message.get("id") or (f"{session_id}-turn-{turn_id_val}" if turn_id_val else None)
            speaker_val = last_message.get("speaker", "")
            transcript_frame = {
                "type": "transcript",
                "session_id": session_id,
                "id": id_val,
                "turn_id": turn_id_val,
                "speaker": speaker_val,
                "speaker_name": speaker_val,
                "text": last_message.get("text", ""),
                "timestamp": last_message.get("timestamp"),
                "source": last_message.get("source", "teams_native")
            }
            for dash_ws in dashboards.difference(dead_sockets):
                try:
                    await dash_ws.send_json(transcript_frame)
                except Exception:
                    pass

        if dead_sockets:
            sess["dashboard_websockets"].difference_update(dead_sockets)

    if sess.get("engine"):
        sess["engine"].on_update_callback = broadcast_update

    # Audio Producer Branch (Teams Bot / Raw Audio Stream for recording.wav capture)
    if is_audio_producer:
        sess["status"] = "Listening to audio stream..."
        sess["last_speech_time"] = time.time()

        # Inactivity timeout monitor (default 15 mins / 900 seconds)
        timeout_sec = int(os.getenv("INACTIVITY_TIMEOUT_SECONDS", "900"))
        
        async def monitor_inactivity():
            logger.info(f"[CopilotWS] Inactivity monitor active for session {session_id} (timeout={timeout_sec}s)")
            while True:
                await asyncio.sleep(15)
                last_time = sess.get("last_speech_time", time.time())
                idle_duration = time.time() - last_time
                if idle_duration > timeout_sec:
                    logger.warning(
                        f"[CopilotWS] Inactivity timeout: No speech/audio received for {int(idle_duration)}s "
                        f"(threshold={timeout_sec}s). Shutting down session {session_id} and terminating bot process..."
                    )
                    sess["is_active"] = False
                    sess["status"] = "Terminated due to audio inactivity."
                    
                    bot_process = sess.get("bot_process")
                    if bot_process and bot_process.poll() is None:
                        logger.info(f"[TeamsBot] Terminating bot process PID {bot_process.pid} due to inactivity.")
                        try:
                            bot_process.terminate()
                            try:
                                bot_process.wait(timeout=3.0)
                            except Exception:
                                bot_process.kill()
                        except Exception as pe:
                            logger.warning(f"[TeamsBot] Error terminating bot process: {pe}")
                    
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                    break

        inactivity_task = asyncio.create_task(monitor_inactivity())

        builder = CopilotPipelineBuilder()
        pipeline_res = builder.build_observer_pipeline(websocket, session_id)

        if pipeline_res is not None:
            pipeline, worker, audio_buffer = pipeline_res
            sess["worker"] = worker
            sess["audio_buffer"] = audio_buffer
            sess["audio_websocket"] = websocket
            runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
            sess["runner"] = runner
            logger.info(f"[CopilotWS] Pipecat audio recorder runner active for audio producer session {session_id}")
            try:
                if hasattr(runner, "add_workers"):
                    await runner.add_workers(worker)
                    await runner.run()
                else:
                    await runner.run(worker)
            except WebSocketDisconnect:
                logger.info(f"[CopilotWS] Audio producer disconnected: {session_id}")
            except Exception as err:
                logger.error(f"[CopilotWS] Audio pipeline error: {err}")
            finally:
                inactivity_task.cancel()
                if audio_buffer:
                    user_audio_snapshot = bytes(audio_buffer._user_audio_buffer) if hasattr(audio_buffer, "_user_audio_buffer") else b""
                    try:
                        logger.info(f"[CopilotWS] Stopping audio_buffer and saving recording for session: {session_id}")
                        await audio_buffer.stop_recording()
                        await asyncio.sleep(0.5)
                    except Exception as abe:
                        logger.warning(f"[CopilotWS] Error flushing audio buffer: {abe}")
                    # Direct disk write fallback if on_audio_data didn't write yet
                    rec_path = os.path.join("interviews", session_id, "recording.wav")
                    if not os.path.exists(rec_path):
                        try:
                            import wave
                            directory = os.path.join("interviews", session_id)
                            os.makedirs(directory, exist_ok=True)
                            frames_to_write = user_audio_snapshot if len(user_audio_snapshot) > 0 else (b"\x00" * 32000)
                            with wave.open(rec_path, "wb") as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(16000)
                                wf.writeframes(frames_to_write)
                            logger.info(f"[CopilotWS] Successfully wrote fallback recording directly: {rec_path} ({len(frames_to_write)} bytes)")
                        except Exception as fe:
                            logger.warning(f"[CopilotWS] Fallback recording write failed: {fe}")
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
            finally:
                inactivity_task.cancel()

    # Native Captions Branch (Teams Bot Live Captions Producer — Production Transcript Source)
    elif is_native_captions:
        sess.setdefault("native_captions", [])
        sess["native_caption_websockets"] = sess.setdefault("native_caption_websockets", set())
        sess["native_caption_websockets"].add(websocket)
        logger.info(f"[CopilotWS] Native captions client connected (session={session_id})")

        # Native Captions Production Pipeline Components
        turn_aggregator: NativeTurnAggregator = sess.setdefault(
            "turn_aggregator",
            NativeTurnAggregator(session_id=session_id)
        )
        logical_aggregator: NativeLogicalTurnAggregator = sess.setdefault(
            "logical_aggregator",
            NativeLogicalTurnAggregator(session_id=session_id, inactivity_threshold_ms=3000.0)
        )
        dispatched_seq_ids: Set[int] = sess.setdefault("dispatched_seq_ids", set())

        session_dir = os.path.join("interviews", session_id)
        os.makedirs(session_dir, exist_ok=True)
        captions_file_path = os.path.join(session_dir, "native_captions.jsonl")

        poc_dir = os.path.join("interviews", "phase2i_poc")
        os.makedirs(poc_dir, exist_ok=True)
        poc_captions_file_path = os.path.join(poc_dir, "native_captions.jsonl")

        async def dispatch_finalized_sequences(newly_fin: Dict[str, list]):
            eng = sess.get("engine")
            if not eng:
                return
            for strat in ("strategy_c_quiescence_800ms", "strategy_b_dom_removal"):
                for fseq in newly_fin.get(strat, []):
                    if fseq.sequence_id not in dispatched_seq_ids:
                        dispatched_seq_ids.add(fseq.sequence_id)
                        completed_turns = logical_aggregator.process_finalized_sequence(fseq)
                        for turn in completed_turns:
                            logger.info(f"[CopilotWS] Emitting completed turn: speaker='{turn.speaker_name}', text='{turn.text}'")
                            last_msg = await eng.add_message(
                                speaker=turn.speaker_name,
                                text=turn.text,
                                source="teams_native",
                                allow_merge=False,
                                turn_id=turn.logical_turn_id,
                                is_final=True
                            )
                            sess["transcript"] = eng.get_transcript()
                            sess["last_speech_time"] = time.time()
                            await broadcast_update(last_msg)

                        # Emit progressive update for active logical turn immediately (<1s display latency)
                        if logical_aggregator.active_turn:
                            active = logical_aggregator.active_turn
                            logger.info(f"[CopilotWS] Emitting progressive turn: speaker='{active.speaker_name}', text='{active.text}'")
                            last_msg = await eng.add_message(
                                speaker=active.speaker_name,
                                text=active.text,
                                source="teams_native",
                                allow_merge=False,
                                turn_id=active.logical_turn_id,
                                is_final=False
                            )
                            sess["transcript"] = eng.get_transcript()
                            sess["last_speech_time"] = time.time()
                            await broadcast_update(last_msg)

        async def monitor_native_turn_inactivity():
            while True:
                await asyncio.sleep(0.25)
                if sess.get("service_off") or sess.get("is_active") is False or bool(sess.get("final_report")):
                    break
                # 1. Quiescence check on turn_aggregator (evaluates 800ms threshold)
                q_fin = turn_aggregator.check_quiescence()
                await dispatch_finalized_sequences(q_fin)

                # 2. Inactivity timeout check on logical_aggregator (finalizes turn after 3s conversational pause)
                timeout_turns = logical_aggregator.check_inactivity()
                eng = sess.get("engine")
                if eng:
                    for turn in timeout_turns:
                        logger.info(f"[CopilotWS] Emitting production turn (inactivity timeout): speaker='{turn.speaker_name}', text='{turn.text}'")
                        last_msg = await eng.add_message(
                            speaker=turn.speaker_name,
                            text=turn.text,
                            source="teams_native",
                            allow_merge=False,
                            turn_id=turn.logical_turn_id,
                            is_final=True
                        )
                        sess["transcript"] = eng.get_transcript()
                        sess["last_speech_time"] = time.time()
                        await broadcast_update(last_msg)

        native_monitor_task = asyncio.create_task(monitor_native_turn_inactivity())

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(code=msg.get("code", 1000))

                # Guard against inactive or service off sessions during live transport
                is_off = sess.get("service_off") or os.path.exists(os.path.join("interviews", session_id, "service_off.flag"))
                if is_off or sess.get("is_active") is False or bool(sess.get("final_report")):
                    logger.info(f"[CopilotWS] Session {session_id} is inactive or Service Off. Closing native captions socket.")
                    try:
                        await websocket.close(code=1000)
                    except Exception:
                        pass
                    break

                if "text" in msg:
                    try:
                        payload = json.loads(msg["text"])
                        if not isinstance(payload, dict):
                            continue

                        event_type = payload.get("event_type")
                        if event_type != "native_caption":
                            logger.warning(f"[CopilotWS] Ignored unexpected event_type '{event_type}' on native captions channel.")
                            continue

                        payload_session_id = payload.get("session_id")
                        if payload_session_id and payload_session_id != session_id:
                            logger.warning(f"[CopilotWS] Mismatched session_id in payload: {payload_session_id} != {session_id}")
                            continue

                        text = (payload.get("text") or "").strip()
                        if not text:
                            continue

                        raw_speaker = payload.get("speaker_name")
                        if not raw_speaker or not str(raw_speaker).strip():
                            raw_speaker = "Unknown"
                        else:
                            raw_speaker = str(raw_speaker).strip()

                        detected_at_str = payload.get("detected_at")
                        received_at_dt = datetime.datetime.now(datetime.timezone.utc)
                        received_at_str = received_at_dt.isoformat()

                        latency_ms = None
                        if detected_at_str:
                            try:
                                detected_dt = datetime.datetime.fromisoformat(detected_at_str.replace("Z", "+00:00"))
                                latency_ms = max(0.0, (received_at_dt - detected_dt).total_seconds() * 1000.0)
                            except Exception:
                                pass

                        seq_num = payload.get("caption_sequence")
                        dom_action = payload.get("dom_action", "updated")

                        event_record = {
                            "event_type": "native_caption",
                            "session_id": session_id,
                            "event_id": payload.get("event_id") or str(uuid.uuid4()),
                            "speaker_name": raw_speaker,
                            "text": text,
                            "detected_at": detected_at_str or received_at_str,
                            "received_at": received_at_str,
                            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
                            "source": "teams_native",
                            "is_final": payload.get("is_final"),
                            "finality": payload.get("finality", "unknown"),
                            "caption_sequence": seq_num,
                            "dom_action": dom_action,
                        }

                        # 1. In-memory runtime persistence
                        sess["native_captions"].append(event_record)
                        sess["last_speech_time"] = time.time()

                        # 2. Append to interview session artifact
                        with open(captions_file_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(event_record) + "\n")

                        # 3. Append to phase2i_poc shared artifact
                        with open(poc_captions_file_path, "a", encoding="utf-8") as pf:
                            pf.write(json.dumps(event_record) + "\n")

                        # 4. Structured log output
                        lat_disp = f"{latency_ms:.1f}ms" if latency_ms is not None else "N/A"
                        logger.info(
                            f"[TEAMS_NATIVE] speaker='{raw_speaker}' text='{text}' "
                            f"seq={seq_num} detected_at='{detected_at_str}' received_at='{received_at_str}' latency={lat_disp}"
                        )

                        # 5. Broadcast to observer/benchmark subscribers on native_caption_websockets
                        for sub_ws in list(sess.get("native_caption_websockets", set())):
                            if sub_ws != websocket:
                                try:
                                    await sub_ws.send_text(json.dumps(event_record))
                                except Exception:
                                    pass

                        # 6. Feed raw caption event into NativeTurnAggregator (Production Path)
                        newly_finalized = turn_aggregator.process_raw_event(event_record)
                        await dispatch_finalized_sequences(newly_finalized)

                    except json.JSONDecodeError as jde:
                        logger.warning(f"[CopilotWS] JSON parse error on native captions payload: {jde}")
                    except Exception as ex:
                        logger.error(f"[CopilotWS] Error processing native caption event: {ex}")

        except WebSocketDisconnect:
            logger.info(f"[CopilotWS] Native captions client disconnected: {session_id}")
        finally:
            native_monitor_task.cancel()
            # Flush pending sequences and turns to production transcript
            flushed_seqs = turn_aggregator.finalize_all_pending()
            await dispatch_finalized_sequences(flushed_seqs)

            final_flushed_turns = logical_aggregator.flush()
            eng = sess.get("engine")
            if eng:
                for turn in final_flushed_turns:
                    logger.info(f"[CopilotWS] Emitting flushed production turn: speaker='{turn.speaker_name}', text='{turn.text}'")
                    last_msg = await eng.add_message(
                        speaker=turn.speaker_name,
                        text=turn.text,
                        source="teams_native",
                        allow_merge=False,
                        turn_id=turn.logical_turn_id,
                        is_final=True
                    )
                    sess["transcript"] = eng.get_transcript()
                    await broadcast_update(last_msg)

            if session_id in active_sessions:
                active_sessions[session_id].get("native_caption_websockets", set()).discard(websocket)

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
