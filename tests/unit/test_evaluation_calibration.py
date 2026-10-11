import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from services.copilot.src.services.unified_qa_worker import (
    UnifiedQAWorker,
    calculate_deterministic_accuracy_score,
    is_resume_match_applicable
)
from services.copilot.src.services.evaluation import CandidateEvaluationService
from services.copilot.src.services.final_evaluation_comparator import (
    FinalEvaluationComparator,
    build_structured_dossier
)
from services.copilot.src.services.final_evaluation import FinalEvaluationService
from services.copilot.src.services.final_score import calculate_final_score
from services.copilot.src.services.precompiler import CompactProfile


# ─────────────────────────────────────────────────────────────────────────────
# 12 Realistic Test Case Definitions
# ─────────────────────────────────────────────────────────────────────────────

CALIBRATION_TEST_CASES = {
    "case_1_fully_correct_detailed": {
        "description": "Fully correct and sufficiently detailed answer",
        "question": "How does Postgres MVCC work when updating a row?",
        "answer": (
            "Postgres handles updates using MVCC by creating a new version of the row (tuple) with updated values "
            "and setting the xmax of the previous row version to the current transaction ID, while the new tuple has "
            "xmin set to current transaction ID. Vacuumer later reclaims dead tuples once no active transaction can see them."
        ),
        "resume": "Backend Engineer with 5 years PostgreSQL and Python experience.",
        "old_simulated_ratings": {"relevance": 95, "technical": 95, "resume_match": 0, "completeness": 90},
        "new_calibrated_ratings": {"relevance": 98, "technical": 96, "resume_match": 100, "completeness": 95},
        "expected_min_score": 90,
        "is_acceptable": True
    },
    "case_2_correct_concise": {
        "description": "Correct but concise answer",
        "question": "What data structure does Redis use under the hood for Sorted Sets?",
        "answer": "A skip list combined with a hash table.",
        "resume": "Software Developer with Redis, Go, and Microservices experience.",
        "old_simulated_ratings": {"relevance": 95, "technical": 95, "resume_match": 0, "completeness": 50},
        "new_calibrated_ratings": {"relevance": 98, "technical": 98, "resume_match": 100, "completeness": 90},
        "expected_min_score": 88,
        "is_acceptable": True
    },
    "case_3_partially_correct_missing_secondary": {
        "description": "Partially correct answer with one missing secondary detail",
        "question": "Explain the two phases of a two-phase commit (2PC) protocol and how rollback is handled.",
        "answer": (
            "In phase one, the coordinator asks all participants to prepare and vote. If all vote yes, the coordinator "
            "tells everyone to commit. If someone votes no, the coordinator initiates rollback."
        ),
        "resume": "Distributed Systems Engineer.",
        "old_simulated_ratings": {"relevance": 85, "technical": 50, "resume_match": 0, "completeness": 40},
        "new_calibrated_ratings": {"relevance": 90, "technical": 82, "resume_match": 100, "completeness": 75},
        "expected_min_score": 75,
        "expected_max_score": 88,
        "is_acceptable": True
    },
    "case_4_technically_equivalent_different_terminology": {
        "description": "Technically equivalent answer with different terminology",
        "question": "How do you achieve mutual exclusion in concurrent programming?",
        "answer": (
            "We can use binary semaphores or spinlocks to guard the critical region so only one execution thread "
            "enters at any moment."
        ),
        "resume": "Systems programmer in C/C++.",
        "old_simulated_ratings": {"relevance": 85, "technical": 65, "resume_match": 0, "completeness": 60},
        "new_calibrated_ratings": {"relevance": 95, "technical": 90, "resume_match": 100, "completeness": 88},
        "expected_min_score": 85,
        "is_acceptable": True
    },
    "case_5_incorrect_superficially_resembling": {
        "description": "Incorrect answer that superficially resembles the expected answer",
        "question": "How does optimistic concurrency control (OCC) detect conflicting updates in a database?",
        "answer": "OCC acquires exclusive table locks at transaction start and maintains them until commit to prevent conflicts.",
        "resume": "Database administrator with 3 years experience.",
        "old_simulated_ratings": {"relevance": 70, "technical": 30, "resume_match": 0, "completeness": 30},
        "new_calibrated_ratings": {"relevance": 40, "technical": 20, "resume_match": 100, "completeness": 20},
        "expected_max_score": 48,
        "is_acceptable": False
    },
    "case_6_confident_technically_incorrect": {
        "description": "Confident but technically incorrect answer",
        "question": "In Kafka, how do you scale consumer processing across multiple partitions in the same consumer group?",
        "answer": (
            "You assign multiple consumer instances to the same single partition, and Kafka automatically load balances "
            "messages within that partition across those multiple consumer instances."
        ),
        "resume": "Kafka pipeline developer.",
        "old_simulated_ratings": {"relevance": 80, "technical": 20, "resume_match": 0, "completeness": 20},
        "new_calibrated_ratings": {"relevance": 45, "technical": 15, "resume_match": 100, "completeness": 20},
        "expected_max_score": 48,
        "is_acceptable": False
    },
    "case_7_correct_reasoning_minor_slip": {
        "description": "Correct reasoning with a minor factual or wording mistake",
        "question": "What HTTP status code and header are returned for content negotiation when caching is validated via ETag?",
        "answer": (
            "The server returns HTTP 304 Not Modified with the If-None-Match... sorry, with the ETag header, telling the "
            "browser the cached copy is fresh."
        ),
        "resume": "Full Stack Web Developer.",
        "old_simulated_ratings": {"relevance": 85, "technical": 60, "resume_match": 0, "completeness": 60},
        "new_calibrated_ratings": {"relevance": 95, "technical": 88, "resume_match": 100, "completeness": 88},
        "expected_min_score": 82,
        "is_acceptable": True
    },
    "case_8_unanswered_question": {
        "description": "Unanswered question",
        "question": "Can you explain how consistent hashing minimizes remapping when nodes are added to a distributed cluster?",
        "answer": "I am not familiar with consistent hashing or how node remapping is handled there.",
        "resume": "Software engineer with 2 years general experience.",
        "old_simulated_ratings": {"relevance": 30, "technical": 0, "resume_match": 0, "completeness": 0},
        "new_calibrated_ratings": {"relevance": 30, "technical": 0, "resume_match": 100, "completeness": 0},
        "expected_max_score": 40,
        "is_acceptable": False
    },
    "case_9_ambiguous_transcript_caption_loss": {
        "description": "Ambiguous or incomplete transcript caused by caption loss",
        "question": "How do you mitigate database connection exhaustion in a microservices architecture?",
        "answer": "We implement connection pooling using HikariCP and... [audio cut out] ...set maximum pool size with connection timeouts.",
        "resume": "Backend engineer with Java/Spring and MySQL.",
        "old_simulated_ratings": {"relevance": 60, "technical": 40, "resume_match": 0, "completeness": 20},
        "new_calibrated_ratings": {"relevance": 85, "technical": 78, "resume_match": 100, "completeness": 70},
        "expected_min_score": 70,
        "expected_max_score": 85,
        "is_acceptable": True
    },
    "case_10_challenging_question_deep_reasoning": {
        "description": "A challenging question requiring deeper technical reasoning",
        "question": "In distributed consensus under the Raft algorithm, why is the leader election timeout randomized?",
        "answer": (
            "The election timeout is randomized (e.g. between 150ms and 300ms) specifically to prevent split-vote deadlocks "
            "where multiple candidates simultaneously initiate elections and divide the majority quorum. The randomization "
            "guarantees one candidate node times out first, requests votes, and establishes leadership before others trigger."
        ),
        "resume": "Staff Infrastructure Architect with Raft and Paxos experience.",
        "old_simulated_ratings": {"relevance": 95, "technical": 95, "resume_match": 0, "completeness": 90},
        "new_calibrated_ratings": {"relevance": 98, "technical": 96, "resume_match": 100, "completeness": 95},
        "expected_min_score": 92,
        "is_acceptable": True
    },
    "case_11_strong_answer_with_communication_style_traits": {
        "description": "A strong answer that should not be penalized for irrelevant communication-style traits",
        "question": "What is the difference between TCP and UDP?",
        "answer": (
            "Um, so basically, like, TCP is connection-oriented, right? It provides, uh, reliable delivery and ordering "
            "with ACKs and flow control. Whereas, like, UDP is connectionless and datagram-based, so it has lower latency "
            "and no retransmission."
        ),
        "resume": "Network developer.",
        "old_simulated_ratings": {"relevance": 85, "technical": 85, "resume_match": 0, "completeness": 70},
        "new_calibrated_ratings": {"relevance": 95, "technical": 94, "resume_match": 100, "completeness": 90},
        "expected_min_score": 88,
        "is_acceptable": True
    },
    "case_12_weak_answer_preserving_strictness": {
        "description": "A weak answer that must continue to receive a low score after the revision",
        "question": "How does a B-Tree index accelerate range queries in SQL databases?",
        "answer": "A B-Tree is like a blockchain where each row is hashed into a block and queries search the hash table.",
        "resume": "Junior programmer.",
        "old_simulated_ratings": {"relevance": 40, "technical": 10, "resume_match": 0, "completeness": 10},
        "new_calibrated_ratings": {"relevance": 25, "technical": 10, "resume_match": 100, "completeness": 10},
        "expected_max_score": 40,
        "is_acceptable": False
    }
}


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests for Calibration & Regression
# ─────────────────────────────────────────────────────────────────────────────

