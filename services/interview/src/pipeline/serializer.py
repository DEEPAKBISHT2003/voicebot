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

    async def serialize(self, frame: Frame) -> str | bytes | None:
        # Convert outgoing synthesized speech frame to raw bytes for web client playback
        if isinstance(frame, OutputAudioRawFrame):
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
