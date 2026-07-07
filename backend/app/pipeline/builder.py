from typing import Callable, Optional, Tuple
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
        transcript_callback: Optional[Callable[[dict], None]] = None
    ) -> Tuple[Pipeline, LLMContext, PipelineWorker]:
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

        accumulator = TranscriptAccumulator(transcript_callback)
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
            user_aggregator,        # Aggregate user words
            accumulator,            # Catch dialogue text
            llm,                    # Feed text history to Groq LLaMA model
            tts,                    # Convert response text -> assistant speech
            playback_buffer,        # Buffer output audio chunks to prevent jitter cracks
            transport.output(),     # Play synthesized speech on system speakers
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
