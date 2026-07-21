import asyncio
import datetime
from typing import List, Dict, Any
from loguru import logger
from backend.copilot.services.repository import CopilotRepository
from backend.app.services.evaluation import CandidateEvaluationService
from backend.copilot.engine.intelligence import ConversationIntelligenceEngine
from backend.copilot.engine.copilot import AICopilotEngine

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
        
        # Normalize and map transcript entries for backward-compatibility with interview role keys
        self.transcript: List[Dict[str, Any]] = []
        raw_list = initial_transcript or []
        for msg in raw_list:
            speaker = msg.get("speaker")
            if not speaker:
                role = msg.get("role")
                if role == "user":
                    speaker = "Candidate"
                elif role == "assistant":
                    speaker = "Interviewer"
                elif role == "system":
                    speaker = "System"
                else:
                    speaker = "System"
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

    async def _update_intelligence_and_assistance(self):
        """Runs intelligence analysis and copilot suggestions concurrently in the background."""
        try:
            intel_task = self.intelligence_engine.analyze(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume
            )
            assist_task = self.copilot_assistant.generate_assistance(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume
            )
            self.intelligence, self.assistance = await asyncio.gather(intel_task, assist_task)
            logger.info(f"Intelligence and suggestions updated concurrently for session {self.session_id}")
            
            await self.repo.save_session(self.session_id, {
                "transcript": self.transcript,
                "intelligence": self.intelligence,
                "assistance": self.assistance
            })
            logger.info(f"Persisted updated transcript and copilot state for session {self.session_id}")
        except Exception as e:
            logger.error(f"Error in background intelligence/assistance update for session {self.session_id}: {e}")

    async def add_message(self, speaker: str, text: str) -> Dict[str, Any]:
        """
        Adds a new message to the session transcript and returns immediately.
        Candidate evaluations run in critical path; intelligence and assistance updates run in background task.
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

        # Evaluate candidate responses in real-time against the specific question
        if speaker == "Candidate":
            # Retrieve the last interviewer question from transcript history
            last_question = ""
            for msg in reversed(self.transcript):
                if msg.get("speaker") == "Interviewer":
                    last_question = msg.get("text", "")
                    break

            try:
                logger.info(f"Evaluating candidate response for session {self.session_id}...")
                evaluation = await self.evaluation_service.evaluate_response(
                    candidate_response=text,
                    jd=self.jd,
                    resume=self.resume,
                    question=last_question
                )
                message["evaluation"] = evaluation
            except Exception as e:
                logger.error(f"Failed to evaluate candidate response: {e}")

        self.transcript.append(message)

        # Trigger background analysis and persistence task (non-blocking)
        asyncio.create_task(self._update_intelligence_and_assistance())

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
                resume=self.resume
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