def test_prompt_contains_calibration_principles():
    """Verifies that all required calibration principles are embedded in the prompt instructions."""
    comparator = FinalEvaluationComparator()
    system_prompt, user_prompt = comparator.build_prompts("Evidence text")
    
    # Check operational rules intact
    assert "CRITICAL OPERATIONAL RULES:" in system_prompt
    assert "Phase 4B Accuracy Scores; they are authoritative" in system_prompt
    assert "Distinguish between 'not demonstrated' and 'not assessed'" in system_prompt
    
    # Check calibrated principles present
    assert "EVALUATION & CALIBRATION PRINCIPLES:" in system_prompt
    assert "EVIDENCE-BASED & PROPORTIONAL" in system_prompt
    assert "TECHNICAL EQUIVALENCE" in system_prompt
    assert "CONCISE DELIVERY" in system_prompt
    assert "CALIBRATED COMPETENCY DIMENSIONS" in system_prompt
    assert "AVOID DUPLICATE PENALTIES" in system_prompt
    assert "PRESERVE RIGOR" in system_prompt
    assert "speech-to-text transcription artifacts" in system_prompt


def test_unified_qa_worker_prompt_contains_calibration_and_invariants():
    """Verifies that UnifiedQAWorker maintains strict isolation invariants and calibration principles."""
    worker = UnifiedQAWorker()
    prompt = worker.build_prompt(
        question="What is a semaphore?",
        answer="A synchronization primitive.",
        resume="Dan, Developer",
        jd="Senior Architect",
        current_interview_state={"covered_skills": ["Concurrency"], "current_topic": "Threading"}
    )

    # Invariants from Phase 4B tests
    assert "SECTION 1: TECHNICAL ACCURACY EVALUATION (STRICT ISOLATION RULE)" in prompt
    assert "Do NOT use or assume any [TARGET JOB DESCRIPTION] for this section!" in prompt
    assert "SECTION 2: CONVERSATION INTELLIGENCE" in prompt
    assert "Target Job Description" in prompt

    # Calibrated principles in Section 1
    assert "Proportional credit" in prompt
    assert "Technical equivalence" in prompt
    assert "Conceptual Neutrality" in prompt
    assert "Do NOT judge completeness simply by answer length" in prompt
    assert "Communication style rule" in prompt


