import os
import json
import time
import asyncio
import datetime
from typing import List, Dict, Any, Set, Tuple, Optional
from openai import AsyncOpenAI
from loguru import logger
from services.copilot.src.core.config import Settings
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.services.evaluation import CandidateEvaluationService
from services.copilot.src.engine.intelligence import ConversationIntelligenceEngine
from services.copilot.src.engine.copilot import AICopilotEngine
from services.copilot.src.services.role_identifier import ConversationalRoleIdentifier


def clean_json_loads(text: str) -> dict:
    """Safely parse JSON responses that may be wrapped in markdown codeblocks."""
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    return json.loads(clean_text)

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
        confirmed_qa_pairs: List[Dict[str, Any]] = None
    ):
        self.session_id = session_id
        self.repo = repo
        self.jd = jd
        self.resume = resume
        self.custom_prompt = custom_prompt
        self.detected_speakers: Set[str] = set()
        self.session_speaker_roles: Dict[str, str] = {}
        self.role_identifier = ConversationalRoleIdentifier()
        self._role_identification_in_progress: bool = False
        
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
            
            raw_role = msg.get("speaker_role")
            if not raw_role:
                if speaker == "Candidate":
                    raw_role = "candidate"
                elif speaker == "Interviewer":
                    raw_role = "interviewer"
                else:
                    raw_role = "unknown"
            if speaker and speaker != "System":
                self.session_speaker_roles[speaker] = raw_role

            t_id = msg.get("turn_id", idx + 1)
            e_id = msg.get("id") or f"{self.session_id}-turn-{t_id}"
            self.transcript.append({
                "id": e_id,
                "turn_id": t_id,
                "speaker": speaker,
                "speaker_role": raw_role,
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
        self.on_qa_evaluated_callback: Any = None

        # Phase 2S: Initial interview question suggestions state
        self.initial_suggestions: List[str] = []
        self.initial_suggestions_generation_count: int = 0
        self._initial_suggestions_generated: bool = False
        self._initial_suggestions_in_progress: bool = False

        # Phase 2T: Dynamic interview question suggestions state
        self.processed_qa_pairs: Set[str] = set()
        self.dynamic_suggestions: List[str] = []
        self.dynamic_suggestions_sequence: int = 0
        self.latest_completed_suggestion_seq: int = 0
        self.dynamic_suggestions_history: List[Dict[str, Any]] = []
        self.background_tasks: Set[asyncio.Task] = set()

        # Phase 2U: 15-Second LLM Q/A Checkpoint State
        self.qa_checkpoint_interval: float = 15.0
        self.qa_checkpoint_running: bool = False
        self.qa_checkpoint_task: Optional[asyncio.Task] = None
        self._qa_checkpoint_lock = asyncio.Lock()
        self.checkpoint_number: int = 0
        self.last_checkpoint_at: Optional[str] = None
        self.last_seen_turn_id: Any = None
        self.last_qa_checkpoint_turn_count: int = 0
        self.last_processed_question_id: Any = None
        self.last_processed_answer_id: Any = None
        self.confirmed_qa_pairs: List[Dict[str, Any]] = list(confirmed_qa_pairs) if confirmed_qa_pairs else []
        self.last_qa_decision: Optional[Dict[str, Any]] = None
        self.client = AsyncOpenAI(api_key=Settings.DEEPSEEK_API_KEY, base_url=Settings.DEEPSEEK_BASE_URL)
        self.model = Settings.DEEPSEEK_MODEL

        # Phase 2V: Answer Accuracy Evaluation State
        self.evaluated_qa_ids: Set[str] = set()
        self.active_evaluation_tasks: Dict[str, asyncio.Task] = {}
        self.active_websocket: Any = None

        # Restore persisted confirmed_qa_pairs from disk cache if not passed directly
        if not self.confirmed_qa_pairs:
            for target_dir in [os.path.join("interviews", str(self.session_id)), os.path.join(getattr(self.repo, "directory", "copilots"), str(self.session_id))]:
                p = os.path.join(target_dir, "confirmed_qa_pairs.json")
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            q_data = json.load(f)
                            if isinstance(q_data, dict):
                                self.confirmed_qa_pairs = q_data.get("confirmed_qa_pairs", [])
                            elif isinstance(q_data, list):
                                self.confirmed_qa_pairs = q_data
                        if self.confirmed_qa_pairs:
                            break
                    except Exception:
                        pass

        # Populate evaluated_qa_ids for any already-evaluated confirmed Q+A pairs (reconnect safety)
        for qa in self.confirmed_qa_pairs:
            qa_id = qa.get("qa_id") or qa.get("pair_id")
            if qa_id and "accuracy_score" in qa:
                self.evaluated_qa_ids.add(qa_id)

        # Phase 2X: Static Scenario and Verification Questions State
        self.scenario_questions: List[str] = []
        self.verification_questions: List[str] = []
        self.static_questions_generated: bool = False
        self._static_questions_in_progress: bool = False
        self.static_questions_generation_count: int = 0

        # Restore persisted static questions from disk cache if present
        for target_dir in [os.path.join("interviews", str(self.session_id)), os.path.join(getattr(self.repo, "directory", "copilots"), str(self.session_id))]:
            p = os.path.join(target_dir, "static_questions.json")
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        sq_data = json.load(f)
                        sc_q = sq_data.get("scenario_questions", [])
                        ver_q = sq_data.get("verification_questions", [])
                        if len(sc_q) == 5 and len(ver_q) == 5:
                            self.scenario_questions = list(sc_q)
                            self.verification_questions = list(ver_q)
                            self.static_questions_generated = True
                            self.assistance["scenario_questions"] = list(sc_q)
                            self.assistance["suggested_practical_questions"] = list(sc_q)
                            self.assistance["verification_questions"] = list(ver_q)
                            break
                except Exception:
                    pass


    def should_identify_roles(self) -> bool:
        """
        Determines whether conversational role identification should be triggered.
        Triggers when there are unresolved human speakers and meaningful conversational
        dialogue (>= 2 distinct human speakers, question + answer exchange, non-greeting).
        """
        if self._role_identification_in_progress:
            return False

        human_speakers = [s for s in self.detected_speakers if s and s != "System"]
        if len(human_speakers) < 2:
            return False

        # If all human speakers already have confident roles, no need to rerun
        unresolved = [s for s in human_speakers if self.session_speaker_roles.get(s, "unknown") == "unknown"]
        if not unresolved:
            return False

        # Valid non-empty turns
        valid_turns = [t for t in self.transcript if t.get("text", "").strip() and t.get("speaker") != "System"]
        if len(valid_turns) < 2:
            return False

        # Check for meaningful conversational exchange (not just "Hello" / "Hi")
        total_words = sum(len(t.get("text", "").split()) for t in valid_turns)
        has_substantive_turn = any(len(t.get("text", "").strip()) >= 15 for t in valid_turns)
        has_question_or_answer = any(
            t.get("text", "").strip().endswith("?") or len(t.get("text", "").split()) >= 6
            for t in valid_turns
        )

        return total_words >= 6 and (has_substantive_turn or has_question_or_answer)

    async def _update_all_background_llm_tasks(self, message: Dict[str, Any], last_question: str, websocket: Any = None):
        """Runs conversational role identification, candidate evaluation, conversation intelligence, and copilot suggestions concurrently in the background."""
        try:
            # 1. Background Conversational Role Identification (if unresolved speakers and meaningful dialogue)
            if self.should_identify_roles():
                self._role_identification_in_progress = True
                try:
                    human_speakers = [s for s in self.detected_speakers if s and s != "System"]
                    role_result = await self.role_identifier.identify_roles(
                        transcript=self.transcript,
                        participants=human_speakers,
                        jd=self.jd,
                        resume=self.resume
                    )
                    speakers_map = role_result.get("speakers", {})
                    newly_resolved = False
                    for spk, info in speakers_map.items():
                        r = info.get("role", "unknown")
                        conf = info.get("confidence", 0.0)
                        if r in ("candidate", "interviewer") and conf >= ConversationalRoleIdentifier.CONFIDENCE_THRESHOLD:
                            if self.session_speaker_roles.get(spk) != r:
                                self.session_speaker_roles[spk] = r
                                newly_resolved = True
                        elif spk not in self.session_speaker_roles:
                            self.session_speaker_roles[spk] = "unknown"

                    if newly_resolved:
                        # Backfill speaker_role in transcript turns for resolved speakers
                        for t in self.transcript:
                            t_spk = t.get("speaker")
                            if t_spk in self.session_speaker_roles and t.get("speaker_role") != self.session_speaker_roles[t_spk]:
                                t["speaker_role"] = self.session_speaker_roles[t_spk]

                        # Update current message role if it changed
                        curr_spk = message.get("speaker")
                        if curr_spk in self.session_speaker_roles:
                            message["speaker_role"] = self.session_speaker_roles[curr_spk]

                        # Phase 2U: Disconnected legacy Q/A trigger from role resolution.
                        # The 15-second checkpoint handles completed-Q/A detection semantically.

                except Exception as role_err:
                    logger.error(f"Error in background conversational role identification: {role_err}")
                finally:
                    self._role_identification_in_progress = False

            # Check if current message is resolved as candidate
            is_candidate_turn = (message.get("speaker_role") == "candidate" or message.get("speaker") == "Candidate")

            # Retrieve last interviewer question if candidate turn and last_question was not provided
            if is_candidate_turn and not last_question:
                for msg in reversed(self.transcript[:-1]):
                    if msg.get("speaker_role") == "interviewer" or msg.get("speaker") == "Interviewer":
                        last_question = msg.get("text", "")
                        break

            if websocket:
                self.active_websocket = websocket

            # Phase 2V: Disconnected candidate evaluation from raw candidate turns.
            # Raw candidate turns, fragments, or silence NO LONGER trigger evaluation.
            # Evaluation runs exclusively on CONFIRMED Q+A pairs from the Phase 2U 15-second checkpoint.
            tasks = []

            # Task 1: Intelligence Analysis
            intel_task = self.intelligence_engine.analyze(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume
            )
            tasks.append(intel_task)

            # Task 2: Copilot Assistance
            assist_task = self.copilot_assistant.generate_assistance(
                transcript=self.transcript,
                jd=self.jd,
                resume=self.resume,
                custom_prompt=self.custom_prompt
            )
            tasks.append(assist_task)

            # Execute intelligence and assistance concurrently in parallel
            intelligence, assistance = await asyncio.gather(*tasks, return_exceptions=True)
            
            if isinstance(intelligence, dict):
                self.intelligence = intelligence
                # Attach live speaker metrics
                self.intelligence["total_speakers_count"] = max(len(self.detected_speakers), 1)

            if isinstance(assistance, dict):
                # Phase 2S: Preserve initial suggestions in assistance across turn updates
                if self.initial_suggestions:
                    assistance["initial_suggestions"] = list(self.initial_suggestions)
                # Phase 2T: Preserve dynamic suggestions in assistance across turn updates
                if self.dynamic_suggestions:
                    assistance["dynamic_suggestions"] = list(self.dynamic_suggestions)
                    assistance["suggested_follow_up_questions"] = list(self.dynamic_suggestions)
                else:
                    # Phase 2U Gate: Before Phase 2U has confirmed any completed Q+A pair,
                    # suppress the generic LLM-generated suggested_follow_up_questions produced
                    # by generate_assistance(). They must NOT appear in the frontend prematurely.
                    # Follow-up questions are ONLY authoritative after:
                    #   detect_completed_qa() → qa_ready=True → _trigger_dynamic_suggestions_for_confirmed_qa()
                    # Once confirmed_qa_pairs is populated, dynamic_suggestions will be non-empty
                    # and the block above takes over as the authoritative source.
                    if not self.confirmed_qa_pairs:
                        assistance["suggested_follow_up_questions"] = []
                # Phase 2X: Lock static Scenario and Verification questions against overwrites
                if self.scenario_questions:
                    assistance["scenario_questions"] = list(self.scenario_questions)
                    assistance["suggested_practical_questions"] = list(self.scenario_questions)
                if self.verification_questions:
                    assistance["verification_questions"] = list(self.verification_questions)
                self.assistance = assistance

            # Phase 2U: Disconnected legacy candidate-turn finalization trigger.
            # Candidate-turn completion no longer directly triggers Q/A detection.
            # The 15-second checkpoint evaluates conversation state semantically.

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

    async def on_transcript_update(
        self,
        speaker: str,
        text: str,
        websocket: Any = None,
        source: str = "teams_native",
        allow_merge: bool = False,
        turn_id: Any = None,
        msg_id: Any = None,
        is_final: bool = True,
        speaker_role: str = None
    ) -> Dict[str, Any]:
        """Convenience method for transcript turn updates with explicit speaker role."""
        if speaker_role:
            self.session_speaker_roles[speaker] = speaker_role
        return await self.add_message(
            speaker=speaker,
            text=text,
            websocket=websocket,
            source=source,
            allow_merge=allow_merge,
            turn_id=turn_id,
            msg_id=msg_id,
            is_final=is_final
        )

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
        All LLM processing (Role identification, Evaluation, Intelligence, Assistance) runs asynchronously in parallel background task.
        """
        self.detected_speakers.add(speaker)
        clean_text = text.strip()
        if not clean_text:
            return {}

        curr_turn_id = turn_id if turn_id is not None else (len(self.transcript) + 1)
        entry_id = msg_id or f"{self.session_id}-turn-{curr_turn_id}"

        speaker_role = self.session_speaker_roles.get(speaker, "unknown")
        if speaker == "Candidate":
            speaker_role = "candidate"
            self.session_speaker_roles[speaker] = "candidate"
        elif speaker == "Interviewer":
            speaker_role = "interviewer"
            self.session_speaker_roles[speaker] = "interviewer"

        # Same-Speaker Utterance Stitching Engine (Merges rapid consecutive chunks if allow_merge is True)
        if allow_merge and self.transcript:
            last_entry = self.transcript[-1]
            if last_entry.get("speaker") == speaker:
                # Merge into existing message bubble
                last_entry["text"] = (last_entry.get("text", "") + " " + clean_text).strip()
                last_entry["timestamp"] = datetime.datetime.now().isoformat()
                last_entry["speaker_role"] = speaker_role
                if "source" not in last_entry:
                    last_entry["source"] = source
                if "id" not in last_entry:
                    last_entry["id"] = entry_id
                if "turn_id" not in last_entry:
                    last_entry["turn_id"] = curr_turn_id
                
                # Retrieve last question if candidate
                last_q = ""
                if speaker_role == "candidate" or speaker == "Candidate":
                    for msg in reversed(self.transcript[:-1]):
                        if msg.get("speaker_role") == "interviewer" or msg.get("speaker") == "Interviewer":
                            last_q = msg.get("text", "")
                            break
                            
                try:
                    await self.repo.save_session(self.session_id, {"transcript": self.transcript})
                except Exception as save_err:
                    logger.debug(f"Immediate transcript save warning: {save_err}")

                if is_final:
                    bg_task = asyncio.create_task(self._update_all_background_llm_tasks(last_entry, last_q, websocket))
                    self.background_tasks.add(bg_task)
                    bg_task.add_done_callback(self.background_tasks.discard)
                return last_entry

        # Retrieve the last interviewer question from transcript history
        last_question = ""
        if speaker_role == "candidate" or speaker == "Candidate":
            for msg in reversed(self.transcript):
                if msg.get("speaker_role") == "interviewer" or msg.get("speaker") == "Interviewer":
                    last_question = msg.get("text", "")
                    break

        # Check if updating an existing turn (e.g. from progressive extension or late arrival)
        for msg in self.transcript:
            if (turn_id is not None and msg.get("turn_id") == curr_turn_id) or (msg_id and msg.get("id") == entry_id):
                if not allow_merge or clean_text.startswith(msg.get("text", "")):
                    msg["text"] = clean_text
                elif not msg.get("text", "").endswith(clean_text):
                    msg["text"] = f"{msg.get('text', '')} {clean_text}".strip()
                msg["timestamp"] = datetime.datetime.now().isoformat()
                msg["speaker_role"] = speaker_role
                try:
                    await self.repo.save_session(self.session_id, {"transcript": self.transcript})
                except Exception as save_err:
                    logger.debug(f"Immediate transcript save warning: {save_err}")
                if is_final and not msg.get("_llm_evaluated"):
                    msg["_llm_evaluated"] = True
                    bg_task = asyncio.create_task(self._update_all_background_llm_tasks(msg, last_question, websocket))
                    self.background_tasks.add(bg_task)
                    bg_task.add_done_callback(self.background_tasks.discard)
                return msg

        message = {
            "id": entry_id,
            "turn_id": curr_turn_id,
            "speaker": speaker,
            "speaker_role": speaker_role,
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
            bg_task = asyncio.create_task(self._update_all_background_llm_tasks(message, last_question, websocket))
            self.background_tasks.add(bg_task)
            bg_task.add_done_callback(self.background_tasks.discard)

        return message

    def get_transcript(self) -> List[Dict[str, Any]]:
        """Returns the current transcript history list."""
        return self.transcript

    def get_intelligence(self) -> Dict[str, Any]:
        """Returns the current conversation intelligence state."""
        return self.intelligence

    def get_assistance(self) -> Dict[str, Any]:
        """Returns the current conversation copilot suggestions state."""
        if self.initial_suggestions:
            self.assistance["initial_suggestions"] = list(self.initial_suggestions)
        if self.dynamic_suggestions:
            self.assistance["dynamic_suggestions"] = list(self.dynamic_suggestions)
            self.assistance["suggested_follow_up_questions"] = list(self.dynamic_suggestions)
        if self.scenario_questions:
            self.assistance["scenario_questions"] = list(self.scenario_questions)
            self.assistance["suggested_practical_questions"] = list(self.scenario_questions)
        if self.verification_questions:
            self.assistance["verification_questions"] = list(self.verification_questions)
        return self.assistance

    def get_dynamic_suggestions(self) -> List[str]:
        """Returns the latest dynamic suggestions generated from the latest Q+A pair."""
        return self.dynamic_suggestions

    def get_initial_suggestions(self) -> List[str]:
        """Returns the exactly 2 initial suggestions generated at interview start."""
        return self.initial_suggestions

    def get_scenario_questions(self) -> List[str]:
        """Returns the exactly 5 static scenario questions generated once per session."""
        return self.scenario_questions

    def get_verification_questions(self) -> List[str]:
        """Returns the exactly 5 static verification questions generated once per session."""
        return self.verification_questions

    async def generate_initial_suggestions(self) -> List[str]:
        """
        Phase 2S: Generates exactly 2 initial interview question suggestions based on
        JD and Resume when the session enters active/IN_MEETING state.
        
        Guarantees:
        - Runs ONCE per session (generation_count = 1).
        - Checked against existing cache & persistence before calling LLM.
        - Exactly 2 suggestions returned.
        - Persisted to disk and database.
        - Broadcast to connected dashboard clients.
        - Completely non-blocking and safe against LLM errors.
        """
        # Guard 1: Already generated or populated in memory
        if self._initial_suggestions_generated or len(self.initial_suggestions) >= 2:
            logger.info(f"[Phase2S] Initial suggestions already available in memory for session {self.session_id}: {self.initial_suggestions}")
            return self.initial_suggestions

        # Guard 2: Already in progress (concurrency lock)
        if self._initial_suggestions_in_progress:
            logger.info(f"[Phase2S] Initial suggestions generation already in progress for session {self.session_id}")
            return self.initial_suggestions

        # Guard 3: Completed session or Service Off
        if getattr(self, "service_off", False):
            logger.info(f"[Phase2S] Session {self.session_id} is Service Off; skipping suggestion generation.")
            return self.initial_suggestions

        session_dir = os.path.join("interviews", self.session_id)
        if os.path.exists(os.path.join(session_dir, "service_off.flag")):
            logger.info(f"[Phase2S] Service Off flag detected for session {self.session_id}; skipping suggestion generation.")
            return self.initial_suggestions

        # Guard 4: Check if persisted on disk from earlier run
        persisted_file = os.path.join(session_dir, "initial_suggestions.json")
        if os.path.exists(persisted_file):
            try:
                with open(persisted_file, "r", encoding="utf-8") as pf:
                    data = json.load(pf)
                    loaded = data.get("initial_suggestions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    if len(loaded) >= 2:
                        self.initial_suggestions = loaded[:2]
                        self._initial_suggestions_generated = True
                        self.assistance["initial_suggestions"] = list(self.initial_suggestions)
                        if not self.assistance.get("suggested_follow_up_questions"):
                            self.assistance["suggested_follow_up_questions"] = list(self.initial_suggestions)
                        logger.info(f"[Phase2S] Loaded {len(self.initial_suggestions)} persisted initial suggestions for {self.session_id}")
                        return self.initial_suggestions
            except Exception as load_err:
                logger.warning(f"[Phase2S] Error loading persisted initial suggestions: {load_err}")

        # Execute single LLM generation
        self._initial_suggestions_in_progress = True
        try:
            self.initial_suggestions_generation_count += 1
            logger.info(
                f"[Phase2S] Generating initial suggestions for session {self.session_id} "
                f"(count={self.initial_suggestions_generation_count}) with JD ({len(self.jd)} chars) & Resume ({len(self.resume)} chars)..."
            )
            suggestions = await self.copilot_assistant.generate_initial_suggestions(
                jd=self.jd,
                resume=self.resume
            )

            # Slicing / validation: exactly 2 suggestions
            if len(suggestions) > 2:
                suggestions = suggestions[:2]

            self.initial_suggestions = suggestions
            self._initial_suggestions_generated = True

            # Populate in assistance structure
            self.assistance["initial_suggestions"] = list(suggestions)
            if not self.assistance.get("suggested_follow_up_questions"):
                self.assistance["suggested_follow_up_questions"] = list(suggestions)

            # Persist to disk and repository
            try:
                os.makedirs(session_dir, exist_ok=True)
                with open(persisted_file, "w", encoding="utf-8") as pf:
                    json.dump({
                        "session_id": self.session_id,
                        "initial_suggestions": self.initial_suggestions,
                        "generation_count": self.initial_suggestions_generation_count,
                        "generated_at": datetime.datetime.now().isoformat()
                    }, pf, indent=2)
            except Exception as disk_err:
                logger.warning(f"[Phase2S] Failed writing initial_suggestions.json: {disk_err}")

            try:
                await self.repo.save_session(self.session_id, {
                    "initial_suggestions": self.initial_suggestions,
                    "assistance": self.assistance
                })
            except Exception as repo_err:
                logger.warning(f"[Phase2S] Failed updating repo with initial suggestions: {repo_err}")

            logger.info(
                f"[Phase2S] Successfully finalized initial suggestions for session {self.session_id}: "
                f"{self.initial_suggestions} (generation_count={self.initial_suggestions_generation_count})"
            )

            # Broadcast updated state to all connected dashboards
            if getattr(self, "on_update_callback", None):
                try:
                    res = self.on_update_callback()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as cb_err:
                    logger.debug(f"[Phase2S] on_update_callback notification: {cb_err}")

            return self.initial_suggestions

        except Exception as gen_err:
            logger.error(f"[Phase2S] Unexpected error generating initial suggestions: {gen_err}")
            return self.initial_suggestions
        finally:
            self._initial_suggestions_in_progress = False

    async def generate_static_scenario_verification_questions(self) -> Dict[str, List[str]]:
        """
        Phase 2X: Generates exactly 5 Scenario questions and exactly 5 Verification questions
        based purely on the Job Description and Candidate Resume.
        
        Guarantees:
        - Generated ONCE per session (static_questions_generation_count = 1).
        - Checked against in-memory lock, existing cache & persistence before calling LLM.
        - Uses ONLY JD and Resume (no transcript or conversation history).
        - Exactly 5 Scenario + exactly 5 Verification questions locked.
        - Persisted to disk and database.
        - Broadcast to connected dashboard clients.
        - Never regenerated by transcript, candidate answers, accuracy evaluation, or reconnects.
        """
        # Guard 1: Already generated and locked in memory
        if self.static_questions_generated and len(self.scenario_questions) == 5 and len(self.verification_questions) == 5:
            logger.info(f"[Phase2X] Static questions already locked in memory for session {self.session_id}")
            return {
                "scenario_questions": list(self.scenario_questions),
                "verification_questions": list(self.verification_questions)
            }

        # Guard 2: Already in progress (concurrency lock)
        if self._static_questions_in_progress:
            logger.info(f"[Phase2X] Static questions generation already in progress for session {self.session_id}")
            return {
                "scenario_questions": list(self.scenario_questions),
                "verification_questions": list(self.verification_questions)
            }

        # Guard 3: Completed session or Service Off
        if getattr(self, "service_off", False):
            logger.info(f"[Phase2X] Session {self.session_id} is Service Off; skipping static question generation.")
            return {"scenario_questions": [], "verification_questions": []}

        session_dir = os.path.join("interviews", str(self.session_id))
        if os.path.exists(os.path.join(session_dir, "service_off.flag")):
            logger.info(f"[Phase2X] Service Off flag detected for session {self.session_id}; skipping static question generation.")
            return {"scenario_questions": [], "verification_questions": []}

        # Guard 4: Check if persisted on disk from earlier run
        persisted_file = os.path.join(session_dir, "static_questions.json")
        if os.path.exists(persisted_file):
            try:
                with open(persisted_file, "r", encoding="utf-8") as pf:
                    data = json.load(pf)
                    sc_q = data.get("scenario_questions", [])
                    ver_q = data.get("verification_questions", [])
                    if len(sc_q) == 5 and len(ver_q) == 5:
                        self.scenario_questions = list(sc_q)
                        self.verification_questions = list(ver_q)
                        self.static_questions_generated = True
                        self.assistance["scenario_questions"] = list(sc_q)
                        self.assistance["suggested_practical_questions"] = list(sc_q)
                        self.assistance["verification_questions"] = list(ver_q)
                        logger.info(f"[Phase2X] Loaded 5+5 persisted static questions for {self.session_id}")
                        return {
                            "scenario_questions": list(self.scenario_questions),
                            "verification_questions": list(self.verification_questions)
                        }
            except Exception as load_err:
                logger.warning(f"[Phase2X] Error loading persisted static questions: {load_err}")

        # Execute single LLM generation using ONLY JD and Resume
        self._static_questions_in_progress = True
        try:
            self.static_questions_generation_count += 1
            logger.info(
                f"[Phase2X] Generating static scenario & verification questions for session {self.session_id} "
                f"(count={self.static_questions_generation_count}) using ONLY JD ({len(self.jd)} chars) & Resume ({len(self.resume)} chars)..."
            )
            result = await self.copilot_assistant.generate_static_scenario_verification_questions(
                jd=self.jd,
                resume=self.resume
            )
            sc_q = result.get("scenario_questions", [])
            ver_q = result.get("verification_questions", [])

            # Validation: exactly 5 for both categories required
            if len(sc_q) != 5 or len(ver_q) != 5:
                logger.error(
                    f"[Phase2X] Static question generation failed validation: received {len(sc_q)} scenario and {len(ver_q)} verification questions. "
                    f"Must be exactly 5 each. Generation will not be marked successful."
                )
                return {
                    "scenario_questions": [],
                    "verification_questions": []
                }

            self.scenario_questions = list(sc_q)
            self.verification_questions = list(ver_q)
            self.static_questions_generated = True

            # Populate in assistance structure and lock
            self.assistance["scenario_questions"] = list(self.scenario_questions)
            self.assistance["suggested_practical_questions"] = list(self.scenario_questions)
            self.assistance["verification_questions"] = list(self.verification_questions)

            # Persist to disk and repository
            try:
                os.makedirs(session_dir, exist_ok=True)
                with open(persisted_file, "w", encoding="utf-8") as pf:
                    json.dump({
                        "session_id": str(self.session_id),
                        "scenario_questions": self.scenario_questions,
                        "verification_questions": self.verification_questions,
                        "generation_count": self.static_questions_generation_count,
                        "generated_at": datetime.datetime.now().isoformat()
                    }, pf, indent=2)
            except Exception as disk_err:
                logger.warning(f"[Phase2X] Failed writing static_questions.json: {disk_err}")

            try:
                await self.repo.save_session(self.session_id, {
                    "scenario_questions": self.scenario_questions,
                    "verification_questions": self.verification_questions,
                    "assistance": self.assistance
                })
            except Exception as repo_err:
                logger.warning(f"[Phase2X] Failed updating repo with static questions: {repo_err}")

            logger.info(
                f"[Phase2X] Successfully finalized static questions for session {self.session_id}: "
                f"5 scenario, 5 verification (generation_count={self.static_questions_generation_count})"
            )

            # Broadcast updated state to all connected dashboards
            if getattr(self, "on_update_callback", None):
                try:
                    res = self.on_update_callback()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as cb_err:
                    logger.debug(f"[Phase2X] on_update_callback notification: {cb_err}")

            return {
                "scenario_questions": list(self.scenario_questions),
                "verification_questions": list(self.verification_questions)
            }

        except Exception as gen_err:
            logger.error(f"[Phase2X] Unexpected error generating static questions: {gen_err}")
            return {
                "scenario_questions": [],
                "verification_questions": []
            }
        finally:
            self._static_questions_in_progress = False

    def _find_latest_qa_pair(self) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
        """
        Finds the latest finalized candidate turn and the preceding interviewer question turn.
        Enforces that it is strictly an interviewer -> candidate exchange (no intervening candidate turns).
        Returns (interviewer_msg, candidate_msg, pair_id) or None if no valid pair exists.
        """
        candidate_msg = None
        cand_idx = -1
        # Search backward for the latest candidate turn
        for idx in range(len(self.transcript) - 1, -1, -1):
            t = self.transcript[idx]
            role = t.get("speaker_role")
            spk = t.get("speaker")
            if (role == "candidate" or spk == "Candidate") and t.get("text", "").strip():
                candidate_msg = t
                cand_idx = idx
                break

        if not candidate_msg or cand_idx <= 0:
            return None

        # Search backward before candidate turn for preceding interviewer turn
        interviewer_msg = None
        interviewer_idx = -1
        for idx in range(cand_idx - 1, -1, -1):
            t = self.transcript[idx]
            role = t.get("speaker_role")
            spk = t.get("speaker")
            if (role == "interviewer" or spk == "Interviewer") and t.get("text", "").strip():
                interviewer_msg = t
                interviewer_idx = idx
                break

        if not interviewer_msg:
            return None

        # Requirement 8: Ensure there is no intervening candidate turn between interviewer and candidate
        for idx in range(interviewer_idx + 1, cand_idx):
            t = self.transcript[idx]
            role = t.get("speaker_role")
            spk = t.get("speaker")
            if (role == "candidate" or spk == "Candidate") and t.get("text", "").strip():
                # Intervening candidate turn found -> candidate -> candidate, not interviewer -> candidate
                return None

        q_id = interviewer_msg.get("turn_id") if interviewer_msg.get("turn_id") is not None else interviewer_msg.get("id")
        a_id = candidate_msg.get("turn_id") if candidate_msg.get("turn_id") is not None else candidate_msg.get("id")
        pair_id = f"Q{q_id}_A{a_id}"

        return (interviewer_msg, candidate_msg, pair_id)

    async def _check_and_trigger_dynamic_suggestions(self, websocket: Any = None):
        """
        Phase 2T: Identifies any unaddressed logical interviewer-question + candidate-answer
        pair in the finalized transcript and triggers generation of exactly 2 dynamic suggestions.
        """
        # Guard: Completed session or Service Off
        if getattr(self, "service_off", False):
            return
        session_dir = os.path.join("interviews", self.session_id)
        if os.path.exists(os.path.join(session_dir, "service_off.flag")):
            return

        qa_pair = self._find_latest_qa_pair()
        if not qa_pair:
            return

        interviewer_msg, candidate_msg, pair_id = qa_pair

        # Guard: Exactly once per logical Q+A pair
        if pair_id in self.processed_qa_pairs:
            return

        # Mark processed immediately to prevent duplicate runs
        self.processed_qa_pairs.add(pair_id)

        # Increment sequence number to prevent race conditions from older async tasks
        self.dynamic_suggestions_sequence += 1
        task_seq = self.dynamic_suggestions_sequence

        interviewer_q = interviewer_msg.get("text", "").strip()
        candidate_a = candidate_msg.get("text", "").strip()

        logger.info(
            f"[Phase2T] Triggering dynamic suggestions for pair {pair_id} (seq={task_seq}): "
            f"Q='{interviewer_q[:40]}...' A='{candidate_a[:40]}...'"
        )

        async def _run_dynamic_generation():
            try:
                start_time = time.time()
                suggestions = await self.copilot_assistant.generate_dynamic_suggestions(
                    jd=self.jd,
                    resume=self.resume,
                    interviewer_question=interviewer_q,
                    candidate_answer=candidate_a
                )

                if len(suggestions) > 2:
                    suggestions = suggestions[:2]

                # Race condition guard: ignore if a newer task sequence has already completed
                if task_seq < self.latest_completed_suggestion_seq:
                    logger.info(f"[Phase2T] Discarding stale suggestions for pair {pair_id} (task_seq={task_seq} < latest={self.latest_completed_suggestion_seq})")
                    return

                self.latest_completed_suggestion_seq = task_seq
                self.dynamic_suggestions = suggestions

                # Update assistance structure
                self.assistance["dynamic_suggestions"] = list(suggestions)
                self.assistance["suggested_follow_up_questions"] = list(suggestions)
                if self.initial_suggestions:
                    self.assistance["initial_suggestions"] = list(self.initial_suggestions)

                duration = time.time() - start_time
                record = {
                    "pair_id": pair_id,
                    "sequence": task_seq,
                    "interviewer_turn_id": interviewer_msg.get("turn_id"),
                    "candidate_turn_id": candidate_msg.get("turn_id"),
                    "interviewer_text": interviewer_q,
                    "candidate_text": candidate_a,
                    "suggestions": suggestions,
                    "duration_seconds": round(duration, 2),
                    "timestamp": datetime.datetime.now().isoformat()
                }
                self.dynamic_suggestions_history.append(record)

                # Persist to disk and repository
                try:
                    os.makedirs(session_dir, exist_ok=True)
                    dyn_file = os.path.join(session_dir, "dynamic_suggestions.json")
                    with open(dyn_file, "w", encoding="utf-8") as f:
                        json.dump({
                            "session_id": self.session_id,
                            "latest_pair_id": pair_id,
                            "latest_suggestions": suggestions,
                            "history": self.dynamic_suggestions_history
                        }, f, indent=2)
                except Exception as disk_err:
                    logger.warning(f"[Phase2T] Error saving dynamic_suggestions.json: {disk_err}")

                try:
                    await self.repo.save_session(self.session_id, {
                        "dynamic_suggestions": suggestions,
                        "assistance": self.assistance
                    })
                except Exception as repo_err:
                    logger.warning(f"[Phase2T] Error updating repo with dynamic suggestions: {repo_err}")

                logger.info(f"[Phase2T] Dynamic suggestions ready for pair {pair_id} in {duration:.2f}s: {suggestions}")

                # Broadcast to connected dashboard clients
                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "copilot_update",
                            "session_id": self.session_id,
                            "transcript": self.transcript,
                            "intelligence": self.intelligence,
                            "assistance": self.assistance,
                            "dynamic_suggestions": suggestions
                        })
                    except Exception as ws_err:
                        logger.debug(f"[Phase2T] WebSocket push error: {ws_err}")

                if getattr(self, "on_update_callback", None):
                    try:
                        res = self.on_update_callback()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as cb_err:
                        logger.debug(f"[Phase2T] on_update_callback notification: {cb_err}")

            except Exception as dyn_err:
                logger.error(f"[Phase2T] Error generating dynamic suggestions: {dyn_err}")

        # Launch in background task and track in background_tasks
        dyn_task = asyncio.create_task(_run_dynamic_generation())
        self.background_tasks.add(dyn_task)
        dyn_task.add_done_callback(self.background_tasks.discard)
        return dyn_task

    async def trigger_dynamic_suggestions(self, websocket: Any = None):
        """Public alias to trigger dynamic suggestions checking (legacy)."""
        return await self._check_and_trigger_dynamic_suggestions(websocket=websocket)

    # -------------------------------------------------------------
    # Phase 2U: 15-Second LLM Q/A Checkpoint Engine
    # -------------------------------------------------------------

    def start_qa_checkpoint(self, websocket: Any = None):
        """
        Starts the session-scoped 15-second Q/A checkpoint loop if not already running.
        Idempotent: guarantees only one checkpoint loop runs per active session.
        """
        # Guard: Service Off or completed session
        if getattr(self, "service_off", False):
            return
        session_dir = os.path.join("interviews", self.session_id)
        if os.path.exists(os.path.join(session_dir, "service_off.flag")):
            return

        if self.qa_checkpoint_running and self.qa_checkpoint_task and not self.qa_checkpoint_task.done():
            return

        self.qa_checkpoint_running = True
        self.qa_checkpoint_task = asyncio.create_task(self._run_qa_checkpoint_loop(websocket))
        self.background_tasks.add(self.qa_checkpoint_task)
        self.qa_checkpoint_task.add_done_callback(self.background_tasks.discard)
        logger.info(f"[QA_CHECKPOINT] Started 15-second checkpoint loop for session {self.session_id}")

    def stop_qa_checkpoint(self):
        """
        Stops and cancels the 15-second Q/A checkpoint loop cleanly.
        Idempotent.
        """
        self.qa_checkpoint_running = False
        if self.qa_checkpoint_task and not self.qa_checkpoint_task.done():
            self.qa_checkpoint_task.cancel()
            logger.info(f"[QA_CHECKPOINT] Cancelled checkpoint task for session {self.session_id}")
        self.qa_checkpoint_task = None

        # Phase 2V: Clean up any active evaluation tasks safely
        for qa_id, task in list(self.active_evaluation_tasks.items()):
            if not task.done():
                task.cancel()
        self.active_evaluation_tasks.clear()

    async def _run_qa_checkpoint_loop(self, websocket: Any = None):
        """
        Session-scoped background loop that ticks every 15 seconds.
        Evaluates whether meaningful new conversation has arrived, and if so,
        invokes the completed-Q/A detector LLM.
        """
        try:
            while self.qa_checkpoint_running:
                await asyncio.sleep(self.qa_checkpoint_interval)

                if not self.qa_checkpoint_running:
                    break

                # Check for Service Off or completion flags
                if getattr(self, "service_off", False) or os.path.exists(os.path.join("interviews", self.session_id, "service_off.flag")):
                    logger.info(f"[QA_CHECKPOINT] Service off detected for session {self.session_id}. Stopping loop.")
                    self.stop_qa_checkpoint()
                    break

                await self.run_qa_checkpoint_cycle(websocket=websocket)
        except asyncio.CancelledError:
            logger.debug(f"[QA_CHECKPOINT] Checkpoint loop cancelled for session {self.session_id}")
        except Exception as e:
            logger.error(f"[QA_CHECKPOINT] Unexpected error in checkpoint loop: {e}")
        finally:
            self.qa_checkpoint_running = False

    async def run_qa_checkpoint_cycle(self, websocket: Any = None) -> Optional[Dict[str, Any]]:
        """
        Executes a single 15-second checkpoint evaluation.
        Protected by self._qa_checkpoint_lock to prevent concurrent executions.
        """
        if self._qa_checkpoint_lock.locked():
            logger.debug(f"[QA_CHECKPOINT] Checkpoint cycle already in progress for session {self.session_id}, skipping concurrent tick.")
            return None

        async with self._qa_checkpoint_lock:
            self.checkpoint_number += 1
            self.last_checkpoint_at = datetime.datetime.now().isoformat()

            # 1. Optimization: Check whether meaningful new conversation has appeared
            current_transcript = list(self.transcript)
            total_turns = len(current_transcript)

            has_new_turns = total_turns > self.last_qa_checkpoint_turn_count
            valid_new_turns = False
            if has_new_turns:
                new_turns_slice = current_transcript[self.last_qa_checkpoint_turn_count:]
                valid_new_turns = any(bool(t.get("text", "").strip()) for t in new_turns_slice)

            logger.info(f"[QA_CHECKPOINT] session_id={self.session_id} new_turns={valid_new_turns}")

            if not valid_new_turns:
                logger.info(f"[QA_CHECKPOINT_SKIP] reason=no_new_conversation")
                return None

            # Update checkpoint turn progress marker
            self.last_qa_checkpoint_turn_count = total_turns
            if current_transcript:
                self.last_seen_turn_id = current_transcript[-1].get("turn_id")

            # 2. Call Q/A Decision LLM
            decision = await self.detect_completed_qa(current_transcript)
            self.last_qa_decision = decision

            if not decision or not decision.get("qa_ready"):
                reason = decision.get("reason", "unknown") if decision else "no_response"
                logger.info(f"[QA_NOT_READY] session_id={self.session_id} reason={reason}")
                return decision

            # 3. Strict Turn Validation
            is_valid, validation_reason, q_turn, a_turns = self._validate_qa_decision(decision, current_transcript)
            if not is_valid:
                logger.warning(f"[QA_VALIDATION_FAILED] session_id={self.session_id} reason={validation_reason}")
                decision["qa_ready"] = False
                decision["validation_error"] = validation_reason
                return decision

            # 4. Deduplication
            q_id = q_turn.get("turn_id") if q_turn.get("turn_id") is not None else q_turn.get("id")
            a_ids = [t.get("turn_id") if t.get("turn_id") is not None else t.get("id") for t in a_turns]
            pair_id = f"Q{q_id}_A{'_'.join(str(x) for x in a_ids)}"

            if pair_id in self.processed_qa_pairs:
                logger.info(f"[QA_CHECKPOINT_SKIP] session_id={self.session_id} pair_id={pair_id} reason=already_processed")
                return decision

            # Mark processed
            self.processed_qa_pairs.add(pair_id)
            self.last_processed_question_id = q_id
            self.last_processed_answer_id = a_ids[-1]

            logger.info(
                f"[QA_CONFIRMED] session_id={self.session_id} question_turn_id={q_id} answer_turn_ids={a_ids}"
            )

            confirmed_qa_record = {
                "pair_id": pair_id,
                "qa_id": pair_id,
                "question_turn_id": q_id,
                "answer_turn_ids": a_ids,
                "question": decision.get("question") or q_turn.get("text", ""),
                "answer": decision.get("answer") or " ".join(t.get("text", "") for t in a_turns),
                "confidence": decision.get("confidence", 1.0),
                "reason": decision.get("reason", ""),
                "checkpoint_number": self.checkpoint_number,
                "confirmed_at": datetime.datetime.now().isoformat()
            }
            self.confirmed_qa_pairs.append(confirmed_qa_record)

            # 5. Pass to existing dynamic suggestion generation flow
            await self._trigger_dynamic_suggestions_for_confirmed_qa(
                pair_id=pair_id,
                interviewer_q=confirmed_qa_record["question"],
                candidate_a=confirmed_qa_record["answer"],
                interviewer_turn_id=q_id,
                candidate_turn_id=a_ids[-1],
                websocket=websocket
            )

            # 6. Phase 2V: Trigger Answer Accuracy Evaluation for the confirmed Q/A pair
            try:
                await self._trigger_accuracy_evaluation_for_confirmed_qa(
                    question=confirmed_qa_record["question"],
                    answer=confirmed_qa_record["answer"],
                    confirmed_qa_record=confirmed_qa_record,
                    websocket=websocket
                )
            except Exception as eval_err:
                logger.error(f"[Phase2V] Error triggering accuracy evaluation for confirmed Q/A: {eval_err}")

            return decision

    def _validate_qa_decision(
        self,
        decision: Dict[str, Any],
        transcript: List[Dict[str, Any]]
    ) -> Tuple[bool, str, Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Validates LLM-generated Q/A decision against actual transcript turns.
        Rules:
        1. question_turn_id must exist in current transcript.
        2. Every answer_turn_id must exist in current transcript.
        3. question turn must belong to Interviewer.
        4. All answer turns must belong to Candidate.
        5. Answer turns must occur chronologically after question turn.
        6. Answer turns must not already belong to an existing confirmed Q/A pair.
        Returns (is_valid, reason, question_turn, answer_turns).
        """
        raw_q_id = decision.get("question_turn_id")
        raw_a_ids = decision.get("answer_turn_ids")

        if raw_q_id is None:
            return False, "Missing question_turn_id", None, []

        if not raw_a_ids or not isinstance(raw_a_ids, list):
            return False, "answer_turn_ids must be a non-empty list", None, []

        # Build turn map and index map
        turn_map = {}
        index_map = {}
        for idx, t in enumerate(transcript):
            tid = t.get("turn_id")
            mid = t.get("id")
            if tid is not None:
                turn_map[str(tid)] = t
                index_map[str(tid)] = idx
            if mid:
                turn_map[str(mid)] = t
                index_map[str(mid)] = idx

        q_key = str(raw_q_id)
        if q_key not in turn_map:
            return False, f"question_turn_id '{raw_q_id}' not found in transcript", None, []

        q_turn = turn_map[q_key]
        q_idx = index_map[q_key]

        # Check question speaker role
        q_role = q_turn.get("speaker_role")
        q_spk = q_turn.get("speaker")
        if q_role != "interviewer" and q_spk != "Interviewer":
            return False, f"Question turn '{raw_q_id}' is not from Interviewer (role={q_role}, speaker={q_spk})", None, []

        # Check previous confirmed answer turns to ensure no reuse
        already_confirmed_a_ids = set()
        for prev in self.confirmed_qa_pairs:
            for prev_a in prev.get("answer_turn_ids", []):
                already_confirmed_a_ids.add(str(prev_a))

        # Validate answer turns
        a_turns = []
        for a_id in raw_a_ids:
            a_key = str(a_id)
            if a_key not in turn_map:
                return False, f"answer_turn_id '{a_id}' not found in transcript", None, []

            a_turn = turn_map[a_key]
            a_idx = index_map[a_key]

            # Check chronological ordering: answer turn must come after question turn
            if a_idx <= q_idx:
                return False, f"Answer turn '{a_id}' (index {a_idx}) does not occur after question turn (index {q_idx})", None, []

            # Check candidate role
            a_role = a_turn.get("speaker_role")
            a_spk = a_turn.get("speaker")
            if a_role != "candidate" and a_spk != "Candidate":
                return False, f"Answer turn '{a_id}' is not from Candidate (role={a_role}, speaker={a_spk})", None, []

            # Check answer turn reuse
            if a_key in already_confirmed_a_ids:
                return False, f"Answer turn '{a_id}' already belongs to a confirmed Q/A pair", None, []

            a_turns.append(a_turn)

        return True, "Valid", q_turn, a_turns

    async def detect_completed_qa(
        self,
        transcript: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Phase 2U: Dedicated completed-Q/A detector using DeepSeek LLM.
        Determines semantically whether an Interviewer Question + Candidate Answer pair is complete.
        """
        active_transcript = transcript if transcript is not None else self.transcript
        if not active_transcript:
            return {
                "qa_ready": False,
                "question_turn_id": None,
                "answer_turn_ids": [],
                "question": "",
                "answer": "",
                "confidence": 0.0,
                "reason": "No transcript available."
            }

        # Format recent transcript turns
        recent_turns = active_transcript[-25:] if len(active_transcript) > 25 else active_transcript
        turns_repr = []
        for t in recent_turns:
            t_id = t.get("turn_id") if t.get("turn_id") is not None else t.get("id")
            spk = t.get("speaker", "Unknown")
            role = t.get("speaker_role", "unknown")
            text = (t.get("text") or "").strip()
            turns_repr.append(f"Turn ID [{t_id}] | Speaker: {spk} | Role: {role} | Text: \"{text}\"")
        turns_text = "\n".join(turns_repr)

        # Build list of previously confirmed pair summaries
        if self.confirmed_qa_pairs:
            prev_summaries = []
            for p in self.confirmed_qa_pairs[-5:]:
                prev_summaries.append(
                    f"- Pair ID: {p.get('pair_id')} (Q turn: {p.get('question_turn_id')}, A turns: {p.get('answer_turn_ids')})"
                )
            previously_processed_summary = "\n".join(prev_summaries)
        else:
            previously_processed_summary = "None"

        # Known participants
        participants_summary = ", ".join(
            f"{spk} ({self.session_speaker_roles.get(spk, 'unknown')})"
            for spk in sorted(self.detected_speakers) if spk and spk != "System"
        ) or "None detected"

        prompt = f"""You are an expert conversational analyst evaluating a live technical interview.
Analyze the following recent interview conversation and determine if a NEW completed Interviewer Question + Candidate Answer pair has occurred.

Target Job Description:
{self.jd.strip() if self.jd else "N/A"}

Candidate Resume:
{self.resume.strip() if self.resume else "N/A"}

Known Participants and Roles:
{participants_summary}

Previously Processed Q/A Pairs (DO NOT re-detect these):
{previously_processed_summary}

Recent Conversation Turns:
{turns_text}

CRITICAL RULES:
1. An Interviewer asks technical questions, and a Candidate provides answers.
2. A Candidate answer may span MULTIPLE transcript turns (e.g. if the candidate took a 4-5 second thinking pause, spoke in consecutive fragments, or gave a multi-part answer). If multiple candidate turns belong to the same answer, list ALL of them in "answer_turn_ids".
3. Conversational fillers like "uh", "umm", "let me think", "yeah" do NOT mean the answer is complete. Wait until a substantive answer is provided.
4. A 3-second or 4-5 second pause is NOT proof that an answer is complete. Determine completion from grammar, conversational meaning, and topic resolution.
5. If the candidate is still in the middle of answering (sentence unfinished, thought trailing off, or only gave a filler), qa_ready MUST be false.
6. If the interviewer just asked a question and the candidate has not answered yet, qa_ready MUST be false.
7. Do NOT re-identify any Q/A pair that was already processed.
8. The "question_turn_id" must be the EXACT Turn ID of the interviewer's question from the conversation turns above.
9. Every ID in "answer_turn_ids" must be an EXACT Turn ID of the candidate's answer from the conversation turns above. Never invent or hallucinate Turn IDs.
10. If no completed Q/A pair is ready, set "qa_ready": false.

Return ONLY valid JSON matching this schema:
{{
  "qa_ready": true,
  "question_turn_id": <int or str from conversation>,
  "answer_turn_ids": [<array of ints or strs from conversation>],
  "question": "<the actual interviewer question>",
  "answer": "<the consolidated candidate answer>",
  "confidence": <float between 0.0 and 1.0>,
  "reason": "<explanation why this Q+A is complete>"
}}

Or when no complete Q/A exists:
{{
  "qa_ready": false,
  "question_turn_id": null,
  "answer_turn_ids": [],
  "question": "",
  "answer": "",
  "confidence": 0.0,
  "reason": "<explanation why no complete Q+A was detected>"
}}
"""

        logger.info(f"[QA_DETECTOR] session_id={self.session_id} turn_count={len(recent_turns)}")

        try:
            chat_completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            raw_content = chat_completion.choices[0].message.content or "{}"
            decision = clean_json_loads(raw_content)
            if not isinstance(decision, dict):
                return {
                    "qa_ready": False,
                    "question_turn_id": None,
                    "answer_turn_ids": [],
                    "question": "",
                    "answer": "",
                    "confidence": 0.0,
                    "reason": "LLM returned non-dict JSON"
                }
            return decision
        except Exception as e:
            logger.error(f"[QA_DETECTOR] Error calling Q/A LLM: {e}")
            return {
                "qa_ready": False,
                "question_turn_id": None,
                "answer_turn_ids": [],
                "question": "",
                "answer": "",
                "confidence": 0.0,
                "reason": f"LLM error: {str(e)}"
            }

    async def _trigger_dynamic_suggestions_for_confirmed_qa(
        self,
        pair_id: str,
        interviewer_q: str,
        candidate_a: str,
        interviewer_turn_id: Any,
        candidate_turn_id: Any,
        websocket: Any = None
    ):
        """
        Phase 2U: Generates dynamic suggestions for a confirmed Q/A pair
        validated by the 15-second checkpoint LLM.
        """
        # Guard: Completed session or Service Off
        if getattr(self, "service_off", False):
            return
        session_dir = os.path.join("interviews", self.session_id)
        if os.path.exists(os.path.join(session_dir, "service_off.flag")):
            return

        self.dynamic_suggestions_sequence += 1
        task_seq = self.dynamic_suggestions_sequence

        logger.info(
            f"[Phase2U] Triggering dynamic suggestions for confirmed pair {pair_id} (seq={task_seq}): "
            f"Q='{interviewer_q[:40]}...' A='{candidate_a[:40]}...'"
        )

        async def _run_dynamic_generation():
            try:
                start_time = time.time()
                suggestions = await self.copilot_assistant.generate_dynamic_suggestions(
                    jd=self.jd,
                    resume=self.resume,
                    interviewer_question=interviewer_q,
                    candidate_answer=candidate_a
                )

                if len(suggestions) > 2:
                    suggestions = suggestions[:2]

                # Race condition guard: ignore if a newer task sequence has already completed
                if task_seq < self.latest_completed_suggestion_seq:
                    logger.info(f"[Phase2U] Discarding stale suggestions for pair {pair_id} (task_seq={task_seq} < latest={self.latest_completed_suggestion_seq})")
                    return

                self.latest_completed_suggestion_seq = task_seq
                self.dynamic_suggestions = suggestions

                # Update assistance structure
                self.assistance["dynamic_suggestions"] = list(suggestions)
                self.assistance["suggested_follow_up_questions"] = list(suggestions)
                if self.initial_suggestions:
                    self.assistance["initial_suggestions"] = list(self.initial_suggestions)

                duration = time.time() - start_time
                record = {
                    "pair_id": pair_id,
                    "sequence": task_seq,
                    "interviewer_turn_id": interviewer_turn_id,
                    "candidate_turn_id": candidate_turn_id,
                    "interviewer_text": interviewer_q,
                    "candidate_text": candidate_a,
                    "suggestions": suggestions,
                    "duration_seconds": round(duration, 2),
                    "timestamp": datetime.datetime.now().isoformat()
                }
                self.dynamic_suggestions_history.append(record)

                # Persist to disk and repository
                try:
                    os.makedirs(session_dir, exist_ok=True)
                    dyn_file = os.path.join(session_dir, "dynamic_suggestions.json")
                    with open(dyn_file, "w", encoding="utf-8") as f:
                        json.dump({
                            "session_id": self.session_id,
                            "latest_pair_id": pair_id,
                            "latest_suggestions": suggestions,
                            "history": self.dynamic_suggestions_history
                        }, f, indent=2)
                except Exception as disk_err:
                    logger.warning(f"[Phase2U] Error saving dynamic_suggestions.json: {disk_err}")

                try:
                    await self.repo.save_session(self.session_id, {
                        "dynamic_suggestions": suggestions,
                        "assistance": self.assistance
                    })
                except Exception as repo_err:
                    logger.warning(f"[Phase2U] Error updating repo with dynamic suggestions: {repo_err}")

                logger.info(f"[Phase2U] Dynamic suggestions ready for pair {pair_id} in {duration:.2f}s: {suggestions}")

                # Broadcast to connected dashboard clients
                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "copilot_update",
                            "session_id": self.session_id,
                            "transcript": self.transcript,
                            "intelligence": self.intelligence,
                            "assistance": self.assistance,
                            "dynamic_suggestions": suggestions
                        })
                    except Exception as ws_err:
                        logger.debug(f"[Phase2U] WebSocket push error: {ws_err}")

                if getattr(self, "on_update_callback", None):
                    try:
                        res = self.on_update_callback()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as cb_err:
                        logger.debug(f"[Phase2U] on_update_callback notification: {cb_err}")

            except Exception as dyn_err:
                logger.error(f"[Phase2U] Error generating dynamic suggestions: {dyn_err}")

        # Launch in background task and track in background_tasks
        dyn_task = asyncio.create_task(_run_dynamic_generation())
        self.background_tasks.add(dyn_task)
        dyn_task.add_done_callback(self.background_tasks.discard)
        return dyn_task

    # -------------------------------------------------------------
    # Phase 2V: Confirmed Q+A Answer Accuracy Evaluation Engine
    # -------------------------------------------------------------

    async def _trigger_accuracy_evaluation_for_confirmed_qa(
        self,
        question: str,
        answer: str,
        confirmed_qa_record: Dict[str, Any],
        websocket: Any = None
    ) -> Optional[asyncio.Task]:
        """
        Phase 2V: Triggers LLM Answer Accuracy Evaluation strictly for confirmed Q+A pairs.
        Evaluates question + answer + resume (zero Job Description).
        Calculates deterministic 4-component weighted accuracy score (0-100%).
        Guarantees:
        - Exactly-once evaluation per confirmed Q+A pair.
        - Non-blocking execution via background asyncio task.
        - Safe error handling (never outputs fake 0% score on LLM failure).
        - Broadcasts 'qa_evaluated' event over WebSocket with {"qa_id": ..., "accuracy_score": ...}.
        """
        qa_id = confirmed_qa_record.get("qa_id") or confirmed_qa_record.get("pair_id")
        if not qa_id:
            logger.warning("[Phase2V] Cannot evaluate Q/A without qa_id/pair_id.")
            return None

        # Guard 1: Service OFF
        if getattr(self, "service_off", False):
            logger.info(f"[Phase2V] Skipping accuracy evaluation for session {self.session_id}: Service Off.")
            return None
        session_dir = os.path.join("interviews", str(self.session_id))
        if os.path.exists(os.path.join(session_dir, "service_off.flag")):
            logger.info(f"[Phase2V] Skipping accuracy evaluation for session {self.session_id}: service_off.flag exists.")
            return None

        # Guard 2: Already successfully evaluated or already has accuracy_score
        if qa_id in self.evaluated_qa_ids or "accuracy_score" in confirmed_qa_record:
            logger.info(f"[Phase2V] Q/A {qa_id} already evaluated. Skipping duplicate evaluation.")
            return None

        # Guard 3: Evaluation currently in-flight for this qa_id
        if qa_id in self.active_evaluation_tasks and not self.active_evaluation_tasks[qa_id].done():
            logger.info(f"[Phase2V] Q/A {qa_id} evaluation already in progress. Skipping.")
            return None

        if websocket:
            self.active_websocket = websocket

        # Launch background evaluation task
        eval_task = asyncio.create_task(
            self._run_accuracy_evaluation_task(
                qa_id=qa_id,
                question=question,
                answer=answer,
                confirmed_qa_record=confirmed_qa_record,
                websocket=websocket
            )
        )
        self.active_evaluation_tasks[qa_id] = eval_task
        self.background_tasks.add(eval_task)
        eval_task.add_done_callback(lambda t: self._cleanup_evaluation_task(qa_id, t))
        return eval_task

    def _cleanup_evaluation_task(self, qa_id: str, task: asyncio.Task):
        self.active_evaluation_tasks.pop(qa_id, None)
        self.background_tasks.discard(task)

    async def _run_accuracy_evaluation_task(
        self,
        qa_id: str,
        question: str,
        answer: str,
        confirmed_qa_record: Dict[str, Any],
        websocket: Any = None
    ):
        try:
            # Guard against Service OFF during task execution
            if getattr(self, "service_off", False) or os.path.exists(os.path.join("interviews", str(self.session_id), "service_off.flag")):
                logger.info(f"[Phase2V] Service OFF during evaluation of {qa_id}. Aborting.")
                return

            result = await self.evaluation_service.evaluate_accuracy(
                question=question,
                answer=answer,
                resume=self.resume
            )

            # Strict requirement: Only record & broadcast when a valid evaluation was actually produced
            if result and isinstance(result, dict) and "accuracy_score" in result:
                score = result["accuracy_score"]
                confirmed_qa_record["accuracy_score"] = score
                self.evaluated_qa_ids.add(qa_id)

                logger.info(f"[Phase2V] Q/A {qa_id} evaluated successfully: accuracy_score={score}%")

                # Persist confirmed_qa_pairs with accuracy_score to disk/DB
                try:
                    await self.repo.save_session(self.session_id, {
                        "confirmed_qa_pairs": self.confirmed_qa_pairs
                    })
                except Exception as repo_err:
                    logger.warning(f"[Phase2V] Error saving evaluated Q/A pairs to repo: {repo_err}")

                # WebSocket notification: dedicated "qa_evaluated" event
                # 1. Primary routing: Broadcast to all connected dashboard WebSockets
                if getattr(self, "on_qa_evaluated_callback", None):
                    try:
                        try:
                            cb_res = self.on_qa_evaluated_callback(qa_id, score, question, answer)
                        except TypeError:
                            cb_res = self.on_qa_evaluated_callback(qa_id, score)
                        if asyncio.iscoroutine(cb_res):
                            await cb_res
                    except Exception as cb_err:
                        logger.debug(f"[Phase2V] on_qa_evaluated_callback error: {cb_err}")
                elif websocket:
                    # Fallback for direct single-socket invocations (e.g. isolated unit tests)
                    try:
                        await websocket.send_json({
                            "type": "qa_evaluated",
                            "session_id": self.session_id,
                            "qa_id": qa_id,
                            "accuracy_score": score,
                            "question": question,
                            "answer": answer
                        })
                    except Exception as ws_err:
                        logger.debug(f"[Phase2V] Direct WebSocket send error: {ws_err}")

                # Notify registered on_update_callback
                if getattr(self, "on_update_callback", None):
                    try:
                        cb_res = self.on_update_callback()
                        if asyncio.iscoroutine(cb_res):
                            await cb_res
                    except Exception as cb_err:
                        logger.debug(f"[Phase2V] on_update_callback error: {cb_err}")
            else:
                # Evaluation failed, timed out, or returned invalid score.
                # DO NOT mark as evaluated and DO NOT broadcast fake 0% score!
                logger.warning(f"[Phase2V] Accuracy evaluation failed for Q/A {qa_id}. Q/A remains eligible for safe retry.")

        except asyncio.CancelledError:
            logger.info(f"[Phase2V] Evaluation task for Q/A {qa_id} was cancelled.")
            raise
        except Exception as err:
            logger.error(f"[Phase2V] Unexpected error evaluating Q/A {qa_id}: {err}")

    async def on_in_meeting(self) -> List[str]:
        """Lifecycle hook triggered when session enters active/IN_MEETING state."""
        asyncio.create_task(self.generate_static_scenario_verification_questions())
        return await self.generate_initial_suggestions()

    async def finalize_report(self) -> Dict[str, Any]:
        """Compiles and finalizes post-session evaluation metrics and summary dossier."""
        self.stop_qa_checkpoint()
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
            if self.initial_suggestions:
                self.assistance["initial_suggestions"] = list(self.initial_suggestions)
            if self.scenario_questions:
                self.assistance["scenario_questions"] = list(self.scenario_questions)
                self.assistance["suggested_practical_questions"] = list(self.scenario_questions)
            if self.verification_questions:
                self.assistance["verification_questions"] = list(self.verification_questions)

            report_data = {
                "transcript": self.transcript,
                "intelligence": self.intelligence,
                "assistance": self.assistance,
                "confirmed_qa_pairs": self.confirmed_qa_pairs
            }
            await self.repo.save_session(self.session_id, {
                "transcript": self.transcript,
                "final_report": report_data,
                "intelligence": self.intelligence,
                "assistance": self.assistance,
                "confirmed_qa_pairs": self.confirmed_qa_pairs,
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



