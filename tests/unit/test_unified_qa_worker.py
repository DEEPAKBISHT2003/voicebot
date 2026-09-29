import os
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.copilot.src.core.config import Settings
from services.copilot.src.services.unified_qa_worker import (
    UnifiedQAWorker,
    calculate_deterministic_accuracy_score,
    compute_jaccard_similarity,
    compute_text_similarity,
    compute_parity_metrics,
    get_unified_qa_shadow_metrics,
    reset_unified_qa_shadow_metrics,
    get_unified_qa_production_metrics,
    reset_unified_qa_production_metrics
)
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


@pytest.fixture(autouse=True)
def clean_metrics():
    reset_unified_qa_shadow_metrics()
    reset_unified_qa_production_metrics()
    yield
    reset_unified_qa_shadow_metrics()
    reset_unified_qa_production_metrics()


@pytest.fixture
def mock_unified_llm_payload():
    return {
        "accuracy_evaluation": {
            "relevance": 90,
            "technical": 85,
            "resume_match": 80,
            "completeness": 70
        },
        "intelligence": {
            "current_topic": "Database Indexing",
            "covered_skills": ["PostgreSQL", "B-Tree"],
            "remaining_skills": ["Redis", "Kafka", "Docker"],
            "resume_projects_covered": ["Distributed Pipeline"],
            "resume_projects_remaining": ["Analytics Engine"]
        },
        "assistance": {
            "recommended_next_topic": "Ask about query optimization",
            "interview_notes": ["Solid explanation of B-Tree lookup complexity"],
            "current_candidate_understanding": "Demonstrates strong backend database knowledge"
        },
        "suggestions": [
            "What trade-offs exist between B-Tree and GiST indexes in PostgreSQL?",
            "How did you monitor index usage in your production pipeline?"
        ]
    }


def create_mock_openai_client(response_data):
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(response_data)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    return mock_client


def test_deterministic_accuracy_score_formula():
    """Verifies that accuracy_score matches the production weighted formula."""
    # 0.30*90 + 0.30*85 + 0.25*80 + 0.15*70 = 27 + 25.5 + 20 + 10.5 = 83.0 -> 83
    score = calculate_deterministic_accuracy_score(
        relevance=90,
        technical=85,
        resume_match=80,
        completeness=70
    )
    assert score == 83

    # Clamping bounds
    assert calculate_deterministic_accuracy_score(150, 150, 150, 150) == 100
    assert calculate_deterministic_accuracy_score(-10, -5, 0, 0) == 0


def test_prompt_strict_isolation():
    """Verifies prompt construction strictly isolates Accuracy Evaluation from Job Description."""
    worker = UnifiedQAWorker()
    prompt = worker.build_prompt(
        question="How does indexing work?",
        answer="Postgres uses B-Tree indexes.",
        resume="Dan Developer. 5 years PostgreSQL experience.",
        jd="Senior Architect with Kafka, Kubernetes, Go, and Python.",
        current_interview_state={"covered_skills": ["PostgreSQL"], "current_topic": "Databases"}
    )

    # Section 1 rules
    assert "SECTION 1: TECHNICAL ACCURACY EVALUATION (STRICT ISOLATION RULE)" in prompt
    assert "Do NOT use or assume any [TARGET JOB DESCRIPTION] for this section!" in prompt

    # Section 2 rules
    assert "SECTION 2: CONVERSATION INTELLIGENCE" in prompt
    assert "Target Job Description" in prompt


@pytest.mark.asyncio
async def test_unified_qa_worker_successful_processing(mock_unified_llm_payload):
    """Verifies that process_completed_qa returns expected structured schema with computed score."""
    mock_client = create_mock_openai_client(mock_unified_llm_payload)
    worker = UnifiedQAWorker(client=mock_client)

    result = await worker.process_completed_qa(
        question="Can you explain B-Tree indexing?",
        answer="B-Tree indexes provide logarithmic search times.",
        resume="Alice Engineer",
        jd="Backend Lead"
    )

    assert result is not None
    assert "accuracy_evaluation" in result
    assert "intelligence" in result
    assert "assistance" in result
    assert "suggestions" in result

    # Computed accuracy score
    assert result["accuracy_score"] == 83
    assert len(result["suggestions"]) == 2
    assert result["intelligence"]["current_topic"] == "Database Indexing"


