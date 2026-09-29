import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class InterviewStartResult:
    interview_started: bool
    trigger: Optional[str] = None
    candidate_speaker: Optional[str] = None
    turn_id: Optional[Any] = None
    reason: Optional[str] = None


class InterviewStartDetector:
    """
    Candidate Stabilization Layer - Part 2: Interview Start Detection.
    
    Determines whether an interview session has officially started.
    Prevents premature role lock during pre-interview connection / setup chatter.
    
    Trigger Conditions:
    - Condition A: Speaker A asks a substantive interview question AND Speaker B answers with >= 20 words.
    - Condition B: A participant explicitly introduces themselves (e.g., 'My name is...', 'I am...', 'I have X years of experience...').
    
    Explicitly ignores:
    - Audio/video setup chatter ("Can you hear me?", "Am I audible?", "Is my screen visible?")
    - Green signal / pre-interview coordination chatter ("how early the green signal works")
    - Waiting room / admission chatter ("let me admit", "just a moment")
    """

    SETUP_CHATTER_PATTERNS = [
        r"can you hear me",
        r"am i audible",
        r"can you hear us",
        r"we can hear you",
        r"are you able to hear",
        r"is my screen visible",
        r"can you see my screen",
        r"let me share my screen",
        r"sharing my screen",
        r"green signal",
        r"audio check",
        r"mic check",
        r"sound check",
        r"test test",
        r"check 1 2",
        r"you are on mute",
        r"you're on mute",
        r"on mute",
        r"please unmute",
        r"give me a second",
        r"give me a moment",
        r"just a moment",
        r"let me admit",
        r"waiting room",
        r"is everyone here",
        r"thanks for joining",
        r"good morning everyone",
        r"good afternoon everyone"
    ]

    QUESTION_STARTERS = (
        "can you introduce", "could you introduce", "tell me about", "walk me through",
        "can you walk", "could you walk", "what is", "what are", "how do you",
        "how would you", "why did you", "explain", "describe", "have you worked with",
        "have you used", "do you have experience", "what was your role",
        "tell us about", "can you explain", "could you explain", "let's start with",
        "to start with", "start by introducing", "tell me something about",
        "what", "how", "why", "can you", "could you", "would you", "did you",
        "have you", "which", "where did you"
    )

    SELF_INTRO_PATTERNS = [
        r"\b(?:i am|i'm|my name is)\s+([A-Za-z]+)",
        r"\b(?:i have|i've got)\s+\d+\+?\s+years(?:\s+of)?\s+experience\b",
        r"\b(?:let me introduce myself|introducing myself|to give a brief intro|to introduce myself)\b",
        r"\b(?:i graduated from|my background is|i currently work as|i am currently working as)\b",
        r"\b(?:working as a|working as an)\s+\w+",
    ]

    def is_setup_chatter(self, text: str) -> bool:
        """Returns True if the text contains connection or meeting setup chatter."""
        if not text:
            return False
        t_lower = text.lower()
        return any(re.search(pat, t_lower) for pat in self.SETUP_CHATTER_PATTERNS)

    def is_self_introduction(self, text: str) -> bool:
        """Returns True if the speaker is explicitly introducing themselves."""
        if not text:
            return False
        words = text.strip().split()
        if len(words) < 6:
            return False
        t_lower = text.lower()
        # Ensure it's not a question asked to someone else
        if text.strip().endswith("?") and not any(p in t_lower for p in ("introduce myself", "my name is")):
            return False
        return any(re.search(pat, t_lower) for pat in self.SELF_INTRO_PATTERNS)

    def is_interview_question(self, text: str) -> bool:
        """Returns True if text represents a substantive interview question."""
        if not text:
            return False
        if self.is_setup_chatter(text):
            return False

        t_clean = text.strip()
        words = t_clean.split()
        if len(words) < 3:
            return False

        t_lower = t_clean.lower()
        ends_with_q = t_clean.endswith("?")
        starts_with_qs = any(t_lower.startswith(qs) or f" {qs}" in t_lower[:30] for qs in self.QUESTION_STARTERS)

        return (ends_with_q and len(words) >= 4) or starts_with_qs

    def detect(self, transcript: List[Dict[str, Any]]) -> InterviewStartResult:
        """
        Scans transcript turns to detect whether the interview has officially started.
        Returns InterviewStartResult with trigger details.
        """
        if not transcript:
            return InterviewStartResult(interview_started=False)

        # 1. Condition B check: Candidate explicit self-introduction
        for turn in transcript:
            speaker = turn.get("speaker")
            if not speaker or speaker == "System":
                continue
            text = turn.get("text", "").strip()
            if self.is_self_introduction(text):
                res = InterviewStartResult(
                    interview_started=True,
                    trigger="Condition B",
                    candidate_speaker=speaker,
                    turn_id=turn.get("turn_id"),
                    reason=f"Self-introduction detected from {speaker}: \"{text[:70]}\""
                )
                logger.info(f"[InterviewStart] trigger={res.trigger} turn_id={res.turn_id} speaker={res.candidate_speaker}")
                return res

        # 2. Condition A check: Speaker A asks interview question, Speaker B answers with >= 20 words
        last_question_speaker: Optional[str] = None
        last_question_turn_id: Optional[Any] = None

        for turn in transcript:
            speaker = turn.get("speaker")
            if not speaker or speaker == "System":
                continue
            text = turn.get("text", "").strip()
            if not text:
                continue

            # Check if this turn is an interview question
            if self.is_interview_question(text):
                last_question_speaker = speaker
                last_question_turn_id = turn.get("turn_id")
                continue

            # Check if this turn is an answer to a previous question from a different speaker
            if last_question_speaker and speaker != last_question_speaker:
                word_count = len(text.split())
                if word_count >= 20 and not self.is_setup_chatter(text):
                    res = InterviewStartResult(
                        interview_started=True,
                        trigger="Condition A",
                        candidate_speaker=speaker,
                        turn_id=turn.get("turn_id"),
                        reason=(
                            f"Interview question from {last_question_speaker} (turn {last_question_turn_id}) "
                            f"answered by {speaker} with {word_count} words"
                        )
                    )
                    logger.info(f"[InterviewStart] trigger={res.trigger} turn_id={res.turn_id} speaker={res.candidate_speaker}")
                    return res

        return InterviewStartResult(interview_started=False)
