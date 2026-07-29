from typing import List
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    OutputAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    LLMFullResponseEndFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

class PlaybackBufferProcessor(FrameProcessor):
    """Buffers a configurable number of synthesized audio frames before playing them to prevent jitter stutters."""
    def __init__(self, buffer_size: int = 5):
        super().__init__()
        self.buffer_size = buffer_size
        self._buffer: List[OutputAudioRawFrame] = []
        self._buffering: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSStartedFrame):
            # When the TTS starts generating an utterance, activate buffering
            logger.debug(f"PlaybackBufferProcessor: TTS started. Activating buffer (size={self.buffer_size}).")
            self._buffering = True
            self._buffer.clear()
            await self.push_frame(frame, direction)

        elif isinstance(frame, OutputAudioRawFrame):
            if self._buffering:
                self._buffer.append(frame)
                # If we've collected enough frames, release the buffer and play
                if len(self._buffer) >= self.buffer_size:
                    logger.debug(f"PlaybackBufferProcessor: Buffer filled ({len(self._buffer)} frames). Releasing to playback.")
                    for buffered_frame in self._buffer:
                        await self.push_frame(buffered_frame, direction)
                    self._buffer.clear()
                    self._buffering = False
            else:
                # Buffer has already been released, play frames immediately
                await self.push_frame(frame, direction)

        elif isinstance(frame, (TTSStoppedFrame, LLMFullResponseEndFrame)):
            # If the utterance ends and there are still unplayed frames in the buffer, flush them
            if self._buffer:
                logger.debug(f"PlaybackBufferProcessor: Session stopped. Flushing remaining {len(self._buffer)} frames.")
                for buffered_frame in self._buffer:
                    await self.push_frame(buffered_frame, direction)
                self._buffer.clear()
            self._buffering = False
            await self.push_frame(frame, direction)

        else:
            # Pass all other control/system frames through unchanged
            await self.push_frame(frame, direction)
