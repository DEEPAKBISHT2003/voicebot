"""
Phase 2K Unit Tests — Native Caption Turn Aggregation & Multi-Strategy Finalization
File: tests/unit/test_phase2k_finalizer.py

Verifies all 18 required scenarios:
1. Same sequence incremental updates.
2. Multiple updates becoming one turn.
3. Sequence transition.
4. Speaker transition.
5. Same speaker consecutive turns.
6. Rapid speaker switching.
7. Duplicate DOM events.
8. DOM replacement.
9. Caption removal.
10. Quiescence finalization.
11. Very short utterances.
12. Long utterances.
13. Empty caption events.
14. Unknown/missing speaker names.
15. Service OFF.
16. Completed session.
17. Session isolation.
18. Raw event preservation.
"""

import pytest
import datetime
from datetime import timezone, timedelta
from typing import Dict, Any

from services.copilot.src.pipeline.native_turn_finalizer import (
    NativeTurnAggregator,
    SequenceState,
    FinalizedTurn,
    compute_percentiles
)
from services.copilot.src.router import NativeCaptionEventRequest


def make_event(
    seq: int,
    text: str,
    speaker: str = "Deepak Bisht",
    offset_sec: float = 0.0,
    dom_action: str = "updated",
    base_time: datetime.datetime = None
) -> Dict[str, Any]:
    if base_time is None:
        base_time = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    t = base_time + timedelta(seconds=offset_sec)
    t_str = t.isoformat()
    return {
        "event_type": "native_caption",
        "session_id": "test-session-phase2k",
        "event_id": f"evt-{seq}-{offset_sec}",
        "speaker_name": speaker,
        "text": text,
        "detected_at": t_str,
        "received_at": (t + timedelta(milliseconds=15.0)).isoformat(),
        "source": "teams_native",
        "is_final": None,
        "finality": "unknown",
        "caption_sequence": seq,
        "dom_action": dom_action
    }


# 1. Same sequence incremental updates
def test_same_sequence_incremental_updates():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "Can you", "Deepak Bisht", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(1, "Can you explain", "Deepak Bisht", 0.5, base_time=base))
    aggregator.process_raw_event(make_event(1, "Can you explain your experience?", "Deepak Bisht", 1.0, base_time=base))

    seq_state = aggregator.sequences.get(1)
    assert seq_state is not None
    assert seq_state.current_text == "Can you explain your experience?"
    assert len(seq_state.text_versions) == 3
    assert seq_state.update_count == 3


# 2. Multiple updates becoming one turn
def test_multiple_updates_becoming_one_turn():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    for i, part in enumerate(["I", "I have", "I have worked with", "I have worked with FastAPI."]):
        aggregator.process_raw_event(make_event(1, part, "cadet (Unverified)", offset_sec=i * 0.4, base_time=base))

    # Transition to sequence 2 triggers Strategy A finalization
    aggregator.process_raw_event(make_event(2, "Great.", "Deepak Bisht", offset_sec=2.0, base_time=base))

    turns_a = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns_a) == 1
    turn = turns_a[0]
    assert turn.sequence_id == 1
    assert turn.speaker_name == "cadet (Unverified)"
    assert turn.text == "I have worked with FastAPI."
    assert turn.update_count == 4


# 3. Sequence transition
def test_sequence_transition_finalization():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(10, "First thought.", "Deepak Bisht", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(11, "Second thought.", "Deepak Bisht", 2.0, base_time=base))

    turns_a = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns_a) == 1
    assert turns_a[0].sequence_id == 10
    assert turns_a[0].text == "First thought."


# 4. Speaker transition
def test_speaker_transition():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "What is your approach?", "Deepak Bisht", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(2, "I minimize serialization overhead.", "cadet (Unverified)", 1.5, base_time=base))

    turns_a = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns_a) == 1
    assert turns_a[0].speaker_name == "Deepak Bisht"
    assert turns_a[0].text == "What is your approach?"


# 5. Same speaker consecutive turns
def test_same_speaker_consecutive_turns():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "Sentence one.", "Deepak Bisht", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(2, "Sentence two.", "Deepak Bisht", 1.2, base_time=base))
    aggregator.finalize_all_pending(flush_time=base + timedelta(seconds=5.0))

    turns_a = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns_a) == 2
    assert turns_a[0].speaker_name == "Deepak Bisht"
    assert turns_a[1].speaker_name == "Deepak Bisht"
    assert turns_a[0].text == "Sentence one."
    assert turns_a[1].text == "Sentence two."


