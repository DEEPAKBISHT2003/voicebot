"""
Simulation API for Copilot Service.
Handles WAV file upload and audio simulation with Deepgram STT transcription.
"""

import os
import asyncio
import wave
import io
import struct
from typing import Dict, Any

from fastapi import APIRouter, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException, Depends
from loguru import logger

from services.copilot.src.core.config import Settings
from services.copilot.src.api.deps import get_copilot_sessions_ws, get_copilot_repo_ws
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.engine.session import CopilotSessionEngine

router = APIRouter()


def normalize_wav_to_16k_mono(file_bytes: bytes) -> bytes:
    """Normalize any WAV file to 16000Hz 16-bit Mono PCM."""
    try:
        in_io = io.BytesIO(file_bytes)
        with wave.open(in_io, "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            raw_data = wf.readframes(nframes)

        logger.info(f"Normalizing WAV: {framerate}Hz, {nchannels}-ch, {sampwidth*8}-bit -> 16000Hz Mono 16-bit PCM...")

        if nchannels == 1 and sampwidth == 2 and framerate == 16000:
            logger.info("WAV is already 16kHz Mono 16-bit PCM.")
            return file_bytes

        total_samples = nframes * nchannels
        if sampwidth == 2:
            samples = list(struct.unpack(f"<{total_samples}h", raw_data))
        elif sampwidth == 1:
            raw_samples = struct.unpack(f"<{total_samples}B", raw_data)
            samples = [(s - 128) * 256 for s in raw_samples]
        elif sampwidth == 4:
            try:
                raw_samples = struct.unpack(f"<{total_samples}i", raw_data)
                samples = [int(s / 65536) for s in raw_samples]
            except Exception:
                raw_samples = struct.unpack(f"<{total_samples}f", raw_data)
                samples = [int(s * 32767) for s in raw_samples]
        else:
            return file_bytes

        if nchannels > 1:
            mono = []
            for i in range(0, len(samples), nchannels):
                chunk = samples[i:i + nchannels]
                mono.append(sum(chunk) // nchannels)
            samples = mono

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
                        val = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
                    else:
                        val = samples[idx] if idx < len(samples) else 0
                    resampled.append(val)
                samples = resampled

        clamped = [max(-32768, min(32767, s)) for s in samples]
        pcm_bytes = struct.pack(f"<{len(clamped)}h", *clamped)

        out_io = io.BytesIO()
        with wave.open(out_io, "wb") as out_wf:
            out_wf.setnchannels(1)
            out_wf.setsampwidth(2)
            out_wf.setframerate(16000)
            out_wf.writeframes(pcm_bytes)

        logger.info(f"Normalized to 16kHz Mono PCM ({len(clamped)/16000:.1f}s).")
        return out_io.getvalue()
    except Exception as e:
        logger.error(f"Failed to normalize WAV: {e}")
        return file_bytes


@router.post("/copilot/{session_id}/upload-audio")
async def upload_simulation_audio(session_id: str, file: UploadFile = File(...)):
    """Upload a WAV file for copilot simulation testing."""
    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    os.makedirs(directory, exist_ok=True)

    file_path = os.path.join(directory, "uploaded_audio.wav")

    try:
        content = await file.read()
        normalized = normalize_wav_to_16k_mono(content)
        with open(file_path, "wb") as f:
            f.write(normalized)
        logger.info(f"Uploaded simulation audio to {file_path}")
        return {"status": "success", "file_path": file_path}
    except Exception as e:
        logger.error(f"Failed to save simulation audio: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save audio: {e}")


@router.websocket("/ws/copilot/{session_id}/simulate")
async def simulation_websocket(
    websocket: WebSocket,
    session_id: str,
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions_ws),
    repo: CopilotRepository = Depends(get_copilot_repo_ws),
):
    """
    Simulation WebSocket:
    1. Streams raw PCM audio bytes to browser (for playback)
    2. Uses Deepgram STT to transcribe the audio
    3. Feeds transcript segments to CopilotSessionEngine
    4. Pushes copilot_update events back to the copilot WebSocket client
    """
    await websocket.accept()
    logger.info(f"Simulation WebSocket connected: {session_id}")

    directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
    file_path = os.path.join(directory, "uploaded_audio.wav")

    if not os.path.exists(file_path):
        await websocket.send_json({
            "type": "error",
            "message": f"No uploaded audio found for session {session_id}."
        })
        await websocket.close()
        return

    # Load or create copilot engine for this session
    if session_id not in active_sessions:
        try:
            db_session = await repo.load_session(session_id)
            engine = CopilotSessionEngine(
                session_id, repo,
                db_session.get("transcript", []),
                jd=db_session.get("jd", ""),
                resume=db_session.get("resume", ""),
                custom_prompt=db_session.get("custom_prompt", "")
            )
            active_sessions[session_id] = {
                "engine": engine,
                "status": "Simulating...",
                "transcript": engine.get_transcript(),
                "is_active": True,
                "websocket": None
            }
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

    sess = active_sessions[session_id]
    engine: CopilotSessionEngine = sess["engine"]

    # Transcribe the full WAV file using Deepgram REST API
    transcript_segments = []
    try:
        import httpx
        deepgram_key = Settings.DEEPGRAM_API_KEY
        if deepgram_key:
            logger.info(f"Transcribing audio for session {session_id} via Deepgram...")
            with open(file_path, "rb") as audio_file:
                audio_bytes = audio_file.read()

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen?model=nova-2&punctuate=true&diarize=true&utterances=true",
                    headers={
                        "Authorization": f"Token {deepgram_key}",
                        "Content-Type": "audio/wav"
                    },
                    content=audio_bytes
                )

            if response.status_code == 200:
                result = response.json()
                utterances = result.get("results", {}).get("utterances", [])
                for utt in utterances:
                    speaker_num = utt.get("speaker", 0)
                    # Map speaker numbers: 0 = Candidate, 1 = Interviewer
                    speaker = "Candidate" if speaker_num == 0 else "Interviewer"
                    transcript_segments.append({
                        "speaker": speaker,
                        "text": utt.get("transcript", ""),
                        "start": utt.get("start", 0),
                        "end": utt.get("end", 0),
                    })
                logger.info(f"Transcribed {len(transcript_segments)} utterances for session {session_id}")
            else:
                logger.warning(f"Deepgram returned {response.status_code}: {response.text}")
        else:
            logger.warning("No DEEPGRAM_API_KEY set — skipping transcription")
    except Exception as e:
        logger.error(f"Transcription failed: {e}")

    try:
        await asyncio.sleep(1.0)

        # Stream audio to browser AND feed transcript to engine simultaneously
        with wave.open(file_path, "rb") as wf:
            total_frames = wf.getnframes()
            framerate = wf.getframerate()
            total_duration = total_frames / framerate
            chunk_size = 1600  # 100ms at 16kHz
            elapsed = 0.0
            seg_index = 0

            while True:
                data = wf.readframes(chunk_size)
                if not data:
                    break

                # Send audio bytes to browser for playback
                try:
                    await websocket.send_bytes(data)
                except Exception:
                    logger.info("Simulation client disconnected during stream.")
                    return

                elapsed += 0.1

                # Feed transcript segments to copilot engine at the right time
                copilot_ws = active_sessions.get(session_id, {}).get("websocket")
                while seg_index < len(transcript_segments):
                    seg = transcript_segments[seg_index]
                    if seg["start"] <= elapsed:
                        logger.info(f"[Sim] [{seg['speaker']}]: {seg['text']}")
                        await engine.add_message(seg["speaker"], seg["text"], websocket=copilot_ws)
                        seg_index += 1
                    else:
                        break

                await asyncio.sleep(0.1)

        # Feed any remaining segments
        copilot_ws = active_sessions.get(session_id, {}).get("websocket")
        while seg_index < len(transcript_segments):
            seg = transcript_segments[seg_index]
            await engine.add_message(seg["speaker"], seg["text"], websocket=copilot_ws)
            seg_index += 1

        logger.info(f"Simulation complete for session {session_id}")
        sess["is_active"] = False
        sess["status"] = "Simulation complete."

        await websocket.send_json({
            "type": "simulation_complete",
            "session_id": session_id
        })

        # Notify copilot WebSocket client too
        if copilot_ws:
            try:
                await copilot_ws.send_json({
                    "type": "simulation_complete",
                    "session_id": session_id
                })
            except Exception:
                pass

    except WebSocketDisconnect:
        logger.info(f"Simulation WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Simulation error: {e}")
    finally:
        logger.info(f"Simulation WebSocket closed: {session_id}")
