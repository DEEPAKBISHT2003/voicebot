import re
import asyncio
import datetime
from enum import Enum
from typing import List, Dict, Any, Optional, Callable, Awaitable
from loguru import logger
from services.copilot.src.core.config import Settings


class QAState(str, Enum):
    WAITING_FOR_QUESTION = "WAITING_FOR_QUESTION"
    QUESTION_CAPTURED = "QUESTION_CAPTURED"
    CANDIDATE_ANSWERING = "CANDIDATE_ANSWERING"
    QA_COMPLETED = "QA_COMPLETED"


# Observability Metrics Registry
qa_fsm_metrics: Dict[str, int] = {
    "qa_fsm_state_transitions_total": 0,
    "qa_fsm_completed_pairs_total": 0,
    "qa_fsm_silence_completions_total": 0,
    "qa_fsm_speaker_change_completions_total": 0,
    "qa_fsm_meeting_end_completions_total": 0,
    "qa_fsm_fallback_to_legacy_total": 0
}


def get_qa_fsm_metrics() -> Dict[str, int]:
    """Returns a snapshot of the QA FSM telemetry metrics."""
    return dict(qa_fsm_metrics)


def reset_qa_fsm_metrics() -> None:
    """Resets QA FSM telemetry counters to zero (useful for tests)."""
    for k in qa_fsm_metrics:
        qa_fsm_metrics[k] = 0


