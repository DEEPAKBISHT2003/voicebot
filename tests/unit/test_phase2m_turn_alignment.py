"""
Phase 2M — Unit Tests for Native Turn Alignment & Real Copilot Evaluation Validation
File: tests/unit/test_phase2m_turn_alignment.py

Validates:
1. Logical turn creation and duck-typing properties.
2. Same-speaker caption sequence stitching within pause thresholds (~2.0s).
3. Part C (Pause/Same-speaker test, Turn 14): multi-paragraph answer produces 1 logical turn.
4. Part E (Long candidate answer, Turn 22): long uninterrupted answer produces 1 logical turn.
5. Part B & D (Short answers): 1-word answers are preserved and not lost.
6. Speaker switch triggers immediate logical turn boundary.
7. Inactivity timeout triggers turn boundary after silence.
8. Interviewer turns NEVER trigger candidate evaluation (count == 0).
9. Candidate turns trigger evaluation EXACTLY ONCE per logical answer.
10. Teams display names ("Deepak Bisht", "kaua (Unverified)") preserved verbatim.
11. Turn alignment classifications (CORRECT_ONE_TO_ONE, FRAGMENTED, DUPLICATED, MISSING).
12. Critical constraint: services/interview/** has zero changes.
"""

import os
import subprocess
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from services.copilot.src.pipeline.native_turn_finalizer import FinalizedTurn
from services.copilot.src.pipeline.native_turn_aligner import (
    LogicalTurn,
    NativeLogicalTurnAggregator,
    NativeShadowTurnController,
    TurnAlignmentClassifier
)
from services.copilot.src.pipeline.native_shadow_evaluator import NativeShadowEvaluationAdapter


def make_finalized_turn(
    turn_id: int,
    sequence_id: int,
    speaker_name: str,
    text: str,
    first_seen: datetime,
    last_update: datetime,
    finalized_at: datetime
) -> FinalizedTurn:
    return FinalizedTurn(
        turn_id=turn_id,
        sequence_id=sequence_id,
        speaker_name=speaker_name,
        text=text,
        text_versions=[text],
        first_seen_at=first_seen,
        last_update_at=last_update,
        finalized_at=finalized_at,
        finalization_strategy="strategy_c_quiescence_1500ms",
        update_count=1,
        source="teams_native"
    )


def test_logical_turn_creation():
    now = datetime.now(timezone.utc)
    turn = LogicalTurn(
        logical_turn_id=1,
        speaker_name="Deepak Bisht",
        text="I have worked with Python and FastAPI for several years.",
        sequence_ids=[101, 102],
        first_seen_at=now,
        last_update_at=now + timedelta(seconds=2),
        finalized_at=now + timedelta(seconds=3),
        boundary_reason="speaker_switch",
        is_candidate=True,
        raw_event_count=5
    )

    assert turn.logical_turn_id == 1
    assert turn.turn_id == 1
    assert turn.sequence_id == 101
    assert turn.speaker_name == "Deepak Bisht"
    assert turn.duration_ms == 2000.0
    assert turn.finalization_latency_ms == 1000.0
    assert turn.to_dict()["boundary_reason"] == "speaker_switch"


def test_same_speaker_stitching_within_pause():
    aggregator = NativeLogicalTurnAggregator(
        session_id="test-stitch",
        candidate_speaker_name="Deepak Bisht",
        inactivity_threshold_ms=3000.0
    )
    t0 = datetime.now(timezone.utc)

    # Sequence 1
    s1 = make_finalized_turn(1, 101, "Deepak Bisht", "I have worked with Python", t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2))
    completed = aggregator.process_finalized_sequence(s1)
    assert len(completed) == 0
    assert aggregator.active_turn is not None
    assert aggregator.active_turn.text == "I have worked with Python"

    # Sequence 2 (starts 1.5s after Sequence 1 ended -> within 3.0s pause)
    t1 = t0 + timedelta(seconds=2.5)
    s2 = make_finalized_turn(2, 102, "Deepak Bisht", "for three years building microservices.", t1, t1 + timedelta(seconds=1), t1 + timedelta(seconds=2))
    completed = aggregator.process_finalized_sequence(s2)
    assert len(completed) == 0
    assert aggregator.active_turn.text == "I have worked with Python for three years building microservices."
    assert aggregator.active_turn.sequence_ids == [101, 102]