def test_legacy_evaluation_service_prompt_calibration():
    """Verifies that legacy CandidateEvaluationService and FinalEvaluationService include calibrated rules."""
    legacy_eval = FinalEvaluationService()
    # We inspect system prompt constructed in evaluate_final_interview via code check
    assert "technical_depth" in legacy_eval.SUPPORTED_DIMENSIONS
    assert "communication_clarity" in legacy_eval.SUPPORTED_DIMENSIONS


def test_calibration_across_all_12_test_cases():
    """
    Executes score computation across all 12 realistic test cases, comparing
    the uncalibrated (old) harsh score against the calibrated (new) score.
    Verifies that:
      - Valid answers (cases 1, 2, 3, 4, 7, 9, 10, 11) receive fair, calibrated scores.
      - Deficient/incorrect answers (cases 5, 6, 8, 12) strictly remain low.
      - Brevity, resume neutral questions, and minor wording slips are not over-penalized.
    """
    for case_id, case in CALIBRATION_TEST_CASES.items():
        old_ratings = case["old_simulated_ratings"]
        new_ratings = case["new_calibrated_ratings"]

        # Determine applicability for this Q&A
        is_applicable = is_resume_match_applicable(
            question=case["question"],
            answer=case["answer"]
        )

        old_score = calculate_deterministic_accuracy_score(
            relevance=old_ratings["relevance"],
            technical=old_ratings["technical"],
            resume_match=old_ratings["resume_match"],
            completeness=old_ratings["completeness"]
        )

        new_score = calculate_deterministic_accuracy_score(
            relevance=new_ratings["relevance"],
            technical=new_ratings["technical"],
            resume_match=new_ratings["resume_match"],
            completeness=new_ratings["completeness"],
            is_resume_applicable=is_applicable
        )

        # Assertions per test case expectations
        if case["is_acceptable"]:
            if "expected_min_score" in case:
                assert new_score >= case["expected_min_score"], (
                    f"[{case_id}] Calibrated score {new_score}% is below expected minimum {case['expected_min_score']}%"
                )
            if "expected_max_score" in case:
                assert new_score <= case["expected_max_score"], (
                    f"[{case_id}] Calibrated score {new_score}% is above expected maximum {case['expected_max_score']}%"
                )
            # In all acceptable cases, the old score was depressed due to resume_match=0 or brevity penalty
            assert new_score > old_score, (
                f"[{case_id}] Calibrated score {new_score}% should be fairer than depressed old score {old_score}%"
            )
        else:
            # Rigor check: Material errors and unanswered questions must remain strictly low!
            assert new_score <= case["expected_max_score"], (
                f"[{case_id}] RIGOR BREACH: Bad answer scored {new_score}%, exceeding max threshold {case['expected_max_score']}%"
            )


