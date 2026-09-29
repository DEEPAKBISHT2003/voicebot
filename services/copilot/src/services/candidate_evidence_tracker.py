import re
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from loguru import logger
from services.copilot.src.services.interview_start_detector import InterviewStartDetector


@dataclass
class SpeakerEvidence:
    speaker: str
    self_intro: bool = False
    long_answers_count: int = 0
    answers_count: int = 0
    questions_asked: int = 0
    handovers_count: int = 0
    resume_matched: bool = False
    score: float = 0.0


class CandidateEvidenceTracker:
    """
    Candidate Stabilization Layer - Part 3 & 4: Evidence Accumulation & Single Candidate Invariant.
    
    Accumulates evidence over conversational turns instead of locking onto the first name utterance:
    Positive Signals:
    - +0.30: Self-introduction ('Hi I am...', 'My name is...', 'I have X years of experience')
    - +0.20: Long answer (>= 30 words, not asking question)
    - +0.20: Answers >= 2 questions
    - +0.25: Matches resume skills/projects
    
    Penalties:
    - -0.30: Asking interview questions ('Can you explain...', 'Tell me about...')
    - -0.50: Handover phrase ('I will hand over to...', 'Over to you...')
    
    Single Candidate Invariant:
    - candidate_count <= 1 at all times.
    - If multiple participants speak, winner = argmax(score) if score >= threshold.
    - All other participants become interviewers.
    """

    HANDOVER_PATTERNS = [
        r"\b(?:i will|i'll|let me|gonna)\s+(?:hand over|pass|hand it over)\s+to\s+([A-Za-z]+)",
        r"\b(?:over to you|take it away|passing over to|passing it to)\s+([A-Za-z]+)",
        r"\b(?:handing over to)\s+([A-Za-z]+)",
    ]

    def __init__(self, start_detector: Optional[InterviewStartDetector] = None):
        self.start_detector = start_detector or InterviewStartDetector()
        self.evidence: Dict[str, SpeakerEvidence] = {}
        self._last_question_speaker: Optional[str] = None
        self._resume_keywords: Set[str] = set()

    def set_resume_keywords(self, resume_text: str):
        """Extracts significant technical and domain keywords from the resume."""
        if not resume_text:
            return
        # Extract alphanumeric words with length >= 4
        words = re.findall(r"\b[A-Za-z][A-Za-z0-9\+\#\.\-]{2,20}\b", resume_text)
        stopwords = {
            "with", "from", "that", "this", "have", "were", "been", "work", "your",
            "about", "more", "project", "experience", "education", "skills", "summary",
            "name", "email", "phone", "curriculum", "vitae", "resume", "responsibilities"
        }
        self._resume_keywords = {w.lower() for w in words if w.lower() not in stopwords and len(w) >= 4}

    def process_turn(self, turn: Dict[str, Any], resume_text: str = "") -> float:
        """
        Processes a single conversational turn and updates evidence for the speaker.
        Returns the updated score for the speaker.
        """
        speaker = turn.get("speaker")
        text = turn.get("text", "").strip()
        if not speaker or speaker == "System" or not text:
            return 0.0

        if resume_text and not self._resume_keywords:
            self.set_resume_keywords(resume_text)

        if speaker not in self.evidence:
            self.evidence[speaker] = SpeakerEvidence(speaker=speaker)

        ev = self.evidence[speaker]
        t_lower = text.lower()
        word_count = len(text.split())

        # Check handover phrases (-0.50)
        has_handover = any(re.search(pat, t_lower) for pat in self.HANDOVER_PATTERNS)
        if has_handover:
            ev.handovers_count += 1
            logger.debug(f"[CandidateEvidenceTracker] Handover detected from {speaker}: \"{text[:60]}\"")

        # Check asking interview questions (-0.30)
        is_question = self.start_detector.is_interview_question(text)
        if is_question:
            ev.questions_asked += 1
            self._last_question_speaker = speaker
        else:
            # If answering a previous question from a different speaker
            if self._last_question_speaker and self._last_question_speaker != speaker:
                if word_count >= 15 and not self.start_detector.is_setup_chatter(text):
                    ev.answers_count += 1

        # Check self introduction (+0.30)
        if not ev.self_intro and self.start_detector.is_self_introduction(text):
            ev.self_intro = True
            logger.debug(f"[CandidateEvidenceTracker] Self-intro detected from {speaker}: \"{text[:60]}\"")

        # Check long answer (+0.20)
        if word_count >= 30 and not is_question and not self.start_detector.is_setup_chatter(text):
            ev.long_answers_count += 1

        # Check resume skill match (+0.25)
        if not ev.resume_matched and self._resume_keywords and not is_question and word_count >= 15:
            turn_words = {w.lower() for w in re.findall(r"\b[A-Za-z][A-Za-z0-9\+\#\.\-]{2,20}\b", text)}
            common_matches = turn_words.intersection(self._resume_keywords)
            if len(common_matches) >= 3:
                ev.resume_matched = True
                logger.debug(f"[CandidateEvidenceTracker] Resume skills match ({len(common_matches)} skills) for {speaker}: {list(common_matches)[:5]}")

        # Compute composite score
        score = 0.0
        if ev.self_intro:
            score += 0.30
        score += min(0.40, ev.long_answers_count * 0.20)
        score += min(0.20, ev.answers_count * 0.10)
        if ev.resume_matched:
            score += 0.25

        score -= (ev.questions_asked * 0.30)
        score -= (ev.handovers_count * 0.50)

        ev.score = round(score, 3)
        return ev.score

    def get_speaker_score(self, speaker: str) -> float:
        """Returns the current score for a given speaker."""
        return self.evidence.get(speaker, SpeakerEvidence(speaker=speaker)).score

    def get_candidate_and_interviewers(
        self,
        speakers: List[str],
        threshold: float = 0.10
    ) -> Tuple[Optional[str], List[str]]:

        """
        Enforces Part 4: Single Candidate Invariant.
        candidate_count <= 1.
        
        Evaluates scores for all provided speakers:
        - Winner = argmax(score) IF max_score >= threshold
        - All other speakers = interviewers
        - If no speaker reaches threshold, candidate is None.
        """
        human_speakers = [s for s in speakers if s and s != "System"]
        if not human_speakers:
            return None, []

        scores: Dict[str, float] = {}
        for spk in human_speakers:
            sc = self.get_speaker_score(spk)
            scores[spk] = sc
            logger.info(f"[RoleScore] speaker={spk} score={sc:.2f}")

        # Select highest scoring speaker
        best_speaker, best_score = max(scores.items(), key=lambda item: item[1])

        if best_score >= threshold:
            candidate = best_speaker
            interviewers = [s for s in human_speakers if s != candidate]
            return candidate, interviewers

        return None, human_speakers
