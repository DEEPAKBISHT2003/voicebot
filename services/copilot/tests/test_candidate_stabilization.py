import os
import uuid
import pytest
import asyncio
from typing import Dict, Any, List

from services.copilot.src.services.interview_start_detector import InterviewStartDetector, InterviewStartResult
from services.copilot.src.services.candidate_evidence_tracker import CandidateEvidenceTracker
from services.copilot.src.services.deterministic_role_classifier import DeterministicRoleClassifier
from services.copilot.src.services.qa_state_machine import QAStateMachine, QAState
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


class DummyRepo:
    def __init__(self):
        self.data: Dict[str, Any] = {}

    async def save_session(self, session_id: str, updates: Dict[str, Any]):
        self.data.update(updates)

    async def get_session(self, session_id: str):
        return self.data


@pytest.mark.asyncio
async def test_case_1_standard_2_participant_no_regression():
    """Case 1: Standard 2-participant interview (no regression)."""
    repo = DummyRepo()
    resume_text = "Candidate: Deepak Bisht\nSkills: Python, FastAPI, Docker, PostgreSQL\nExperience: 4 years"
    
    engine = CopilotSessionEngine(
        session_id="test-2p-session",
        repo=repo,
        resume=resume_text
    )

    # Turn 1: Interviewer asks question
    turn1 = await engine.add_message(
        speaker="Ankit Kumar",
        text="Hello Deepak, welcome to the interview today. Could you please introduce yourself and tell us about your experience?",
        is_final=True
    )
    # Turn 2: Candidate answers with self-introduction and >= 20 words
    turn2 = await engine.add_message(
        speaker="Deepak Bisht",
        text="Thank you Ankit. Hi, my name is Deepak Bisht. I have 4 years of experience as a Python backend engineer working with FastAPI, Docker, and PostgreSQL.",
        is_final=True
    )

    # Turn 3: Technical question
    turn3 = await engine.add_message(
        speaker="Ankit Kumar",
        text="Great. Can you explain how FastAPI handles dependency injection?",
        is_final=True
    )

    # Turn 4: Technical answer
    turn4 = await engine.add_message(
        speaker="Deepak Bisht",
        text="FastAPI uses Depends to inject dependencies. You define a callable and FastAPI calls it and passes the result into the path operation.",
        is_final=True
    )

    # Assertions
    assert engine.interview_started is True
    assert engine.roles_locked is True
    assert engine.locked_candidate == "Deepak Bisht"
    assert engine.session_speaker_roles.get("Deepak Bisht") == "candidate"
    assert engine.session_speaker_roles.get("Ankit Kumar") == "interviewer"
    
    # Invariant: single candidate
    candidates = [s for s, r in engine.session_speaker_roles.items() if r == "candidate"]
    assert len(candidates) == 1
    assert candidates[0] == "Deepak Bisht"

    # QA FSM check
    assert engine.qa_fsm.roles_locked is True


@pytest.mark.asyncio
async def test_case_2_hr_interviewer_candidate_3_participants():
    """
    Case 2: HR + Interviewer + Candidate (3-participant interview).
    Reproduces Session A scenario and verifies that:
    1. Pre-interview chatter addressing Ankit does NOT lock Ankit as candidate.
    2. Deepak's introduction triggers interview start and locks Deepak as candidate.
    3. Ankit is classified as interviewer and Ankit's technical questions are captured by QA FSM.
    """
    repo = DummyRepo()
    resume_text = "Deepak Bisht\nSenior Python Developer\nExperience with FastAPI, Redis, Microservices"

    engine = CopilotSessionEngine(
        session_id="test-3p-session",
        repo=repo,
        resume=resume_text
    )

    # Turn 1: HR setup chatter addressing Ankit (The exact trigger that previously broke Session A)
    turn1 = await engine.add_message(
        speaker="Mahima",
        text="Ankit, can you tell me about how early the green signal works?",
        is_final=True
    )
    # Turn 2: Ankit answers setup chatter
    turn2 = await engine.add_message(
        speaker="Ankit",
        text="Yeah, it usually takes a few seconds to initialize the audio pipeline.",
        is_final=True
    )

    # Verification after setup chatter:
    assert engine.interview_started is False
    assert engine.roles_locked is False
    assert engine.locked_candidate is None

    # Turn 3: HR handover
    turn3 = await engine.add_message(
        speaker="Mahima",
        text="Great, Deepak is here now. I will hand over to Ankit to begin the technical round.",
        is_final=True
    )

    # Turn 4: Ankit asks interview intro question
    turn4 = await engine.add_message(
        speaker="Ankit",
        text="Thanks Mahima. Hi Deepak, could you please introduce yourself and walk us through your projects?",
        is_final=True
    )

    # Turn 5: Deepak self-introduction with details
    turn5 = await engine.add_message(
        speaker="Deepak",
        text="Hello everyone, my name is Deepak. I have 5 years of experience developing microservices using Python, FastAPI, and Redis.",
        is_final=True
    )

    # Role lock must now be triggered
    assert engine.interview_started is True
    assert engine.roles_locked is True
    assert engine.locked_candidate == "Deepak"
    assert engine.session_speaker_roles.get("Deepak") == "candidate"
    assert engine.session_speaker_roles.get("Ankit") == "interviewer"
    assert engine.session_speaker_roles.get("Mahima") == "interviewer"

    # Turn 6: Technical question from Ankit
    turn6 = await engine.add_message(
        speaker="Ankit",
        text="Can you explain how you handle race conditions in distributed systems?",
        is_final=True
    )

    # Verify that Ankit's question is captured by QA FSM (not discarded!)
    assert engine.qa_fsm.state in (QAState.QUESTION_CAPTURED, QAState.CANDIDATE_ANSWERING)
    assert engine.qa_fsm.current_question is not None
    assert engine.qa_fsm.current_question["speaker"] == "Ankit"


