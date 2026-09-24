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