def test_parity_metrics_computation():
    """Verifies the 6 comparison metrics calculation."""
    legacy = {
        "accuracy_score": 85,
        "covered_skills": ["Python", "PostgreSQL"],
        "remaining_skills": ["Docker", "Kubernetes"],
        "dynamic_suggestions": [
            "What trade-offs exist with B-Tree indexes?",
            "How do you profile queries in PostgreSQL?"
        ],
        "current_topic": "Database Indexing",
        "recommended_next_topic": "Ask about query optimization"
    }

    unified = {
        "accuracy_score": 83,
        "intelligence": {
            "current_topic": "Database Indexing",
            "covered_skills": ["PostgreSQL", "Python"],
            "remaining_skills": ["Docker", "Kubernetes", "Kafka"]
        },
        "assistance": {
            "recommended_next_topic": "Ask about query optimization"
        },
        "suggestions": [
            "What trade-offs exist with B-Tree indexes?",
            "How do you monitor query degradation?"
        ]
    }

    parity = compute_parity_metrics(legacy_data=legacy, unified_data=unified)

    # 1. Accuracy Score Delta: |85 - 83| = 2
    assert parity["accuracy_score_delta"] == 2

    # 2. Skill Coverage Similarity: Jaccard = 2/2 = 1.0
    assert parity["skill_coverage_similarity"] == 1.0

    # 3. Remaining Skills Similarity: intersection=2, union=3 -> 0.667
    assert parity["remaining_skills_similarity"] == 0.667

    # 4. Topic Detection Similarity: exact match -> 1.0
    assert parity["topic_detection_similarity"] == 1.0

    # 5. Recommendation Similarity: exact match -> 1.0
    assert parity["recommendation_similarity"] == 1.0

    # Telemetry metrics updated
    metrics = get_unified_qa_shadow_metrics()
    assert metrics["unified_qa_shadow_runs_total"] == 1
    assert metrics["unified_qa_shadow_high_accuracy_matches"] == 1
    assert metrics["unified_qa_shadow_high_skill_matches"] == 1