class QAStateMachine:
    """
    Phase 3: Deterministic Event-Driven Finite State Machine for Q/A Completion Detection.
    
    States:
      - WAITING_FOR_QUESTION: Waiting for an interviewer question.
      - QUESTION_CAPTURED: Interviewer question identified, waiting for candidate to begin answering.
      - CANDIDATE_ANSWERING: Candidate actively answering, accumulating turns.
      - QA_COMPLETED: Answer complete, triggers follow-up and evaluation, transitions to WAITING_FOR_QUESTION.
      
    Deterministic Signals:
      1. Speaker change detection: Candidate -> Interviewer (High confidence).
      2. Extended candidate silence: Configurable threshold (default 3.0s).
      3. New interviewer question arrival.
      4. Meeting end / finalization.
    """

    QUESTION_STARTERS = (
        "can you", "could you", "tell me", "tell us", "what is", "what are", "how do",
        "how would", "how did", "why did", "why do", "explain", "walk me through",
        "walk us through", "describe", "have you worked", "which", "when did", "so tell",
        "let's talk about", "do you have experience", "how do you approach",
        "what's your approach", "would you mind", "how does", "what was your role",
        "please walk", "please explain", "please describe", "please introduce", "introduce yourself",
        "what", "how", "why", "could", "would", "do you", "did you", "have you", "can we", "is it"
    )

    GREETINGS = (
        "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
        "can you hear me", "am i audible", "thanks for joining", "welcome", "thank you"
    )

    FILLER_PREFIXES = {
        "ok", "okay", "alright", "all right", "so", "well", "now", "great", "sure",
        "right", "yeah", "yes", "also", "and", "please"
    }

    BACKCHANNEL_PHRASES = {
        "yes", "yeah", "yep", "sure", "ok", "okay", "right", "alright",
        "go ahead", "go on", "understood", "got it", "i see", "makes sense",
        "exactly", "correct"
    }

    def __init__(
        self,
        session_id: str,
        silence_threshold: float = Settings.QA_FSM_SILENCE_THRESHOLD,
        on_qa_completed: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        self.session_id = session_id
        self.silence_threshold = silence_threshold
        self.on_qa_completed = on_qa_completed
        self.loop = loop

        self.state: QAState = QAState.WAITING_FOR_QUESTION
        self.current_question: Optional[Dict[str, Any]] = None
        self.current_answer_turns: List[Dict[str, Any]] = []
        self.silence_timer_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # Candidate answer continuation tracking after silence timeout
        self.last_question: Optional[Dict[str, Any]] = None
        self.last_answer_turns: List[Dict[str, Any]] = []
        self.last_completion_reason: str = ""

    def get_state(self) -> QAState:
        """Returns the current state of the FSM."""
        return self.state

    def _transition_to(self, new_state: QAState, reason: str = "") -> None:
        """Executes a state transition and updates observability telemetry."""
        if self.state != new_state:
            prev = self.state
            self.state = new_state
            qa_fsm_metrics["qa_fsm_state_transitions_total"] += 1
            logger.info(f"[QA_FSM] session_id={self.session_id} transition: {prev.value} -> {new_state.value} ({reason})")

    def _is_question(self, text: str) -> bool:
        """Determines deterministically if an utterance is an interview question."""
        cleaned = text.strip()
        if not cleaned:
            return False

        t_lower = cleaned.lower()

        # Check for interrogative punctuation
        if cleaned.endswith("?"):
            return True

        # Check if greeting or acknowledgment
        if any(t_lower.startswith(g) for g in self.GREETINGS) and len(cleaned.split()) <= 6:
            return False

        # Direct check for question starter phrasing
        if any(t_lower.startswith(qs) for qs in self.QUESTION_STARTERS):
            return True

        # Clause-based analysis for compound utterances with conversational prefixes/names
        clauses = [c.strip() for c in re.split(r"[,;.!?\n]+", cleaned) if c.strip()]
        for clause in clauses:
            c = clause.strip().lower()
            c = re.sub(r"^[^\w]+|[^\w]+$", "", c)
            tokens = c.split()
            while tokens and tokens[0] in self.FILLER_PREFIXES:
                tokens.pop(0)
            c_clean = " ".join(tokens)
            if any(c_clean.startswith(qs) for qs in self.QUESTION_STARTERS):
                return True
            if len(tokens) > 2:
                without_name = " ".join(tokens[1:])
                if any(without_name.startswith(qs) for qs in self.QUESTION_STARTERS):
                    return True

        # Regex matching for core modal/interrogative verbs
        pattern = r"\b(can you|could you|tell me|tell us|how do you|how would you|what is|what are|explain|walk me through|describe|introduce yourself)\b"
        if re.search(pattern, t_lower):
            return True

        return False

    def _is_backchannel(self, text: str) -> bool:
        """Determines if an interviewer utterance is a short backchannel/acknowledgment."""
        cleaned = text.strip().lower()
        cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", cleaned)
        if not cleaned:
            return False
        words = cleaned.split()
        if len(words) <= 3 and (cleaned in self.BACKCHANNEL_PHRASES or any(w in self.BACKCHANNEL_PHRASES for w in words)):
            return True
        return False

    def _cancel_silence_timer(self) -> None:
        """Cancels any pending silence timeout task."""
        if self.silence_timer_task and not self.silence_timer_task.done():
            self.silence_timer_task.cancel()
            self.silence_timer_task = None

    def _schedule_silence_timer(self) -> None:
        """Schedules a silence timer when the candidate is actively answering."""
        self._cancel_silence_timer()
        try:
            current_loop = self.loop or asyncio.get_running_loop()
            self.silence_timer_task = current_loop.create_task(self._silence_watchdog())
        except RuntimeError:
            pass

    async def _silence_watchdog(self) -> None:
        """Asynchronous watchdog that fires after silence_threshold seconds of silence."""
        try:
            await asyncio.sleep(self.silence_threshold)
            async with self._lock:
                if self.state == QAState.CANDIDATE_ANSWERING and self.current_answer_turns:
                    qa_fsm_metrics["qa_fsm_silence_completions_total"] += 1
                    logger.info(
                        f"[QA_FSM] session_id={self.session_id} candidate silence threshold ({self.silence_threshold}s) "
                        f"exceeded. Finalizing QA pair."
                    )
                    await self._complete_current_qa(reason=f"Candidate silence threshold exceeded ({self.silence_threshold}s)")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[QA_FSM] Unexpected error in silence watchdog: {e}")

    def _build_completed_qa(self, reason: str) -> Dict[str, Any]:
        """Builds a confirmed Q/A record matching existing session schema."""
        q_turn = self.current_question or {}
        q_id = q_turn.get("turn_id") if q_turn.get("turn_id") is not None else q_turn.get("id")
        
        a_ids = [
            t.get("turn_id") if t.get("turn_id") is not None else t.get("id")
            for t in self.current_answer_turns
        ]
        pair_id = f"Q{q_id}_A{'_'.join(str(x) for x in a_ids)}"
        
        q_text = q_turn.get("text", "").strip()
        a_text = " ".join(t.get("text", "").strip() for t in self.current_answer_turns).strip()

        return {
            "pair_id": pair_id,
            "qa_id": pair_id,
            "question_turn_id": q_id,
            "answer_turn_ids": a_ids,
            "question": q_text,
            "answer": a_text,
            "confidence": 1.0,
            "reason": reason,
            "confirmed_at": datetime.datetime.now().isoformat()
        }

    async def _complete_current_qa(self, reason: str) -> Optional[Dict[str, Any]]:
        """Transitions to QA_COMPLETED, emits event, and resets state to WAITING_FOR_QUESTION."""
        if not self.current_question or not self.current_answer_turns:
            self._transition_to(QAState.WAITING_FOR_QUESTION, "No active question or answer turns to complete")
            self.current_question = None
            self.current_answer_turns = []
            return None

        self._cancel_silence_timer()
        completed_qa = self._build_completed_qa(reason)
        self._transition_to(QAState.QA_COMPLETED, reason)
        qa_fsm_metrics["qa_fsm_completed_pairs_total"] += 1

        # Track for candidate continuation if completed via silence timeout
        if "silence threshold exceeded" in reason.lower():
            self.last_question = self.current_question
            self.last_answer_turns = list(self.current_answer_turns)
            self.last_completion_reason = reason
        else:
            self.last_question = None
            self.last_answer_turns = []
            self.last_completion_reason = ""

        if self.on_qa_completed:
            try:
                res = self.on_qa_completed(completed_qa)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as cb_err:
                logger.error(f"[QA_FSM] Error executing on_qa_completed callback: {cb_err}")

        # Reset to waiting for next question
        self._transition_to(QAState.WAITING_FOR_QUESTION, "Ready for next question")
        self.current_question = None
        self.current_answer_turns = []
        return completed_qa

    async def on_turn(self, turn: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Processes an incoming transcript turn synchronously/deterministically.
        Returns a completed Q/A pair dictionary if this turn completed one, or None.
        """
        text = turn.get("text", "").strip()
        speaker = turn.get("speaker", "Unknown")
        role = turn.get("speaker_role", "unknown")

        if not text or speaker == "System" or role == "system":
            return None

        # Normalize role
        if speaker == "Candidate":
            role = "candidate"
        elif speaker == "Interviewer":
            role = "interviewer"

        async with self._lock:
            # -----------------------------------------------------------------
            # STATE 1: WAITING_FOR_QUESTION
            # -----------------------------------------------------------------
            if self.state == QAState.WAITING_FOR_QUESTION:
                if role == "interviewer" or (role == "unknown" and self._is_question(text)):
                    self.last_question = None
                    self.last_answer_turns = []
                    self.last_completion_reason = ""
                    if self._is_question(text):
                        self.current_question = {
                            "turn_id": turn.get("turn_id"),
                            "id": turn.get("id"),
                            "text": text,
                            "speaker": speaker,
                            "turn": turn
                        }
                        self.current_answer_turns = []
                        self._transition_to(QAState.QUESTION_CAPTURED, f"Question captured from {speaker}")
                elif role == "candidate":
                    if self.last_question is not None:
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Candidate continuation detected for question "
                            f"{self.last_question.get('turn_id')}. Re-opening answer."
                        )
                        self.current_question = self.last_question
                        self.current_answer_turns = list(self.last_answer_turns) + [turn]
                        self.last_question = None
                        self.last_answer_turns = []
                        self.last_completion_reason = ""
                        self._transition_to(QAState.CANDIDATE_ANSWERING, "Candidate continuation after silence")
                        self._schedule_silence_timer()
                return None

            # -----------------------------------------------------------------
            # STATE 2: QUESTION_CAPTURED
            # -----------------------------------------------------------------
            elif self.state == QAState.QUESTION_CAPTURED:
                if role == "interviewer":
                    # Consecutive interviewer turn: extends or clarifies the question
                    if self.current_question:
                        self.current_question["text"] = f"{self.current_question['text']} {text}".strip()
                        self.current_question["turn_id"] = turn.get("turn_id", self.current_question["turn_id"])
                    return None

                elif role == "candidate":
                    # Candidate starts speaking: transition to CANDIDATE_ANSWERING
                    self._transition_to(QAState.CANDIDATE_ANSWERING, f"Candidate {speaker} started answering")
                    self.current_answer_turns.append(turn)
                    self._schedule_silence_timer()
                    return None

            # -----------------------------------------------------------------
            # STATE 3: CANDIDATE_ANSWERING
            # -----------------------------------------------------------------
            elif self.state == QAState.CANDIDATE_ANSWERING:
                if role == "candidate":
                    # Candidate continues speaking: accumulate turn and reset silence timer
                    self.current_answer_turns.append(turn)
                    self._schedule_silence_timer()
                    return None

                elif role == "interviewer" or (role == "unknown" and speaker != "Candidate"):
                    is_new_q = self._is_question(text)
                    if not is_new_q and self._is_backchannel(text):
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Interviewer backchannel/acknowledgment detected: "
                            f"'{text}'. Remaining in CANDIDATE_ANSWERING."
                        )
                        self._schedule_silence_timer()
                        return None

                    # Priority 1 or 3: Speaker Change or New Question!
                    self._cancel_silence_timer()
                    qa_fsm_metrics["qa_fsm_speaker_change_completions_total"] += 1
                    
                    reason = "New interviewer question arrived" if is_new_q else "Speaker change from candidate to interviewer"
                    logger.info(f"[QA_FSM] session_id={self.session_id} {reason}. Finalizing QA pair.")

                    completed_qa = self._build_completed_qa(reason)
                    self._transition_to(QAState.QA_COMPLETED, reason)
                    qa_fsm_metrics["qa_fsm_completed_pairs_total"] += 1

                    if self.on_qa_completed:
                        try:
                            res = self.on_qa_completed(completed_qa)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as cb_err:
                            logger.error(f"[QA_FSM] Error executing on_qa_completed callback: {cb_err}")

                    # Transition based on whether new turn is a question
                    if is_new_q:
                        self.current_question = {
                            "turn_id": turn.get("turn_id"),
                            "id": turn.get("id"),
                            "text": text,
                            "speaker": speaker,
                            "turn": turn
                        }
                        self.current_answer_turns = []
                        self._transition_to(QAState.QUESTION_CAPTURED, "Captured new interviewer question")
                    else:
                        self.current_question = None
                        self.current_answer_turns = []
                        self._transition_to(QAState.WAITING_FOR_QUESTION, "Waiting for next question")

                    return completed_qa

        return None

    async def finalize_current_qa(self) -> Optional[Dict[str, Any]]:
        """
        Priority 4: Meeting End / Finalization.
        If meeting ends while candidate answer is active, automatically finalize current QA pair.
        """
        async with self._lock:
            self._cancel_silence_timer()
            if self.state == QAState.CANDIDATE_ANSWERING and self.current_answer_turns:
                qa_fsm_metrics["qa_fsm_meeting_end_completions_total"] += 1
                logger.info(f"[QA_FSM] session_id={self.session_id} Meeting ended. Finalizing active QA pair.")
                return await self._complete_current_qa(reason="Meeting ended while candidate answer was active")
            elif self.current_question and self.current_answer_turns:
                return await self._complete_current_qa(reason="Meeting ended with pending QA")
            else:
                self._transition_to(QAState.WAITING_FOR_QUESTION, "Meeting ended with no active QA")
                return None

    def close(self) -> None:
        """Cleans up timers and active tasks."""
        self._cancel_silence_timer()


def compare_legacy_vs_fsm(
    legacy_decision: Dict[str, Any],
    fsm_record: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Shadow-mode evaluation utility comparing legacy LLM output with deterministic FSM output.
    Returns comparison metrics and parity determination.
    """
    legacy_ready = bool(legacy_decision and legacy_decision.get("qa_ready"))
    fsm_ready = fsm_record is not None

    if not legacy_ready and not fsm_ready:
        return {
            "status": "EXACT_MATCH",
            "parity": True,
            "reason": "Both detected no completed Q/A pair",
            "legacy_ready": False,
            "fsm_ready": False
        }

    if legacy_ready != fsm_ready:
        return {
            "status": "DISCREPANCY",
            "parity": False,
            "reason": f"Mismatch in readiness: legacy={legacy_ready}, fsm={fsm_ready}",
            "legacy_ready": legacy_ready,
            "fsm_ready": fsm_ready
        }

    # Both ready: compare turn IDs
    leg_q_id = str(legacy_decision.get("question_turn_id"))
    fsm_q_id = str(fsm_record.get("question_turn_id"))
    q_matches = (leg_q_id == fsm_q_id)

    leg_a_ids = set(str(x) for x in legacy_decision.get("answer_turn_ids", []))
    fsm_a_ids = set(str(x) for x in fsm_record.get("answer_turn_ids", []))
    
    intersection = leg_a_ids.intersection(fsm_a_ids)
    union = leg_a_ids.union(fsm_a_ids)
    jaccard = len(intersection) / len(union) if union else 1.0

    is_exact = q_matches and (leg_a_ids == fsm_a_ids)
    is_equivalent = q_matches and (jaccard >= 0.5)

    return {
        "status": "EXACT_MATCH" if is_exact else ("SEMANTIC_EQUIVALENT" if is_equivalent else "DISCREPANCY"),
        "parity": is_exact or is_equivalent,
        "question_id_match": q_matches,
        "answer_ids_jaccard": round(jaccard, 2),
        "legacy_q_id": leg_q_id,
        "fsm_q_id": fsm_q_id,
        "legacy_a_ids": sorted(list(leg_a_ids)),
        "fsm_a_ids": sorted(list(fsm_a_ids)),
        "legacy_ready": True,
        "fsm_ready": True
    }

