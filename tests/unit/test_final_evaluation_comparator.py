import os
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.copilot.src.core.config import Settings
from services.copilot.src.services.precompiler import CompactProfile
from services.copilot.src.services.final_evaluation import FinalEvaluationService
from services.copilot.src.services.final_evaluation_comparator import (
    FinalEvaluationComparator,
    build_structured_dossier,
    extract_closing_candidate_dialogue,
    is_conversational_noise,
    compute_jaccard_similarity,
    compute_text_similarity,
    get_final_eval_shadow_metrics,
    reset_final_eval_shadow_metrics,
    get_optimized_final_eval_production_metrics,
    reset_optimized_final_eval_production_metrics,
    optimized_final_eval_production_metrics
)
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


@pytest.fixture(autouse=True)
def clean_shadow_metrics():
    reset_final_eval_shadow_metrics()
    reset_optimized_final_eval_production_metrics()
    yield
    reset_final_eval_shadow_metrics()
    reset_optimized_final_eval_production_metrics()


@pytest.fixture
def sample_compact_profile():
    return CompactProfile(
        session_id="test_sess_001",
        candidate_name="Alice Smith",
        target_role="Senior Distributed Systems Engineer",
        core_skills=["Go", "Distributed Consensus", "Kafka", "Kubernetes", "PostgreSQL"],
        project_claims=["High-Throughput Ingestion Engine", "Raft Consensus Key-Value Store"]
    )


@pytest.fixture
def sample_confirmed_qa_pairs():
    return [
        {
            "pair_id": "Q1_A2",
            "qa_id": "Q1_A2",
            "question_turn_id": 1,
            "answer_turn_ids": [2],
            "question": "How did you implement the consensus algorithm in your key-value store?",
            "answer": "We implemented the Raft protocol in Go, handling leader election, log replication, and heartbeat timers.",
            "accuracy_score": 88
        },
        {
            "pair_id": "Q3_A4",
            "qa_id": "Q3_A4",
            "question_turn_id": 3,
            "answer_turn_ids": [4],
            "question": "How do you handle network partitions and split-brain scenarios?",
            "answer": "Raft prevents split-brain by requiring a strict majority quorum for committing log entries.",
            "accuracy_score": 92
        }
    ]


@pytest.fixture
def sample_transcript():
    return [
        {"turn_id": 0, "speaker": "Interviewer", "text": "Hi Alice, can you hear me loud and clear?"},
        {"turn_id": 1, "speaker": "Interviewer", "text": "How did you implement the consensus algorithm in your key-value store?"},
        {"turn_id": 2, "speaker": "Candidate", "text": "We implemented the Raft protocol in Go, handling leader election, log replication, and heartbeat timers."},
        {"turn_id": 3, "speaker": "Interviewer", "text": "How do you handle network partitions and split-brain scenarios?"},
        {"turn_id": 4, "speaker": "Candidate", "text": "Raft prevents split-brain by requiring a strict majority quorum for committing log entries."},
        {"turn_id": 5, "speaker": "Interviewer", "text": "Okay, sounds good. Let's move on."},
        {"turn_id": 6, "speaker": "Candidate", "text": "Before we wrap up, what consensus mechanism does your internal storage cluster currently use?"},
        {"turn_id": 7, "speaker": "Interviewer", "text": "We use a customized Paxos layer with blue-green failover."},
        {"turn_id": 8, "speaker": "Candidate", "text": "Thank you, bye."}
    ]


