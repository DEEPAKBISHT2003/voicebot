from abc import ABC, abstractmethod
from typing import Callable, Optional, Tuple
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext

class IPipelineBuilder(ABC):
    """Interface defining the setup rules for building a Pipecat audio pipeline."""
    @abstractmethod
    def build_pipeline(
        self, 
        system_instruction: str, 
        session_id: Optional[str] = None,
        transcript_callback: Optional[Callable[[dict], None]] = None
    ) -> Tuple[Pipeline, LLMContext, PipelineWorker]:
        """Construct, connect, and wrap Pipecat components (STT, LLM, TTS, Accumulators)."""
        pass
