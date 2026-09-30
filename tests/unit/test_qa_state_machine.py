import os
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.copilot.src.core.config import Settings
from services.copilot.src.services.qa_state_machine import (
    QAStateMachine,
    QAState,
    get_qa_fsm_metrics,
    reset_qa_fsm_metrics,
    compare_legacy_vs_fsm
)
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


@pytest.fixture(autouse=True)
def clean_metrics():
    reset_qa_fsm_metrics()
    yield
    reset_qa_fsm_metrics()


@pytest.fixture
def temp_storage(tmp_path):
    storage_dir = str(tmp_path / "interviews")
    os.makedirs(storage_dir, exist_ok=True)
    return storage_dir


@pytest.mark.asyncio
async def test_scenario_1_normal_qa_flow():
    """
    Scenario 1: Normal Interviewer -> Candidate -> Interviewer flow.
    Verifies state transitions and completion event emitted on speaker change.
    """
    completed_records = []

    async def on_qa_done(record):
        completed_records.append(record)

    fsm = QAStateMachine(session_id="test_sess_1", silence_threshold=5.0, on_qa_completed=on_qa_done)
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    # Interviewer asks question
    turn1 = {"turn_id": 1, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Can you explain how indexing works in PostgreSQL?"}
    res1 = await fsm.on_turn(turn1)
    assert res1 is None
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # Candidate answers
    turn2 = {"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "PostgreSQL uses B-Tree indexes by default to provide logarithmic lookup time."}
    res2 = await fsm.on_turn(turn2)
    assert res2 is None
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Interviewer speaks next (speaker change completes QA)
    turn3 = {"turn_id": 3, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "That makes sense. What about GiST indexes?"}
    res3 = await fsm.on_turn(turn3)

    assert res3 is not None
    assert len(completed_records) == 1
    record = completed_records[0]
    assert record["pair_id"] == "Q1_A2"
    assert record["question_turn_id"] == 1
    assert record["answer_turn_ids"] == [2]
    assert record["question"] == "Can you explain how indexing works in PostgreSQL?"
    assert "PostgreSQL uses B-Tree indexes" in record["answer"]
    assert record["confidence"] == 1.0

    # Since turn 3 was itself a question, FSM should now be in QUESTION_CAPTURED for turn 3
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert fsm.current_question["turn_id"] == 3

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_completed_pairs_total"] == 1
    assert metrics["qa_fsm_speaker_change_completions_total"] == 1
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_2_candidate_silence_completion():
    """
    Scenario 2: Candidate silence completion (watchdog timer).
    Uses a 0.1s silence threshold to test timer-driven completion without lag.
    """
    completed_records = []

    async def on_qa_done(record):
        completed_records.append(record)

    fsm = QAStateMachine(session_id="test_sess_2", silence_threshold=0.1, on_qa_completed=on_qa_done)

    # Question
    await fsm.on_turn({"turn_id": 10, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "What is eventual consistency?"})
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # Answer
    await fsm.on_turn({"turn_id": 11, "speaker": "Candidate", "speaker_role": "candidate", "text": "It means all replicas will eventually converge to the same state given no new updates."})
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Wait for silence timer to expire (threshold is 0.1s, wait 0.25s)
    await asyncio.sleep(0.25)

    assert len(completed_records) == 1
    record = completed_records[0]
    assert record["pair_id"] == "Q10_A11"
    assert record["question_turn_id"] == 10
    assert record["answer_turn_ids"] == [11]
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_completed_pairs_total"] == 1
    assert metrics["qa_fsm_silence_completions_total"] == 1
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_3_meeting_end_finalization():
    """
    Scenario 3: Meeting end / finalization completion.
    Verifies that finalize_current_qa flushes in-flight answer turns cleanly.
    """
    completed_records = []

    async def on_qa_done(record):
        completed_records.append(record)

    fsm = QAStateMachine(session_id="test_sess_3", silence_threshold=5.0, on_qa_completed=on_qa_done)

    await fsm.on_turn({"turn_id": 20, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "How do you handle deadlocks in MySQL?"})
    await fsm.on_turn({"turn_id": 21, "speaker": "Candidate", "speaker_role": "candidate", "text": "We configure innodb_lock_wait_timeout and use dead-lock detection with rollbacks."})
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Meeting ends before another turn or silence timeout
    res = await fsm.finalize_current_qa()
    assert res is not None
    assert len(completed_records) == 1
    assert completed_records[0]["pair_id"] == "Q20_A21"
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_completed_pairs_total"] == 1
    assert metrics["qa_fsm_meeting_end_completions_total"] == 1

    # Calling finalize again when idle returns None cleanly
    res_second = await fsm.finalize_current_qa()
    assert res_second is None
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_4_interrupted_multi_turn_answer():
    """
    Scenario 4: Multi-turn answer aggregation followed by interviewer interruption.
    Verifies that multiple candidate turns are joined in chronological order.
    """
    completed_records = []

    async def on_qa_done(record):
        completed_records.append(record)

    fsm = QAStateMachine(session_id="test_sess_4", silence_threshold=5.0, on_qa_completed=on_qa_done)

    await fsm.on_turn({"turn_id": 1, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Describe your deployment pipeline."})
    await fsm.on_turn({"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "We use GitHub Actions for CI to run automated unit tests."})
    await fsm.on_turn({"turn_id": 3, "speaker": "Candidate", "speaker_role": "candidate", "text": "Then ArgoCD deploys the manifests to our Kubernetes cluster via GitOps."})

    assert len(fsm.current_answer_turns) == 2
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Interviewer statement (not a question)
    turn4 = {"turn_id": 4, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Got it, that is very clear."}
    await fsm.on_turn(turn4)

    assert len(completed_records) == 1
    rec = completed_records[0]
    assert rec["pair_id"] == "Q1_A2_3"
    assert rec["question_turn_id"] == 1
    assert rec["answer_turn_ids"] == [2, 3]
    assert "GitHub Actions" in rec["answer"] and "ArgoCD" in rec["answer"]
    # Since turn 4 was not a question, state should be WAITING_FOR_QUESTION
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_5_consecutive_interviewer_question_expansion():
    """
    Scenario 5: Consecutive interviewer turns expand the current question.
    """
    fsm = QAStateMachine(session_id="test_sess_5", silence_threshold=5.0)

    # First turn
    await fsm.on_turn({"turn_id": 100, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Can you tell me about your experience with Redis caching?"})
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # Follow-up before candidate answers
    await fsm.on_turn({"turn_id": 101, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Specifically around cluster replication and eviction policies?"})
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert "Redis caching?" in fsm.current_question["text"]
    assert "cluster replication" in fsm.current_question["text"]
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_6_feature_flag_disabled_fallback(temp_storage):
    """
    Scenario 6: Feature flag ENABLE_QA_FSM=False falls back to legacy loop.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = temp_storage
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_QA_FSM", False):
        session = CopilotSessionEngine(
            session_id="sess_legacy_fallback",
            jd="Python engineer",
            resume="Alice engineer",
            repo=mock_repo
        )
        assert session.enable_qa_fsm is False

        # When starting QA checkpoint with flag False, starts legacy task
        session.start_qa_checkpoint()
        assert session.qa_checkpoint_running is True
        assert session.qa_checkpoint_task is not None

        metrics = get_qa_fsm_metrics()
        assert metrics["qa_fsm_fallback_to_legacy_total"] == 1

        session.stop_qa_checkpoint()
        assert session.qa_checkpoint_running is False


@pytest.mark.asyncio
async def test_scenario_7_session_engine_fsm_integration(temp_storage):
    """
    Scenario 7: SessionEngine integration with ENABLE_QA_FSM=True.
    Verifies that incoming turns via add_message feed FSM and trigger both dynamic follow-ups and accuracy evaluation.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = temp_storage
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_QA_FSM", True), \
         patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", False):
        session = CopilotSessionEngine(
            session_id="sess_fsm_integration",
            jd="Senior Backend Engineer",
            resume="Experienced Python Architect",
            repo=mock_repo
        )
        assert session.enable_qa_fsm is True
        session.start_qa_checkpoint()

        # Verify no 15s polling task was created
        assert session.qa_checkpoint_task is None
        assert session.qa_checkpoint_running is True

        # Mock downstream dynamic suggestions and accuracy evaluation
        session._trigger_dynamic_suggestions_for_confirmed_qa = AsyncMock()
        session._trigger_accuracy_evaluation_for_confirmed_qa = AsyncMock()

        # Turn 1: Interviewer Question
        await session.add_message("Interviewer", "How do you structure database migrations in a production service?", is_final=True)
        assert session.qa_fsm.get_state() == QAState.QUESTION_CAPTURED

        # Turn 2: Candidate Answer
        await session.add_message("Candidate", "We use Alembic with backward-compatible schema changes and blue-green deployments.", is_final=True)
        assert session.qa_fsm.get_state() == QAState.CANDIDATE_ANSWERING

        # Turn 3: Interviewer Next Question
        await session.add_message("Interviewer", "How do you handle zero-downtime rollbacks when a migration fails?", is_final=True)

        # Wait briefly for callbacks
        await asyncio.sleep(0.05)

        # Check confirmed QA pairs
        assert len(session.confirmed_qa_pairs) == 1
        rec = session.confirmed_qa_pairs[0]
        assert "How do you structure database migrations" in rec["question"]
        assert "Alembic" in rec["answer"]

        # Check downstream triggers
        session._trigger_dynamic_suggestions_for_confirmed_qa.assert_awaited_once()
        session._trigger_accuracy_evaluation_for_confirmed_qa.assert_awaited_once()

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_scenario_8_session_finalize_report_fsm(temp_storage):
    """
    Scenario 8: Calling finalize_report() with active QA pair flushes QA via FSM.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = temp_storage
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_QA_FSM", True):
        session = CopilotSessionEngine(
            session_id="sess_finalize_fsm",
            jd="JD",
            resume="Resume",
            repo=mock_repo
        )

        session._trigger_dynamic_suggestions_for_confirmed_qa = AsyncMock()
        session._trigger_accuracy_evaluation_for_confirmed_qa = AsyncMock()
        session.evaluation_service.evaluate_session = AsyncMock(return_value={
            "overall_score": 85,
            "strengths": ["Clear communication"],
            "growth_areas": [],
            "technical_competence": 85,
            "communication_clarity": 85,
            "problem_solving": 85
        })

        # Add question and answer turns
        await session.add_message("Interviewer", "What is an inverted index?", is_final=True)
        await session.add_message("Candidate", "An inverted index maps words to their location in documents.", is_final=True)
        assert session.qa_fsm.get_state() == QAState.CANDIDATE_ANSWERING

        # Finalize report
        report = await session.finalize_report()
        assert report is not None
        assert len(session.confirmed_qa_pairs) == 1
        assert session.confirmed_qa_pairs[0]["pair_id"] == "Q1_A2"


def test_scenario_9_shadow_mode_comparator():
    """
    Scenario 9: Shadow-mode comparison utility validation.
    """
    # 1. Exact Match
    legacy = {
        "qa_ready": True,
        "question_turn_id": 1,
        "answer_turn_ids": [2, 3],
        "question": "What is Docker?",
        "answer": "A containerization platform."
    }
    fsm = {
        "pair_id": "Q1_A2_3",
        "question_turn_id": 1,
        "answer_turn_ids": [2, 3],
        "question": "What is Docker?",
        "answer": "A containerization platform."
    }
    res_exact = compare_legacy_vs_fsm(legacy, fsm)
    assert res_exact["status"] == "EXACT_MATCH"
    assert res_exact["parity"] is True
    assert res_exact["answer_ids_jaccard"] == 1.0

    # 2. Semantic Equivalent (partial answer ID overlap)
    legacy_partial = {
        "qa_ready": True,
        "question_turn_id": 1,
        "answer_turn_ids": [2, 3, 4],
        "question": "What is Docker?",
        "answer": "A containerization platform."
    }
    res_eq = compare_legacy_vs_fsm(legacy_partial, fsm)
    assert res_eq["status"] == "SEMANTIC_EQUIVALENT"
    assert res_eq["parity"] is True

    # 3. Discrepancy
    legacy_no = {"qa_ready": False}
    res_disc = compare_legacy_vs_fsm(legacy_no, fsm)
    assert res_disc["status"] == "DISCREPANCY"
    assert res_disc["parity"] is False

    # 4. Both Not Ready
    res_both_none = compare_legacy_vs_fsm({"qa_ready": False}, None)
    assert res_both_none["status"] == "EXACT_MATCH"
    assert res_both_none["parity"] is True


@pytest.mark.asyncio
async def test_scenario_10_multiple_sequential_qa_pairs():
    """
    Scenario 10: Multiple full Q/A cycles in a single session.
    Verifies that state cleanly resets and handles 3 consecutive Q/A pairs.
    """
    completed_pairs = []

    async def on_qa(rec):
        completed_pairs.append(rec)

    fsm = QAStateMachine(session_id="test_sess_seq", silence_threshold=5.0, on_qa_completed=on_qa)

    # Q1 + A1
    await fsm.on_turn({"turn_id": 1, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "What is Python GIL?"})
    await fsm.on_turn({"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "Global Interpreter Lock prevents multiple threads from executing Python bytecodes simultaneously."})

    # Q2 + A2
    await fsm.on_turn({"turn_id": 3, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "How do you bypass it?"})
    assert len(completed_pairs) == 1
    assert completed_pairs[0]["pair_id"] == "Q1_A2"

    await fsm.on_turn({"turn_id": 4, "speaker": "Candidate", "speaker_role": "candidate", "text": "By using multiprocessing or native C extensions."})

    # Q3 + A3
    await fsm.on_turn({"turn_id": 5, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Great, what about asyncio?"})
    assert len(completed_pairs) == 2
    assert completed_pairs[1]["pair_id"] == "Q3_A4"

    await fsm.on_turn({"turn_id": 6, "speaker": "Candidate", "speaker_role": "candidate", "text": "Asyncio uses single-threaded cooperative multitasking."})
    await fsm.finalize_current_qa()

    assert len(completed_pairs) == 3
    assert completed_pairs[2]["pair_id"] == "Q5_A6"

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_completed_pairs_total"] == 3
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_11_compound_question_detection():
    """
    Scenario 11: Compound question detection with prefixes, filler words, and name addresses.
    Verifies that questions ending with periods or with leading conversational starters are captured.
    """
    fsm = QAStateMachine(session_id="test_sess_q_detect")

    # Turn 17 style question (ended with period, conversational prefix + candidate name)
    q1 = "OK. Deepak, can you also design an MCP server in Fast API that exposes Fhir patient lookup and clinical notes summarization as tools to an LLM agent."
    assert fsm._is_question(q1) is True

    # Punctuation-delimited clauses
    q2 = "Alright, Deepak, what is your approach to handling database deadlocks?"
    assert fsm._is_question(q2) is True

    # Conversational starter without punctuation
    q3 = "So how would you handle distributed transactions?"
    assert fsm._is_question(q3) is True

    # Greeting / non-question
    nq1 = "Hello Deepak, welcome to the interview."
    assert fsm._is_question(nq1) is False

    # Short acknowledgment
    nq2 = "OK, great."
    assert fsm._is_question(nq2) is False
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_12_interviewer_backchannel_handling():
    """
    Scenario 12: Interviewer backchannel (e.g. 'Yes.', 'Right', 'Okay') does not prematurely terminate candidate answer.
    """
    completed_pairs = []

    async def on_qa(rec):
        completed_pairs.append(rec)

    fsm = QAStateMachine(session_id="test_sess_backchannel", silence_threshold=5.0, on_qa_completed=on_qa)

    # Interviewer asks question
    await fsm.on_turn({"turn_id": 1, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Can you design an MCP server?"})
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # Candidate asks clarification / speaks
    await fsm.on_turn({"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "Sir, are you going off script?"})
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Interviewer says "Yes." (backchannel / acknowledgment)
    await fsm.on_turn({"turn_id": 3, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Yes."})
    # Should remain in CANDIDATE_ANSWERING without premature finalization!
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING
    assert len(completed_pairs) == 0

    # Candidate continues full answer
    await fsm.on_turn({"turn_id": 4, "speaker": "Candidate", "speaker_role": "candidate", "text": "MCP stands for model context protocol..."})
    await fsm.on_turn({"turn_id": 5, "speaker": "Candidate", "speaker_role": "candidate", "text": "It acts like a USB port for AI models."})

    # New interviewer question arrives
    await fsm.on_turn({"turn_id": 6, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "How does vector search work?"})
    assert len(completed_pairs) == 1
    assert completed_pairs[0]["pair_id"] == "Q1_A2_4_5"
    assert completed_pairs[0]["answer_turn_ids"] == [2, 4, 5]
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_13_candidate_continuation_after_silence():
    """
    Scenario 13: Candidate pauses > silence threshold, then resumes speaking before interviewer speaks.
    Verifies that the candidate continuation re-opens the answer and accumulates turns cleanly.
    """
    completed_pairs = []

    async def on_qa(rec):
        completed_pairs.append(rec)

    fsm = QAStateMachine(session_id="test_sess_continuation", silence_threshold=0.1, on_qa_completed=on_qa)

    # Question
    await fsm.on_turn({"turn_id": 1, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "Please introduce yourself."})
    # Candidate speaks turn 2
    await fsm.on_turn({"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "I am Deepak from Delhi."})
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Silence timer expires
    await asyncio.sleep(0.25)
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION
    assert len(completed_pairs) == 1
    assert completed_pairs[0]["pair_id"] == "Q1_A2"

    # Candidate resumes speaking before interviewer asks anything!
    await fsm.on_turn({"turn_id": 3, "speaker": "Candidate", "speaker_role": "candidate", "text": "I completed my degree with 75% marks."})
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    await fsm.on_turn({"turn_id": 4, "speaker": "Candidate", "speaker_role": "candidate", "text": "Then I worked on full stack AI applications."})

    # Interviewer asks next question
    await fsm.on_turn({"turn_id": 5, "speaker": "Interviewer", "speaker_role": "interviewer", "text": "What is FastAPI?"})
    assert len(completed_pairs) == 2
    assert completed_pairs[1]["pair_id"] == "Q1_A2_3_4"
    assert completed_pairs[1]["answer_turn_ids"] == [2, 3, 4]
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_14_candidate_clarification_flow():
    """
    Scenario 14: Candidate Clarification Flow inside a single QA Exchange.
    Interviewer Question -> Candidate Clarification Question -> Interviewer Clarification Response -> Candidate Actual Answer.
    Verifies that the FSM does not prematurely finalize the QA pair on candidate clarification,
    integrates the interviewer clarification into question context, and accurately captures the actual answer.
    """
    completed_pairs = []

    async def on_qa(rec):
        completed_pairs.append(rec)

    fsm = QAStateMachine(session_id="test_sess_clarification", silence_threshold=5.0, on_qa_completed=on_qa)

    # 1. Interviewer asks Question
    turn1 = {
        "turn_id": 1,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Can you walk me through a specific AI feature or API you built at Appzlogic?"
    }
    await fsm.on_turn(turn1)
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 2. Candidate asks Clarification Question
    turn2 = {
        "turn_id": 2,
        "speaker": "Mahima",
        "speaker_role": "candidate",
        "text": "OK, so talking about AI like. AI capabilities or like challenges?"
    }
    await fsm.on_turn(turn2)
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING
    assert len(completed_pairs) == 0

    # 3. Interviewer responds with Clarification / Guidance (NOT a new question topic)
    turn3 = {
        "turn_id": 3,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "The like I want to know the experience what you had like what you implemented."
    }
    res3 = await fsm.on_turn(turn3)
    # MUST NOT finalize QA pair!
    assert res3 is None
    assert len(completed_pairs) == 0
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert "Clarification: The like I want to know" in fsm.current_question["text"]

    # 4. Candidate delivers Actual Technical Answer
    turn4 = {
        "turn_id": 4,
        "speaker": "Mahima",
        "speaker_role": "candidate",
        "text": "OK, so I implemented different RAG projects, fine-tuning, and function calling with tool selection."
    }
    await fsm.on_turn(turn4)
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING
    assert len(completed_pairs) == 0

    turn5 = {
        "turn_id": 5,
        "speaker": "Mahima",
        "speaker_role": "candidate",
        "text": "Functions perform retrieval and execute actions based on user query requirements."
    }
    await fsm.on_turn(turn5)
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING
    assert len(completed_pairs) == 0

    # 5. Interviewer moves to Next Question
    turn6 = {
        "turn_id": 6,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Great. How would you design a simple multi agent AI system?"
    }
    res6 = await fsm.on_turn(turn6)
    assert res6 is not None
    assert len(completed_pairs) == 1

    record = completed_pairs[0]
    assert "Can you walk me through a specific AI feature" in record["question"]
    assert "Clarification: The like I want to know the experience" in record["question"]
    assert "implemented different RAG projects" in record["answer"]
    assert "Functions perform retrieval and execute actions" in record["answer"]
    assert record["answer_turn_ids"] == [4, 5]

    # Verify FSM is now in QUESTION_CAPTURED for the new multi-agent question
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert fsm.current_question["turn_id"] == 6
    assert "multi agent AI system" in fsm.current_question["text"]

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_clarification_exchanges_total"] == 1

    fsm.close()


@pytest.mark.asyncio
async def test_scenario_15_audio_check_and_noise_filtering():
    """
    Scenario 15: Audio check, troubleshooting, and transcription noise filtering.
    Verifies that utterances such as 'Can you also hear a noise from my end?',
    'Is it still merging from my side?', 'Am I audible?', 'What's the picnic?'
    are filtered as non-evaluable and never enter QA pair creation or evaluation.
    """
    completed_pairs = []

    async def on_qa_done(record):
        completed_pairs.append(record)

    fsm = QAStateMachine(session_id="test_sess_15", silence_threshold=5.0, on_qa_completed=on_qa_done)

    # 1. Real interview question: Introduction
    await fsm.on_turn({
        "turn_id": 1,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "OK. Hi, Deepak, can you please introduce yourself?"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    await fsm.on_turn({
        "turn_id": 2,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "I'm Deepak, working as an AI developer developing LLM applications."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # 2. Audio check occurs immediately after:
    # "OK, that is very different. Can you also hear a noise from my end?"
    turn3 = {
        "turn_id": 3,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "OK, that is very different. Can you also hear a noise from my end?"
    }
    res3 = await fsm.on_turn(turn3)
    # The previous valid QA pair (Introduction) should be finalized cleanly
    assert res3 is not None
    assert len(completed_pairs) == 1
    assert "introduce yourself" in completed_pairs[0]["question"]

    # BUT the FSM should NOT capture "Can you also hear a noise" as a new question!
    # It must transition to WAITING_FOR_QUESTION, filtering out the audio check.
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION
    assert fsm.current_question is None

    # Candidate responds to the audio check
    await fsm.on_turn({
        "turn_id": 4,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "Yeah, yeah, it's very noisy this."
    })
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION
    assert len(completed_pairs) == 1

    # Additional troubleshooting turns
    await fsm.on_turn({
        "turn_id": 5,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "OK. Is it still merging from my side or? Is it?"
    })
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    await fsm.on_turn({
        "turn_id": 6,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "Sorry, you are not audible at all."
    })
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    # ASR noise / hallucination
    await fsm.on_turn({
        "turn_id": 7,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "What's the picnic?"
    })
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    await fsm.on_turn({
        "turn_id": 8,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "I'm."
    })
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    # Meeting ends
    await fsm.finalize_current_qa()

    # Verify ONLY the 1 real interview question was completed, and 0 noise/audio checks
    assert len(completed_pairs) == 1
    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_non_evaluable_filtered_total"] >= 2
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_16_candidate_unheard_reprompt_flow():
    """
    Scenario 16: Candidate did not hear question / audio cut out.
    Verifies that when a candidate says 'Sorry, I didn't hear the question',
    the FSM does NOT finalize a 0% QA pair. Instead it stays in QUESTION_CAPTURED,
    allows the interviewer to re-prompt, and captures the actual subsequent answer.
    """
    completed_pairs = []

    async def on_qa_done(record):
        completed_pairs.append(record)

    fsm = QAStateMachine(session_id="test_sess_16", silence_threshold=5.0, on_qa_completed=on_qa_done)

    # 1. Interviewer asks technical question
    turn1 = {
        "turn_id": 26,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "You claim you developed an enterprise RAG platform. Can you walk me through that?"
    }
    await fsm.on_turn(turn1)
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 2. Candidate could not hear the question due to audio packet loss
    turn2 = {
        "turn_id": 27,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "Sorry, I didn't hear the question."
    }
    await fsm.on_turn(turn2)
    # Crucial: Must NOT transition to CANDIDATE_ANSWERING, must NOT create a QA pair
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert len(completed_pairs) == 0

    # 3. Interviewer re-prompts the question
    turn3 = {
        "turn_id": 28,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Yeah, I'm saying, can you walk me through where did you use your RAG platform using Postgres?"
    }
    await fsm.on_turn(turn3)
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert "Re-prompt:" in fsm.current_question["text"]
    assert "RAG platform using Postgres" in fsm.current_question["text"]

    # 4. Candidate now answers substantively
    turn4 = {
        "turn_id": 29,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "So I used RAG in my previous project where we built a customer support chatbot using pgvector embeddings."
    }
    await fsm.on_turn(turn4)
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING
    assert len(completed_pairs) == 0

    # 5. Next interviewer turn completes the QA pair
    turn5 = {
        "turn_id": 30,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "That makes sense. What embedding model did you choose?"
    }
    res = await fsm.on_turn(turn5)
    assert res is not None
    assert len(completed_pairs) == 1

    record = completed_pairs[0]
    assert "enterprise RAG platform" in record["question"]
    assert "RAG platform using Postgres" in record["question"]
    assert "pgvector embeddings" in record["answer"]
    assert "didn't hear" not in record["answer"]
    assert record["answer_turn_ids"] == [29]

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_unheard_reprompts_total"] == 1
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_17_follow_up_question_distinction():
    """
    Scenario 17: Follow-up question distinction (Risk 1).
    Verifies that when an interviewer asks a follow-up question (e.g., 'Why did you choose pgvector?')
    after a candidate's substantive answer, it is treated as a NEW question (Q2), NOT merged
    into the previous question as a clarification response.
    Expected: Q1 -> A1 and Q2 -> A2.
    """
    completed_pairs = []

    async def on_qa_done(record):
        completed_pairs.append(record)

    fsm = QAStateMachine(session_id="test_sess_17", silence_threshold=5.0, on_qa_completed=on_qa_done)

    # Q1: Tell me about your RAG system
    await fsm.on_turn({
        "turn_id": 1,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Tell me about your RAG system."
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # A1: Candidate explains architecture
    await fsm.on_turn({
        "turn_id": 2,
        "speaker": "Candidate",
        "speaker_role": "candidate",
        "text": "We built a RAG pipeline using LangChain and pgvector for semantic retrieval of enterprise documentation."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Q2: Follow-up question (NOT a clarification!)
    await fsm.on_turn({
        "turn_id": 3,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Why did you choose pgvector?"
    })
    # Q1 must be completed immediately upon Q2 arrival
    assert len(completed_pairs) == 1
    q1 = completed_pairs[0]
    assert q1["question"] == "Tell me about your RAG system."
    assert "pgvector for semantic retrieval" in q1["answer"]
    assert "Clarification:" not in q1["question"]

    # FSM must now be captured on Q2
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert fsm.current_question["turn_id"] == 3
    assert fsm.current_question["text"] == "Why did you choose pgvector?"

    # A2: Candidate explains pgvector
    await fsm.on_turn({
        "turn_id": 4,
        "speaker": "Candidate",
        "speaker_role": "candidate",
        "text": "Because pgvector runs natively inside PostgreSQL, saving us from hosting and synchronizing a dedicated vector database cluster."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Next turn ends meeting
    await fsm.finalize_current_qa()

    assert len(completed_pairs) == 2
    q2 = completed_pairs[1]
    assert q2["question"] == "Why did you choose pgvector?"
    assert "natively inside PostgreSQL" in q2["answer"]
    assert "Clarification:" not in q2["question"]

    fsm.close()


@pytest.mark.asyncio
async def test_scenario_18_interruption_hold_keeps_answer_open():
    """
    Scenario 18: Interruption / brief hold handling (Risk 2).
    Verifies that when an interviewer briefly pauses the conversation
    (e.g., 'Sorry, one second.', 'Hold on a moment.'), the candidate's active answer
    is NOT prematurely finalized as a speaker change. The answer remains open and
    accumulates subsequent turns seamlessly.
    """
    completed_pairs = []

    async def on_qa_done(record):
        completed_pairs.append(record)

    fsm = QAStateMachine(session_id="test_sess_18", silence_threshold=5.0, on_qa_completed=on_qa_done)

    # Q: Interviewer asks question
    await fsm.on_turn({
        "turn_id": 10,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Can you explain how CrewAI coordinates multiple agents?"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # A (partial): Candidate begins answering
    await fsm.on_turn({
        "turn_id": 11,
        "speaker": "Candidate",
        "speaker_role": "candidate",
        "text": "In our project, CrewAI organized agents into sequential and hierarchical processes—"
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Interviewer brief interruption / hold
    res_int = await fsm.on_turn({
        "turn_id": 12,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Sorry, one second."
    })
    # Must NOT finalize the answer!
    assert res_int is None
    assert len(completed_pairs) == 0
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # A (resumed): Candidate continues answering
    await fsm.on_turn({
        "turn_id": 13,
        "speaker": "Candidate",
        "speaker_role": "candidate",
        "text": "where a manager agent delegated tasks to specialized scraping and formatting agents."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING
    assert len(completed_pairs) == 0

    # Next interview question completes the answer
    res_next = await fsm.on_turn({
        "turn_id": 14,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "How did you monitor agent tool calling errors?"
    })
    assert res_next is not None
    assert len(completed_pairs) == 1

    record = completed_pairs[0]
    assert "CrewAI coordinates multiple agents" in record["question"]
    # Both partial and resumed turns should be united in the answer
    assert "organized agents into sequential" in record["answer"]
    assert "delegated tasks to specialized scraping" in record["answer"]
    assert record["answer_turn_ids"] == [11, 13]

    metrics = get_qa_fsm_metrics()
    assert metrics["qa_fsm_interruption_pauses_total"] >= 1
    fsm.close()


@pytest.mark.asyncio
async def test_scenario_19_qa_fsm_filtered_ratio_and_telemetry():
    """
    Scenario 19: Operational telemetry and filtered ratio tracking.
    Verifies that qa_fsm_question_candidates_total and qa_fsm_filtered_ratio accurately
    reflect the proportion of non-evaluable questions filtered.
    """
    completed_pairs = []

    fsm = QAStateMachine(session_id="test_sess_19", silence_threshold=5.0, on_qa_completed=lambda r: completed_pairs.append(r))

    # Candidate Question 1: Real interview question
    await fsm.on_turn({"turn_id": 1, "speaker": "Ankit", "speaker_role": "interviewer", "text": "What is eventual consistency?"})
    await fsm.on_turn({"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "Replicas eventually reach identical state."})

    # Candidate Question 2: Audio check (filtered)
    await fsm.on_turn({"turn_id": 3, "speaker": "Ankit", "speaker_role": "interviewer", "text": "Can you also hear a noise from my end?"})
    await fsm.on_turn({"turn_id": 4, "speaker": "Candidate", "speaker_role": "candidate", "text": "Yeah, very noisy."})

    # Candidate Question 3: Troubleshooting (filtered)
    await fsm.on_turn({"turn_id": 5, "speaker": "Ankit", "speaker_role": "interviewer", "text": "Is it still merging from my side or? Is it?"})

    metrics = get_qa_fsm_metrics()
    # 3 question candidates, 2 filtered -> filtered ratio = 2 / 3 = 0.667
    assert metrics["qa_fsm_question_candidates_total"] == 3
    assert metrics["qa_fsm_non_evaluable_filtered_total"] == 2
    assert 0.66 <= metrics["qa_fsm_filtered_ratio"] <= 0.67

    fsm.close()


@pytest.mark.asyncio
async def test_scenario_20_asr_quality_evaluation_and_gating():
    """
    Scenario 20: ASR quality tagging (Risk 3).
    Verifies that severely degraded transcription tokens (e.g., phonetic garbage)
    are flagged as asr_quality='low' with reduced confidence to prevent punitive scoring.
    """
    completed_pairs = []

    fsm = QAStateMachine(session_id="test_sess_20", silence_threshold=5.0, on_qa_completed=lambda r: completed_pairs.append(r))

    # Normal high-quality QA
    await fsm.on_turn({"turn_id": 1, "speaker": "Ankit", "speaker_role": "interviewer", "text": "Explain indexing in PostgreSQL."})
    await fsm.on_turn({"turn_id": 2, "speaker": "Candidate", "speaker_role": "candidate", "text": "PostgreSQL uses B-Tree indexes for fast log(N) lookups."})
    await fsm.on_turn({"turn_id": 3, "speaker": "Ankit", "speaker_role": "interviewer", "text": "Great."})

    assert len(completed_pairs) == 1
    assert completed_pairs[0]["asr_quality"] == "high"
    assert completed_pairs[0]["confidence"] == 1.0

    # Degraded transcription turn containing ASR corruptions
    await fsm.on_turn({"turn_id": 4, "speaker": "Ankit", "speaker_role": "interviewer", "text": "How do you integrate components into the platform?"})
    await fsm.on_turn({"turn_id": 5, "speaker": "Candidate", "speaker_role": "candidate", "text": "Maybe honey. Yes, honey Singh badsha. Kalaran Kismeli Karan Kaka. Dek."})
    await fsm.finalize_current_qa()

    assert len(completed_pairs) == 2
    assert completed_pairs[1]["asr_quality"] == "low"
    assert completed_pairs[1]["confidence"] == 0.4

    fsm.close()


@pytest.mark.asyncio
async def test_scenario_21_question_state_replacement_after_silence_timeout():
    """
    Scenario 21: Question state replacement after silence timeout (Session 6b4bcd20 regression).
    Verifies that when Q1 (Introduction) is completed via silence timeout, and then:
    - Q2 is asked ("how did you validate and test the reliability claims...")
    - Candidate asks for repetition ("Sorry, could you repeat that?")
    - Interviewer re-prompts / clarifies
    - Candidate answers ("I validate based on the test cases...")
    The resulting Q2 record strictly captures the reliability claims question and does NOT
    reuse or inherit the Q1 introduction question text!
    """
    completed_pairs = []

    async def on_qa_done(record):
        completed_pairs.append(record)

    fsm = QAStateMachine(session_id="test_sess_21", silence_threshold=0.1, on_qa_completed=on_qa_done)

    # 1. Q1: Introduction
    await fsm.on_turn({
        "turn_id": 2,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Deepak, can you please introduce yourself?"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 2. A1: Candidate introduces himself
    await fsm.on_turn({
        "turn_id": 3,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "Yes, I am Deepak, currently working as an AI developer."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # 3. Silence timeout fires for Q1
    await asyncio.sleep(0.15)
    assert len(completed_pairs) == 1
    assert "introduce yourself" in completed_pairs[0]["question"]
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION

    # 4. Q2: Interviewer asks technical question
    await fsm.on_turn({
        "turn_id": 9,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "OK. So moving forward to next question, like how did you validate and test the reliability claims you mentioned in your resume?"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 5. Candidate asks for repetition (unheard query)
    await fsm.on_turn({
        "turn_id": 10,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "Sorry, could you repeat that?"
    })
    # Must remain in QUESTION_CAPTURED awaiting re-prompt without finalizing
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert len(completed_pairs) == 1

    # 6. Interviewer re-prompts
    await fsm.on_turn({
        "turn_id": 11,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "Yes, I am asking how did you validate the test and reliability claims mentioned in?"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 7. Candidate asks scoping clarification
    await fsm.on_turn({
        "turn_id": 12,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "So do you want me to give a product based answer or points?"
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # 8. Interviewer provides clarification
    await fsm.on_turn({
        "turn_id": 14,
        "speaker": "Ankit",
        "speaker_role": "interviewer",
        "text": "So project based answers will be finite."
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 9. Candidate delivers actual technical answer
    await fsm.on_turn({
        "turn_id": 15,
        "speaker": "Deepak",
        "speaker_role": "candidate",
        "text": "OK so I validate based on the test cases that I will be performing on the project."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # 10. Silence timeout completes Q2
    await asyncio.sleep(0.15)
    assert len(completed_pairs) == 2

    q2_pair = completed_pairs[1]
    # Q2 question MUST NOT inherit Q1 introduction text!
    assert "introduce yourself" not in q2_pair["question"]
    assert "reliability claims" in q2_pair["question"]
    assert "validate based on the test cases" in q2_pair["answer"]
    assert q2_pair["answer_turn_ids"] == [15]

    fsm.close()


@pytest.mark.asyncio
async def test_scenario_22_session_e62541a3_clean_pair_isolation_intro_and_mcp():
    """
    Scenario 22: Session e62541a3 Regression Verification.
    Verifies that Question 1 (Introduction) and Question 2 (MCP) are cleanly isolated into
    distinct Q&A pairs without answer merging, even when:
    - Candidate concludes Turn 11 with 'And that's all about me.'
    - Silence timeout completes Turn 7
    - Interviewer asks Turn 12 starting with 'OK. OK. The first question from my side will be...' without a trailing '?'
    - Candidate answers Turns 13-19 with MCP details
    - Interviewer asks Turn 20 about GRC system
    """
    completed_pairs = []

    async def on_qa(rec):
        completed_pairs.append(rec)

    fsm = QAStateMachine(session_id="test_sess_e62541a3", silence_threshold=0.1, on_qa_completed=on_qa)

    # 1. Interviewer asks Question 1 (Intro)
    await fsm.on_turn({
        "turn_id": 7,
        "speaker": "Ankit Kumar",
        "speaker_role": "interviewer",
        "text": "No, you we can start just right now. OK. Deepak, can you please introduce yourself?"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED

    # 2. Candidate answers Turns 8, 9, 10, 11
    await fsm.on_turn({
        "turn_id": 8,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "Yes, so I'm Deepak. So currently I'm working as AI developer at tab Logic."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    await fsm.on_turn({
        "turn_id": 9,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "Then I did my graduation from College of Engineering, which is in Gurgaon."
    })

    await fsm.on_turn({
        "turn_id": 10,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "75 and now I have joined apps logic as I end up with."
    })

    await fsm.on_turn({
        "turn_id": 11,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "And that's all about me."
    })

    # 3. Candidate silence timeout fires (0.1s threshold)
    await asyncio.sleep(0.15)
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION
    assert len(completed_pairs) == 1

    pair1 = completed_pairs[0]
    assert pair1["pair_id"] == "Q7_A8_9_10_11"
    assert pair1["question_turn_id"] == 7
    assert pair1["answer_turn_ids"] == [8, 9, 10, 11]
    assert "Deepak" in pair1["answer"]
    assert "MCP" not in pair1["answer"]
    # Because Turn 11 had 'And that's all about me.', last_question MUST be None!
    assert fsm.last_question is None

    # 4. Interviewer asks Question 2 (MCP) with conversational prefix, no question mark
    await fsm.on_turn({
        "turn_id": 12,
        "speaker": "Ankit Kumar",
        "speaker_role": "interviewer",
        "text": "OK. OK. The first question from my side will be like given this roles focus on fast API RAG and MCP, how have you used MCP in practice or how would you apply it to one of your existing AI systems"
    })
    assert fsm.get_state() == QAState.QUESTION_CAPTURED
    assert fsm.current_question["turn_id"] == 12

    # 5. Candidate answers Turns 13, 14, 15
    await fsm.on_turn({
        "turn_id": 13,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "So currently."
    })
    assert fsm.get_state() == QAState.CANDIDATE_ANSWERING

    await fsm.on_turn({
        "turn_id": 14,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "So currently I was just making a prototype on my ID which is anti gravity."
    })

    await fsm.on_turn({
        "turn_id": 15,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "So I use the GitHub MCP tool which I used for searching trending repos."
    })

    # 6. Interviewer asks Question 3 (GRC system)
    await fsm.on_turn({
        "turn_id": 20,
        "speaker": "Ankit Kumar",
        "speaker_role": "interviewer",
        "text": "That is so also like in the multiagent GRC system. How did you implement risk identification and compliance validation Measured against"
    })
    assert len(completed_pairs) == 2

    pair2 = completed_pairs[1]
    assert pair2["pair_id"] == "Q12_A13_14_15"
    assert pair2["question_turn_id"] == 12
    assert pair2["answer_turn_ids"] == [13, 14, 15]
    assert "MCP" in pair2["answer"]
    assert "tab Logic" not in pair2["answer"]

    # 7. Candidate answers Question 3
    await fsm.on_turn({
        "turn_id": 25,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "OK. So basically GRC stands for governance, risk and compliance."
    })
    await fsm.on_turn({
        "turn_id": 26,
        "speaker": "Deepak Bisht",
        "speaker_role": "candidate",
        "text": "So what we did in this, we set up local LLM using Ollama and ISO 27001 documents."
    })

    # 8. Meeting finishes / finalization
    await fsm.finalize_current_qa()
    assert len(completed_pairs) == 3

    pair3 = completed_pairs[2]
    assert pair3["question_turn_id"] == 20
    assert pair3["answer_turn_ids"] == [25, 26]
    assert "governance, risk and compliance" in pair3["answer"]

    fsm.close()


@pytest.mark.asyncio
async def test_scenario_23_explicit_candidate_conclusion_prevents_reopening():
    """
    Scenario 23: Explicit answer conclusion marker sealing.
    Verifies that when a candidate explicitly concludes ('And that's all about me.'),
    the FSM seals the answer buffer, prevents reopening even if candidate speaks again
    before interviewer question, and never conflates separate topics into one pair.
    """
    completed_pairs = []

    async def on_qa(rec):
        completed_pairs.append(rec)

    fsm = QAStateMachine(session_id="test_sess_conclusion_seal", silence_threshold=0.1, on_qa_completed=on_qa)

    # 1. Question
    await fsm.on_turn({
        "turn_id": 1,
        "speaker": "Interviewer",
        "speaker_role": "interviewer",
        "text": "Could you please introduce yourself?"
    })

    # 2. Answer with explicit conclusion
    await fsm.on_turn({
        "turn_id": 2,
        "speaker": "Candidate",
        "speaker_role": "candidate",
        "text": "I am a software engineer with 3 years of experience. And that's all about me."
    })

    # 3. Silence expires
    await asyncio.sleep(0.15)
    assert len(completed_pairs) == 1
    assert completed_pairs[0]["pair_id"] == "Q1_A2"
    assert fsm.last_question is None

    # 4. Candidate starts speaking again unprompted
    await fsm.on_turn({
        "turn_id": 3,
        "speaker": "Candidate",
        "speaker_role": "candidate",
        "text": "Now regarding my projects, I worked on Python FastAPI microservices."
    })
    # Must NOT re-open Q1!
    assert fsm.current_question is None
    assert fsm.get_state() == QAState.WAITING_FOR_QUESTION
    assert len(completed_pairs) == 1

    fsm.close()