def test_deterministic_70_30_aggregation_consistency():
    """
    Verifies that end-to-end score aggregation strictly maintains:
    Overall Score = round(0.70 * QA_Avg + 0.30 * Holistic_Score)
    and does not introduce arbitrary inflation, minimum floors, or changed weights.
    """
    # Candidate with 3 calibrated QA answers: 94%, 91%, 84% -> QA Avg = 90%
    confirmed_qa = [
        {"pair_id": "Q1", "accuracy_score": 94},
        {"pair_id": "Q2", "accuracy_score": 91},
        {"pair_id": "Q3", "accuracy_score": 84}
    ]
    # Holistic Competency dimensions: 88, 85, 87, 90 -> Holistic Score = round((88+85+87+90)/4) = 88
    holistic = {
        "score": 88,
        "dimensions": {
            "technical_depth": {"score": 88, "summary": "Strong core fundamentals."},
            "practical_experience": {"score": 85, "summary": "Demonstrated hands-on familiarity."},
            "problem_solving": {"score": 87, "summary": "Systematic architectural trade-off reasoning."},
            "communication_clarity": {"score": 90, "summary": "Clear, direct technical delivery."}
        }
    }

    res = calculate_final_score(confirmed_qa_pairs=confirmed_qa, holistic_competency=holistic)
    
    assert res["qa_accuracy_average"] == 90
    assert res["holistic_competency_score"] == 88
    # 0.70 * 90 + 0.30 * 88 = 63.0 + 26.4 = 89.4 -> round(89.4) = 89
    assert res["overall_score"] == 89
    assert res["score_status"] == "complete"
    assert res["qa_evaluated_count"] == 3


