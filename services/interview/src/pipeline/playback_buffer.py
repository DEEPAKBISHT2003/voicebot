import time
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
        self._buffer_start_time: float = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSStartedFrame):
            # When the TTS starts generating an utterance, activate buffering
            self._buffering = True
            self._buffer_start_time = time.monotonic()
            self._buffer.clear()
            logger.info(f"[MIA BACKEND BUFFER] TTS started. Activating buffer (size={self.buffer_size})")
            await self.push_frame(frame, direction)

        elif isinstance(frame, OutputAudioRawFrame):
            if self._buffering:
                self._buffer.append(frame)
                # If we've collected enough frames, release the buffer and play
                if len(self._buffer) >= self.buffer_size:
                    buf_elapsed_ms = (time.monotonic() - self._buffer_start_time) * 1000.0
                    logger.info(f"[MIA BACKEND BUFFER RELEASE] frames={len(self._buffer)} buffer_wait_ms={buf_elapsed_ms:.1f}ms")
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
                buf_elapsed_ms = (time.monotonic() - self._buffer_start_time) * 1000.0
                logger.info(f"[MIA BACKEND BUFFER FLUSH] flushing={len(self._buffer)} frames buffer_wait_ms={buf_elapsed_ms:.1f}ms")
                for buffered_frame in self._buffer:
                    await self.push_frame(buffered_frame, direction)
                self._buffer.clear()
            self._buffering = False
            await self.push_frame(frame, direction)

        else:
            # Pass all other control/system frames through unchanged
            await self.push_frame(frame, direction)