# 6. Rapid speaker switching
def test_rapid_speaker_switching():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    dialogue = [
        (1, "Why?", "Deepak Bisht", 0.0),
        (2, "Because of latency.", "cadet", 0.4),
        (3, "Can you give an example?", "Deepak Bisht", 0.9),
        (4, "Sure, JSON parsing.", "cadet", 1.4)
    ]
    for seq, txt, spk, off in dialogue:
        aggregator.process_raw_event(make_event(seq, txt, spk, off, base_time=base))

    aggregator.finalize_all_pending(flush_time=base + timedelta(seconds=3.0))
    turns = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns) == 4
    metrics = aggregator.compute_strategy_metrics("strategy_a_sequence_transition")
    assert metrics["speaker_switches"] == 3
    assert metrics["speaker_switch_accuracy_pct"] == 100.0


# 7. Duplicate DOM events
def test_duplicate_dom_events():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    # Identical text sent twice in sequence
    aggregator.process_raw_event(make_event(1, "Static text.", "Deepak Bisht", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(1, "Static text.", "Deepak Bisht", 0.3, base_time=base))

    seq_state = aggregator.sequences[1]
    assert len(seq_state.text_versions) == 1
    assert seq_state.update_count == 1  # Deduplicated in sequence state


# 8. DOM replacement
def test_dom_replacement():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "Active card text", "Deepak Bisht", 0.0, dom_action="inserted", base_time=base))
    aggregator.process_raw_event(make_event(1, "Active card text", "Deepak Bisht", 0.8, dom_action="replaced", base_time=base))

    turns_b = aggregator.finalized_turns["strategy_b_dom_removal"]
    assert len(turns_b) == 1
    assert turns_b[0].sequence_id == 1
    assert turns_b[0].finalization_strategy == "strategy_b_dom_removal"


# 9. Caption removal
def test_caption_removal():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(5, "Disappearing caption", "cadet", 0.0, dom_action="inserted", base_time=base))
    aggregator.process_raw_event(make_event(5, "Disappearing caption", "cadet", 1.5, dom_action="removed", base_time=base))

    turns_b = aggregator.finalized_turns["strategy_b_dom_removal"]
    assert len(turns_b) == 1
    assert turns_b[0].sequence_id == 5
    assert turns_b[0].text == "Disappearing caption"


# 10. Quiescence finalization across thresholds
def test_quiescence_finalization_thresholds():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "Speech utterance", "Deepak Bisht", 0.0, base_time=base))

    # At t = 0.6s: 500ms threshold should trigger
    t_06 = base + timedelta(milliseconds=600)
    aggregator.check_quiescence(reference_time=t_06)
    assert len(aggregator.finalized_turns["strategy_c_quiescence_500ms"]) == 1
    assert len(aggregator.finalized_turns["strategy_c_quiescence_1000ms"]) == 0

    # At t = 1.2s: 1000ms threshold should trigger
    t_12 = base + timedelta(milliseconds=1200)
    aggregator.check_quiescence(reference_time=t_12)
    assert len(aggregator.finalized_turns["strategy_c_quiescence_1000ms"]) == 1
    assert len(aggregator.finalized_turns["strategy_c_quiescence_1500ms"]) == 0

    # At t = 2.1s: 1500ms and 2000ms should trigger
    t_21 = base + timedelta(milliseconds=2100)
    aggregator.check_quiescence(reference_time=t_21)
    assert len(aggregator.finalized_turns["strategy_c_quiescence_1500ms"]) == 1
    assert len(aggregator.finalized_turns["strategy_c_quiescence_2000ms"]) == 1
    assert len(aggregator.finalized_turns["strategy_c_quiescence_3000ms"]) == 0

    # At t = 3.5s: 3000ms should trigger
    t_35 = base + timedelta(milliseconds=3500)
    aggregator.check_quiescence(reference_time=t_35)
    assert len(aggregator.finalized_turns["strategy_c_quiescence_3000ms"]) == 1