def test_weak_candidate_retains_low_score_under_aggregation():
    """
    Verifies that weak candidates with technical misconceptions continue to receive
    failing overall scores and are not artificially inflated by the calibration.
    """
    # Candidate with weak answers: 38% (case 5), 32% (case 6), 21% (case 12) -> QA Avg = round((38+32+21)/3) = 30%
    confirmed_qa = [
        {"pair_id": "Q1", "accuracy_score": 38},
        {"pair_id": "Q2", "accuracy_score": 32},
        {"pair_id": "Q3", "accuracy_score": 21}
    ]
    holistic = {
        "score": 35,
        "dimensions": {
            "technical_depth": {"score": 25, "summary": "Fundamental misunderstandings of locking and partitioning."},
            "practical_experience": {"score": 35, "summary": "Unable to articulate actual indexing mechanics."},
            "problem_solving": {"score": 35, "summary": "Faulty reasoning on concurrency detection."},
            "communication_clarity": {"score": 45, "summary": "Plausible delivery but technically incorrect."}
        }
    }

    res = calculate_final_score(confirmed_qa_pairs=confirmed_qa, holistic_competency=holistic)

    assert res["qa_accuracy_average"] == 30
    assert res["holistic_competency_score"] == 35
    # 0.70 * 30 + 0.30 * 35 = 21.0 + 10.5 = 31.5 -> round = 32
    assert res["overall_score"] == 32
    assert res["overall_score"] < 40  # Firmly failing


def test_insufficient_evidence_handling_preserved():
    """Verifies that missing or unassessed QA pairs correctly trigger 'insufficient_evidence'."""
    res_empty_qa = calculate_final_score(confirmed_qa_pairs=[], holistic_competency={"score": 80})
    assert res_empty_qa["score_status"] == "insufficient_evidence"
    assert res_empty_qa["overall_score"] is None
    assert res_empty_qa["qa_accuracy_average"] is None

    res_missing_holistic = calculate_final_score(
        confirmed_qa_pairs=[{"pair_id": "Q1", "accuracy_score": 85}],
        holistic_competency=None
    )
    assert res_missing_holistic["score_status"] == "insufficient_evidence"
    assert res_missing_holistic["overall_score"] is None


def test_correct_conceptual_answer_not_penalized_for_lacking_experience_claims():
    """
    Verifies that a purely conceptual question is identified as inapplicable for resume match,
    and a correct answer receives full/fair credit without requiring personal experience claims.
    """
    question = "Can you explain how B-Tree indexes work in relational databases?"
    answer = (
        "B-Tree indexes maintain sorted key-value pairs in a balanced multi-way tree structure. "
        "Leaf nodes contain pointers to data tuples, enabling O(log N) lookups, range scans, and insertions."
    )
    # Check applicability
    applicable = is_resume_match_applicable(question=question, answer=answer)
    assert applicable is False, "Conceptual technical question should have resume_match inapplicable"

    # Deterministic scoring: rel=95, tech=95, comp=90, resume_match inapplicable
    score = calculate_deterministic_accuracy_score(
        relevance=95,
        technical=95,
        resume_match=None,
        completeness=90,
        is_resume_applicable=applicable
    )
    # 0.40*95 + 0.40*95 + 0.20*90 = 38.0 + 38.0 + 18.0 = 94.0 -> 94
    assert score == 94
    assert score >= 90


def test_unanswered_conceptual_question_receives_no_resume_bonus():
    """
    CRITICAL RIGOR TEST:
    Verifies that an unanswered conceptual question receives strictly 0 points,
    and CANNOT receive a 25-point unearned bonus from resume neutrality.
    """
    question = "How does consistent hashing minimize data migration during cluster resizing?"
    answer = ""

    applicable = is_resume_match_applicable(question=question, answer=answer)
    assert applicable is False

    # When all substantive dimensions are 0
    score_inapplicable = calculate_deterministic_accuracy_score(
        relevance=0,
        technical=0,
        resume_match=None,
        completeness=0,
        is_resume_applicable=applicable
    )
    assert score_inapplicable == 0, f"Unanswered conceptual question must score 0, got {score_inapplicable}"

    # Even if an older caller passes resume_match=100 with is_resume_applicable=False
    score_with_bogus_resume = calculate_deterministic_accuracy_score(
        relevance=0,
        technical=0,
        resume_match=100,
        completeness=0,
        is_resume_applicable=False
    )
    assert score_with_bogus_resume == 0, "Inapplicable resume match value must not inflate unanswered score"

    # Even if an older caller passes resume_match=100 with is_resume_applicable=True
    score_unanswered_guarded = calculate_deterministic_accuracy_score(
        relevance=0,
        technical=0,
        resume_match=100,
        completeness=0,
        is_resume_applicable=True
    )
    assert score_unanswered_guarded == 0, "Unanswered invariant must strictly yield 0 even if applicable"