@pytest.fixture
def mock_evaluation_payload():
    return {
        "holistic_competency": {
            "dimensions": {
                "technical_depth": {"score": 90, "summary": "Exceptional understanding of Raft consensus protocol."},
                "practical_experience": {"score": 85, "summary": "Hands-on implementation experience in Go."},
                "problem_solving": {"score": 88, "summary": "Clear handling of network partition scenarios."},
                "communication_clarity": {"score": 92, "summary": "Articulate, structured technical explanations."}
            }
        },
        "strengths": [
            "Deep expertise in distributed consensus protocols (Raft)",
            "Strong grasp of concurrency and network partition recovery"
        ],
        "development_areas": [
            "Could elaborate further on cluster state compaction and snapshots"
        ],
        "jd_analysis": {
            "covered_skills": ["Go", "Distributed Consensus", "Raft"],
            "remaining_skills": ["Kafka", "Kubernetes"],
            "summary": "Covers primary distributed systems requirements with excellence."
        },
        "resume_validation": {
            "verified_projects": ["Raft Consensus Key-Value Store"],
            "unverified_projects": ["High-Throughput Ingestion Engine"],
            "summary": "Validated key distributed storage project from resume."
        },
        "question_analysis": [
            {
                "pair_id": "Q1_A2",
                "observations": "Clear explanation of Raft mechanics."
            },
            {
                "pair_id": "Q3_A4",
                "observations": "Correct quorum reasoning for split-brain prevention."
            }
        ],
        "conversation_summary": "Alice demonstrated strong competency in distributed consensus algorithms with rigorous technical grounding.",
        "observer_notes": [
            "Candidate inquired thoughtfully about production storage topology."
        ]
    }


def create_mock_client(payload_dict):
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(payload_dict)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    return mock_client


def test_conversational_noise_detection():
    """Verifies that greetings, audio checks, and filler utterances are filtered."""
    assert is_conversational_noise("Hi Alice, can you hear me loud and clear?") is True
    assert is_conversational_noise("ok") is True
    assert is_conversational_noise("thank you") is True
    assert is_conversational_noise("Let me share my screen") is True
    assert is_conversational_noise("What consensus mechanism does your team use?") is False


def test_extract_closing_candidate_dialogue(sample_transcript, sample_confirmed_qa_pairs):
    """Verifies that post-QA turns are extracted while filtering administrative noise."""
    closing = extract_closing_candidate_dialogue(sample_transcript, sample_confirmed_qa_pairs)
    assert len(closing) == 2
    # Turn 6 & 7 are substantive closing dialogue
    assert closing[0]["speaker"] == "Candidate"
    assert "consensus mechanism" in closing[0]["text"]
    assert closing[1]["speaker"] == "Interviewer"
    assert "Paxos layer" in closing[1]["text"]


def test_build_structured_dossier_assembly(
    sample_compact_profile,
    sample_confirmed_qa_pairs,
    sample_transcript
):
    """Verifies structured dossier includes all required sections and excludes conversational noise."""
    intelligence = {
        "covered_skills": ["Go", "Raft"],
        "remaining_skills": ["Kafka", "Kubernetes"],
        "resume_projects_covered": ["Raft Consensus Key-Value Store"],
        "conversation_timeline": [{"topic": "Distributed Storage"}]
    }
    assistance = {
        "interview_notes": ["Solid explanation of quorum"],
        "current_candidate_understanding": "Advanced systems engineer"
    }

    dossier = build_structured_dossier(
        compact_profile=sample_compact_profile,
        confirmed_qa_pairs=sample_confirmed_qa_pairs,
        intelligence=intelligence,
        assistance=assistance,
        transcript=sample_transcript,
        jd="Senior Distributed Systems Engineer",
        resume="Alice Smith, 6 years Go",
        custom_prompt="Focus on quorum behavior"
    )

    # Section 1: Candidate & Role Context
    assert "Alice Smith" in dossier
    assert "Senior Distributed Systems Engineer" in dossier
    assert "Raft Consensus Key-Value Store" in dossier

    # Section 2: Confirmed Q&A with locked scores
    assert "[Pair Q1_A2]" in dossier
    assert "Phase 4B Evaluated Accuracy Score: 88%" in dossier
    assert "[Pair Q3_A4]" in dossier
    assert "Phase 4B Evaluated Accuracy Score: 92%" in dossier

    # Section 3: Intelligence
    assert "Covered Skills in Interview: Go, Raft" in dossier
    assert "Remaining / Unassessed Skills: Kafka, Kubernetes" in dossier

    # Section 4: Assistance
    assert "Solid explanation of quorum" in dossier

    # Section 5: Closing Candidate Inquiries
    assert "consensus mechanism does your internal storage cluster currently use" in dossier

    # Noise exclusion
    assert "can you hear me loud and clear" not in dossier