@pytest.mark.asyncio
async def test_case_3_pre_interview_chatter_does_not_lock():
    """Case 3: Pre-interview connection chatter does NOT lock candidate."""
    detector = InterviewStartDetector()
    tracker = CandidateEvidenceTracker(start_detector=detector)

    chatter_transcript = [
        {"turn_id": 1, "speaker": "Mahima", "text": "Hello Ankit, can you hear me properly?"},
        {"turn_id": 2, "speaker": "Ankit", "text": "Yes Mahima, I can hear you loud and clear. Is my screen visible?"},
        {"turn_id": 3, "speaker": "Mahima", "text": "Yes, your screen is visible. Let's wait a moment for everyone to join."},
        {"turn_id": 4, "speaker": "Deepak", "text": "Hi, I just joined. Can you hear us?"},
        {"turn_id": 5, "speaker": "Ankit", "text": "Yes Deepak, we can hear you. Let me share my screen."},
    ]

    res = detector.detect(chatter_transcript)
    assert res.interview_started is False

    for t in chatter_transcript:
        tracker.process_turn(t)

    candidate, interviewers = tracker.get_candidate_and_interviewers(["Mahima", "Ankit", "Deepak"], threshold=0.10)
    assert candidate is None


@pytest.mark.asyncio
async def test_case_4_interviewer_handoff_does_not_confuse_candidate():
    """Case 4: Interviewer handoff does not confuse candidate."""
    repo = DummyRepo()
    engine = CopilotSessionEngine(session_id="test-handoff-session", repo=repo)

    await engine.add_message(
        speaker="Coordinator",
        text="Welcome everyone. I will hand over to Interviewer1.",
        is_final=True
    )
    await engine.add_message(
        speaker="Interviewer1",
        text="Thanks. Candidate, can you walk me through your latest project?",
        is_final=True
    )
    await engine.add_message(
        speaker="Candidate",
        text="Sure! In my recent project, I built a high-throughput data processing pipeline using Kafka and FastAPI with asynchronous workers. We ingested millions of events daily and streamed them reliably into our backend databases.",
        is_final=True
    )
    await engine.add_message(
        speaker="Interviewer1",
        text="Nice. Now over to you Interviewer2 for the database questions.",
        is_final=True
    )
    await engine.add_message(
        speaker="Interviewer2",
        text="Candidate, what indexing strategies did you use in PostgreSQL?",
        is_final=True
    )


    assert engine.roles_locked is True
    assert engine.locked_candidate == "Candidate"
    assert engine.session_speaker_roles.get("Candidate") == "candidate"
    assert engine.session_speaker_roles.get("Coordinator") == "interviewer"
    assert engine.session_speaker_roles.get("Interviewer1") == "interviewer"
    assert engine.session_speaker_roles.get("Interviewer2") == "interviewer"

    # Both interviewers should be able to ask questions captured by QA FSM
    assert engine.qa_fsm.current_question is not None
    assert engine.qa_fsm.current_question["speaker"] == "Interviewer2"


