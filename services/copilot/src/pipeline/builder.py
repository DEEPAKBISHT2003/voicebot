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
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from services.copilot.src.core.config import Settings
from services.copilot.src.pipeline.serializer import RawPCMAudioSerializer
from services.copilot.src.pipeline.accumulator import TranscriptAccumulator


class CopilotPipelineBuilder:
    """Builds and manages Pipecat STT Audio Observer Pipelines for Copilot sessions."""
    def __init__(self, deepgram_api_key: Optional[str] = None):
        self.deepgram_api_key = deepgram_api_key or Settings.DEEPGRAM_API_KEY

    def build_observer_pipeline(
        self,
        websocket: Any,
        session_id: str,
        transcript_callback: Optional[Callable[[dict], Any]] = None
    ) -> Optional[Tuple[Pipeline, PipelineWorker]]:
        """Constructs an STT audio processing pipeline using FastAPIWebsocketTransport."""
        if not self.deepgram_api_key:
            logger.warning("[CopilotPipeline] DEEPGRAM_API_KEY is not configured. Audio STT pipeline disabled.")
            return None

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

            stt = DeepgramSTTService(
                api_key=self.deepgram_api_key,
                settings=DeepgramSTTService.Settings(
                    endpointing=400,
                    diarize=True,
                    smart_format=True
                )
            )

            accumulator = TranscriptAccumulator(callback=transcript_callback)

            # Audio buffer processor for recording audio
            audio_buffer = AudioBufferProcessor(
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
                    logger.debug(f"[CopilotPipeline] Saved audio recording to {recording_path}")
                except Exception as e:
                    logger.error(f"[CopilotPipeline] Failed to save audio recording: {e}")

            pipeline = Pipeline([
                transport.input(),
                stt,
                accumulator,
                audio_buffer
            ])

            worker = PipelineWorker(
                pipeline,
                params=PipelineParams(
                    enable_metrics=False,
                    enable_usage_metrics=False,
                ),
                # Disable idle timeout — pipeline runs continuously even during silence
                # Default timeout kills the pipeline when no speech is detected
                idle_timeout_secs=None,
                cancel_on_idle_timeout=False,
            )

            logger.info(f"[CopilotPipeline] Successfully built audio observer pipeline for session: {session_id}")
            return pipeline, worker

        except Exception as err:
            logger.error(f"[CopilotPipeline] Failed to build observer pipeline: {err}")
            return None
