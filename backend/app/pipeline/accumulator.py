import asyncio
from typing import Callable, List, Dict, Optional
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    LLMTextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

class TranscriptAccumulator(FrameProcessor):
    """Intercepts transcription frames downstream to construct complete session logs."""
    def __init__(self, callback: Optional[Callable[[dict], None]] = None):
        super().__init__()
        self.callback = callback
        self.history: List[Dict[str, str]] = []
        self._current_assistant_text: List[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # User speech transcript
        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                speaker_val = getattr(frame, "speaker", getattr(frame, "user_id", None))
                entry = {"role": "user", "text": text}
                if speaker_val is not None:
                    entry["speaker"] = str(speaker_val)
                self.history.append(entry)
                logger.info(f"User transcript ({entry.get('speaker', 'user')}): {text}")
                if self.callback:
                    if asyncio.iscoroutinefunction(self.callback):
                        await self.callback(entry)
                    else:
                        self.callback(entry)
                    
        # Assistant speech tracking
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._current_assistant_text = []
        elif isinstance(frame, LLMTextFrame):
            self._current_assistant_text.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._current_assistant_text).strip()
            if text:
                # Strip markdown asterisks to make text friendly to logs/TTS
                clean_text = text.replace("**", "").replace("*", "")
                entry = {"role": "assistant", "text": clean_text}
                self.history.append(entry)
                logger.info(f"Assistant response: {clean_text}")
                if self.callback:
                    if asyncio.iscoroutinefunction(self.callback):
                        await self.callback(entry)
                    else:
                        self.callback(entry)
            self._current_assistant_text = []
            
        await self.push_frame(frame, direction)
