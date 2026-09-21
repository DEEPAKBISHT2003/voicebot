"""
Phase 2N — Unit & Integration Tests: Complete Deepgram Removal & Teams Native Captions Production Pipeline
File: tests/unit/test_phase2n_native_production.py

Verifies:
1. Native caption event is accepted.
2. Incremental Native Caption updates do not create duplicate transcript turns.
3. Same-speaker sequences are aggregated.
4. Speaker switch creates separate logical turns.
5. Teams speaker_name is preserved exactly (no role mapping/renaming).
6. Native logical turn reaches the production transcript path.
7. Production transcript is persisted through the existing repository.
8. Deepgram STT is not initialized.
9. Deepgram connection is not created.
10. Deepgram transcript callbacks are not used.
11. SERVICE OFF rejects late Native Caption events.
12. Completed sessions do not accept new Native transcript events.
13. Existing relevant Copilot functionality and Interview Service safety (0 diffs).
"""

import os
import json
import asyncio
import subprocess
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient
from fastapi import FastAPI

from services.copilot.src.websocket.handler import router as copilot_ws_router
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.pipeline.builder import CopilotPipelineBuilder
from services.copilot.src.pipeline.native_turn_finalizer import NativeTurnAggregator, FinalizedTurn
from services.copilot.src.pipeline.native_turn_aligner import NativeLogicalTurnAggregator, LogicalTurn
from services.copilot.src.api.deps import get_copilot_sessions_ws, get_copilot_repo_ws
from services.copilot.src.core.config import Settings


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.save_session = AsyncMock()
    repo.load_session = AsyncMock(return_value={
        "transcript": [],
        "jd": "Python Engineer",
        "resume": "FastAPI developer",
        "service_off": False,
        "final_report": None
    })
    return repo


@pytest.fixture
def test_app(mock_repo):
    app = FastAPI()
    app.include_router(copilot_ws_router)
    active_sessions = {}

    app.dependency_overrides[get_copilot_sessions_ws] = lambda: active_sessions
    app.dependency_overrides[get_copilot_repo_ws] = lambda: mock_repo
    return app, active_sessions, mock_repo


