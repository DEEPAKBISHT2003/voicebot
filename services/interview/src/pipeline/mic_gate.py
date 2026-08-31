import asyncio
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    LLMFullResponseStartFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

class MicGateProcessor(FrameProcessor):
    """Filters out microphone frames while Mia is speaking (greeting or question turns)."""
    def __init__(self, shared_state: dict):
        super().__init__()
        self.shared_state = shared_state
        self._allowed_count = 0
        self._dropped_count = 0
        self._gate_locked_time = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            # Auto-unmute safety timeout: if mic stays locked for > 5 seconds, unlock automatically
            if not self.shared_state.get("mic_enabled", False):
                if self._gate_locked_time is None:
                    self._gate_locked_time = asyncio.get_event_loop().time()
                elif asyncio.get_event_loop().time() - self._gate_locked_time > 5.0:
                    logger.info("[MIA MIC GATE] Safety timeout (5s) reached. Auto-unmuting candidate microphone.")
                    self.shared_state["mic_enabled"] = True
                    self._gate_locked_time = None
            else:
                self._gate_locked_time = None

            # Only allow raw candidate audio bytes to pass through if mic_enabled is True
            if self.shared_state.get("mic_enabled", False):
                self._allowed_count += 1
                if self._allowed_count <= 5 or self._allowed_count % 50 == 0:
                    logger.info(f"[MIA MIC GATE] PASS candidate audio frame #{self._allowed_count}, len={len(frame.audio)}")
                await self.push_frame(frame, direction)
            else:
                self._dropped_count += 1
                if self._dropped_count <= 5 or self._dropped_count % 50 == 0:
                    logger.info(f"[MIA MIC GATE] BLOCK candidate audio frame #{self._dropped_count}, len={len(frame.audio)}")
        else:
            await self.push_frame(frame, direction)

class MicUnmuterProcessor(FrameProcessor):
    """Dynamically manages candidate audio gating across every turn:
    - Mutes candidate audio when Mia begins speaking (LLM/TTS starts)
    - Unmutes candidate audio strictly after TTSStoppedFrame completes + 300ms tail drain
    - Uses turn token (_turn_id) to eliminate rapid turn race conditions
    """
    def __init__(self, shared_state: dict):
        super().__init__()
        self.shared_state = shared_state
        self._turn_id = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        # Detect when Mia starts speaking (LLM starts generating or TTS starts outputting audio)
        if isinstance(frame, (TTSStartedFrame, LLMFullResponseStartFrame)):
            self._turn_id += 1
            if self.shared_state.get("mic_enabled", True):
                logger.info(f"[MicGate] Mia started speaking (turn #{self._turn_id}). Muting candidate audio input.")
                self.shared_state["mic_enabled"] = False

        # Detect strictly when TTS audio emission completes for the turn
        elif isinstance(frame, TTSStoppedFrame):
            current_turn = self._turn_id
            # 300ms buffer drain delay to absorb WebAudio/WebRTC tail playback echo
            await asyncio.sleep(0.3)
            # Only unmute if no new turn has started during the 300ms drain delay
            if self._turn_id == current_turn and not self.shared_state.get("mic_enabled", False):
                logger.info(f"[MicGate] Mia finished speaking (turn #{current_turn}). Unmuting candidate microphone for answer.")
                self.shared_state["mic_enabled"] = True