@pytest.mark.asyncio
async def test_session_engine_shadow_execution_parallel(tmp_path, mock_unified_llm_payload):
    """
    Verifies that CopilotSessionEngine triggers UnifiedQAWorker in shadow mode
    without altering production state or WebSocket contracts.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    mock_client = create_mock_openai_client(mock_unified_llm_payload)

    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER_SHADOW", True), \
         patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", False):
        session = CopilotSessionEngine(
            session_id="sess_shadow_test",
            jd="Backend Engineer",
            resume="Alice Architect",
            repo=mock_repo
        )
        session.unified_qa_worker.client = mock_client
        session.start_qa_checkpoint()

        # Mock legacy triggers to verify legacy continues unaffected
        session._trigger_dynamic_suggestions_for_confirmed_qa = AsyncMock()
        session._trigger_accuracy_evaluation_for_confirmed_qa = AsyncMock()

        # Feed Question turn
        await session.add_message("Interviewer", "How does indexing work in Postgres?", is_final=True)
        # Feed Answer turn
        await session.add_message("Candidate", "PostgreSQL uses B-Tree indexes by default.", is_final=True)
        # Feed Next Question turn (triggers FSM QA completion)
        await session.add_message("Interviewer", "What about GiST indexes?", is_final=True)

        # Allow async shadow task to complete
        await asyncio.sleep(0.3)

        # 1. Legacy production pipeline was triggered
        session._trigger_dynamic_suggestions_for_confirmed_qa.assert_awaited_once()
        session._trigger_accuracy_evaluation_for_confirmed_qa.assert_awaited_once()

        # 2. Shadow worker ran in parallel and recorded parity telemetry
        assert len(session.shadow_qa_comparisons) >= 1
        comp = session.shadow_qa_comparisons[0]
        assert comp["pair_id"] == "Q1_A2"
        assert comp["unified_accuracy_score"] == 83

        # 3. Production state confirmed_qa_pairs does not expose shadow outputs
        assert len(session.confirmed_qa_pairs) == 1
        assert "unified_result" not in session.confirmed_qa_pairs[0]

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_feature_flag_disabled_suppresses_shadow_worker(tmp_path):
    """Verifies that setting ENABLE_UNIFIED_QA_WORKER_SHADOW=False disables shadow task."""
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER_SHADOW", False), \
         patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", False):
        session = CopilotSessionEngine(
            session_id="sess_shadow_disabled",
            jd="JD",
            resume="Resume",
            repo=mock_repo
        )
        assert session.enable_unified_qa_shadow is False

        session._trigger_dynamic_suggestions_for_confirmed_qa = AsyncMock()
        session._trigger_accuracy_evaluation_for_confirmed_qa = AsyncMock()

        await session.add_message("Interviewer", "What is Docker?", is_final=True)
        await session.add_message("Candidate", "A container tool.", is_final=True)
        await session.add_message("Interviewer", "Next question.", is_final=True)

        await asyncio.sleep(0.1)

        # No shadow comparisons should be made
        assert len(session.shadow_qa_comparisons) == 0
        metrics = get_unified_qa_shadow_metrics()
        assert metrics["unified_qa_shadow_runs_total"] == 0

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_unified_qa_worker_graceful_error_handling():
    """Verifies that an LLM timeout or error does not crash and logs telemetry error."""
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("Connection timeout"))

    worker = UnifiedQAWorker(client=mock_client)
    res = await worker.process_completed_qa(
        question="What is Kafka?",
        answer="A streaming platform.",
        timeout=1.0
    )

    assert res is None
    metrics = get_unified_qa_shadow_metrics()
    assert metrics["unified_qa_shadow_errors_total"] == 1


@pytest.mark.asyncio
async def test_suggestions_count_enforcement():
    """Verifies that suggestion lists of unexpected lengths are clamped or padded to exactly 2."""
    payload_3 = {
        "accuracy_evaluation": {"relevance": 80, "technical": 80, "resume_match": 80, "completeness": 80},
        "intelligence": {"current_topic": "Kafka"},
        "assistance": {},
        "suggestions": ["Q1", "Q2", "Q3"]
    }
    client_3 = create_mock_openai_client(payload_3)
    worker_3 = UnifiedQAWorker(client=client_3)
    res_3 = await worker_3.process_completed_qa(question="Q", answer="A")
    assert len(res_3["suggestions"]) == 2
    assert res_3["suggestions"] == ["Q1", "Q2"]

    payload_1 = {
        "accuracy_evaluation": {"relevance": 80, "technical": 80, "resume_match": 80, "completeness": 80},
        "intelligence": {"current_topic": "Kafka"},
        "assistance": {},
        "suggestions": ["Q1"]
    }
    client_1 = create_mock_openai_client(payload_1)
    worker_1 = UnifiedQAWorker(client=client_1)
    res_1 = await worker_1.process_completed_qa(question="Q", answer="A")
    assert len(res_1["suggestions"]) == 2
    assert res_1["suggestions"][0] == "Q1"


# ============================================================================
# PHASE 4B: PRODUCTION CUTOVER AND FALLBACK UNIT TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_phase4b_production_flow_success(tmp_path, mock_unified_llm_payload):
    """
    Verifies Phase 4B primary production execution:
    - UnifiedQAWorker updates confirmed_qa_pairs, dynamic_suggestions, intelligence, and assistance in-place.
    - Saves state to repository and disk.
    - Triggers WebSocket update callbacks.
    - Updates production success telemetry.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    mock_client = create_mock_openai_client(mock_unified_llm_payload)
    evaluated_cb = AsyncMock()
    update_cb = AsyncMock()

    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", True), \
         patch.object(Settings, "ENABLE_LEGACY_FALLBACK", True):
        session = CopilotSessionEngine(
            session_id="sess_phase4b_prod",
            jd="Senior Distributed Systems Architect",
            resume="Dan Developer. 5 years PostgreSQL experience.",
            repo=mock_repo
        )
        session.unified_qa_worker.client = mock_client
        session.on_qa_evaluated_callback = evaluated_cb
        session.on_update_callback = update_cb
        session.start_qa_checkpoint()

        # Turn 1: Question
        await session.add_message("Interviewer", "How does indexing work in Postgres?", is_final=True)
        # Turn 2: Answer
        await session.add_message("Candidate", "PostgreSQL uses B-Tree indexes by default.", is_final=True)
        # Turn 3: Next Question (triggers FSM QA completion)
        await session.add_message("Interviewer", "What about GiST indexes?", is_final=True)

        await asyncio.sleep(0.3)

        # 1. QA pair evaluated with deterministic accuracy score
        assert len(session.confirmed_qa_pairs) == 1
        qa_pair = session.confirmed_qa_pairs[0]
        assert qa_pair["accuracy_score"] == 83
        assert "Q1_A2" in session.evaluated_qa_ids

        # 2. Dynamic suggestions updated
        assert len(session.dynamic_suggestions) == 2
        assert session.dynamic_suggestions == [
            "What trade-offs exist between B-Tree and GiST indexes in PostgreSQL?",
            "How did you monitor index usage in your production pipeline?"
        ]

        # 3. Conversation intelligence updated
        assert session.intelligence["current_topic"] == "Database Indexing"
        assert "PostgreSQL" in session.intelligence["covered_skills"]
        assert "B-Tree" in session.intelligence["covered_skills"]
        assert session.intelligence["interview_progress"]["covered_count"] >= 2

        # 4. Assistance updated
        assert session.assistance["recommended_next_topic"] == "Ask about query optimization"
        assert session.assistance["dynamic_suggestions"] == session.dynamic_suggestions

        # 5. Repository save and callbacks executed
        mock_repo.save_session.assert_awaited()
        evaluated_cb.assert_awaited_once()
        update_cb.assert_awaited()

        # 6. Production telemetry verified
        prod_metrics = get_unified_qa_production_metrics()
        assert prod_metrics["unified_qa_production_runs_total"] >= 1
        assert prod_metrics["unified_qa_production_success_total"] >= 1
        assert prod_metrics["unified_qa_production_fallback_total"] == 0

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_phase4b_automatic_legacy_fallback(tmp_path):
    """
    Verifies that when UnifiedQAWorker fails, the session automatically falls back
    to the legacy post-QA pipeline without raising exceptions to users.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", True), \
         patch.object(Settings, "ENABLE_LEGACY_FALLBACK", True):
        session = CopilotSessionEngine(
            session_id="sess_phase4b_fallback",
            jd="Senior Systems Engineer",
            resume="Dan Developer",
            repo=mock_repo
        )
        # Force worker error
        session.unified_qa_worker.process_completed_qa = AsyncMock(side_effect=Exception("OpenAI 503 Unavailable"))
        session._execute_legacy_post_qa_flow = AsyncMock()
        session.start_qa_checkpoint()

        await session.add_message("Interviewer", "How does indexing work?", is_final=True)
        await session.add_message("Candidate", "Using B-Trees.", is_final=True)
        await session.add_message("Interviewer", "What about hashing?", is_final=True)

        await asyncio.sleep(0.3)

        # Legacy fallback was invoked
        session._execute_legacy_post_qa_flow.assert_awaited_once()

        # Telemetry recorded fallback
        prod_metrics = get_unified_qa_production_metrics()
        assert prod_metrics["unified_qa_production_fallback_total"] >= 1

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_phase4b_fallback_disabled_behavior(tmp_path):
    """
    Verifies that when ENABLE_LEGACY_FALLBACK=False, legacy pipeline is not triggered on failure.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", True), \
         patch.object(Settings, "ENABLE_LEGACY_FALLBACK", False):
        session = CopilotSessionEngine(
            session_id="sess_fallback_disabled",
            jd="JD",
            resume="Resume",
            repo=mock_repo
        )
        session.unified_qa_worker.process_completed_qa = AsyncMock(side_effect=Exception("LLM down"))
        session._execute_legacy_post_qa_flow = AsyncMock()
        session.start_qa_checkpoint()

        await session.add_message("Interviewer", "Q1", is_final=True)
        await session.add_message("Candidate", "A1", is_final=True)
        await session.add_message("Interviewer", "Q2", is_final=True)

        await asyncio.sleep(0.2)

        session._execute_legacy_post_qa_flow.assert_not_awaited()
        prod_metrics = get_unified_qa_production_metrics()
        assert prod_metrics["unified_qa_production_fallback_total"] == 0

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_phase4b_feature_flag_disabled_legacy_routing(tmp_path):
    """
    Verifies instant rollback: setting ENABLE_UNIFIED_QA_WORKER=False
    routes directly to the legacy post-QA pipeline.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", False):
        session = CopilotSessionEngine(
            session_id="sess_rollback_test",
            jd="JD",
            resume="Resume",
            repo=mock_repo
        )
        session._execute_legacy_post_qa_flow = AsyncMock()
        session._execute_unified_qa_production_flow = AsyncMock()
        session.start_qa_checkpoint()

        await session.add_message("Interviewer", "Explain garbage collection.", is_final=True)
        await session.add_message("Candidate", "It frees memory automatically.", is_final=True)
        await session.add_message("Interviewer", "What is mark-and-sweep?", is_final=True)

        await asyncio.sleep(0.2)

        # Directly routed to legacy pipeline
        session._execute_legacy_post_qa_flow.assert_awaited_once()
        session._execute_unified_qa_production_flow.assert_not_awaited()

        session.stop_qa_checkpoint()


@pytest.mark.asyncio
async def test_phase4b_per_turn_llm_bypass(tmp_path):
    """
    Verifies that per-turn background LLM calls are completely bypassed
    when ENABLE_UNIFIED_QA_WORKER=True, eliminating redundant per-turn calls.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)

    # 1. When ENABLE_UNIFIED_QA_WORKER=True: background per-turn LLMs are bypassed
    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", True):
        session_prod = CopilotSessionEngine(
            session_id="sess_turn_bypass",
            jd="JD",
            resume="Resume",
            repo=mock_repo
        )
        session_prod.intelligence_engine.analyze = AsyncMock()
        session_prod.copilot_assistant.generate_assistance = AsyncMock()

        await session_prod.add_message("Candidate", "I worked on Kubernetes clusters.", is_final=True)
        await asyncio.sleep(0.05)
        session_prod.intelligence_engine.analyze.assert_not_called()
        session_prod.copilot_assistant.generate_assistance.assert_not_called()

    # 2. When ENABLE_UNIFIED_QA_WORKER=False (Legacy): background per-turn LLMs ARE triggered
    with patch.object(Settings, "ENABLE_UNIFIED_QA_WORKER", False):
        session_legacy = CopilotSessionEngine(
            session_id="sess_turn_legacy",
            jd="JD",
            resume="Resume",
            repo=mock_repo
        )
        session_legacy.intelligence_engine.analyze = AsyncMock(return_value={})
        session_legacy.copilot_assistant.generate_assistance = AsyncMock(return_value={})

        await session_legacy.add_message("Candidate", "I worked on Kubernetes clusters.", is_final=True)
        await asyncio.sleep(0.05)
        session_legacy.intelligence_engine.analyze.assert_awaited_once()
        session_legacy.copilot_assistant.generate_assistance.assert_awaited_once()


def test_phase4b_production_telemetry_functions():
    """Verifies get_unified_qa_production_metrics and reset_unified_qa_production_metrics."""
    reset_unified_qa_production_metrics()
    metrics = get_unified_qa_production_metrics()
    assert metrics["unified_qa_production_runs_total"] == 0
    assert metrics["unified_qa_production_success_total"] == 0
    assert metrics["unified_qa_production_fallback_total"] == 0
    assert metrics["unified_qa_total_latency_seconds"] == 0.0
    assert metrics["unified_qa_tokens_used_estimated"] == 0