@pytest.mark.asyncio
async def test_optimized_evaluation_execution_and_invariants(
    sample_compact_profile,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """
    Verifies that evaluate_optimized_interview produces the exact 8-key schema,
    recalculates holistic_competency.score deterministically in Python, and preserves
    the immutable Phase 4B accuracy_scores on question_analysis.
    """
    mock_client = create_mock_client(mock_evaluation_payload)
    comparator = FinalEvaluationComparator(client=mock_client)

    result = await comparator.evaluate_optimized_interview(
        compact_profile=sample_compact_profile,
        confirmed_qa_pairs=sample_confirmed_qa_pairs,
        intelligence={"covered_skills": ["Go", "Raft"], "remaining_skills": ["Kafka"]},
        assistance={"interview_notes": ["Good understanding"]},
        transcript=sample_transcript
    )

    assert result is not None
    report = result["report"]
    meta = result["meta"]

    # 1. Exact 8-key schema
    required_keys = [
        "holistic_competency", "strengths", "development_areas", "jd_analysis",
        "resume_validation", "question_analysis", "conversation_summary", "observer_notes"
    ]
    for key in required_keys:
        assert key in report

    # 2. Holistic score deterministic arithmetic mean: round((90 + 85 + 88 + 92) / 4) = 89
    assert report["holistic_competency"]["score"] == 89

    # 3. Accuracy score immutability invariant
    qa_analysis = report["question_analysis"]
    assert len(qa_analysis) == 2
    assert qa_analysis[0]["pair_id"] == "Q1_A2"
    assert qa_analysis[0]["accuracy_score"] == 88  # Locked from confirmed_qa_pairs
    assert qa_analysis[1]["pair_id"] == "Q3_A4"
    assert qa_analysis[1]["accuracy_score"] == 92  # Locked from confirmed_qa_pairs

    assert meta["duration_ms"] >= 0.0
    assert meta["estimated_tokens"] > 0


def test_compare_evaluations_similarity_metrics(mock_evaluation_payload):
    """Verifies granular comparison metrics and success threshold validations."""
    comparator = FinalEvaluationComparator()

    legacy_report = dict(mock_evaluation_payload)
    legacy_report["holistic_competency"] = {
        "score": 88,
        "dimensions": {
            "technical_depth": {"score": 90, "summary": "Great depth"},
            "practical_experience": {"score": 85, "summary": "Great practical"},
            "problem_solving": {"score": 85, "summary": "Good problem solving"},
            "communication_clarity": {"score": 90, "summary": "Clear communication"}
        }
    }

    opt_report = dict(mock_evaluation_payload)
    opt_report["holistic_competency"] = {
        "score": 89,
        "dimensions": {
            "technical_depth": {"score": 90, "summary": "Great depth"},
            "practical_experience": {"score": 85, "summary": "Great practical"},
            "problem_solving": {"score": 88, "summary": "Good problem solving"},
            "communication_clarity": {"score": 92, "summary": "Clear communication"}
        }
    }

    legacy_meta = {"duration_ms": 22000.0, "estimated_tokens": 15000}
    opt_meta = {"duration_ms": 6500.0, "estimated_tokens": 4200}

    comp = comparator.compare_evaluations(
        legacy_report=legacy_report,
        optimized_report=opt_report,
        legacy_meta=legacy_meta,
        optimized_meta=opt_meta
    )

    # Score delta: |88 - 89| = 1 <= 3
    assert comp["score_delta"] == 1
    assert comp["thresholds_met"]["holistic_score_delta_met"] is True

    # Similarity thresholds
    assert comp["strength_similarity"] == 1.0
    assert comp["skill_coverage_similarity"] == 1.0
    assert comp["resume_validation_similarity"] == 1.0
    assert comp["conversation_summary_similarity"] == 1.0
    assert comp["report_similarity_score"] >= 0.90
    assert comp["all_thresholds_met"] is True

    # Efficiency reductions
    assert comp["token_reduction_pct"] == 72.0
    assert comp["latency_reduction_pct"] == 70.5


@pytest.mark.asyncio
async def test_session_engine_finalize_report_shadow_execution(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """
    Verifies that finalize_report() executes FinalEvaluationComparator in shadow mode,
    writing telemetry comparisons without altering the recruiter-facing final report.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    mock_client = create_mock_client(mock_evaluation_payload)

    with patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", True), \
         patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", False):
        session = CopilotSessionEngine(
            session_id="sess_final_shadow_test",
            jd="Senior Distributed Systems Architect",
            resume="Alice Smith, Principal Engineer",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        session.transcript = list(sample_transcript)
        session.final_evaluation_comparator.client = mock_client

        # Mock legacy service to return predictable report
        legacy_service_mock = MagicMock()
        legacy_service_mock.format_interview_evidence = MagicMock(return_value="Evidence text")
        legacy_service_mock.evaluate_final_interview = AsyncMock(return_value=mock_evaluation_payload)

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            final_report = await session.finalize_report()
            if session.background_tasks:
                await asyncio.gather(*list(session.background_tasks))

            # 1. Recruiter report matches legacy source of truth
            assert final_report is not None
            assert final_report["is_finalized"] is True
            assert final_report["conversation_summary"] == mock_evaluation_payload["conversation_summary"]

            # 2. Shadow comparison was captured in session state
            assert len(session.final_eval_shadow_comparisons) == 1
            comparison = session.final_eval_shadow_comparisons[0]
            assert comparison["session_id"] == "sess_final_shadow_test"
            assert comparison["score_delta"] == 0  # Identical mock payload
            assert comparison["report_similarity_score"] >= 0.90

            # 3. Telemetry metrics incremented
            metrics = get_final_eval_shadow_metrics()
            assert metrics["final_eval_shadow_runs_total"] == 1
            assert metrics["final_eval_shadow_success_total"] == 1
            assert metrics["final_eval_shadow_errors_total"] == 0

            # 4. Shadow comparison is NOT present in the returned final report contract
            assert "final_eval_shadow_comparisons" not in final_report


@pytest.mark.asyncio
async def test_feature_flag_disabled_suppresses_shadow_evaluator(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """Verifies that ENABLE_FINAL_EVAL_SHADOW=False disables shadow execution completely."""
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", False), \
         patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", False):
        session = CopilotSessionEngine(
            session_id="sess_final_shadow_off",
            jd="Senior Distributed Systems Architect",
            resume="Alice Smith",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        assert session.enable_final_eval_shadow is False

        legacy_service_mock = MagicMock()
        legacy_service_mock.format_interview_evidence = MagicMock(return_value="Evidence text")
        legacy_service_mock.evaluate_final_interview = AsyncMock(return_value=mock_evaluation_payload)

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            final_report = await session.finalize_report()
            assert final_report["is_finalized"] is True
            assert len(session.final_eval_shadow_comparisons) == 0

            metrics = get_final_eval_shadow_metrics()
            assert metrics["final_eval_shadow_runs_total"] == 0


@pytest.mark.asyncio
async def test_shadow_evaluator_graceful_error_isolation(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """
    Verifies that if the shadow comparator encounters an LLM timeout or exception,
    the production finalize_report() flow continues smoothly with zero errors.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    # Client that raises an exception
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("OpenAI 500 Internal Server Error"))

    with patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", True), \
         patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", False):
        session = CopilotSessionEngine(
            session_id="sess_final_shadow_err",
            jd="JD",
            resume="Resume",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        session.final_evaluation_comparator.client = mock_client

        legacy_service_mock = MagicMock()
        legacy_service_mock.format_interview_evidence = MagicMock(return_value="Evidence text")
        legacy_service_mock.evaluate_final_interview = AsyncMock(return_value=mock_evaluation_payload)

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            # Must succeed without throwing
            final_report = await session.finalize_report()
            if session.background_tasks:
                await asyncio.gather(*list(session.background_tasks))
            assert final_report["is_finalized"] is True

            # Telemetry records error safely
            metrics = get_final_eval_shadow_metrics()
            assert metrics["final_eval_shadow_errors_total"] == 1
            assert metrics["final_eval_shadow_success_total"] == 0


# =========================================================================
# Phase 5B Production Cutover & Fallback Tests
# =========================================================================

@pytest.mark.asyncio
async def test_phase5b_production_flow_success(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """
    Phase 5B Production Cutover Verification:
    - Primary optimized evaluator runs and returns valid report.
    - Python deterministically computes overall_score and holistic score.
    - Question analysis preserves immutable accuracy scores.
    - Production metrics track success and duration.
    - Legacy evaluator is NOT invoked.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    mock_client = create_mock_client(mock_evaluation_payload)

    with patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", True), \
         patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", False):
        session = CopilotSessionEngine(
            session_id="sess_phase5b_prod_success",
            jd="Senior Distributed Systems Architect",
            resume="Alice Smith, Principal Engineer",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        session.transcript = list(sample_transcript)
        session.final_evaluation_comparator.client = mock_client

        legacy_service_mock = MagicMock()
        legacy_service_mock.evaluate_final_interview = AsyncMock()

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            final_report = await session.finalize_report()

            # 1. Verification of authoritative final report assembly
            assert final_report is not None
            assert final_report["is_finalized"] is True
            assert final_report["session_id"] == "sess_phase5b_prod_success"
            assert final_report["conversation_summary"] == mock_evaluation_payload["conversation_summary"]

            # 2. Holistic score calculated: (90 + 85 + 88 + 92) / 4 = 88.75 -> 89
            assert final_report["holistic_competency"]["score"] == 89

            # 3. Overall score: 70% of QA average ( (88+92)/2 = 90 ) + 30% of Holistic (89) = 63 + 26.7 = 89.7 -> 90
            assert final_report["overall_score"] == 90
            assert final_report["qa_accuracy_average"] == 90

            # 4. Immutable accuracy scores on question analysis
            qa_analysis = final_report["question_analysis"]
            assert len(qa_analysis) == 2
            assert qa_analysis[0]["accuracy_score"] == 88
            assert qa_analysis[1]["accuracy_score"] == 92

            # 5. Legacy evaluator was never invoked (zero legacy calls)
            legacy_service_mock.evaluate_final_interview.assert_not_called()

            # 6. Production metrics recorded correctly
            metrics = get_optimized_final_eval_production_metrics()
            assert metrics["optimized_eval_success_total"] == 1
            assert metrics["optimized_eval_failure_total"] == 0
            assert metrics["optimized_eval_fallback_total"] == 0
            assert metrics["optimized_eval_latency_ms"] >= 0.0


@pytest.mark.asyncio
async def test_phase5b_automatic_legacy_fallback(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """
    Phase 5B Automatic Legacy Fallback Verification:
    - If optimized evaluator fails or raises an exception, the system catches it,
      increments fallback telemetry, and seamlessly executes legacy FinalEvaluationService.
    - Recruiter receives a valid finalized report with zero failure exposure.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    # Broken client triggering exception
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("Optimized LLM context window exceeded"))

    with patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", True), \
         patch.object(Settings, "ENABLE_LEGACY_FINAL_EVAL_FALLBACK", True), \
         patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", False):
        session = CopilotSessionEngine(
            session_id="sess_phase5b_fallback",
            jd="Senior Distributed Systems Architect",
            resume="Alice Smith, Principal Engineer",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        session.transcript = list(sample_transcript)
        session.final_evaluation_comparator.client = mock_client

        legacy_service_mock = MagicMock()
        legacy_service_mock.format_interview_evidence = MagicMock(return_value="Legacy evidence text")
        legacy_service_mock.evaluate_final_interview = AsyncMock(return_value=mock_evaluation_payload)

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            final_report = await session.finalize_report()

            # 1. Report is finalized via fallback with zero user disruption
            assert final_report is not None
            assert final_report["is_finalized"] is True
            assert final_report["conversation_summary"] == mock_evaluation_payload["conversation_summary"]

            # 2. Legacy evaluator was called exactly once as fallback
            legacy_service_mock.evaluate_final_interview.assert_called_once()

            # 3. Telemetry captures the failure and the fallback transition
            metrics = get_optimized_final_eval_production_metrics()
            assert metrics["optimized_eval_success_total"] == 0
            assert metrics["optimized_eval_failure_total"] == 1
            assert metrics["optimized_eval_fallback_total"] == 1
            assert metrics["legacy_eval_latency_ms"] >= 0.0


@pytest.mark.asyncio
async def test_phase5b_fallback_disabled_behavior(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript
):
    """
    Phase 5B Fallback Disabled Safety Verification:
    - If optimized evaluator fails and ENABLE_LEGACY_FINAL_EVAL_FALLBACK=False,
      legacy evaluator is NOT called and session returns an error dict without being finalized.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API Key revoked"))

    with patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", True), \
         patch.object(Settings, "ENABLE_LEGACY_FINAL_EVAL_FALLBACK", False), \
         patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", False):
        session = CopilotSessionEngine(
            session_id="sess_phase5b_no_fallback",
            jd="JD",
            resume="Resume",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        session.transcript = list(sample_transcript)
        session.final_evaluation_comparator.client = mock_client

        legacy_service_mock = MagicMock()
        legacy_service_mock.evaluate_final_interview = AsyncMock()

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            final_report = await session.finalize_report()

            # 1. Error result returned, session not finalized
            assert final_report["is_finalized"] is False
            assert "error" in final_report

            # 2. Legacy service was never invoked
            legacy_service_mock.evaluate_final_interview.assert_not_called()

            # 3. Metrics confirm failure without fallback
            metrics = get_optimized_final_eval_production_metrics()
            assert metrics["optimized_eval_failure_total"] == 1
            assert metrics["optimized_eval_fallback_total"] == 0


@pytest.mark.asyncio
async def test_phase5b_feature_flag_disabled_legacy_routing(
    tmp_path,
    sample_confirmed_qa_pairs,
    sample_transcript,
    mock_evaluation_payload
):
    """
    Phase 5B Configuration Rollback Verification:
    - Setting ENABLE_OPTIMIZED_FINAL_EVAL=False directly routes to legacy FinalEvaluationService,
      ensuring instantaneous zero-code rollback capability.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    with patch.object(Settings, "ENABLE_OPTIMIZED_FINAL_EVAL", False), \
         patch.object(Settings, "ENABLE_FINAL_EVAL_SHADOW", False):
        session = CopilotSessionEngine(
            session_id="sess_phase5b_legacy_direct",
            jd="JD",
            resume="Resume",
            repo=mock_repo,
            confirmed_qa_pairs=sample_confirmed_qa_pairs
        )
        session.transcript = list(sample_transcript)

        legacy_service_mock = MagicMock()
        legacy_service_mock.format_interview_evidence = MagicMock(return_value="Legacy evidence text")
        legacy_service_mock.evaluate_final_interview = AsyncMock(return_value=mock_evaluation_payload)

        with patch("services.copilot.src.engine.session.FinalEvaluationService", return_value=legacy_service_mock):
            final_report = await session.finalize_report()

            assert final_report["is_finalized"] is True
            assert final_report["conversation_summary"] == mock_evaluation_payload["conversation_summary"]
            legacy_service_mock.evaluate_final_interview.assert_called_once()

            # Optimized evaluator metrics untouched
            metrics = get_optimized_final_eval_production_metrics()
            assert metrics["optimized_eval_success_total"] == 0
            assert metrics["optimized_eval_failure_total"] == 0
            assert metrics["optimized_eval_fallback_total"] == 0


def test_phase5b_production_telemetry_functions():
    """Verifies get and reset functions for production telemetry dictionary."""
    metrics = get_optimized_final_eval_production_metrics()
    assert "optimized_eval_success_total" in metrics
    assert "optimized_eval_failure_total" in metrics
    assert "optimized_eval_fallback_total" in metrics

    # Simulate metric recording
    optimized_final_eval_production_metrics["optimized_eval_success_total"] = 42
    assert get_optimized_final_eval_production_metrics()["optimized_eval_success_total"] == 42

    reset_optimized_final_eval_production_metrics()
    assert get_optimized_final_eval_production_metrics()["optimized_eval_success_total"] == 0