def test_part_c_pause_same_speaker_turn14():
    """Validates Turn 14 from script: 4 sentences with ~2s pauses are stitched into 1 logical turn."""
    aggregator = NativeLogicalTurnAggregator(
        session_id="test-part-c",
        candidate_speaker_name="Deepak Bisht",
        inactivity_threshold_ms=3000.0
    )
    t = datetime.now(timezone.utc)

    sentences = [
        "I would start with a stateless application layer.",
        "Then I would put the service behind a load balancer and run multiple instances.",
        "For shared state, I would use PostgreSQL and Redis where appropriate.",
        "For long-running operations, I would move the work into background workers."
    ]

    for i, s in enumerate(sentences):
        seq = make_finalized_turn(
            turn_id=i + 1,
            sequence_id=200 + i,
            speaker_name="Deepak Bisht",
            text=s,
            first_seen=t,
            last_update=t + timedelta(seconds=1.5),
            finalized_at=t + timedelta(seconds=3.0)
        )
        completed = aggregator.process_finalized_sequence(seq)
        assert len(completed) == 0
        t += timedelta(seconds=3.5)  # 2.0s pause before next speech begins (within 3000ms threshold)

    # Next speaker speaks (Interviewer Turn 15) -> closes Turn 14!
    interviewer_seq = make_finalized_turn(
        turn_id=5,
        sequence_id=205,
        speaker_name="kaua (Unverified)",
        text="REST or WebSocket?",
        first_seen=t + timedelta(seconds=1.0),
        last_update=t + timedelta(seconds=2.0),
        finalized_at=t + timedelta(seconds=3.5)
    )
    completed = aggregator.process_finalized_sequence(interviewer_seq)
    assert len(completed) == 1
    cand_turn = completed[0]
    assert cand_turn.speaker_name == "Deepak Bisht"
    assert cand_turn.sequence_ids == [200, 201, 202, 203]
    for s in sentences:
        assert s in cand_turn.text
    assert cand_turn.boundary_reason == "speaker_switch"


def test_part_e_long_candidate_answer_turn22():
    """Validates Turn 22 from script: long uninterrupted answer produces exactly 1 logical turn."""
    aggregator = NativeLogicalTurnAggregator(session_id="test-part-e", candidate_speaker_name="Deepak Bisht")
    t = datetime.now(timezone.utc)

    paragraphs = [
        "I would first identify where latency is being introduced across the entire pipeline.",
        "For example, I would look at audio capture, network transport, speech-to-text processing, application processing, model inference, and response delivery.",
        "I would measure each stage independently instead of assuming that the model itself is the bottleneck.",
        "For streaming systems, I would also make sure that data is processed incrementally instead of waiting for the complete request.",
        "I would use asynchronous processing where it makes sense and avoid unnecessary serialization and network hops.",
        "Finally, I would add metrics for each stage so that latency regressions can be identified over time."
    ]

    for i, p in enumerate(paragraphs):
        seq = make_finalized_turn(i + 1, 300 + i, "Deepak Bisht", p, t, t + timedelta(seconds=1.5), t + timedelta(seconds=3.0))
        aggregator.process_finalized_sequence(seq)
        t += timedelta(seconds=2.5)

    flushed = aggregator.flush(current_time=t)
    assert len(flushed) == 1
    assert flushed[0].speaker_name == "Deepak Bisht"
    assert len(flushed[0].sequence_ids) == 6
    assert "Finally, I would add metrics" in flushed[0].text