@pytest.mark.asyncio
async def test_case_5_dual_candidate_prevention():
    """Case 5: Dual candidate prevention (Single candidate invariant candidate_count <= 1)."""
    class MockDualCandidateFallback:
        async def identify_roles(self, *args, **kwargs):
            return {
                "speakers": {
                    "Alice": {"role": "interviewer", "confidence": 0.90, "reasoning": "Interviewer"},
                    "Bob Smith": {"role": "candidate", "confidence": 0.90, "reasoning": "Candidate 1"},
                    "Charlie": {"role": "candidate", "confidence": 0.85, "reasoning": "Candidate 2 (Adversarial collision)"}
                }
            }

    classifier = DeterministicRoleClassifier(fallback_identifier=MockDualCandidateFallback())
    
    transcript = [
        {"turn_id": 1, "speaker": "Alice", "text": "Can you explain your experience in Python?"},
        {"turn_id": 2, "speaker": "Bob Smith", "text": "I worked on distributed systems and microservices with FastAPI for three years at ABC Corp."},
        {"turn_id": 3, "speaker": "Charlie", "text": "I also designed the cloud infrastructure and implemented kubernetes clusters for two years."},
    ]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=["Alice", "Bob Smith", "Charlie"],
        resume="Bob Smith\nPython Backend Engineer\nMicroservices"
    )

    speakers = res.get("speakers", {})
    candidates = [p for p, info in speakers.items() if info.get("role") == "candidate"]
    
    # Invariant: candidate_count <= 1 strictly enforced
    assert len(candidates) == 1
    assert candidates[0] == "Bob Smith"
    # Charlie must have been demoted to interviewer
    assert speakers["Charlie"]["role"] == "interviewer"
    assert "single candidate invariant" in speakers["Charlie"]["reasoning"].lower()




@pytest.mark.asyncio
async def test_case_6_fsm_rescue_safety_net():
    """Verifies that an unconfirmed candidate asking a question triggers [FSMRescue] and avoids starvation."""
    fsm = QAStateMachine(session_id="test-rescue-fsm", roles_locked=False)

    # Turn from speaker with tentative role "candidate" asking an interview question before lock
    turn = {
        "turn_id": 1,
        "id": "turn-1",
        "speaker": "Ankit",
        "speaker_role": "candidate",
        "text": "Could you please introduce yourself and tell us about your background?"
    }

    await fsm.on_turn(turn)
    # Question should be rescued and captured!
    assert fsm.state == QAState.QUESTION_CAPTURED
    assert fsm.current_question is not None
    assert fsm.current_question["speaker"] == "Ankit"


@pytest.mark.asyncio
async def test_case_7_reconnect_and_disk_restoration(tmp_path):
    """Verifies that role lock state persists to disk and rehydrates seamlessly upon reconnect."""
    session_id = f"test-reconnect-{uuid.uuid4().hex[:8]}"
    
    repo = DummyRepo()
    repo.directory = str(tmp_path)
    engine1 = CopilotSessionEngine(
        session_id=session_id,
        repo=repo,
        resume="Deepak Bisht\nPython Backend Engineer",
        jd="Senior Python Engineer"
    )

    await engine1.add_message(
        speaker="Ankit",
        text="Hi Deepak, can you please walk us through your experience?",
        is_final=True
    )
    await engine1.add_message(
        speaker="Deepak",
        text="Sure! I have 4 years of experience building Python microservices with FastAPI, PostgreSQL, and Redis.",
        is_final=True
    )

    assert engine1.roles_locked is True
    assert engine1.locked_candidate == "Deepak"

    # Verify role_lock.json exists on disk
    lock_file = os.path.join(str(tmp_path), session_id, "role_lock.json")
    assert os.path.exists(lock_file)

    # Simulate fresh session rehydration (e.g. server restart or client reconnect)
    engine2 = CopilotSessionEngine(
        session_id=session_id,
        repo=repo,
        resume="Deepak Bisht\nPython Backend Engineer",
        jd="Senior Python Engineer"
    )

    assert engine2.roles_locked is True
    assert engine2.locked_candidate == "Deepak"
    assert engine2.session_speaker_roles.get("Deepak") == "candidate"
    assert engine2.session_speaker_roles.get("Ankit") == "interviewer"
    assert engine2.qa_fsm.roles_locked is True

