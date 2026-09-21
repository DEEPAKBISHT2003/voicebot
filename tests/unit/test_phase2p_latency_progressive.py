import asyncio
import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.copilot.src.pipeline.native_turn_finalizer import NativeTurnAggregator
from services.copilot.src.pipeline.native_turn_aligner import NativeLogicalTurnAggregator
from services.copilot.src.engine.session import CopilotSessionEngine


def test_800ms_quiescence_threshold_registered():
    """Verify strategy_c_quiescence_800ms is registered and configured at 800.0ms."""
    agg = NativeTurnAggregator(session_id="test-lat-800")
    assert "strategy_c_quiescence_800ms" in agg.STRATEGIES
    assert agg.QUIESCENCE_THRESHOLDS_MS.get("strategy_c_quiescence_800ms") == 800.0


def test_800ms_quiescence_finalization():
    """Verify sequence is finalized once 800ms of silence elapses."""
    agg = NativeTurnAggregator(session_id="test-lat-seq")
    t0 = datetime.datetime(2026, 9, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)

    event = {
        "event_type": "native_caption",
        "session_id": "test-lat-seq",
        "event_id": "evt-1",
        "speaker_name": "Deepak Bisht",
        "text": "Hello, testing low latency.",
        "detected_at": t0.isoformat(),
        "received_at": t0.isoformat(),
        "caption_sequence": 1,
        "dom_action": "updated"
    }

    # Process event at t0
    agg.process_raw_event(event)

    # Check quiescence at t0 + 500ms -> should NOT finalize yet
    t_500 = t0 + datetime.timedelta(milliseconds=500)
    q_500 = agg.check_quiescence(reference_time=t_500)
    assert len(q_500.get("strategy_c_quiescence_800ms", [])) == 0

    # Check quiescence at t0 + 850ms -> SHOULD finalize!
    t_850 = t0 + datetime.timedelta(milliseconds=850)
    q_850 = agg.check_quiescence(reference_time=t_850)
    finalized = q_850.get("strategy_c_quiescence_800ms", [])
    assert len(finalized) == 1
    assert finalized[0].text == "Hello, testing low latency."
    assert finalized[0].speaker_name == "Deepak Bisht"


def test_progressive_in_place_same_turn_id():
    """Verify consecutive sequences from same speaker preserve turn_id and stitch in-place."""
    aligner = NativeLogicalTurnAggregator(session_id="test-stitch", inactivity_threshold_ms=3000.0)
    t0 = datetime.datetime(2026, 9, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # First finalized sequence
    fseq1 = MagicMock()
    fseq1.sequence_id = 1
    fseq1.speaker_name = "Deepak Bisht"
    fseq1.text = "I worked on microservices."
    fseq1.first_seen_at = t0
    fseq1.last_update_at = t0 + datetime.timedelta(seconds=1)
    fseq1.finalized_at = t0 + datetime.timedelta(milliseconds=1800)
    fseq1.update_count = 3
    fseq1.received_at = fseq1.last_update_at

    completed = aligner.process_finalized_sequence(fseq1)
    assert completed == []  # Not completed yet (kept open)
    active = aligner.get_active_turn()
    assert active is not None
    assert active.logical_turn_id == 1
    assert active.text == "I worked on microservices."

    # Second finalized sequence (1.2s pause within 3.0s window)
    t1 = t0 + datetime.timedelta(seconds=2, milliseconds=200)
    fseq2 = MagicMock()
    fseq2.sequence_id = 2
    fseq2.speaker_name = "Deepak Bisht"
    fseq2.text = "and distributed systems."
    fseq2.first_seen_at = t1
    fseq2.last_update_at = t1 + datetime.timedelta(seconds=1)
    fseq2.finalized_at = t1 + datetime.timedelta(milliseconds=1800)
    fseq2.update_count = 2
    fseq2.received_at = fseq2.last_update_at

    completed2 = aligner.process_finalized_sequence(fseq2)
    assert completed2 == []  # Stitched into active turn
    active2 = aligner.get_active_turn()
    assert active2.logical_turn_id == 1  # Exact same turn ID!
    assert active2.text == "I worked on microservices. and distributed systems."


@pytest.mark.asyncio
async def test_session_engine_in_place_extension_and_deferred_llm():
    """Verify add_message with is_final=False updates in-place and defers LLM until is_final=True."""
    mock_repo = MagicMock()
    mock_repo.save_session = AsyncMock()
    eng = CopilotSessionEngine(session_id="test-engine-defer", repo=mock_repo, initial_transcript=[])

    # 1. Progressive update (is_final=False)
    msg1 = await eng.add_message(
        speaker="Deepak Bisht",
        text="Starting thought",
        source="teams_native",
        allow_merge=False,
        turn_id=1,
        is_final=False
    )
    assert msg1["turn_id"] == 1
    assert msg1["text"] == "Starting thought"
    assert "_llm_evaluated" not in msg1
    assert len(eng.transcript) == 1

    # 2. Extension update (is_final=False)
    msg2 = await eng.add_message(
        speaker="Deepak Bisht",
        text="Starting thought continued with more details",
        source="teams_native",
        allow_merge=False,
        turn_id=1,
        is_final=False
    )
    assert msg2["turn_id"] == 1
    assert msg2["text"] == "Starting thought continued with more details"
    assert "_llm_evaluated" not in msg2
    assert len(eng.transcript) == 1  # Still 1 bubble!

    # 3. Finalization on timeout (is_final=True)
    msg3 = await eng.add_message(
        speaker="Deepak Bisht",
        text="Starting thought continued with more details",
        source="teams_native",
        allow_merge=False,
        turn_id=1,
        is_final=True
    )
    assert msg3["turn_id"] == 1
    assert msg3.get("_llm_evaluated") is True
    assert len(eng.transcript) == 1  # Still 1 bubble!
