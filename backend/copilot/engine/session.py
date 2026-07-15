import datetime
from typing import List, Dict, Any
from loguru import logger
from backend.copilot.services.repository import CopilotRepository
from backend.app.services.evaluation import CandidateEvaluationService
from backend.copilot.engine.intelligence import ConversationIntelligenceEngine

class CopilotSessionEngine:
    """Manages the transcript state and incremental memory storage for an active Copilot Session."""
    def __init__(
        self, 
        session_id: str, 
        repo: CopilotRepository, 
        initial_transcript: List[Dict[str, Any]] = None,
        jd: str = "",
        resume: str = ""
    ):
        self.session_id = session_id
        self.repo = repo
        self.jd = jd
        self.resume = resume
        self.transcript: List[Dict[str, Any]] = initial_transcript or []
        self.evaluation_service = CandidateEvaluationService()
        self.intelligence_engine = ConversationIntelligenceEngine()
        self.intelligence: Dict[str, Any] = self.intelligence_engine._get_empty_state()

    async def add_message(self, speaker: str, text: str) -> Dict[str, Any]:
        """
        Adds a new message to the session transcript and persists it to database.
        Valid speaker values: 'Interviewer', 'Candidate', 'System'
        """
        valid_speakers = {"Interviewer", "Candidate", "System"}
        if speaker not in valid_speakers:
            logger.warning(f"Unexpected speaker identity '{speaker}' for session {self.session_id}. Expected one of: {valid_speakers}")

        message = {
            "speaker": speaker,
            "text": text,
            "timestamp": datetime.datetime.now().isoformat()
        }

        # Evaluate candidate responses in real-time
        if speaker == "Candidate":
            try:
                logger.info(f"Evaluating candidate response for session {self.session_id}...")
                evaluation = await self.evaluation_service.evaluate_response(
                    candidate_response=text,
                    jd=self.jd,
                    resume=self.resume
                )
                message["evaluation"] = evaluation
            except Exception as e:
                logger.error(f"Failed to evaluate candidate response: {e}")

        self.transcript.append(message)

        # Run conversation intelligence analysis after every message
        try:
            self.intelligence = await self.intelligence_engine.analyze(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume
            )
            logger.info(f"Intelligence state updated for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to update intelligence state for session {self.session_id}: {e}")

        # Persist incrementally to database and local storage files
        try:
            await self.repo.save_session(self.session_id, {"transcript": self.transcript})
            logger.info(f"Persisted new {speaker} transcript entry for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to incrementally persist transcript entry for session {self.session_id}: {e}")

        return message

    def get_transcript(self) -> List[Dict[str, Any]]:
        """Returns the current transcript history list."""
        return self.transcript

    def get_intelligence(self) -> Dict[str, Any]:
        """Returns the current conversation intelligence state."""
        return self.intelligence

