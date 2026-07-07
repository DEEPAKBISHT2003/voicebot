import asyncio
import threading
from typing import Callable, Optional
from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.workers.runner import WorkerRunner

from backend.app.core.interfaces.bot_runner import IBotRunner
from backend.app.prompts.interview_prompt import InterviewPromptBuilder
from backend.app.pipeline.builder import LocalPipecatPipelineBuilder

class LocalBotRunner(IBotRunner):
    """Spawns the asyncio pipeline run in a background daemon thread."""
    def __init__(self, deepgram_api_key: str, groq_api_key: str):
        self.deepgram_api_key = deepgram_api_key
        self.groq_api_key = groq_api_key
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker: Optional[Any] = None
        self._running: bool = False
        
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()
        
    def start(
        self, 
        jd: str, 
        resume: str, 
        session_id: str, 
        status_callback: Optional[Callable[[str], None]] = None, 
        transcript_callback: Optional[Callable[[dict], None]] = None,
        custom_prompt: Optional[str] = None
    ) -> None:
        if self.is_running():
            logger.warning("Bot runner is already active.")
            return
            
        self._running = True
        self._loop = asyncio.new_event_loop()
        
        # Build prompt guidelines
        prompt_builder = InterviewPromptBuilder()
        system_instruction = prompt_builder.build_system_instruction(jd, resume, custom_prompt)
        
        # Build pipeline workers
        pipeline_builder = LocalPipecatPipelineBuilder(self.deepgram_api_key, self.groq_api_key)
        _, _, worker = pipeline_builder.build_pipeline(system_instruction, transcript_callback)
        self._worker = worker
        
        # Start daemon thread execution
        self._thread = threading.Thread(
            target=self._run_async_loop,
            args=(status_callback,),
            daemon=True
        )
        self._thread.start()
        
    def _run_async_loop(self, status_callback: Optional[Callable[[str], None]]):
        asyncio.set_event_loop(self._loop)
        if status_callback:
            status_callback("Microphone online! Say 'Hello' to start.")
            
        try:
            self._loop.run_until_complete(self._async_run())
        except Exception as e:
            logger.error(f"Error in background bot loop: {e}", exc_info=True)
            if status_callback:
                status_callback(f"Error: {e}")
        finally:
            self._running = False
            try:
                # Clean up any remaining active tasks inside the loop to avoid warnings
                pending = asyncio.all_tasks(self._loop)
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self._loop.close()
            except Exception as e:
                logger.warning(f"Error closing event loop: {e}")
            if status_callback:
                status_callback("Mock Interview Stopped.")

    async def _async_run(self):
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(self._worker)
        
        # Dispatch startup spoken trigger
        await self._worker.queue_frames([LLMRunFrame()])
        await runner.run()
        
    def stop(self) -> None:
        if not self.is_running():
            return
            
        logger.info("Stopping local audio bot runner...")
        self._running = False
        
        if self._worker and self._loop:
            future = asyncio.run_coroutine_threadsafe(self._worker.cancel(), self._loop)
            try:
                future.result(timeout=3.0)
            except Exception as e:
                logger.error(f"Exception during worker.cancel(): {e}")
                
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
            
        self._loop = None
        self._worker = None
        logger.info("Local audio bot runner stopped successfully.")
