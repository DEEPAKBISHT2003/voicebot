import os
import wave
from typing import Any, Callable, Optional, Tuple
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams
)
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from backend.app.core.interfaces.pipeline_builder import IPipelineBuilder
from backend.app.core.config import Settings
from backend.app.pipeline.accumulator import TranscriptAccumulator
from backend.app.pipeline.playback_buffer import PlaybackBufferProcessor
from backend.app.pipeline.mic_gate import MicGateProcessor, MicUnmuterProcessor

class LocalPipecatPipelineBuilder(IPipelineBuilder):
    """Sets up local hardware audio transport and chains STT/Groq/TTS/Accumulator elements."""
    def __init__(self, deepgram_api_key: str, groq_api_key: str):
        self.deepgram_api_key = deepgram_api_key
        self.groq_api_key = groq_api_key

    def build_pipeline(
        self, 
        system_instruction: str, 
        session_id: Optional[str] = None,
        transcript_callback: Optional[Callable[[dict], None]] = None,
        websocket: Optional[Any] = None
    ) -> Tuple[Pipeline, LLMContext, PipelineWorker]:
        if websocket is not None:
            from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
            from backend.app.pipeline.serializer import RawPCMAudioSerializer
            
            transport = FastAPIWebsocketTransport(
                websocket=websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_sample_rate=16000,
                    audio_out_enabled=True,
                    audio_out_sample_rate=16000,
                    add_wav_header=False,
                    serializer=RawPCMAudioSerializer(sample_rate=16000),
                )
            )
        else:
            # Connect PyAudio local input/output device streams
            transport = LocalAudioTransport(
                LocalAudioTransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                )
            )

        stt = DeepgramSTTService(api_key=self.deepgram_api_key)
        tts = DeepgramTTSService(
            api_key=self.deepgram_api_key,
            settings=DeepgramTTSService.Settings(voice="aura-2-amalthea-en")
        )
        
        # Groq LLM running llama-3.3-70b-versatile
        llm = GroqLLMService(
            api_key=self.groq_api_key,
            model=Settings.GROQ_MODEL,
            settings=GroqLLMService.Settings(
                system_instruction=system_instruction
            )
        )

        # Thread-safe aggregate pair for pipeline processing
        context = LLMContext()
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=0.7,      # Standard confidence requirement (default 0.7)
                        min_volume=0.25,     # Increased volume threshold to filter noise
                        start_secs=0.3,      # Ignore brief clicks/pops
                        stop_secs=0.4        # Allow natural breathing pauses
                    )
                )
            ),
        )

        # Double accumulators: user_accumulator is placed before user_aggregator,
        # assistant_accumulator is placed after llm.
        user_accumulator = TranscriptAccumulator(transcript_callback)
        assistant_accumulator = TranscriptAccumulator(transcript_callback)
        
        # Audio buffer processor for recording
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
                from loguru import logger
                logger.info(f"Saved complete session recording to {recording_path}")
            except Exception as e:
                from loguru import logger
                logger.error(f"Failed to save audio recording: {e}")

        playback_buffer = PlaybackBufferProcessor(buffer_size=5)
        
        # Shared state for microphone gating (starts disabled)
        shared_state = {"mic_enabled": False}
        mic_gate = MicGateProcessor(shared_state)
        mic_unmuter = MicUnmuterProcessor(shared_state)

        # Sequentially connect audio frames flow
        pipeline = Pipeline([
            transport.input(),      # Capture audio raw data from system microphone
            mic_gate,               # Block mic inputs until the AI greeting ends
            stt,                    # Convert user audio -> text
            user_accumulator,       # Catch candidate speech text before aggregator consumption
            user_aggregator,        # Aggregate user words
            llm,                    # Feed text history to Groq LLaMA model
            assistant_accumulator,  # Catch interviewer response text after LLM generation
            tts,                    # Convert response text -> assistant speech
            playback_buffer,        # Buffer output audio chunks to prevent jitter cracks
            transport.output(),     # Play synthesized speech on system speakers
            audio_buffer,           # Record audio of user and bot
            mic_unmuter,            # Detects end of first greeting to toggle mic_gate
            assistant_aggregator,   # Aggregate assistant words
        ])

        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                enable_metrics=False,
                enable_usage_metrics=False,
            )
        )

        # Trigger initial spoken greetings frame
        context.add_message({
            "role": "system", 
            "content": "Initiate the mock interview by introducing yourself as Miaaa, welcoming the candidate by extracting their name from the resume, and asking: 'Please introduce yourself, [Name]'."
        })

        return pipeline, context, worker

