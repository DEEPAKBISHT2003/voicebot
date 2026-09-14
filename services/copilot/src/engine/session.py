import asyncio
import datetime
import time
from typing import List, Dict, Any, Set, Optional
from loguru import logger
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.services.evaluation import CandidateEvaluationService
from services.copilot.src.engine.intelligence import ConversationIntelligenceEngine
from services.copilot.src.engine.copilot import AICopilotEngine

class CopilotSessionEngine:
    """Manages the transcript state and incremental memory storage for an active Copilot Session."""
    def __init__(
        self, 
        session_id: str, 
        repo: CopilotRepository, 
        initial_transcript: List[Dict[str, Any]] = None,
        jd: str = "",
        resume: str = "",
        custom_prompt: str = ""
    ):
        self.session_id = session_id
        self.repo = repo
        self.jd = jd
        self.resume = resume
        self.custom_prompt = custom_prompt
        self.transcript: List[Dict[str, Any]] = []
        self.detected_speakers: Set[str] = set()
        
        # Load initial history if provided
        for msg in (initial_transcript or []):
            speaker = msg.get("speaker", "Candidate")
            if speaker not in ("Candidate", "Interviewer", "System"):
                if speaker.lower() in ("candidate", "user"):
                    speaker = "Candidate"
                elif speaker.lower() in ("interviewer", "assistant"):
                    speaker = "Interviewer"
                elif speaker.lower() == "system":
                    speaker = "System"
                else:
                    speaker = "System"
            if speaker in ("Candidate", "Interviewer", "System"):
                self.detected_speakers.add(speaker)
            self.transcript.append({
                "speaker": speaker,
                "text": msg.get("text", ""),
                "timestamp": msg.get("timestamp") or datetime.datetime.now().isoformat(),
                "evaluation": msg.get("evaluation")
            })

        self.evaluation_service = CandidateEvaluationService()
        self.intelligence_engine = ConversationIntelligenceEngine()
        self.intelligence: Dict[str, Any] = self.intelligence_engine._get_empty_state()
        self.copilot_assistant = AICopilotEngine()
        self.assistance: Dict[str, Any] = self.copilot_assistant._get_empty_state()
        self.current_interim: Optional[Dict[str, Any]] = None

        # Smart Time-Window Stitching: merge utterances from same speaker if within 2-3 seconds
        self.stitch_window_seconds: float = 3.0
        self._last_message_epoch: float = time.time() if self.transcript else 0.0

    def update_interim(self, speaker: str, text: str) -> Dict[str, Any]:
        """Updates the current ephemeral interim transcription segment for real-time display."""
        clean_text = text.strip()
        if not clean_text:
            self.current_interim = None
            return {}
        self.current_interim = {
            "speaker": speaker,
            "text": clean_text,
            "timestamp": datetime.datetime.now().isoformat(),
            "is_interim": True
        }
        return self.current_interim

    def clear_interim(self) -> None:
        """Clears the current ephemeral interim transcription segment."""
        self.current_interim = None

    def get_interim(self) -> Optional[Dict[str, Any]]:
        """Returns the current ephemeral interim transcription segment, if any."""
        return self.current_interim

    def get_ui_transcript(self) -> List[Dict[str, Any]]:
        """
        Returns the combined transcript for live UI rendering:
        Permanent committed messages + the active temporary interim segment (if any).
        """
        if self.current_interim and self.current_interim.get("text"):
            return self.transcript + [self.current_interim]
        return self.transcript

    async def _update_all_background_llm_tasks(self, message: Dict[str, Any], last_question: str, websocket: Any = None):
        """Runs candidate evaluation, conversation intelligence, and copilot suggestions concurrently in the background."""
        try:
            tasks = []
            
            # Task 1: Candidate Evaluation (if candidate speaker)
            if message.get("speaker") == "Candidate":
                eval_task = self.evaluation_service.evaluate_response(
                    candidate_response=message.get("text", ""),
                    jd=self.jd,
                    resume=self.resume,
                    question=last_question
                )
                tasks.append(eval_task)
            else:
                tasks.append(asyncio.sleep(0, result=None))

            # Task 2: Intelligence Analysis
            intel_task = self.intelligence_engine.analyze(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume
            )
            tasks.append(intel_task)

            # Task 3: Copilot Assistance
            assist_task = self.copilot_assistant.generate_assistance(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume,
                custom_prompt=self.custom_prompt
            )
            tasks.append(assist_task)

            # Execute all 3 tasks concurrently in parallel
            evaluation, intelligence, assistance = await asyncio.gather(*tasks, return_exceptions=True)

            if isinstance(evaluation, dict):
                message["evaluation"] = evaluation
            
            if isinstance(intelligence, dict):
                self.intelligence = intelligence
                # Attach live speaker metrics
                self.intelligence["total_speakers_count"] = max(len(self.detected_speakers), 1)

            if isinstance(assistance, dict):
                self.assistance = assistance

            logger.info(f"Background evaluation & copilot analysis complete for session {self.session_id}")

            # Persist updated state to DB/storage
            await self.repo.save_session(self.session_id, {
                "transcript": self.transcript,
                "intelligence": self.intelligence,
                "assistance": self.assistance
            })

            # Broadcast updated state frame to frontend UI if websocket is provided
            if websocket:
                try:
                    await websocket.send_json({
                        "type": "copilot_update",
                        "session_id": self.session_id,
                        "last_message": message,
                        "transcript": self.transcript,
                        "intelligence": self.intelligence,
                        "assistance": self.assistance
                    })
                except Exception as ws_err:
                    logger.debug(f"Could not push background update frame over WebSocket: {ws_err}")

        except Exception as e:
            logger.error(f"Error in background LLM task execution for session {self.session_id}: {e}")

    async def add_message(self, speaker: str, text: str, websocket: Any = None) -> Dict[str, Any]:
        """
        Adds a new finalized message to the permanent transcript history and returns INSTANTLY (<5ms).
        Clears any active interim transcription state.
        Each finalized utterance remains a distinct permanent message (no unconditional same-speaker overwriting).
        Legitimate repeated speech (e.g. 'Yes.' followed by 'Yes.') is preserved as distinct utterances.
        All LLM processing (Evaluation, Intelligence, Assistance) runs asynchronously in parallel background task.
        """
        self.clear_interim()
        self.detected_speakers.add(speaker)
        clean_text = text.strip()
        if not clean_text:
            return {}

        # Retrieve the last interviewer question from transcript history if speaker is candidate
        last_question = ""
        if speaker == "Candidate":
            for msg in reversed(self.transcript):
                if msg.get("speaker") == "Interviewer":
                    last_question = msg.get("text", "")
                    break

        message = {
            "speaker": speaker,
            "text": clean_text,
            "timestamp": datetime.datetime.now().isoformat()
        }

        self.transcript.append(message)

        # Trigger concurrent background processing (non-blocking, <5ms return)
        asyncio.create_task(self._update_all_background_llm_tasks(message, last_question, websocket))

        return message

    def get_transcript(self) -> List[Dict[str, Any]]:
        """Returns the current transcript history list."""
        return self.transcript

    def get_intelligence(self) -> Dict[str, Any]:
        """Returns the current conversation intelligence state."""
        return self.intelligence

    def get_assistance(self) -> Dict[str, Any]:
        """Returns the current conversation copilot suggestions state."""
        return self.assistance

    async def finalize_report(self) -> Dict[str, Any]:
        """Compiles and finalizes post-session evaluation metrics and summary dossier."""
        try:
            self.intelligence = await self.intelligence_engine.analyze(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume
            )
            self.assistance = await self.copilot_assistant.generate_assistance(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume,
                custom_prompt=self.custom_prompt
            )
            await self.repo.save_session(self.session_id, {
                "transcript": self.transcript,
                "intelligence": self.intelligence,
                "assistance": self.assistance,
                "is_finalized": True
            })
            logger.info(f"Finalized post-interview evaluation report for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to finalize report for session {self.session_id}: {e}")
        return {
            "transcript": self.transcript,
            "intelligence": self.intelligence,
            "assistance": self.assistance
        }


