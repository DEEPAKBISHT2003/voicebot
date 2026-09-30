import pytest
import asyncio
import io
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock
from loguru import logger
from services.copilot.src.services.deterministic_role_classifier import DeterministicRoleClassifier
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


@pytest.fixture
def classifier():
    mock_llm = MagicMock()
    mock_llm.identify_roles = AsyncMock(return_value={"speakers": {}})
    return DeterministicRoleClassifier(fallback_identifier=mock_llm)


@pytest.mark.asyncio
async def test_scenario_1_resume_owner_is_candidate(classifier):
    """
    Scenario 1: Resume owner is indeed the candidate.
    Alice Smith (Resume) speaks as candidate.
    Bob Jones speaks as interviewer.
    Verify: Alice=candidate (>=0.80), Bob=interviewer (>=0.80), 0 conflict detected.
    """
    resume = "Alice Smith\nLead AI Architect\nPyTorch, LLMs, Agents"
    transcript = [
        {"speaker": "Bob Jones", "text": "Welcome to the interview Alice. Can you introduce yourself?"},
        {"speaker": "Alice Smith", "text": "Thank you Bob. I'm Alice, currently working as an AI developer at TechCorp."}
    ]
    participants = ["Alice Smith", "Bob Jones"]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=resume
    )
    speakers = res["speakers"]
    assert speakers["Alice Smith"]["role"] == "candidate"
    assert speakers["Alice Smith"]["confidence"] >= 0.80
    assert speakers["Bob Jones"]["role"] == "interviewer"
    assert speakers["Bob Jones"]["confidence"] >= 0.80


@pytest.mark.asyncio
async def test_scenario_2_resume_owner_is_interviewer_conflict_remediation(classifier):
    """
    Scenario 2: Resume owner is actually the interviewer (Incident INC-2026-0930-01).
    Resume uploaded: Ankit Kumar.
    Interview reality: Ankit Kumar is asking questions; Deepak Bisht is introducing himself and answering.
    Verify:
    - Role conflict detected and logged with exact required log lines (Rule 4).
    - Ankit Kumar assigned interviewer (confidence 0.91).
    - Deepak Bisht assigned candidate (confidence 0.94).
    """
    resume = "Ankit Kumar\nSenior Data & AI Specialist"
    transcript = [
        {"speaker": "Ankit Kumar", "text": "Deepak, can you please introduce yourself?"},
        {"speaker": "Deepak Bisht", "text": "Yes, I'm Deepak, currently serving as a AI developer at apps logic. One of my recent project is an interview platform where a bot joins the meeting."},
        {"speaker": "Ankit Kumar", "text": "OK. So moving forward to next question, how did you validate and test the reliability claims?"},
        {"speaker": "Deepak Bisht", "text": "I validate based on evaluation metrics, latency benchmarking and precision."}
    ]
    participants = ["Ankit Kumar", "Deepak Bisht"]

    log_stream = io.StringIO()
    sink_id = logger.add(log_stream, format="{message}")
    try:
        res = await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume
        )
    finally:
        logger.remove(sink_id)

    speakers = res["speakers"]
    assert speakers["Ankit Kumar"]["role"] == "interviewer"
    assert speakers["Ankit Kumar"]["confidence"] == 0.91
    assert speakers["Deepak Bisht"]["role"] == "candidate"
    assert speakers["Deepak Bisht"]["confidence"] == 0.94

    # Verify Rule 4 log output captured from loguru
    log_text = log_stream.getvalue()
    assert "[RoleClassifier] Resume candidate = Ankit Kumar" in log_text
    assert "Ankit Kumar = Interviewer" in log_text
    assert "Deepak Bisht = Candidate" in log_text
    assert "[RoleClassifier] Role conflict detected" in log_text
    assert "[RoleClassifier] Reassigning roles" in log_text
    assert "Ankit Kumar interviewer=0.91" in log_text
    assert "Deepak Bisht candidate=0.94" in log_text


