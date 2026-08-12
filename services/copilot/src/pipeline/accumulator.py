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

def extract_speaker_from_frame(frame: Any) -> Optional[str]:
    """Extracts speaker identity from TranscriptionFrame or Deepgram word-level diarization response."""
    result = getattr(frame, "result", None)
    if result:
        try:
            if isinstance(result, dict):
                alternatives = result.get("channel", {}).get("alternatives", [])
            else:
                alternatives = getattr(getattr(result, "channel", None), "alternatives", [])
            
            if alternatives:
                alt = alternatives[0]
                words = alt.get("words", []) if isinstance(alt, dict) else getattr(alt, "words", [])
                if words:
                    counts: Dict[Any, int] = {}
                    for w in words:
                        spk = w.get("speaker") if isinstance(w, dict) else getattr(w, "speaker", None)
                        if spk is not None:
                            counts[spk] = counts.get(spk, 0) + 1
                    if counts:
                        dominant_spk = max(counts, key=counts.get)
                        return str(dominant_spk)
        except Exception as err:
            logger.debug(f"[CopilotAccumulator] Error parsing word-level speaker: {err}")
            
    speaker_val = getattr(frame, "speaker", None)
    if speaker_val is not None:
        return str(speaker_val)
        
    user_id_val = getattr(frame, "user_id", None)
    if user_id_val:
        return str(user_id_val)
        
    return None


class TranscriptAccumulator(FrameProcessor):
    """Intercepts transcription frames downstream to construct complete session logs and fire callbacks."""
    def __init__(self, callback: Optional[Callable[[dict], None]] = None):
        super().__init__()
        self.callback = callback
        self.history: List[Dict[str, str]] = []
        self._current_assistant_text: List[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # User speech transcript frame (only final TranscriptionFrame to prevent duplicate interim entries)
        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip() if hasattr(frame, "text") and frame.text else ""
            frame_type = type(frame).__name__
            logger.info(f"[TRANSCRIPT_DEBUG] [Accumulator] Intercepted {frame_type}: text='{text}'")
            if text:
                speaker_val = extract_speaker_from_frame(frame)
                entry = {"role": "user", "text": text}
                if speaker_val is not None:
                    entry["speaker"] = str(speaker_val)
                self.history.append(entry)
                logger.info(f"[CopilotAccumulator] Transcript ({entry.get('speaker', 'user')}): {text}")
                if self.callback:
                    try:
                        logger.info(f"[TRANSCRIPT_DEBUG] [Accumulator] Invoking callback for: '{text}'")
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
