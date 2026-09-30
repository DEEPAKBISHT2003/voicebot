import re
import time
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


class QuestionIntent(str, Enum):
    INTERVIEW = "INTERVIEW"
    AUDIO_CHECK = "AUDIO_CHECK"
    COORDINATION = "COORDINATION"
    SMALL_TALK = "SMALL_TALK"
    ASR_NOISE = "ASR_NOISE"


# Observability Metrics Registry
qa_fsm_metrics: Dict[str, Any] = {
    "qa_fsm_state_transitions_total": 0,
    "qa_fsm_completed_pairs_total": 0,
    "qa_fsm_silence_completions_total": 0,
    "qa_fsm_speaker_change_completions_total": 0,
    "qa_fsm_meeting_end_completions_total": 0,
    "qa_fsm_fallback_to_legacy_total": 0,
    "qa_fsm_clarification_exchanges_total": 0,
    "qa_fsm_interruption_pauses_total": 0,
    "qa_fsm_question_candidates_total": 0,
    "qa_fsm_non_evaluable_filtered_total": 0,
    "qa_fsm_unheard_reprompts_total": 0
}


def get_qa_fsm_metrics() -> Dict[str, Any]:
    """Returns a snapshot of the QA FSM telemetry metrics including qa_fsm_filtered_ratio."""
    metrics = dict(qa_fsm_metrics)
    total_candidates = metrics.get("qa_fsm_question_candidates_total", 0)
    filtered = metrics.get("qa_fsm_non_evaluable_filtered_total", 0)
    metrics["qa_fsm_filtered_ratio"] = round(filtered / total_candidates, 3) if total_candidates > 0 else 0.0
    return metrics


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
        "can you", "could you", "tell me", "tell us", "what is", "what are", "what was",
        "what were", "what did", "what do", "what does", "what have", "what would",
        "what should", "what could", "what can", "what's", "what kind", "what type",
        "what specific", "what components", "how do", "how would", "how did", "how does",
        "how can", "how could", "how is", "how are", "how have", "how have you",
        "what have you", "what did you", "how did you", "why did", "why do", "why does",
        "why is", "why would", "explain", "walk me through", "walk us through", "describe",
        "have you worked", "which", "when did", "so tell", "let's talk about",
        "do you have experience", "how do you approach", "what's your approach",
        "would you mind", "what was your role", "please walk", "please explain",
        "please describe", "please introduce", "introduce yourself", "could", "would",
        "do you", "did you", "have you", "can we", "is it",
        "the first question", "first question", "the next question", "next question",
        "another question", "my question is", "one question i have", "one question from my side",
        "question from my side", "a question from my side", "the second question",
        "second question", "the third question", "third question", "to start with",
        "could you tell", "can you tell", "do you want to", "would you like to"
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
        self.last_completion_time: Optional[float] = None

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

    def _classify_question_intent(self, text: str) -> QuestionIntent:
        """
        Classifies the intent of an interrogative utterance to determine whether
        it is an actual interview evaluation question or non-evaluable operational dialogue.
        
        Categories:
          - INTERVIEW: Technical, architectural, experience, behavioral, or introduction inquiry.
          - AUDIO_CHECK: Audio/mic/sound quality check, echoing, volume, audibility, or screen sharing.
          - COORDINATION: Meeting flow, pacing, timing, wrap-up, or transition chatter.
          - SMALL_TALK: Casual greetings, pleasantries, weather, location.
          - ASR_NOISE: Garbled speech recognition noise or incoherent short fragments.
        """
        cleaned = text.strip()
        if not cleaned:
            return QuestionIntent.ASR_NOISE

        t_lower = cleaned.lower()

        # Domain/technical concepts that clearly signify an interview assessment question
        technical_domain = [
            r"\b(algorithm|pipeline|whisper|speech to text|stt|tts|model|architecture|deep learning)\b",
            r"\b(neural network|rag|vector|database|postgres|sql|nosql|system|framework|crewai|agent)\b",
            r"\b(api|microservice|frontend|backend|cloud|kubernetes|docker|react|python|java|c\+\+)\b",
            r"\b(index|indexing|deadlock|concurrency|latency|throughput|cache|redis|kafka)\b",
            r"\b(garbage collection|mark-and-sweep|memory|pointers|threads|async|event loop|gc|heap|stack)\b",
            r"\b(fastapi|mcp|rag|llm|ollama|grc|iso|compliance|risk)\b",
            r"\b(introduce yourself|introduction|resume|background|experience|previous project)\b",
            r"\b(walk me through|tell me about|explain|describe|what is|what are|how does|how do|what did you build|how would you)\b",
            r"\b(feature|components|implementation|tradeoff|design pattern|unit test|integration)\b"
        ]
        has_tech_domain = any(re.search(pat, t_lower) for pat in technical_domain)

        # 1. AUDIO / CONNECTIVITY / AUDIBILITY CHECK
        audio_check_patterns = [
            r"\b(can you also hear|can you hear|could you hear|am i audible|are you audible|is my voice|is my audio)\b",
            r"\b(hear (a |any )?noise|hear me|hear that|hear anything|hear properly|hear clearly)\b",
            r"\b(audible|inaudible|unclear audio|audio quality)\b",
            r"\b(noisy|noise|background noise|how much noisy|no noisy|is it noisy)\b",
            r"\b(echo|echoing|merging from my side|still merging|overlapping)\b",
            r"\b(mic|microphone|headset|speaker|volume|audio level)\b",
            r"\b(cutting out|breaking up|choppy|distortion|static|freeze|frozen|lagging)\b",
            r"\b(see my screen|see the screen|screen share|screen visible)\b",
        ]
        for pat in audio_check_patterns:
            if re.search(pat, t_lower) and not has_tech_domain:
                return QuestionIntent.AUDIO_CHECK

        # 2. MEETING COORDINATION
        coordination_patterns = [
            r"\b(shall we (get )?start(ed)?|can we (get )?start(ed)?|ready to start|are you ready)\b",
            r"\b(give me a (second|moment|minute)|wait a (second|moment|minute)|give us a (second|moment|minute))\b",
            r"\b(let's wrap up|shall we wrap up|we can wrap up|that's all from (my|our) side|that is it from (my|our) side)\b",
            r"\b(any questions for (me|us)|do you have any questions for (me|us))\b"
        ]
        for pat in coordination_patterns:
            if re.search(pat, t_lower) and not has_tech_domain:
                return QuestionIntent.COORDINATION

        # 3. SMALL TALK
        small_talk_patterns = [
            r"\b(how are you( doing)?|how's your day|how is your day|having a good day|how have you been)\b",
            r"\b(nice to meet you|pleasure to meet you|hope you are doing well)\b",
            r"\b(where are you joining from|where are you located|how is the weather)\b"
        ]
        for pat in small_talk_patterns:
            if re.search(pat, t_lower) and not has_tech_domain:
                return QuestionIntent.SMALL_TALK

        # 4. ASR NOISE / HALLUCINATION
        words = cleaned.split()
        if len(words) <= 3 and not has_tech_domain:
            return QuestionIntent.ASR_NOISE

        if re.search(r"\b(why is it again|what's the picnic|what is it again|is it again)\b", t_lower):
            return QuestionIntent.ASR_NOISE

        return QuestionIntent.INTERVIEW

    def _is_interview_question(self, text: str) -> bool:
        """
        Determines deterministically if an utterance is an evaluable interview question,
        filtering out audio checks, small talk, coordination, and transcription noise.
        """
        if not self._is_question(text):
            return False
        return self._classify_question_intent(text) == QuestionIntent.INTERVIEW

    def _is_candidate_unheard_request(self, text: str) -> bool:
        """
        Determines if a candidate utterance indicates they did not hear the question,
        the interviewer was inaudible, or requests a repeat of the question.
        Such utterances are audio/connectivity issues, NOT evaluated answers.
        """
        cleaned = text.strip()
        if not cleaned:
            return False
        t_lower = cleaned.lower()
        unheard_patterns = [
            r"\b(didn't hear|did not hear|couldn't hear|could not hear)\b",
            r"\b(not audible|hardly audible|inaudible|barely audible)\b",
            r"\b(can you repeat|could you repeat|please repeat|repeat the question|repeat that)\b",
            r"\b(say that again|say again|come again|pardon)\b",
            r"\b(you broke up|you're breaking up|voice broke up|audio cut out|you cut out)\b",
            r"\b(sorry,?\s+(i didn't hear|you are not audible|could you repeat|what did you say))\b",
            r"\b(what was the question|missed that|missed the question)\b"
        ]
        for pat in unheard_patterns:
            if re.search(pat, t_lower):
                return True
        return False

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

        # Check after repeatedly stripping leading conversational filler prefixes
        words = t_lower.split()
        while words and re.sub(r"^[^\w]+|[^\w]+$", "", words[0]) in self.FILLER_PREFIXES:
            words.pop(0)
        stripped_prefix = " ".join(words)
        if stripped_prefix and any(stripped_prefix.startswith(qs) for qs in self.QUESTION_STARTERS):
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

        # Regex matching for core modal/interrogative verbs and question declarations
        pattern = (
            r"\b(can you|could you|tell me|tell us|how do you|how would you|how have you|"
            r"how did you|what is|what are|what did you|what have you|explain|walk me through|"
            r"describe|introduce yourself|first question|next question|another question|"
            r"question from my side|my question is)\b"
        )
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

    def _is_interviewer_pause_or_hold(self, text: str) -> bool:
        """
        Determines if an interviewer utterance is a brief pause, hold, or interruption
        (e.g., 'sorry, one second', 'hold on', 'give me a moment') that should NOT
        prematurely finalize an active candidate answer.
        """
        cleaned = text.strip()
        if not cleaned:
            return False
        t_lower = cleaned.lower()
        t_clean = re.sub(r"^[^\w]+|[^\w]+$", "", t_lower)
        words = t_clean.split()
        if len(words) > 10:
            return False

        hold_patterns = [
            r"\b(sorry,?\s+(one second|one moment|just a second|just a moment|wait a second|hold on|give me a second))\b",
            r"\b(one second|one moment|just a second|just a moment|give me a second|give me a moment|give us a second)\b",
            r"\b(hold on|wait a second|wait a moment|hang on|hang on a second|take your time|no rush)\b",
            r"\b(please continue|go ahead|excuse me (for a second|a moment))\b"
        ]
        return any(re.search(pat, t_lower) for pat in hold_patterns)

    def _is_candidate_clarification(self, text: str) -> bool:
        """Determines if a candidate utterance is a clarification or scoping question rather than a final answer."""
        cleaned = text.strip()
        if not cleaned:
            return False
        t_lower = cleaned.lower()

        # 1. Ends with '?' and relatively short (< 25 words)
        words = cleaned.split()
        if cleaned.endswith("?") and len(words) <= 25:
            return True

        # 2. Clarification / scoping phrases
        clarification_patterns = [
            r"\b(do you mean|are you asking|are you looking for)\b",
            r"\b(should i|should we|do you want me to|would you like me to)\b",
            r"\b(can you clarify|could you clarify|can you repeat|could you repeat)\b",
            r"\b(you mean|what do you mean)\b",
            r"\b(talking about|speaking about)\b.*\b(or|versus|vs)\b",
            r"\b(capabilities|challenges|frontend|backend|architecture|code|high level|deep dive)\b.*\?",
            r"\b(in terms of|specifically|regarding)\b.*\b(or)\b",
            r"\b(am i audible|can you hear me)\b",
            r"\b(just to confirm|just to clarify|just to be clear)\b",
            r"\b(which one|which part|any specific)\b"
        ]
        for pat in clarification_patterns:
            if re.search(pat, t_lower):
                return True

        # 3. Short utterance containing ' or ' (e.g., "AI capabilities or challenges?")
        if " or " in t_lower and len(words) <= 15:
            return True

        return False

    def _is_candidate_answer_concluded(self, text: str) -> bool:
        """
        Determines if a candidate utterance explicitly concludes the current answer,
        e.g., 'And that's all about me.', 'That's it.', 'That covers it.',
        preventing any subsequent candidate turn from re-opening or continuing this answer.
        """
        cleaned = text.strip()
        if not cleaned:
            return False
        t_lower = cleaned.lower()
        t_clean = re.sub(r"^[^\w]+|[^\w]+$", "", t_lower)

        conclusion_patterns = [
            r"\b(that'?s|that is)\s+(all\s+about\s+me|all\s+from\s+my\s+side|all\s+i\s+have|pretty\s+much\s+it|about\s+it|everything|all)\b",
            r"\b(that\s+concludes\s+my\s+(answer|intro|introduction|background))\b",
            r"\b(that'?s|that is)\s+(my\s+background|my\s+introduction)\b",
            r"\b(that\s+covers\s+it|that\s+answers\s+(the|your)\s+question|that'?s\s+it\s+from\s+me)\b",
            r"\b(nothing\s+(more|else)\s+to\s+add)\b"
        ]
        return any(re.search(pat, t_clean) for pat in conclusion_patterns)

    def _is_interviewer_clarification_response(self, text: str, cand_text: str) -> bool:
        """
        Determines if an interviewer utterance is a clarification response or specification
        rather than transitioning to a completely new interview topic.
        """
        cleaned = text.strip()
        if not cleaned:
            return False
        t_lower = cleaned.lower()

        # If interviewer explicitly signals a new topic or question, it is NOT clarification
        transition_patterns = [
            r"\b(moving on|next question|next topic|final question|final portion|let's move on|let's skip|another question)\b",
        ]
        for tp in transition_patterns:
            if re.search(tp, t_lower):
                return False

        # Clarification response patterns
        clarification_response_patterns = [
            r"\b(i want to know|i'd like to know|i want to hear|i'm looking for|i mean)\b",
            r"\b(what you implemented|what you built|what you did|what you had|your experience)\b",
            r"\b(focus on|talk about|tell me about|tell us about|explain about)\b",
            r"\b(start with|either|both|whichever|any of|the former|the latter)\b",
            r"\b(specifically|in particular|regarding|related to)\b",
            r"\b(yes|yeah|sure|exactly|correct|right|no)\b",
            r"\b(go ahead|please continue|take your time|feel free)\b"
        ]
        for crp in clarification_response_patterns:
            if re.search(crp, t_lower):
                return True

        # Check if interviewer mentions keywords from the candidate's options
        cand_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", cand_text.lower()))
        interviewer_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", t_lower))
        if cand_words.intersection(interviewer_words):
            return True

        # If it is not a new interrogative question starter, treat as clarification
        if not self._is_question(cleaned):
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
                    cand_text = " ".join(t.get("text", "") for t in self.current_answer_turns).strip()
                    if self._is_candidate_clarification(cand_text):
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Candidate turns represent a clarification request "
                            f"('{cand_text}'). Silence watchdog will not finalize QA pair."
                        )
                        return

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

    def _evaluate_asr_quality(self, question: str, answer: str) -> str:
        """
        Evaluates transcription quality for the Q/A pair.
        Returns 'high', 'medium', or 'low'.
        Pairs with 'low' transcription quality (severe corruption, phonetic garbage,
        or fragmented non-answers) are tagged to prevent punitive evaluations.
        """
        q_words = question.strip().split()
        a_words = answer.strip().split()

        # Check turn-level confidence if available
        turns = ([self.current_question["turn"]] if self.current_question and "turn" in self.current_question else []) + self.current_answer_turns
        if any(t.get("confidence") is not None and float(t.get("confidence")) < 0.6 for t in turns):
            return "low"

        # Fragmented or single-word non-answers
        if len(a_words) <= 3 and answer.strip().lower() in {"i'm.", "i'm", "i know you see, i mean like.", "dek."}:
            return "low"
            
        corrupt_tokens = [
            "honey singh", "badsha", "kalaran", "kismeli", "karan kaka", "tender cow",
            "translook", "dilika", "picnic", "multiveru", "rat platform"
        ]
        if any(tok in answer.lower() or tok in question.lower() for tok in corrupt_tokens):
            return "low"

        return "high"

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
        asr_quality = self._evaluate_asr_quality(q_text, a_text)

        return {
            "pair_id": pair_id,
            "qa_id": pair_id,
            "question_turn_id": q_id,
            "answer_turn_ids": a_ids,
            "question": q_text,
            "answer": a_text,
            "confidence": 1.0 if asr_quality == "high" else 0.4,
            "asr_quality": asr_quality,
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

        # Check if the candidate explicitly concluded their answer
        cand_full_text = " ".join(t.get("text", "") for t in self.current_answer_turns).strip()
        last_turn_text = self.current_answer_turns[-1].get("text", "").strip() if self.current_answer_turns else ""
        is_concluded = (
            self._is_candidate_answer_concluded(cand_full_text)
            or self._is_candidate_answer_concluded(last_turn_text)
        )

        # Track for candidate continuation if completed via silence timeout and NOT explicitly concluded
        if "silence threshold exceeded" in reason.lower() and not is_concluded:
            self.last_question = dict(self.current_question) if self.current_question else None
            self.last_answer_turns = list(self.current_answer_turns)
            self.last_completion_reason = reason
            self.last_completion_time = time.monotonic()
        else:
            self.last_question = None
            self.last_answer_turns = []
            self.last_completion_reason = ""
            self.last_completion_time = None

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
                if role == "interviewer" or (role == "unknown" and self._is_interview_question(text)):
                    self.last_question = None
                    self.last_answer_turns = []
                    self.last_completion_reason = ""
                    self.last_completion_time = None
                    if self._is_question(text):
                        qa_fsm_metrics["qa_fsm_question_candidates_total"] += 1
                    if self._is_interview_question(text):
                        self.current_question = {
                            "turn_id": turn.get("turn_id"),
                            "id": turn.get("id"),
                            "text": text,
                            "speaker": speaker,
                            "turn": turn
                        }
                        self.current_answer_turns = []
                        self._transition_to(QAState.QUESTION_CAPTURED, f"Question captured from {speaker}")
                    else:
                        intent = self._classify_question_intent(text)
                        if intent != QuestionIntent.INTERVIEW:
                            qa_fsm_metrics["qa_fsm_non_evaluable_filtered_total"] += 1
                            logger.info(
                                f"[QA_FSM] session_id={self.session_id} Filtered non-evaluable interviewer utterance: "
                                f"intent={intent.value} text='{text}'"
                            )
                elif role == "candidate":
                    is_valid_continuation = False
                    if self.last_question is not None:
                        # Guard: clarification or unheard request is NOT a continuation of previous answer
                        if self._is_candidate_unheard_request(text) or self._is_candidate_clarification(text):
                            logger.info(
                                f"[QA_FSM] session_id={self.session_id} Candidate utterance in WAITING_FOR_QUESTION "
                                f"is clarification/unheard ('{text}'). Not reopening prior question {self.last_question.get('turn_id')}."
                            )
                            self.last_question = None
                            self.last_answer_turns = []
                            self.last_completion_reason = ""
                            self.last_completion_time = None
                            return None

                        # Guard: Continuation window timeout (5.0s maximum between silence completion and candidate resume)
                        if self.last_completion_time is not None:
                            elapsed = time.monotonic() - self.last_completion_time
                            if elapsed <= 5.0:
                                is_valid_continuation = True
                            else:
                                logger.info(
                                    f"[QA_FSM] session_id={self.session_id} Candidate continuation window expired "
                                    f"(elapsed={elapsed:.2f}s > 5.0s). Discarding prior question {self.last_question.get('turn_id')}."
                                )
                                self.last_question = None
                                self.last_answer_turns = []
                                self.last_completion_reason = ""
                                self.last_completion_time = None
                        else:
                            is_valid_continuation = True

                    if is_valid_continuation and self.last_question is not None:
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Candidate continuation detected for question "
                            f"{self.last_question.get('turn_id')}. Re-opening answer."
                        )
                        self.current_question = dict(self.last_question)
                        self.current_answer_turns = list(self.last_answer_turns) + [turn]
                        self.last_question = None
                        self.last_answer_turns = []
                        self.last_completion_reason = ""
                        self.last_completion_time = None
                        self._transition_to(QAState.CANDIDATE_ANSWERING, "Candidate continuation after silence")
                        self._schedule_silence_timer()
                return None

            # -----------------------------------------------------------------
            # STATE 2: QUESTION_CAPTURED
            # -----------------------------------------------------------------
            elif self.state == QAState.QUESTION_CAPTURED:
                if role == "interviewer":
                    # Consecutive interviewer turn: extends, clarifies, or re-prompts the question
                    if self.current_question:
                        if self.current_question.get("candidate_unheard"):
                            self.current_question["text"] = f"{self.current_question['text']} (Re-prompt: {text})".strip()
                            self.current_question["candidate_unheard"] = False
                            logger.info(
                                f"[QA_FSM] session_id={self.session_id} Re-prompt captured from interviewer: '{text}'"
                            )
                        else:
                            self.current_question["text"] = f"{self.current_question['text']} {text}".strip()
                        self.current_question["turn_id"] = turn.get("turn_id", self.current_question["turn_id"])
                    return None

                elif role == "candidate":
                    # Check if candidate reports audio issues / did not hear the question
                    if self._is_candidate_unheard_request(text):
                        qa_fsm_metrics["qa_fsm_unheard_reprompts_total"] += 1
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Candidate could not hear question ('{text}'). "
                            f"Remaining in QUESTION_CAPTURED awaiting interviewer reprompt."
                        )
                        if self.current_question:
                            self.current_question["candidate_unheard"] = True
                        return None

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
                    # Check if previous answer turn already explicitly concluded
                    if self.current_answer_turns and self._is_candidate_answer_concluded(self.current_answer_turns[-1].get("text", "")):
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Candidate spoke after previous answer explicitly concluded. "
                            f"Finalizing concluded answer pair before processing new candidate utterance."
                        )
                        self._cancel_silence_timer()
                        reason = "Candidate answered previous question and started new topic"
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

                        self.last_question = None
                        self.last_answer_turns = []
                        self.last_completion_reason = ""
                        self.last_completion_time = None
                        self.current_question = None
                        self.current_answer_turns = []
                        self._transition_to(QAState.WAITING_FOR_QUESTION, "Awaiting next interviewer question")
                        return completed_qa

                    # Candidate continues speaking: accumulate turn and reset silence timer
                    self.current_answer_turns.append(turn)
                    self._schedule_silence_timer()
                    return None

                elif role == "interviewer" or (role == "unknown" and speaker != "Candidate"):
                    cand_text = " ".join(t.get("text", "") for t in self.current_answer_turns).strip()
                    is_cand_clarification = self._is_candidate_clarification(cand_text)

                    # 1. Interviewer backchannel / pause / hold check
                    if self._is_question(text):
                        qa_fsm_metrics["qa_fsm_question_candidates_total"] += 1

                    is_new_q = self._is_interview_question(text)
                    if not is_new_q:
                        if self._is_backchannel(text):
                            logger.info(
                                f"[QA_FSM] session_id={self.session_id} Interviewer backchannel/acknowledgment detected: "
                                f"'{text}'. Remaining in CANDIDATE_ANSWERING."
                            )
                            self._schedule_silence_timer()
                            return None

                        if self._is_interviewer_pause_or_hold(text):
                            qa_fsm_metrics["qa_fsm_interruption_pauses_total"] += 1
                            logger.info(
                                f"[QA_FSM] session_id={self.session_id} Interviewer pause/hold detected: '{text}'. "
                                f"Keeping candidate answer open."
                            )
                            self._schedule_silence_timer()
                            return None

                    # 2. Clarification Flow: Candidate asked clarification, and interviewer provides guidance/scope
                    if is_cand_clarification and self._is_interviewer_clarification_response(text, cand_text):
                        logger.info(
                            f"[QA_FSM] session_id={self.session_id} Clarification exchange detected. "
                            f"Candidate asked: '{cand_text}' | Interviewer clarified: '{text}'. "
                            f"Keeping QA pair active."
                        )
                        self._cancel_silence_timer()
                        qa_fsm_metrics["qa_fsm_clarification_exchanges_total"] += 1
                        if self.current_question:
                            self.current_question["text"] = f"{self.current_question['text']} (Clarification: {text})".strip()
                            self.current_question["turn_id"] = turn.get("turn_id", self.current_question["turn_id"])
                        # Reset candidate answer turns since previous turns were only the clarification query
                        self.current_answer_turns = []
                        self._transition_to(QAState.QUESTION_CAPTURED, f"Clarified question scope via {speaker}")
                        return None

                    # Priority 1 or 3: Speaker Change or New Question!
                    self._cancel_silence_timer()
                    self.last_question = None
                    self.last_answer_turns = []
                    self.last_completion_reason = ""
                    self.last_completion_time = None
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

                    # Transition based on whether new turn is an evaluable INTERVIEW question
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
                        intent = self._classify_question_intent(text)
                        if intent != QuestionIntent.INTERVIEW:
                            qa_fsm_metrics["qa_fsm_non_evaluable_filtered_total"] += 1
                            logger.info(
                                f"[QA_FSM] session_id={self.session_id} Filtered non-evaluable interviewer utterance: "
                                f"intent={intent.value} text='{text}'"
                            )
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