def test_short_answers_not_lost():
    """Validates Part B and Part D rapid single-word answers ("Python", "Productivity", "WebSocket", "HTTP")."""
    aggregator = NativeLogicalTurnAggregator(session_id="test-short", candidate_speaker_name="Deepak Bisht")
    t = datetime.now(timezone.utc)

    # Turn 9: Interviewer: Python or Java?
    seq9 = make_finalized_turn(1, 401, "kaua (Unverified)", "Python or Java?", t, t + timedelta(seconds=1), t + timedelta(seconds=2.5))
    aggregator.process_finalized_sequence(seq9)
    t += timedelta(seconds=3)

    # Turn 10: Candidate: Python.
    seq10 = make_finalized_turn(2, 402, "Deepak Bisht", "Python.", t, t + timedelta(seconds=0.5), t + timedelta(seconds=2.0))
    res10 = aggregator.process_finalized_sequence(seq10)
    assert len(res10) == 1
    assert res10[0].text == "Python or Java?"

    t += timedelta(seconds=2.5)
    # Turn 11: Interviewer: Why?
    seq11 = make_finalized_turn(3, 403, "kaua (Unverified)", "Why?", t, t + timedelta(seconds=0.5), t + timedelta(seconds=2.0))
    res11 = aggregator.process_finalized_sequence(seq11)
    assert len(res11) == 1
    assert res11[0].text == "Python."
    assert res11[0].speaker_name == "Deepak Bisht"

    t += timedelta(seconds=2.5)
    # Turn 12: Candidate: Productivity.
    seq12 = make_finalized_turn(4, 404, "Deepak Bisht", "Productivity.", t, t + timedelta(seconds=0.5), t + timedelta(seconds=2.0))
    res12 = aggregator.process_finalized_sequence(seq12)
    assert len(res12) == 1
    assert res12[0].text == "Why?"

    flushed = aggregator.flush(current_time=t + timedelta(seconds=3))
    assert len(flushed) == 1
    assert flushed[0].text == "Productivity."
    assert flushed[0].speaker_name == "Deepak Bisht"


def test_inactivity_timeout_boundary():
    aggregator = NativeLogicalTurnAggregator(
        session_id="test-timeout",
        candidate_speaker_name="Deepak Bisht",
        inactivity_threshold_ms=3000.0
    )
    t0 = datetime.now(timezone.utc)

    seq = make_finalized_turn(1, 501, "Deepak Bisht", "I am thinking about it.", t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2.5))
    aggregator.process_finalized_sequence(seq)

    # 1 second later: not timed out
    res = aggregator.check_inactivity(current_time=t0 + timedelta(seconds=2))
    assert len(res) == 0

    # 4 seconds later (>3000ms after last_update): timed out!
    res = aggregator.check_inactivity(current_time=t0 + timedelta(seconds=5))
    assert len(res) == 1
    assert res[0].boundary_reason == "inactivity_timeout"
    assert aggregator.active_turn is None


@pytest.mark.asyncio
async def test_interviewer_turns_filtered_from_evaluation():
    mock_adapter = MagicMock(spec=NativeShadowEvaluationAdapter)
    mock_adapter.shadow_transcript = []
    mock_adapter.evaluate_turn = AsyncMock()

    controller = NativeShadowTurnController(
        session_id="test-filter",
        shadow_adapter=mock_adapter,
        candidate_speaker_name="Deepak Bisht"
    )

    t = datetime.now(timezone.utc)
    interviewer_turn = LogicalTurn(
        logical_turn_id=1,
        speaker_name="kaua (Unverified)",
        text="What is your approach to reducing latency in a real-time system?",
        sequence_ids=[601],
        first_seen_at=t,
        last_update_at=t + timedelta(seconds=2),
        finalized_at=t + timedelta(seconds=3),
        boundary_reason="speaker_switch"
    )

    result = await controller.handle_logical_turn(interviewer_turn)

    # STRICT RULE: Interviewer turns NEVER trigger evaluation!
    assert result is None
    assert controller.interviewer_evaluations_count == 0
    assert controller.interviewer_turns_count == 1
    assert controller.last_interviewer_question == interviewer_turn.text
    mock_adapter.evaluate_turn.assert_not_called()
    assert len(mock_adapter.shadow_transcript) == 1