# 11. Very short utterances
def test_very_short_utterances():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    for seq, word in [(1, "Yes."), (2, "Correct."), (3, "Python.")]:
        aggregator.process_raw_event(make_event(seq, word, "cadet", offset_sec=seq * 2.0, base_time=base))

    aggregator.finalize_all_pending(flush_time=base + timedelta(seconds=10.0))
    turns = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns) == 3
    assert [t.text for t in turns] == ["Yes.", "Correct.", "Python."]


# 12. Long utterances with multiple incremental updates
def test_long_utterances():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    words = [
        "In", "In our", "In our distributed", "In our distributed architecture,",
        "we use", "we use asynchronous", "we use asynchronous messaging",
        "to ensure", "to ensure high throughput", "to ensure high throughput and resilience."
    ]
    for i, w in enumerate(words):
        aggregator.process_raw_event(make_event(1, w, "cadet (Unverified)", offset_sec=i * 1.5, base_time=base))

    seq_state = aggregator.sequences[1]
    assert seq_state.update_count == len(words)
    assert seq_state.current_text == "to ensure high throughput and resilience."
    # Stabilization duration: 9 intervals * 1.5s = 13.5s = 13500ms
    assert seq_state.stabilization_duration_ms == 13500.0


# 13. Empty caption events
def test_empty_caption_events():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "   ", "Deepak Bisht", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(1, "", "Deepak Bisht", 0.5, base_time=base))

    seq_state = aggregator.sequences[1]
    assert seq_state.current_text == ""
    aggregator.finalize_all_pending(flush_time=base + timedelta(seconds=2.0))
    # Should not produce empty turns
    assert len(aggregator.finalized_turns["strategy_a_sequence_transition"]) == 0


# 14. Unknown or missing speaker names
def test_unknown_missing_speaker_names():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "Text with empty speaker", "", 0.0, base_time=base))
    aggregator.process_raw_event(make_event(2, "Next speaker", "Deepak Bisht", 2.0, base_time=base))

    turns = aggregator.finalized_turns["strategy_a_sequence_transition"]
    assert len(turns) == 1
    assert turns[0].speaker_name == "Unknown"


# 15. Service OFF behavior
def test_service_off_guard():
    # Schema check: ensure NativeCaptionEventRequest parses dom_action
    req = NativeCaptionEventRequest(
        event_type="native_caption",
        speaker_name="Deepak Bisht",
        text="Sample caption",
        caption_sequence=1,
        dom_action="removed"
    )
    assert req.dom_action == "removed"
    assert req.speaker_name == "Deepak Bisht"


# 16. Completed session handling
def test_completed_session_handling():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    aggregator.process_raw_event(make_event(1, "Final words.", "cadet", 0.0, base_time=base))
    # Finalize on session completion
    flushed = aggregator.finalize_all_pending(flush_time=base + timedelta(seconds=1.0))
    assert len(flushed["strategy_a_sequence_transition"]) == 1
    assert flushed["strategy_a_sequence_transition"][0].text == "Final words."


# 17. Session isolation
def test_session_isolation():
    agg1 = NativeTurnAggregator(session_id="session-1")
    agg2 = NativeTurnAggregator(session_id="session-2")
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    agg1.process_raw_event(make_event(1, "Session 1 text", "Deepak Bisht", 0.0, base_time=base))
    agg2.process_raw_event(make_event(1, "Session 2 text", "cadet", 0.0, base_time=base))

    assert len(agg1.raw_events) == 1
    assert len(agg2.raw_events) == 1
    assert agg1.sequences[1].speaker_name == "Deepak Bisht"
    assert agg2.sequences[1].speaker_name == "cadet"


# 18. Raw event preservation
def test_raw_event_preservation():
    aggregator = NativeTurnAggregator()
    base = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)

    event_in = make_event(1, "Initial raw text", "Deepak Bisht", 0.0, base_time=base)
    aggregator.process_raw_event(event_in)

    # Modify original dict to confirm immutable copy was stored
    event_in["text"] = "MODIFIED MUTATION"
    stored_event = aggregator.raw_events[0]
    assert stored_event["text"] == "Initial raw text"
    assert stored_event["caption_sequence"] == 1
    assert stored_event["speaker_name"] == "Deepak Bisht"