@pytest.mark.asyncio
async def test_scenario_3_candidate_name_differs_slightly_with_behavioral_boost(classifier):
    """
    Scenario 3: Candidate name differs slightly from resume.
    Resume: Deepak Kumar.
    Participant: Deepak Bisht.
    Deepak Bisht introduces himself and explains project experience.
    Verify: Deepak Bisht is resolved as candidate with confidence >= 0.80 deterministically.
    """
    resume = "Deepak Kumar\nSenior Backend Engineer"
    transcript = [
        {"speaker": "Interviewer Jane", "text": "Let's start the interview. Can you please introduce yourself?"},
        {"speaker": "Deepak Bisht", "text": "Sure, I'm Deepak, currently serving as a software engineer. One of my recent project involves distributed streaming."}
    ]
    participants = ["Deepak Bisht", "Interviewer Jane"]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=resume
    )
    speakers = res["speakers"]
    assert speakers["Deepak Bisht"]["role"] == "candidate"
    assert speakers["Deepak Bisht"]["confidence"] >= 0.80
    assert speakers["Interviewer Jane"]["role"] == "interviewer"
    assert speakers["Interviewer Jane"]["confidence"] >= 0.80


@pytest.mark.asyncio
async def test_scenario_4_two_interviewers_single_candidate_invariant(classifier):
    """
    Scenario 4: Panel interview with two interviewers and one candidate.
    Participants: Alice Smith (Candidate), Bob Jones (Interviewer 1), Charlie Davis (Interviewer 2).
    Verify single candidate invariant:
    - candidate_count == 1 (Alice Smith)
    - interviewer_count == 2 (Bob Jones, Charlie Davis)
    """
    resume = "Alice Smith\nStaff Systems Engineer"
    transcript = [
        {"speaker": "Bob Jones", "text": "Welcome Alice. Charlie and I will be conducting your technical round today."},
        {"speaker": "Alice Smith", "text": "Thank you Bob and Charlie. Excited to speak with both of you."},
        {"speaker": "Charlie Davis", "text": "Can you walk us through how you designed your event bus?"}
    ]
    participants = ["Alice Smith", "Bob Jones", "Charlie Davis"]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=resume
    )
    speakers = res["speakers"]
    assert speakers["Alice Smith"]["role"] == "candidate"
    assert speakers["Alice Smith"]["confidence"] >= 0.80
    assert speakers["Bob Jones"]["role"] == "interviewer"
    assert speakers["Bob Jones"]["confidence"] >= 0.80
    assert speakers["Charlie Davis"]["role"] == "interviewer"
    assert speakers["Charlie Davis"]["confidence"] >= 0.80

    candidates = [p for p, info in speakers.items() if info["role"] == "candidate"]
    interviewers = [p for p, info in speakers.items() if info["role"] == "interviewer"]
    assert len(candidates) == 1
    assert len(interviewers) == 2


@pytest.mark.asyncio
async def test_scenario_5_session_76873b09_replay_end_to_end(tmp_path):
    """
    Scenario 5: End-to-end replay of actual production transcript from session 76873b09-d7f7-42a1-a36e-380ab708be04.
    Under inverted roles, Ankit Kumar matched resume and was crowned candidate.
    With behavioral conflict remediation, roles dynamically invert, FSM replays,
    and genuine interview Q&A pairs ('Can you please introduce yourself?' and 'what data engineer is basically?') are extracted!
    """
    conn = sqlite3.connect("db.sqlite3")
    cur = conn.cursor()
    cur.execute("SELECT transcript FROM copilot_sessions WHERE session_id = ?", ("76873b09-d7f7-42a1-a36e-380ab708be04",))
    row = cur.fetchone()
    conn.close()

    assert row is not None, "Session 76873b09 transcript must exist in db.sqlite3"
    raw_transcript = json.loads(row[0])

    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    mock_llm = MagicMock()
    mock_llm.identify_roles = AsyncMock(return_value={"speakers": {}})

    engine = CopilotSessionEngine(
        session_id="test_76873b09_replay",
        resume="Ankit Kumar\nAI Developer",
        repo=mock_repo
    )
    # Ensure role_identifier uses mock LLM for fallback to keep tests fast & hermetic
    engine.role_identifier = DeterministicRoleClassifier(fallback_identifier=mock_llm)

    # Feed turns progressively as would occur in production
    for turn in raw_transcript:
        spk = turn.get("speaker") or turn.get("speaker_name")
        txt = turn.get("text", "")
        await engine.add_message(speaker=spk, text=txt, is_final=True)
        await asyncio.sleep(0.01)

    # Give background tasks time to complete
    await asyncio.sleep(0.1)

    # Verify roles inverted correctly
    assert engine.session_speaker_roles.get("Ankit Kumar") == "interviewer"
    assert engine.session_speaker_roles.get("Deepak Bisht") == "candidate"
    assert engine.session_speaker_confidences.get("Ankit Kumar") >= 0.90
    assert engine.session_speaker_confidences.get("Deepak Bisht") >= 0.90

    # Finalize any open QA pair
    await engine.qa_fsm.finalize_current_qa()

    # Verify confirmed QA pairs contain genuine questions, not noise
    assert len(engine.confirmed_qa_pairs) >= 1
    questions_text = " ".join(qa["question"] for qa in engine.confirmed_qa_pairs)
    assert "introduce yourself" in questions_text or "what data engineer is basically" in questions_text


