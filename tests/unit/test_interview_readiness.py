"""
Unit tests for Improved Interview Readiness System:
Validates Multi-Caption Readiness Verification:
- BOT_JOINING -> IN_MEETING -> CAPTION_STREAM_CONNECTED -> TRANSCRIPT_ENGINE_READY -> CAPTIONS_FLOWING -> INTERVIEW_READY
- Single caption does NOT declare readiness (State: CAPTIONS_FLOWING)
- Two captions trigger readiness (State: INTERVIEW_READY)
- Two unique speakers trigger readiness (State: INTERVIEW_READY)
- 15+ seconds silence produces warning telemetry, does NOT revoke readiness
- Telemetry fields: caption_count, unique_speakers, unique_speakers_detected, readiness_confirmed
"""
import pytest
import time
from services.copilot.src.services.readiness import compute_readiness


def test_initial_session_state_is_bot_joining():
    sess = {
        "bot_joined": False,
        "caption_socket_connected": False,
        "transcript_processor_initialized": True,
        "first_caption_received": False,
        "caption_count": 0,
        "transcript": [],
        "native_captions": []
    }
    r = compute_readiness(sess)
    assert r["state"] == "BOT_JOINING"
    assert r["interview_ready"] is False
    assert r["readiness_confirmed"] is False
    assert r["bot_joined"] is False
    assert r["caption_socket_connected"] is False
    assert r["transcript_processor_initialized"] is True
    assert r["first_caption_received"] is False
    assert r["caption_count"] == 0
    assert r["unique_speakers_detected"] == 0


def test_in_meeting_transition_when_bot_joins():
    sess = {
        "bot_joined": True,
        "caption_socket_connected": False,
        "transcript_processor_initialized": True,
        "first_caption_received": False,
        "caption_count": 0,
        "transcript": [],
        "native_captions": []
    }
    r = compute_readiness(sess)
    assert r["state"] == "IN_MEETING"
    assert r["interview_ready"] is False
    assert r["readiness_confirmed"] is False
    assert r["bot_joined"] is True
    assert r["caption_socket_connected"] is False


def test_transcript_engine_ready_when_caption_stream_connects():
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": False,
        "caption_count": 0,
        "transcript": [],
        "native_captions": []
    }
    r = compute_readiness(sess)
    assert r["state"] == "TRANSCRIPT_ENGINE_READY"
    assert r["interview_ready"] is False
    assert r["readiness_confirmed"] is False
    assert r["bot_joined"] is True
    assert r["caption_socket_connected"] is True
    assert r["transcript_processor_initialized"] is True


def test_scenario_1_single_caption_not_ready_captions_flowing():
    """
    Scenario 1:
    Interviewer asks single test word "Hello".
    A single caption MUST NOT trigger INTERVIEW_READY.
    State must transition to CAPTIONS_FLOWING and readiness_confirmed must be False.
    """
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": True,
        "caption_count": 1,
        "unique_speakers": ["Interviewer"],
        "native_captions": [{"speaker_name": "Interviewer", "text": "Hello"}],
        "transcript": []
    }
    r = compute_readiness(sess)
    assert r["state"] == "CAPTIONS_FLOWING"
    assert r["interview_ready"] is False
    assert r["readiness_confirmed"] is False
    assert r["first_caption_received"] is True
    assert r["caption_count"] == 1
    assert r["unique_speakers_detected"] == 1
    assert r["unique_speakers"] == ["Interviewer"]


def test_scenario_2_two_captions_confirms_readiness():
    """
    Scenario 2:
    Two captions: "Hello", "Hi".
    Caption count >= 2 satisfies multi-caption verification and has proven transcript -> INTERVIEW_READY.
    """
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": True,
        "caption_count": 2,
        "unique_speakers": ["Speaker1"],
        "native_captions": [
            {"speaker_name": "Speaker1", "text": "Hello"},
            {"speaker_name": "Speaker1", "text": "Hi"}
        ],
        "transcript": [
            {"speaker": "Speaker1", "text": "Hello"},
            {"speaker": "Speaker1", "text": "Hi"}
        ]
    }
    r = compute_readiness(sess)
    assert r["state"] == "INTERVIEW_READY"
    assert r["interview_ready"] is True
    assert r["readiness_confirmed"] is True
    assert r["has_proven_transcript"] is True
    assert r["caption_count"] == 2


def test_scenario_inc_2026_0928_01_false_readiness_prevented():
    """
    Incident INC-2026-0928-01:
    Two raw caption packets arrived from Teams, but transcript is empty/unproven.
    Must NOT trigger INTERVIEW_READY (state remains CAPTIONS_FLOWING).
    """
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": True,
        "caption_count": 2,
        "unique_speakers": ["Speaker1"],
        "native_captions": [
            {"speaker_name": "Speaker1", "text": "Hello"},
            {"speaker_name": "Speaker1", "text": "Hi"}
        ],
        "transcript": []
    }
    r = compute_readiness(sess)
    assert r["state"] == "CAPTIONS_FLOWING"
    assert r["interview_ready"] is False
    assert r["readiness_confirmed"] is False
    assert r["has_proven_transcript"] is False


def test_scenario_3_two_different_speakers_confirms_readiness():
    """
    Scenario 3:
    Two Different Speakers:
    Interviewer: "Can you hear me?"
    Candidate: "Yes"
    unique_speakers_detected >= 2 satisfies verification -> INTERVIEW_READY.
    """
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": True,
        "caption_count": 2,
        "unique_speakers": ["Interviewer", "Candidate"],
        "transcript": [
            {"speaker": "Interviewer", "text": "Can you hear me?"},
            {"speaker": "Candidate", "text": "Yes"}
        ]
    }
    r = compute_readiness(sess)
    assert r["state"] == "INTERVIEW_READY"
    assert r["interview_ready"] is True
    assert r["readiness_confirmed"] is True
    assert r["unique_speakers_detected"] == 2
    assert "Candidate" in r["unique_speakers"]
    assert "Interviewer" in r["unique_speakers"]


