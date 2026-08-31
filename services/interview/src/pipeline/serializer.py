from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame

class RawPCMAudioSerializer(FrameSerializer):
    """Serializer that maps Pipecat audio frames directly to raw PCM audio bytes.
    
    Used to stream binary audio over raw WebSocket connections.
    """
    def __init__(self, sample_rate: int = 16000, num_channels: int = 1):
        super().__init__()
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.total_frames = 0
        self.total_bytes = 0

    async def serialize(self, frame: Frame) -> str | bytes | None:
        # Convert outgoing synthesized speech frame to raw bytes for web client playback
        if isinstance(frame, OutputAudioRawFrame):
            self.total_frames += 1
            self.total_bytes += len(frame.audio)
            from loguru import logger
            logger.info(f"[WS OUT DEBUG] session_id active binary frame size={len(frame.audio)}, total_frames={self.total_frames}, total_bytes={self.total_bytes}")
            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        # Convert incoming browser microphone PCM bytes into InputAudioRawFrame for STT
        if isinstance(data, bytes):
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self.sample_rate,
                num_channels=self.num_channels
            )
        return None
