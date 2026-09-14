import asyncio
from typing import Callable, List, Dict, Optional, Any
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    InterimTranscriptionFrame,
    LLMTextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

def extract_speaker_from_frame(frame: Frame) -> Optional[str]:
    """Extracts speaker identity from TranscriptionFrame/InterimTranscriptionFrame or Deepgram word-level diarization response."""
    result = getattr(frame, "result", None)
    if result:
        try:
            alternatives = []
            # 1. Pydantic ListenV1Results or object with channel attribute
            channel = getattr(result, "channel", None)
            if channel is not None:
                alternatives = getattr(channel, "alternatives", []) or []
            # 2. Pydantic model_dump fallback
            elif hasattr(result, "model_dump"):
                alternatives = result.model_dump().get("channel", {}).get("alternatives", []) or []
            # 3. Dict fallback
            elif isinstance(result, dict):
                alternatives = result.get("channel", {}).get("alternatives", []) or []

            if alternatives:
                alt = alternatives[0]
                words = getattr(alt, "words", None) if not isinstance(alt, dict) else alt.get("words", [])
                if words:
                    counts: Dict[Any, int] = {}
                    for w in words:
                        spk = getattr(w, "speaker", None) if not isinstance(w, dict) else w.get("speaker")
                        if spk is not None:
                            counts[spk] = counts.get(spk, 0) + 1
                    if counts:
                        dominant_spk = max(counts, key=counts.get)
                        return str(dominant_spk)
        except Exception as err:
            logger.debug(f"[CopilotAccumulator] Error parsing word-level speaker: {err}")

    speaker_val = getattr(frame, "speaker", None)
    if speaker_val is not None and str(speaker_val).strip():
        return str(speaker_val).strip()

    user_id_val = getattr(frame, "user_id", None)
    if user_id_val and str(user_id_val).strip():
        return str(user_id_val).strip()

    return None


class TranscriptAccumulator(FrameProcessor):
    """Intercepts transcription frames downstream to construct complete session logs and fire callbacks."""
    def __init__(
        self,
        callback: Optional[Callable[[dict], None]] = None,
        interim_callback: Optional[Callable[[dict], None]] = None
    ):
        super().__init__()
        self.callback = callback
        self.interim_callback = interim_callback
        self.history: List[Dict[str, str]] = []
        self._current_assistant_text: List[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # User speech - Interim transcript frame (ephemeral preview, no DB, no LLM)
        if isinstance(frame, InterimTranscriptionFrame):
            text = frame.text.strip()
            if text:
                speaker_val = extract_speaker_from_frame(frame)
                entry = {"role": "user", "text": text, "is_final": False}
                if speaker_val:
                    entry["speaker"] = str(speaker_val)
                cb = self.interim_callback or self.callback
                if cb:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(entry)
                        else:
                            cb(entry)
                    except Exception as cb_err:
                        logger.error(f"[CopilotAccumulator] Interim callback error: {cb_err}")

        # User speech - Final transcript frame (persisted to history, triggers downstream)
        elif isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                speaker_val = extract_speaker_from_frame(frame)
                entry = {"role": "user", "text": text, "is_final": True}
                if speaker_val:
                    entry["speaker"] = str(speaker_val)
                self.history.append(entry)
                logger.info(f"[CopilotAccumulator] Final Transcript ({entry.get('speaker', 'user')}): {text}")
                if self.callback:
                    try:
                        if asyncio.iscoroutinefunction(self.callback):
                            await self.callback(entry)
                        else:
                            self.callback(entry)
                    except Exception as cb_err:
                        logger.error(f"[CopilotAccumulator] Callback error: {cb_err}")
                    
        # Assistant speech tracking frame (if any)
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._current_assistant_text = []
        elif isinstance(frame, LLMTextFrame):
            self._current_assistant_text.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._current_assistant_text).strip()
            if text:
                clean_text = text.replace("**", "").replace("*", "")
                entry = {"role": "assistant", "text": clean_text}
                self.history.append(entry)
                logger.info(f"[CopilotAccumulator] Assistant response: {clean_text}")
                if self.callback:
                    try:
                        if asyncio.iscoroutinefunction(self.callback):
                            await self.callback(entry)
                        else:
                            self.callback(entry)
                    except Exception as cb_err:
                        logger.error(f"[CopilotAccumulator] Callback error: {cb_err}")
            self._current_assistant_text = []
            
        await self.push_frame(frame, direction)