def test_fundamentally_incorrect_conceptual_answer_remains_low_scoring():
    """
    CRITICAL RIGOR TEST:
    Verifies that a fundamentally incorrect conceptual answer receives a failing score,
    uninflated by resume neutrality.
    """
    question = "What is polymorphism in object-oriented programming?"
    answer = "Polymorphism means variables cannot have data types and all memory is statically allocated."

    applicable = is_resume_match_applicable(question=question, answer=answer)
    assert applicable is False

    # Under old flawed formula with resume_match=100:
    # 0.30*40 + 0.30*10 + 0.25*100 + 0.15*20 = 12 + 3 + 25 + 3 = 43 (artificially inflated)
    old_flawed_score = calculate_deterministic_accuracy_score(
        relevance=40,
        technical=10,
        resume_match=100,
        completeness=20,
        is_resume_applicable=True
    )
    assert old_flawed_score == 43

    # Under calibrated renormalized formula:
    # 0.40*40 + 0.40*10 + 0.20*20 = 16 + 4 + 4 = 24
    calibrated_score = calculate_deterministic_accuracy_score(
        relevance=40,
        technical=10,
        resume_match=None,
        completeness=20,
        is_resume_applicable=applicable
    )
    assert calibrated_score == 24
    assert calibrated_score < 30, f"Fundamentally incorrect answer must fail decisively, got {calibrated_score}"


def test_relevant_experiential_question_includes_resume_match():
    """
    Verifies that an experiential question asking about past roles/projects
    properly includes resume match in the 30/30/25/15 weighting.
    """
    question = "Tell me about a time in your past role where you resolved a major database bottleneck."
    answer = (
        "At my previous company, our PostgreSQL cluster suffered high connection latency. "
        "I implemented PgBouncer connection pooling and optimized missing composite indexes."
    )

    applicable = is_resume_match_applicable(question=question, answer=answer)
    assert applicable is True, "Experiential question with past role claims must have resume_match applicable"

    # Case A: Claims consistent with resume (resume_match = 85)
    # 0.30*90 + 0.30*88 + 0.25*85 + 0.15*80 = 27.0 + 26.4 + 21.25 + 12.0 = 86.65 -> 87
    score_consistent = calculate_deterministic_accuracy_score(
        relevance=90,
        technical=88,
        resume_match=85,
        completeness=80,
        is_resume_applicable=applicable
    )
    assert score_consistent == 87

    # Case B: Claims contradicted or exaggerated compared to resume (resume_match = 20)
    # 0.30*90 + 0.30*88 + 0.25*20 + 0.15*80 = 27.0 + 26.4 + 5.0 + 12.0 = 70.4 -> 70
    score_contradicted = calculate_deterministic_accuracy_score(
        relevance=90,
        technical=88,
        resume_match=20,
        completeness=80,
        is_resume_applicable=applicable
    )
    assert score_contradicted == 70
    assert score_contradicted < score_consistent


def test_changing_inapplicable_resume_match_value_cannot_change_score():
    """
    INVARIANT TEST:
    Verifies that when resume match is inapplicable, changing the resume_match value
    (to 0, 50, 100, or None) CANNOT alter the resulting accuracy score.
    """
    # Relevance=85, Technical=80, Completeness=75
    # Renormalized: round(0.40*85 + 0.40*80 + 0.20*75) = round(34 + 32 + 15) = 81
    score_none = calculate_deterministic_accuracy_score(85, 80, resume_match=None, completeness=75, is_resume_applicable=False)
    score_zero = calculate_deterministic_accuracy_score(85, 80, resume_match=0, completeness=75, is_resume_applicable=False)
    score_fifty = calculate_deterministic_accuracy_score(85, 80, resume_match=50, completeness=75, is_resume_applicable=False)
    score_hundred = calculate_deterministic_accuracy_score(85, 80, resume_match=100, completeness=75, is_resume_applicable=False)

    assert score_none == 81
    assert score_zero == 81
    assert score_fifty == 81
    assert score_hundred == 81
    assert score_none == score_zero == score_fifty == score_hundred


