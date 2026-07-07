from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TTSStoppedFrame,
    LLMFullResponseEndFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

class MicGateProcessor(FrameProcessor):
    """Filters out microphone frames at the start of the interview to keep it muted during greetings."""
    def __init__(self, shared_state: dict):
        super().__init__()
        self.shared_state = shared_state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            # Only allow raw microphone bytes to pass through if mic_enabled is True
            if self.shared_state.get("mic_enabled", False):
                await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

class MicUnmuterProcessor(FrameProcessor):
    """Detects when the AI finishes its initial greeting and enables the microphone."""
    def __init__(self, shared_state: dict):
        super().__init__()
        self.shared_state = shared_state
        self._first_turn_completed = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if not self._first_turn_completed:
            # TTSStoppedFrame indicates the synthesized audio stream has ended
            # LLMFullResponseEndFrame is checked as a safety backup
            if isinstance(frame, (TTSStoppedFrame, LLMFullResponseEndFrame)):
                logger.info("MicUnmuterProcessor: AI finished initial greeting. Enabling candidate microphone.")
                self.shared_state["mic_enabled"] = True
                self._first_turn_completed = True