# ==============================================================================
# Test 1: Native caption event is accepted
# ==============================================================================
def test_native_caption_event_accepted(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "test-session-accepted-001"

    with client.websocket_connect(f"/api/ws/copilot/{session_id}?mode=native_captions") as ws:
        payload = {
            "event_type": "native_caption",
            "session_id": session_id,
            "event_id": "evt-001",
            "speaker_name": "Deepak Bisht",
            "text": "Hello, can you hear me?",
            "caption_sequence": 1,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "dom_action": "added"
        }
        ws.send_text(json.dumps(payload))

    assert session_id in active_sessions
    sess = active_sessions[session_id]
    assert len(sess.get("native_captions", [])) == 1
    rec = sess["native_captions"][0]
    assert rec["speaker_name"] == "Deepak Bisht"
    assert rec["text"] == "Hello, can you hear me?"
    assert rec["source"] == "teams_native"


# ==============================================================================
# Test 2: Incremental updates do not create duplicate transcript turns
# ==============================================================================
def test_incremental_native_updates_do_not_create_duplicate_turns():
    finalizer = NativeTurnAggregator(session_id="test-incr")
    logical = NativeLogicalTurnAggregator(session_id="test-incr")

    now = datetime.now(timezone.utc)
    # Sequence 1 incremental updates
    updates = [
        "I have",
        "I have worked",
        "I have worked with",
        "I have worked with Python and FastAPI."
    ]

    all_emitted_turns = []
    for idx, text in enumerate(updates):
        evt = {
            "caption_sequence": 1,
            "speaker_name": "Deepak Bisht",
            "text": text,
            "detected_at": (now + timedelta(milliseconds=200 * idx)).isoformat(),
            "received_at": (now + timedelta(milliseconds=210 * idx)).isoformat(),
            "dom_action": "updated"
        }
        newly_fin = finalizer.process_raw_event(evt)
        # Check Strategy C or A
        for fseq in newly_fin.get("strategy_c_quiescence_1500ms", []):
            all_emitted_turns.extend(logical.process_finalized_sequence(fseq))

    # During incremental updates, no turn should have been prematurely emitted
    assert len(all_emitted_turns) == 0

    # Fast-forward past quiescence threshold
    quiescence_fin = finalizer.check_quiescence(reference_time=now + timedelta(milliseconds=3000))
    for fseq in quiescence_fin.get("strategy_c_quiescence_1500ms", []):
        all_emitted_turns.extend(logical.process_finalized_sequence(fseq))

    # Flush logical turn
    all_emitted_turns.extend(logical.flush(current_time=now + timedelta(milliseconds=3500)))

    # Only 1 logical turn should be emitted for the entire sequence
    assert len(all_emitted_turns) == 1
    assert all_emitted_turns[0].speaker_name == "Deepak Bisht"
    assert all_emitted_turns[0].text == "I have worked with Python and FastAPI."


# ==============================================================================
# Test 3: Same-speaker sequences are aggregated
# ==============================================================================
def test_same_speaker_sequences_aggregated():
    logical = NativeLogicalTurnAggregator(session_id="test-same-spk", inactivity_threshold_ms=3000.0)
    t0 = datetime.now(timezone.utc)

    # Sequence 1 from Deepak
    fseq1 = FinalizedTurn(
        turn_id=1,
        sequence_id=101,
        speaker_name="Deepak Bisht",
        text="I have built microservices with FastAPI.",
        text_versions=["..."],
        first_seen_at=t0,
        last_update_at=t0 + timedelta(seconds=2),
        finalized_at=t0 + timedelta(seconds=3.5),
        finalization_strategy="strategy_c_quiescence_1500ms",
        update_count=3,
        source="teams_native"
    )

    # Sequence 2 from Deepak after a 1-second pause
    t_seq2 = t0 + timedelta(seconds=3)
    fseq2 = FinalizedTurn(
        turn_id=2,
        sequence_id=102,
        speaker_name="Deepak Bisht",
        text="They handled real-time WebSocket traffic efficiently.",
        text_versions=["..."],
        first_seen_at=t_seq2,
        last_update_at=t_seq2 + timedelta(seconds=2),
        finalized_at=t_seq2 + timedelta(seconds=3.5),
        finalization_strategy="strategy_c_quiescence_1500ms",
        update_count=2,
        source="teams_native"
    )

    completed1 = logical.process_finalized_sequence(fseq1)
    assert len(completed1) == 0  # Active turn buffered

    completed2 = logical.process_finalized_sequence(fseq2)
    assert len(completed2) == 0  # Stitched into active turn

    flushed = logical.flush(current_time=t_seq2 + timedelta(seconds=6))
    assert len(flushed) == 1
    assert flushed[0].speaker_name == "Deepak Bisht"
    assert flushed[0].sequence_ids == [101, 102]
    assert "FastAPI" in flushed[0].text
    assert "WebSocket" in flushed[0].text


# ==============================================================================
# Test 4: Speaker switch creates separate logical turns
# ==============================================================================
def test_speaker_switch_creates_separate_logical_turns():
    logical = NativeLogicalTurnAggregator(session_id="test-switch")
    t0 = datetime.now(timezone.utc)

    # Speaker 1: Interviewer asks question
    fseq1 = FinalizedTurn(
        turn_id=1,
        sequence_id=1,
        speaker_name="kaua (Unverified)",
        text="Can you explain your experience with async programming?",
        text_versions=["..."],
        first_seen_at=t0,
        last_update_at=t0 + timedelta(seconds=2),
        finalized_at=t0 + timedelta(seconds=3.5),
        finalization_strategy="strategy_c_quiescence_1500ms",
        update_count=2,
        source="teams_native"
    )

    # Speaker 2: Candidate answers
    t1 = t0 + timedelta(seconds=4)
    fseq2 = FinalizedTurn(
        turn_id=2,
        sequence_id=2,
        speaker_name="Deepak Bisht",
        text="Yes, I have used asyncio for high concurrency.",
        text_versions=["..."],
        first_seen_at=t1,
        last_update_at=t1 + timedelta(seconds=3),
        finalized_at=t1 + timedelta(seconds=4.5),
        finalization_strategy="strategy_c_quiescence_1500ms",
        update_count=2,
        source="teams_native"
    )

    logical.process_finalized_sequence(fseq1)
    # When speaker 2 arrives, speaker 1's turn completes immediately!
    turns_from_switch = logical.process_finalized_sequence(fseq2)
    assert len(turns_from_switch) == 1
    assert turns_from_switch[0].speaker_name == "kaua (Unverified)"
    assert turns_from_switch[0].boundary_reason == "speaker_switch"

    # When session ends, speaker 2's turn closes
    final_turns = logical.flush()
    assert len(final_turns) == 1
    assert final_turns[0].speaker_name == "Deepak Bisht"


# ==============================================================================
# Test 5: Teams speaker_name is preserved exactly
# ==============================================================================
@pytest.mark.asyncio
async def test_teams_speaker_name_preserved_exactly(mock_repo):
    engine = CopilotSessionEngine(session_id="test-spk-exact", repo=mock_repo)

    # Add message with raw Teams speaker name
    msg1 = await engine.add_message(speaker="Deepak Bisht", text="Hello world", source="teams_native")
    msg2 = await engine.add_message(speaker="kaua (Unverified)", text="Welcome to the interview", source="teams_native")

    assert msg1["speaker"] == "Deepak Bisht"
    assert msg1["source"] == "teams_native"
    assert msg2["speaker"] == "kaua (Unverified)"
    assert msg2["source"] == "teams_native"

    # Verify not renamed to Candidate or Interviewer
    transcript = engine.get_transcript()
    assert transcript[0]["speaker"] == "Deepak Bisht"
    assert transcript[1]["speaker"] == "kaua (Unverified)"
    assert transcript[0]["speaker"] not in ("Candidate", "Interviewer")
    assert transcript[1]["speaker"] not in ("Candidate", "Interviewer")


# ==============================================================================
# Test 6: Native logical turn reaches the production transcript path
# ==============================================================================
@pytest.mark.asyncio
async def test_native_logical_turn_reaches_production_transcript(mock_repo):
    engine = CopilotSessionEngine(session_id="test-prod-path", repo=mock_repo)

    logical_turn = LogicalTurn(
        logical_turn_id=1,
        speaker_name="Deepak Bisht",
        text="Production transcript from Teams Native Captions.",
        sequence_ids=[1, 2],
        first_seen_at=datetime.now(timezone.utc),
        last_update_at=datetime.now(timezone.utc),
        finalized_at=datetime.now(timezone.utc),
        boundary_reason="speaker_switch",
        source="teams_native"
    )

    last_msg = await engine.add_message(
        speaker=logical_turn.speaker_name,
        text=logical_turn.text,
        source="teams_native",
        allow_merge=False
    )

    assert last_msg["text"] == "Production transcript from Teams Native Captions."
    assert last_msg["speaker"] == "Deepak Bisht"
    assert last_msg["source"] == "teams_native"

    transcript = engine.get_transcript()
    assert len(transcript) == 1
    assert transcript[0] == last_msg


# ==============================================================================
# Test 7: Production transcript is persisted through the existing repository
# ==============================================================================
@pytest.mark.asyncio
async def test_production_transcript_persisted_to_repository(mock_repo):
    engine = CopilotSessionEngine(session_id="test-persist", repo=mock_repo)

    await engine.add_message(
        speaker="Deepak Bisht",
        text="Testing database persistence.",
        source="teams_native"
    )

    # Verify save_session was called on repository
    mock_repo.save_session.assert_called()
    call_args = mock_repo.save_session.call_args[0]
    assert call_args[0] == "test-persist"
    assert "transcript" in call_args[1]
    saved_transcript = call_args[1]["transcript"]
    assert len(saved_transcript) == 1
    assert saved_transcript[0]["speaker"] == "Deepak Bisht"
    assert saved_transcript[0]["text"] == "Testing database persistence."


# ==============================================================================
# Test 8: Deepgram STT is not initialized
# ==============================================================================
def test_deepgram_stt_not_initialized():
    builder = CopilotPipelineBuilder()
    # Builder should not have or require deepgram_api_key attribute
    assert not hasattr(builder, "deepgram_api_key")

    # Verify Deepgram is not in Copilot Settings
    assert not hasattr(Settings, "DEEPGRAM_API_KEY")


# ==============================================================================
# Test 9: Deepgram connection is not created in audio observer pipeline
# ==============================================================================
def test_deepgram_connection_not_created():
    builder = CopilotPipelineBuilder()
    mock_ws = MagicMock()

    # build_observer_pipeline should construct audio recording pipeline without STT
    pipeline_res = builder.build_observer_pipeline(websocket=mock_ws, session_id="test-audio-no-dg")
    assert pipeline_res is not None
    pipeline, worker, audio_buffer = pipeline_res

    # Pipeline processors should only be Transport input and AudioBufferProcessor
    processors = getattr(pipeline, "_processors", [])
    proc_names = [p.__class__.__name__ for p in processors]
    assert "DeepgramSTTService" not in proc_names
    assert "TranscriptAccumulator" not in proc_names
    assert "AudioBufferProcessor" in proc_names


# ==============================================================================
# Test 10: Deepgram transcript callbacks are not used
# ==============================================================================
def test_deepgram_transcript_callbacks_not_used():
    builder = CopilotPipelineBuilder()
    # build_observer_pipeline signature does NOT accept transcript_callback
    import inspect
    sig = inspect.signature(builder.build_observer_pipeline)
    assert "transcript_callback" not in sig.parameters


# ==============================================================================
# Test 11: SERVICE OFF rejects late Native Caption events
# ==============================================================================
def test_service_off_rejects_late_native_captions(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "test-session-off-001"

    # Pre-seed session as Service Off
    mock_engine = MagicMock()
    mock_engine.get_transcript.return_value = []
    mock_engine.get_intelligence.return_value = {}
    mock_engine.get_assistance.return_value = {}

    active_sessions[session_id] = {
        "engine": mock_engine,
        "service_off": True,
        "is_active": False,
        "status": "Service Off",
        "final_report": None,
        "transcript": []
    }

    with client.websocket_connect(f"/api/ws/copilot/{session_id}?mode=native_captions") as ws:
        # Connection should be rejected/closed with session state frame
        data = ws.receive_json()
        assert data.get("service_off") is True
        assert data.get("is_active") is False

        # Attempting to send a late caption should fail or be rejected
        try:
            ws.send_text(json.dumps({
                "event_type": "native_caption",
                "session_id": session_id,
                "text": "Late message after Service Off",
                "speaker_name": "Deepak Bisht"
            }))
            # Subsequent receive should be close or disconnect
            ws.receive()
        except Exception:
            pass

    # Confirm no messages added
    mock_engine.add_message.assert_not_called()


# ==============================================================================
# Test 12: Completed sessions do not accept new Native transcript events
# ==============================================================================
def test_completed_session_rejects_native_events(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "test-session-completed-001"

    mock_engine = MagicMock()
    mock_engine.get_transcript.return_value = [{"speaker": "Deepak Bisht", "text": "Prior"}]
    mock_engine.get_intelligence.return_value = {}
    mock_engine.get_assistance.return_value = {}

    # Pre-seed session with final_report
    active_sessions[session_id] = {
        "engine": mock_engine,
        "service_off": False,
        "is_active": False,
        "status": "Session completed.",
        "final_report": {"summary": "Completed interview report"},
        "transcript": [{"speaker": "Deepak Bisht", "text": "Prior"}]
    }

    with client.websocket_connect(f"/api/ws/copilot/{session_id}?mode=native_captions") as ws:
        data = ws.receive_json()
        assert data.get("is_active") is False
        assert data.get("status") == "Session completed."

    # Engine must not have received any new messages
    mock_engine.add_message.assert_not_called()


# ==============================================================================
# Test 13: Strict Isolation — services/interview/** has ZERO changes
# ==============================================================================
def test_interview_service_remains_strictly_untouched():
    """Mandatory verification: services/interview is completely separate and not imported or modified."""
    import sys
    import shutil
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


# ==============================================================================
# Test 14: Single-Speaker Testing Flow (No second speaker required)
# ==============================================================================
def test_single_speaker_production_flow():
    """
    Verifies that when testing with only ONE speaker:
    - Sequence is ingested and finalized.
    - Same-speaker utterances are stitched across natural pauses.
    - Conversational turn boundary is cleanly finalized via inactivity timeout.
    - Speaker name is preserved.
    """
    logical = NativeLogicalTurnAggregator(session_id="test-single-speaker", inactivity_threshold_ms=3000.0)
    t0 = datetime.now(timezone.utc)

    # Single speaker utterance
    fseq = FinalizedTurn(
        turn_id=1,
        sequence_id=101,
        speaker_name="Deepak Bisht",
        text="This is a test with only one speaker in the meeting.",
        text_versions=["This is a test", "This is a test with only one speaker in the meeting."],
        first_seen_at=t0,
        last_update_at=t0 + timedelta(seconds=2),
        finalized_at=t0 + timedelta(seconds=3.5),
        finalization_strategy="strategy_c_quiescence_1500ms",
        update_count=2,
        source="teams_native"
    )

    # Ingest sequence into logical aggregator
    turns = logical.process_finalized_sequence(fseq)
    # Turn is pending since there is only 1 speaker and silence has not yet exceeded 3000ms
    assert len(turns) == 0

    # 3.5 seconds later: single speaker stopped speaking -> inactivity timeout closes turn
    t_later = t0 + timedelta(seconds=6)
    timeout_turns = logical.check_inactivity(current_time=t_later)
    assert len(timeout_turns) == 1
    assert timeout_turns[0].speaker_name == "Deepak Bisht"
    assert timeout_turns[0].text == "This is a test with only one speaker in the meeting."
    assert timeout_turns[0].source in ("teams_native", "teams_native_logical")
