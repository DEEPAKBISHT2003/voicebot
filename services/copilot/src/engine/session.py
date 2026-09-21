import asyncio
import datetime
from typing import List, Dict, Any, Set
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
        self.detected_speakers: Set[str] = set()
        
        # Normalize and map transcript entries for backward-compatibility with interview role keys
        self.transcript: List[Dict[str, Any]] = []
        raw_list = initial_transcript or []
        for idx, msg in enumerate(raw_list):
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
            t_id = msg.get("turn_id", idx + 1)
            e_id = msg.get("id") or f"{self.session_id}-turn-{t_id}"
            self.transcript.append({
                "id": e_id,
                "turn_id": t_id,
                "speaker": speaker,
                "text": msg.get("text", ""),
                "timestamp": msg.get("timestamp") or datetime.datetime.now().isoformat(),
                "evaluation": msg.get("evaluation"),
                "source": msg.get("source", "teams_native")
            })

        self.evaluation_service = CandidateEvaluationService()
        self.intelligence_engine = ConversationIntelligenceEngine()
        self.intelligence: Dict[str, Any] = self.intelligence_engine._get_empty_state()
        self.copilot_assistant = AICopilotEngine()
        self.assistance: Dict[str, Any] = self.copilot_assistant._get_empty_state()
        self.on_update_callback: Any = None

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

            # Notify dashboard subscribers via global on_update_callback if registered
            if getattr(self, "on_update_callback", None):
                try:
                    await self.on_update_callback(message)
                except Exception as cb_err:
                    logger.debug(f"Could not invoke on_update_callback: {cb_err}")

        except Exception as e:
            logger.error(f"Error in background LLM task execution for session {self.session_id}: {e}")

    async def add_message(
        self,
        speaker: str,
        text: str,
        websocket: Any = None,
        source: str = "teams_native",
        allow_merge: bool = True,
        turn_id: Any = None,
        msg_id: Any = None,
        is_final: bool = True
    ) -> Dict[str, Any]:
        """
        Adds a new message to the session transcript and returns INSTANTLY (<5ms).
        Stitches rapid same-speaker utterances into unified thoughts when allow_merge=True.
        When allow_merge=False (e.g. from NativeLogicalTurnAggregator), each logical turn is recorded distinctly.
        All LLM processing (Evaluation, Intelligence, Assistance) runs asynchronously in parallel background task.
        """
        self.detected_speakers.add(speaker)
        clean_text = text.strip()
        if not clean_text:
            return {}

        curr_turn_id = turn_id if turn_id is not None else (len(self.transcript) + 1)
        entry_id = msg_id or f"{self.session_id}-turn-{curr_turn_id}"

        # Same-Speaker Utterance Stitching Engine (Merges rapid consecutive chunks if allow_merge is True)
        if allow_merge and self.transcript:
            last_entry = self.transcript[-1]
            if last_entry.get("speaker") == speaker:
                # Merge into existing message bubble
                last_entry["text"] = (last_entry.get("text", "") + " " + clean_text).strip()
                last_entry["timestamp"] = datetime.datetime.now().isoformat()
                if "source" not in last_entry:
                    last_entry["source"] = source
                if "id" not in last_entry:
                    last_entry["id"] = entry_id
                if "turn_id" not in last_entry:
                    last_entry["turn_id"] = curr_turn_id
                
                # Retrieve last question if candidate
                last_q = ""
                if speaker == "Candidate":
                    for msg in reversed(self.transcript[:-1]):
                        if msg.get("speaker") == "Interviewer":
                            last_q = msg.get("text", "")
                            break
                            
                try:
                    await self.repo.save_session(self.session_id, {"transcript": self.transcript})
                except Exception as save_err:
                    logger.debug(f"Immediate transcript save warning: {save_err}")

                if is_final:
                    asyncio.create_task(self._update_all_background_llm_tasks(last_entry, last_q, websocket))
                return last_entry

        # Retrieve the last interviewer question from transcript history
        last_question = ""
        if speaker == "Candidate":
            for msg in reversed(self.transcript):
                if msg.get("speaker") == "Interviewer":
                    last_question = msg.get("text", "")
                    break

        # Check if updating an existing turn (e.g. from progressive extension or late arrival)
        for msg in self.transcript:
            if (turn_id is not None and msg.get("turn_id") == curr_turn_id) or (msg_id and msg.get("id") == entry_id):
                if clean_text.startswith(msg.get("text", "")):
                    msg["text"] = clean_text
                elif not msg.get("text", "").endswith(clean_text):
                    msg["text"] = f"{msg.get('text', '')} {clean_text}".strip()
                msg["timestamp"] = datetime.datetime.now().isoformat()
                try:
                    await self.repo.save_session(self.session_id, {"transcript": self.transcript})
                except Exception as save_err:
                    logger.debug(f"Immediate transcript save warning: {save_err}")
                if is_final and not msg.get("_llm_evaluated"):
                    msg["_llm_evaluated"] = True
                    asyncio.create_task(self._update_all_background_llm_tasks(msg, last_question, websocket))
                return msg

        message = {
            "id": entry_id,
            "turn_id": curr_turn_id,
            "speaker": speaker,
            "text": clean_text,
            "timestamp": datetime.datetime.now().isoformat(),
            "source": source
        }

        self.transcript.append(message)

        # Immediate DB persistence of new transcript turn
        try:
            await self.repo.save_session(self.session_id, {"transcript": self.transcript})
        except Exception as save_err:
            logger.debug(f"Immediate transcript save warning: {save_err}")

        # Trigger concurrent background processing (non-blocking, <5ms return)
        if is_final:
            message["_llm_evaluated"] = True
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
            report_data = {
                "transcript": self.transcript,
                "intelligence": self.intelligence,
                "assistance": self.assistance
            }
            await self.repo.save_session(self.session_id, {
                "transcript": self.transcript,
                "final_report": report_data,
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