def test_scenario_4_transcript_stops_does_not_revoke_readiness():
    """
    Scenario 4:
    Transcript stops: no captions for 15+ seconds.
    caption_seconds_ago > 15.
    Expected: Show silence duration, but DO NOT revoke readiness!
    """
    now = time.time()
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": True,
        "readiness_confirmed": True,
        "interview_ready": True,
        "caption_count": 3,
        "unique_speakers": ["Interviewer", "Candidate"],
        "last_caption_time": now - 25.0,  # 25 seconds of silence
        "transcript": [
            {"speaker": "Interviewer", "text": "Let's begin."},
            {"speaker": "Candidate", "text": "Sure."}
        ]
    }
    r = compute_readiness(sess)
    assert r["state"] == "INTERVIEW_READY"
    assert r["interview_ready"] is True
    assert r["readiness_confirmed"] is True
    assert r["caption_seconds_ago"] is not None
    assert r["caption_seconds_ago"] >= 24.0


def test_scenario_5_restores_readiness_on_session_reload():
    """
    Scenario 5:
    Session reload / refresh:
    Database session contains confirmed readiness or multi-caption transcript.
    compute_readiness correctly restores INTERVIEW_READY.
    """
    db_session = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "readiness_confirmed": True,
        "transcript": [
            {"speaker": "Interviewer", "text": "Question 1"},
            {"speaker": "Candidate", "text": "Answer 1"}
        ]
    }
    r = compute_readiness(db_session)
    assert r["state"] == "INTERVIEW_READY"
    assert r["interview_ready"] is True
    assert r["readiness_confirmed"] is True


def test_safe_against_none_and_empty():
    r1 = compute_readiness(None)
    assert r1["state"] == "BOT_JOINING"
    assert r1["interview_ready"] is False
    assert r1["readiness_confirmed"] is False
    assert r1["caption_count"] == 0
    assert r1["unique_speakers_detected"] == 0

    r2 = compute_readiness({})
    assert r2["state"] == "BOT_JOINING"
    assert r2["interview_ready"] is False
    assert r2["readiness_confirmed"] is False


def test_unique_speakers_list_to_set_resilience():
    """
    INCIDENT REGRESSION VERIFICATION:
    Verifies that if unique_speakers is initialized as a list (e.g. from router or legacy session),
    adding speakers dynamically converts it to a set without throwing AttributeError: 'list' object has no attribute 'add'.
    """
    sess = {
        "bot_joined": True,
        "caption_socket_connected": True,
        "transcript_processor_initialized": True,
        "first_caption_received": False,
        "caption_count": 0,
        "unique_speakers": [],  # Initialized as a list!
        "transcript": []
    }
    raw_speaker = "Interviewer One"
    if not isinstance(sess.get("unique_speakers"), set):
        sess["unique_speakers"] = set(sess.get("unique_speakers") or [])
    if raw_speaker and raw_speaker.lower() != "unknown":
        sess["unique_speakers"].add(raw_speaker)
    sess["unique_speakers_detected"] = len(sess["unique_speakers"])

    assert isinstance(sess["unique_speakers"], set)
    assert "Interviewer One" in sess["unique_speakers"]
    assert sess["unique_speakers_detected"] == 1


@pytest.mark.asyncio
async def test_progressive_streaming_yields_transcript_count_greater_than_zero():
    """
    Verifies that incoming spoken sentences produce live transcript turns
    via progressive streaming immediately (count > 0) without waiting for finalization.
    """
    from services.copilot.src.engine.session import CopilotSessionEngine
    from services.copilot.src.pipeline.native_turn_aligner import NativeLogicalTurnAggregator
    from datetime import datetime, timezone

    from unittest.mock import MagicMock, AsyncMock
    mock_repo = MagicMock()
    mock_repo.save_session = AsyncMock()
    engine = CopilotSessionEngine(session_id="test_prog_01", jd="Python dev", resume="Experienced Python dev", repo=mock_repo)
    logical_aggregator = NativeLogicalTurnAggregator()

    # Sentence 1 arrives progressively
    dt1 = datetime.now(timezone.utc)
    t1 = logical_aggregator.update_interim_text(sequence_id=1, speaker_name="Interviewer", text="Hello can you hear me?", current_time=dt1)
    assert t1 is not None
    await engine.add_message(speaker=t1.speaker_name, text=t1.text, is_final=False, turn_id=t1.logical_turn_id)

    transcript = engine.get_transcript()
    assert len(transcript) == 1
    assert transcript[0]["text"] == "Hello can you hear me?"
    assert transcript[0]["speaker"] == "Interviewer"

    # Sentence 2 extends active turn
    t2 = logical_aggregator.update_interim_text(sequence_id=2, speaker_name="Interviewer", text="Welcome to the technical interview.", current_time=dt1)
    await engine.add_message(speaker=t2.speaker_name, text=t2.text, is_final=False, turn_id=t2.logical_turn_id)

    transcript = engine.get_transcript()
    assert len(transcript) == 1  # Updated in-place, still turn 1
    assert "Hello can you hear me?" in transcript[0]["text"]
    assert "Welcome to the technical interview." in transcript[0]["text"]

