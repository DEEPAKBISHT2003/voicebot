import os
import wave
from typing import Any, Callable, Optional, Tuple
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
try:
    from pipecat.pipeline.task import PipelineParams, PipelineTask as PipelineWorker
except ImportError:
    try:
        from pipecat.pipeline.runner import PipelineParams, PipelineRunner as PipelineWorker
    except ImportError:
        from pipecat.pipeline.pipeline import PipelineParams, PipelineWorker
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from services.copilot.src.core.config import Settings
from services.copilot.src.pipeline.serializer import RawPCMAudioSerializer


class CopilotPipelineBuilder:
    """Builds and manages Pipecat Audio Recording Pipelines for Copilot sessions (Deepgram STT removed)."""
    def __init__(self):
        pass

    def build_observer_pipeline(
        self,
        websocket: Any,
        session_id: str
    ) -> Optional[Tuple[Pipeline, PipelineWorker, AudioBufferProcessor]]:
        """Constructs an audio recording pipeline using FastAPIWebsocketTransport and AudioBufferProcessor."""
        try:
            transport = FastAPIWebsocketTransport(
                websocket=websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_sample_rate=16000,
                    audio_out_enabled=False,
                    add_wav_header=False,
                    serializer=RawPCMAudioSerializer(sample_rate=16000),
                )
            )

            # Audio buffer processor for recording audio (16kHz mono PCM)
            audio_buffer = AudioBufferProcessor(
                sample_rate=16000,
                num_channels=1,
                auto_start_recording=True
            )

            @audio_buffer.event_handler("on_audio_data")
            async def on_audio_data(processor, audio, sample_rate, num_channels):
                if not session_id:
                    return
                directory = os.path.join(Settings.DEFAULT_STORAGE_DIR, session_id)
                os.makedirs(directory, exist_ok=True)
                recording_path = os.path.join(directory, "recording.wav")
                try:
                    with wave.open(recording_path, "wb") as wf:
                        wf.setnchannels(num_channels)
                        wf.setsampwidth(2)  # 16-bit
                        wf.setframerate(sample_rate)
                        wf.writeframes(audio)
                    logger.info(f"[CopilotPipeline] Successfully saved audio recording to {recording_path} ({len(audio)} bytes)")
                except Exception as e:
                    logger.error(f"[CopilotPipeline] Failed to save audio recording: {e}")

            # Audio buffer captures all incoming PCM frames for recording.wav persistence
            pipeline = Pipeline([
                transport.input(),
                audio_buffer
            ])

            worker = PipelineWorker(
                pipeline,
                enable_rtvi=False,
                enable_turn_tracking=False,
                setup_timeout_secs=60.0,
                params=PipelineParams(
                    enable_metrics=False,
                    enable_usage_metrics=False,
                    audio_in_sample_rate=16000,
                    audio_out_sample_rate=16000,
                )
            )

            logger.info(f"[CopilotPipeline] Successfully built audio recording pipeline for session: {session_id}")
            return pipeline, worker, audio_buffer

        except Exception as err:
            logger.error(f"[CopilotPipeline] Failed to build observer pipeline: {err}")
            return None

