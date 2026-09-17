import asyncio
import datetime
import os
import json
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
        custom_prompt: str = "",
        verification_count: int = 10,
        scenario_count: int = 10
    ):
        self.session_id = session_id
        self.repo = repo
        self.jd = jd
        self.resume = resume
        self.custom_prompt = custom_prompt
        self.verification_count = max(1, min(int(verification_count), 50))
        self.scenario_count = max(1, min(int(scenario_count), 50))
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
        self.on_update_callback: Any = None

        # Static Verification and Scenario questions per meeting/interview
        self.session_dir = os.path.join(self.repo.directory, str(self.session_id))
        self.questions_file = os.path.join(self.session_dir, "interview_questions.json")
        self.verification_questions: List[str] = []
        self.scenario_questions: List[str] = []
        self._static_questions_initialized: bool = False
        self._static_questions_lock = asyncio.Lock()

        # Load from disk if already generated for this session
        if os.path.exists(self.questions_file):
            try:
                with open(self.questions_file, "r", encoding="utf-8") as f:
                    q_data = json.load(f)
                    self.verification_questions = [str(q).strip() for q in q_data.get("verification_questions", []) if str(q).strip()][:self.verification_count]
                    self.scenario_questions = [str(q).strip() for q in q_data.get("scenario_questions", []) if str(q).strip()][:self.scenario_count]
                    if self.verification_questions or self.scenario_questions:
                        self._static_questions_initialized = True
            except Exception as load_err:
                logger.warning(f"Could not load cached interview questions for session {self.session_id}: {load_err}")

        self.assistance["verification_questions"] = self.verification_questions
        self.assistance["suggested_practical_questions"] = self.scenario_questions

    async def ensure_static_questions(self, websocket: Any = None):
        """
        Generates configurable verification and scenario questions once per interview/meeting.
        Persists them to disk and ensures they remain fixed throughout the session.
        """
        if self._static_questions_initialized and len(self.verification_questions) >= self.verification_count and len(self.scenario_questions) >= self.scenario_count:
            return

        async with self._static_questions_lock:
            if self._static_questions_initialized and len(self.verification_questions) >= self.verification_count and len(self.scenario_questions) >= self.scenario_count:
                return

            if os.path.exists(self.questions_file):
                try:
                    with open(self.questions_file, "r", encoding="utf-8") as f:
                        q_data = json.load(f)
                        self.verification_questions = [str(q).strip() for q in q_data.get("verification_questions", []) if str(q).strip()][:self.verification_count]
                        self.scenario_questions = [str(q).strip() for q in q_data.get("scenario_questions", []) if str(q).strip()][:self.scenario_count]
                        if len(self.verification_questions) > 0 or len(self.scenario_questions) > 0:
                            self._static_questions_initialized = True
                            self.assistance["verification_questions"] = self.verification_questions
                            self.assistance["suggested_practical_questions"] = self.scenario_questions
                            return
                except Exception as e:
                    logger.warning(f"Error reading existing interview questions: {e}")

            if self.jd or self.resume:
                logger.info(f"Generating {self.verification_count} verification & {self.scenario_count} scenario questions once for session {self.session_id}")
                q_res = await self.copilot_assistant.generate_static_interview_questions(
                    jd=self.jd,
                    resume=self.resume,
                    verification_count=self.verification_count,
                    scenario_count=self.scenario_count
                )
                self.verification_questions = q_res.get("verification_questions", [])[:self.verification_count]
                self.scenario_questions = q_res.get("scenario_questions", [])[:self.scenario_count]
                self._static_questions_initialized = True
                self.assistance["verification_questions"] = self.verification_questions
                self.assistance["suggested_practical_questions"] = self.scenario_questions

                try:
                    os.makedirs(self.session_dir, exist_ok=True)
                    with open(self.questions_file, "w", encoding="utf-8") as f:
                        json.dump({
                            "verification_questions": self.verification_questions,
                            "scenario_questions": self.scenario_questions
                        }, f, indent=2)
                except Exception as save_err:
                    logger.warning(f"Could not save interview questions to file: {save_err}")

                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "copilot_update",
                            "session_id": self.session_id,
                            "transcript": self.transcript,
                            "intelligence": self.intelligence,
                            "assistance": self.assistance
                        })
                    except Exception:
                        pass
                elif getattr(self, "on_update_callback", None):
                    try:
                        await self.on_update_callback(None)
                    except Exception:
                        pass

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

            # Ensure static questions are generated if not yet initialized
            if not self._static_questions_initialized and (self.jd or self.resume):
                tasks.append(self.ensure_static_questions(websocket))

            # Execute tasks concurrently in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)
            evaluation = results[0] if len(results) > 0 else None
            intelligence = results[1] if len(results) > 1 else None
            assistance = results[2] if len(results) > 2 else None

            if isinstance(evaluation, dict):
                message["evaluation"] = evaluation
            
            if isinstance(intelligence, dict):
                self.intelligence = intelligence
                # Attach live speaker metrics
                self.intelligence["total_speakers_count"] = max(len(self.detected_speakers), 1)

            if isinstance(assistance, dict):
                # Retain static 10 verification and 10 scenario questions per meeting
                assistance["verification_questions"] = self.verification_questions
                assistance["suggested_practical_questions"] = self.scenario_questions
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
                resume=self.resume,
                custom_prompt=self.custom_prompt
            )
            if isinstance(self.assistance, dict):
                self.assistance["verification_questions"] = self.verification_questions
                self.assistance["suggested_practical_questions"] = self.scenario_questions
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