@pytest.mark.asyncio
async def test_rule_5_evaluation_gate(tmp_path):
    """
    Rule 5 Evaluation Gate:
    Verify:
    1. Blocks evaluation when candidate_count == 0 or > 1
    2. Blocks evaluation when interviewer_count < 1
    3. Blocks evaluation when candidate_confidence < 0.70
    4. Passes evaluation when candidate_count == 1, interviewer_count >= 1, candidate_confidence >= 0.70
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    engine = CopilotSessionEngine(
        session_id="test_gate_sess",
        repo=mock_repo
    )

    log_stream = io.StringIO()
    sink_id = logger.add(log_stream, format="{message}")
    try:
        # Case 1: 0 candidates
        engine.session_speaker_roles = {"Bob": "interviewer", "Charlie": "interviewer"}
        engine.session_speaker_confidences = {"Bob": 0.85, "Charlie": 0.85}
        assert engine._verify_evaluation_gate() is False
        assert "Expected exactly 1 candidate, found 0" in log_stream.getvalue()

        # Case 2: 2 candidates
        log_stream.truncate(0)
        log_stream.seek(0)
        engine.session_speaker_roles = {"Alice": "candidate", "Eve": "candidate", "Bob": "interviewer"}
        engine.session_speaker_confidences = {"Alice": 0.85, "Eve": 0.85, "Bob": 0.85}
        assert engine._verify_evaluation_gate() is False
        assert "Expected exactly 1 candidate, found 2" in log_stream.getvalue()

        # Case 3: 0 interviewers
        log_stream.truncate(0)
        log_stream.seek(0)
        engine.session_speaker_roles = {"Alice": "candidate"}
        engine.session_speaker_confidences = {"Alice": 0.90}
        assert engine._verify_evaluation_gate() is False
        assert "Expected at least 1 interviewer, found 0" in log_stream.getvalue()

        # Case 4: Candidate confidence < 0.70
        log_stream.truncate(0)
        log_stream.seek(0)
        engine.session_speaker_roles = {"Alice": "candidate", "Bob": "interviewer"}
        engine.session_speaker_confidences = {"Alice": 0.55, "Bob": 0.85}
        assert engine._verify_evaluation_gate() is False
        assert "Candidate 'Alice' role confidence (0.55) < 0.70 threshold" in log_stream.getvalue()

        # Case 5: Valid role topology
        engine.session_speaker_roles = {"Alice": "candidate", "Bob": "interviewer"}
        engine.session_speaker_confidences = {"Alice": 0.94, "Bob": 0.91}
        assert engine._verify_evaluation_gate() is True
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
async def test_structured_telemetry_emission(classifier):
    """
    Verify exact structured telemetry tags:
    - [RoleConflictDetected]
    - [RoleConflictResolved]
    """
    resume = "Ankit Kumar\nAI Developer"
    transcript = [
        {"speaker": "Ankit Kumar", "text": "Deepak, can you please introduce yourself?"},
        {"speaker": "Deepak Bisht", "text": "Yes, I'm Deepak, currently serving as a AI developer at apps logic. One of my recent project is an interview platform."}
    ]
    participants = ["Ankit Kumar", "Deepak Bisht"]

    log_stream = io.StringIO()
    sink_id = logger.add(log_stream, format="{message}")
    try:
        await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume,
            session_id="test_telemetry_sess"
        )
    finally:
        logger.remove(sink_id)

    log_text = log_stream.getvalue()
    assert "[RoleConflictDetected]" in log_text
    assert "session_id=test_telemetry_sess" in log_text
    assert "resume_candidate='Ankit Kumar'" in log_text
    assert "detected_interviewer='Ankit Kumar'" in log_text
    assert "candidate_peer='Deepak Bisht'" in log_text
    assert "[RoleConflictResolved]" in log_text
    assert "strategy='behavioral_inversion'" in log_text


@pytest.mark.asyncio
async def test_candidate_clarification_does_not_trigger_conflict(classifier):
    """
    Scenario: Candidate (Alice Smith) asks clarification questions during the interview.
    Interviewer (Bob Jones) asks technical question.
    Candidate (Alice Smith) asks: "Do you mean horizontal partitioning or vertical sharding?"
    Interviewer (Bob Jones) clarifies.
    Candidate (Alice Smith) provides the full architectural answer.
    Verify:
    - Alice Smith remains candidate.
    - Bob Jones remains interviewer.
    - No role conflict detected or flipped.
    """
    resume = "Alice Smith\nCloud Data Architect"
    transcript = [
        {"speaker": "Bob Jones", "text": "Alice, can you explain how you handle high-throughput database sharding?"},
        {"speaker": "Alice Smith", "text": "Do you mean horizontal partitioning across regions or vertical sharding by tenant?"},
        {"speaker": "Bob Jones", "text": "Horizontal partitioning across regions."},
        {"speaker": "Alice Smith", "text": "I'm Alice and in my recent project at TechCorp, we used Citus on top of PostgreSQL to distribute tables by hash."}
    ]
    participants = ["Alice Smith", "Bob Jones"]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=resume,
        session_id="test_clarif_sess"
    )
    speakers = res["speakers"]
    assert speakers["Alice Smith"]["role"] == "candidate"
    assert speakers["Bob Jones"]["role"] == "interviewer"


@pytest.mark.asyncio
async def test_interviewer_answering_candidate_does_not_trigger_conflict(classifier):
    """
    Scenario: Interviewer (Bob Jones) speaks at length explaining company architecture
    when candidate asks a question.
    Verify: Interviewer does not falsely trigger candidate role.
    """
    resume = "Alice Smith\nSenior AI Engineer"
    transcript = [
        {"speaker": "Bob Jones", "text": "Can you please introduce yourself Alice?"},
        {"speaker": "Alice Smith", "text": "I'm Alice, currently working as an AI developer. In my project we built RAG systems."},
        {"speaker": "Alice Smith", "text": "Could you also tell me what tech stack the team currently uses for LLM orchestration?"},
        {"speaker": "Bob Jones", "text": "At our company, we are using LangGraph and FastAPI with vLLM for local model inference across multiple GPU clusters."}
    ]
    participants = ["Alice Smith", "Bob Jones"]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=resume,
        session_id="test_int_ans_sess"
    )
    speakers = res["speakers"]
    assert speakers["Alice Smith"]["role"] == "candidate"
    assert speakers["Bob Jones"]["role"] == "interviewer"


@pytest.mark.asyncio
async def test_suppressed_when_auto_resolve_disabled():
    """
    Verify [RoleConflictSuppressed] when auto_resolve_conflict=False.
    """
    classifier = DeterministicRoleClassifier(
        enable_conflict_detection=True,
        auto_resolve_conflict=False
    )
    resume = "Ankit Kumar\nAI Developer"
    transcript = [
        {"speaker": "Ankit Kumar", "text": "Deepak, can you please introduce yourself?"},
        {"speaker": "Deepak Bisht", "text": "Yes, I'm Deepak, currently serving as a AI developer at apps logic. One of my recent project is an interview platform."}
    ]
    participants = ["Ankit Kumar", "Deepak Bisht"]

    log_stream = io.StringIO()
    sink_id = logger.add(log_stream, format="{message}")
    try:
        res = await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume,
            session_id="test_suppress_sess"
        )
    finally:
        logger.remove(sink_id)

    log_text = log_stream.getvalue()
    assert "[RoleConflictDetected]" in log_text
    assert "[RoleConflictSuppressed]" in log_text
    assert "reason='auto_resolve_disabled'" in log_text
    # Without auto-resolve, resume owner remains candidate (fallback to standard priority)
    assert res["speakers"]["Ankit Kumar"]["role"] == "candidate"

