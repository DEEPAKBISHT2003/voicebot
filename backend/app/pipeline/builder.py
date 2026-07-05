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
        tts = DeepgramTTSService(api_key=self.deepgram_api_key)
        
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
                        confidence=0.5,      # Lower confidence requirement (default 0.7)
                        min_volume=0.1,      # Lower volume threshold (default 0.6)
                        start_secs=0.2,
                        stop_secs=0.2
                    )
                )
            ),
        )

        accumulator = TranscriptAccumulator(transcript_callback)

        # Sequentially connect audio frames flow
        pipeline = Pipeline([
            transport.input(),      # Capture audio raw data from system microphone
            stt,                    # Convert user audio -> text
            user_aggregator,        # Aggregate user words
            accumulator,            # Catch dialogue text
            llm,                    # Feed text history to Groq LLaMA model
            tts,                    # Convert response text -> assistant speech
            transport.output(),     # Play synthesized speech on system speakers
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
            "content": "Initiate the mock interview by introducing yourself as Sheela, welcoming the candidate by extracting their name from the resume, and asking: 'Please introduce yourself, [Name]'. Then follow with: 'Alright, let's start the interview with a basic technical question...'"
        })

        return pipeline, context, worker