@pytest.mark.asyncio
async def test_candidate_turn_triggers_evaluation_exactly_once():
    mock_adapter = MagicMock(spec=NativeShadowEvaluationAdapter)
    mock_adapter.shadow_transcript = []
    mock_adapter.evaluate_turn = AsyncMock(return_value={
        "session_id": "test-eval",
        "turn_id": 2,
        "evaluation": {
            "percentage": 85,
            "topic": "Latency Reduction",
            "next_question": "How do you measure database overhead?"
        }
    })

    controller = NativeShadowTurnController(
        session_id="test-eval",
        shadow_adapter=mock_adapter,
        candidate_speaker_name="Deepak Bisht"
    )
    controller.last_interviewer_question = "What is your approach to reducing latency?"

    t = datetime.now(timezone.utc)
    cand_turn = LogicalTurn(
        logical_turn_id=2,
        speaker_name="Deepak Bisht",
        text="I usually start by measuring the complete request path before optimizing components.",
        sequence_ids=[701, 702],
        first_seen_at=t,
        last_update_at=t + timedelta(seconds=3),
        finalized_at=t + timedelta(seconds=4),
        boundary_reason="speaker_switch"
    )

    result = await controller.handle_logical_turn(cand_turn)

    assert result is not None
    assert controller.candidate_turns_count == 1
    assert len(controller.candidate_evaluations) == 1
    mock_adapter.evaluate_turn.assert_called_once_with(
        finalized_turn=cand_turn,
        last_question="What is your approach to reducing latency?",
        is_service_off=False,
        is_completed=False
    )
    assert cand_turn.evaluation["evaluation"]["percentage"] == 85


def test_teams_speaker_names_preserved_verbatim():
    aggregator = NativeLogicalTurnAggregator(session_id="test-names")
    t = datetime.now(timezone.utc)

    # Exact raw names
    s1 = make_finalized_turn(1, 801, "Deepak Bisht", "Hello.", t, t, t)
    s2 = make_finalized_turn(2, 802, "kaua (Unverified)", "Hi there.", t + timedelta(seconds=2), t + timedelta(seconds=2), t + timedelta(seconds=3))

    res1 = aggregator.process_finalized_sequence(s1)
    res2 = aggregator.process_finalized_sequence(s2)

    assert res2[0].speaker_name == "Deepak Bisht"
    assert aggregator.active_turn.speaker_name == "kaua (Unverified)"


def test_turn_alignment_classifier():
    exp = {"expected_text": "I have worked with Python and FastAPI."}

    # 1. Correct One-to-One
    res1 = TurnAlignmentClassifier.classify_turn(exp, [{"text": "I have worked with Python and FastAPI."}])
    assert res1["classification"] == "CORRECT_ONE_TO_ONE"

    # 2. Missing
    res2 = TurnAlignmentClassifier.classify_turn(exp, [])
    assert res2["classification"] == "MISSING"

    # 3. Fragmented
    res3 = TurnAlignmentClassifier.classify_turn(exp, [
        {"text": "I have worked with Python"},
        {"text": "and FastAPI."}
    ])
    assert res3["classification"] == "FRAGMENTED"

    # 4. Duplicated
    res4 = TurnAlignmentClassifier.classify_turn(exp, [
        {"text": "I have worked with Python and FastAPI."},
        {"text": "I have worked with Python and FastAPI."}
    ])
    assert res4["classification"] == "DUPLICATED"


def test_interview_service_remains_untouched():
    """Mandatory verification: services/interview is completely separate and not imported or modified."""
    import sys
    import shutil
    from services.copilot.src.pipeline import native_turn_aligner
    for mod_name in sys.modules:
        assert not mod_name.startswith("services.interview"), f"Forbidden import detected: {mod_name}"

    if shutil.which("git"):
        res = subprocess.run(
            ["git", "diff", "--", "services/interview/"],
            capture_output=True,
            text=True
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "", f"Interview service was modified! Diff:\n{res.stdout}"
