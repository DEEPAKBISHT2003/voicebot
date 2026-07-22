import asyncio
import datetime
from typing import List, Dict, Any, Set
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
        self.detected_speakers: Set[str] = set()
        
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
                resume=self.resume
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
        Adds a new message to the session transcript and returns INSTANTLY (<5ms).
        Stitches rapid same-speaker utterances into unified thoughts.
        All LLM processing (Evaluation, Intelligence, Assistance) runs asynchronously in parallel background task.
        """
        self.detected_speakers.add(speaker)
        clean_text = text.strip()
        if not clean_text:
            return {}

        # Same-Speaker Utterance Stitching Engine (Merges rapid consecutive chunks)
        if self.transcript:
            last_entry = self.transcript[-1]
            if last_entry.get("speaker") == speaker:
                # Merge into existing message bubble
                last_entry["text"] = (last_entry.get("text", "") + " " + clean_text).strip()
                last_entry["timestamp"] = datetime.datetime.now().isoformat()
                
                # Retrieve last question if candidate
                last_q = ""
                if speaker == "Candidate":
                    for msg in reversed(self.transcript[:-1]):
                        if msg.get("speaker") == "Interviewer":
                            last_q = msg.get("text", "")
                            break
                            
                asyncio.create_task(self._update_all_background_llm_tasks(last_entry, last_q, websocket))
                return last_entry

        # Retrieve the last interviewer question from transcript history
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