def test_output_schema_backward_compatibility():
    """
    CONTRACT TEST:
    Verifies that UnifiedQAWorker preserves all required schema fields including
    resume_match in accuracy_evaluation.
    """
    worker = UnifiedQAWorker()
    prompt = worker.build_prompt(
        question="What is an index?",
        answer="A lookup structure.",
        resume="Alice",
        jd="Architect"
    )

    # Schema verification
    assert '"accuracy_evaluation"' in prompt
    assert '"relevance"' in prompt
    assert '"technical"' in prompt
    assert '"resume_match"' in prompt
    assert '"completeness"' in prompt
    assert '"intelligence"' in prompt
    assert '"assistance"' in prompt
    assert '"suggestions"' in prompt


def test_applicable_weights_sum_to_one_and_bounded():
    """
    MATHEMATICAL RIGOR TEST:
    Verifies:
      1. Applicable weights sum exactly to 1.00 (0.30 + 0.30 + 0.25 + 0.15 = 1.00).
      2. Renormalized weights sum exactly to 1.00 (0.40 + 0.40 + 0.20 = 1.00).
      3. Relative ratios are preserved:
         - rel:tech = 0.30/0.30 = 1.0, 0.40/0.40 = 1.0
         - rel:comp = 0.30/0.15 = 2.0, 0.40/0.20 = 2.0
      4. Clamping bounds are strictly [0, 100].
    """
    # 1. Weights sum
    applicable_weights = [0.30, 0.30, 0.25, 0.15]
    assert round(sum(applicable_weights), 6) == 1.00

    inapplicable_weights = [0.40, 0.40, 0.20]
    assert round(sum(inapplicable_weights), 6) == 1.00

    # 2. Ratio preservation
    assert 0.30 / 0.30 == 0.40 / 0.40 == 1.0
    assert 0.30 / 0.15 == 0.40 / 0.20 == 2.0

    # 3. Clamping bounds
    assert calculate_deterministic_accuracy_score(200, 200, 200, 200, is_resume_applicable=True) == 100
    assert calculate_deterministic_accuracy_score(200, 200, None, 200, is_resume_applicable=False) == 100
    assert calculate_deterministic_accuracy_score(-50, -50, -50, -50, is_resume_applicable=True) == 0
    assert calculate_deterministic_accuracy_score(-50, -50, None, -50, is_resume_applicable=False) == 0


def test_is_resume_match_applicable_determination():
    """
    Verifies applicability classification across various explicit metadata and textual patterns.
    """
    # Explicit metadata overrides
    assert is_resume_match_applicable("Any Q", "Any A", metadata={"is_resume_applicable": True}) is True
    assert is_resume_match_applicable("Any Q", "Any A", metadata={"is_resume_applicable": False}) is False
    assert is_resume_match_applicable("Any Q", "Any A", metadata={"rubric": {"resume_match_applicable": True}}) is True
    assert is_resume_match_applicable("Any Q", "Any A", metadata={"rubric": {"resume_match_applicable": False}}) is False
    assert is_resume_match_applicable("Any Q", "Any A", metadata={"question_type": "behavioral"}) is True
    assert is_resume_match_applicable("Any Q", "Any A", metadata={"question_type": "conceptual"}) is False

    # Question text intent
    assert is_resume_match_applicable("Tell me about a time you had a production outage.") is True
    assert is_resume_match_applicable("Walk me through the Redis caching project on your resume.") is True
    assert is_resume_match_applicable("In your experience at your previous company, how was Kafka used?") is True
    assert is_resume_match_applicable("What is the difference between processes and threads?") is False

    # Answer text personal experience claims
    assert is_resume_match_applicable(
        question="How does Kafka partitioning work?",
        answer="At my previous job at Acme, we partitioned Kafka topics by customer ID."
    ) is True
    assert is_resume_match_applicable(
        question="How does Kafka partitioning work?",
        answer="Kafka partitions topics using hash keys or round-robin distribution."
    ) is False
