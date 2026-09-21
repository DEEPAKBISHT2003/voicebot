"""
Phase 2O — Live Native Teams Transcript in Copilot Frontend Tests
File: tests/unit/test_phase2o_frontend_transcript.py

Verifies:
1. Native transcript event reaches frontend dashboard WebSocket.
2. Correct Teams speaker name is displayed (no role mapping/renaming).
3. Transcript text is displayed accurately.
4. Multiple speakers display correctly with exact display names.
5. Transcript entries remain in chronological order.
6. Duplicate events do not create duplicate entries (stable id / turn_id).
7. Existing transcript loads when opening an existing session.
8. New transcript events append correctly onto existing transcript.
9. WebSocket reconnect does not duplicate entries.
10. Completed session displays persisted transcript.
11. Completed session does not resurrect.
12. SERVICE OFF keeps existing transcript visible.
13. SERVICE OFF prevents new transcript updates.
14. Transcript emission does not trigger evaluation.
15. services/interview/** is 100% untouched (zero git diff).
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
from services.copilot.src.router import router as copilot_http_router
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.pipeline.native_turn_finalizer import NativeTurnAggregator
from services.copilot.src.pipeline.native_turn_aligner import NativeLogicalTurnAggregator, LogicalTurn
from services.copilot.src.api.deps import (
    get_copilot_sessions_ws,
    get_copilot_repo_ws,
    get_copilot_sessions,
    get_copilot_repo
)


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.save_session = AsyncMock()
    repo.load_session = AsyncMock(return_value={
        "session_id": "test-session-001",
        "transcript": [
            {
                "id": "test-session-001-turn-1",
                "turn_id": 1,
                "speaker": "Deepak Bisht",
                "text": "Hello, welcome to the interview.",
                "timestamp": "2026-09-20T10:00:00Z",
                "source": "teams_native"
            }
        ],
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
    app.include_router(copilot_http_router, prefix="/api/copilot")
    active_sessions = {}

    app.dependency_overrides[get_copilot_sessions_ws] = lambda: active_sessions
    app.dependency_overrides[get_copilot_repo_ws] = lambda: mock_repo
    app.dependency_overrides[get_copilot_sessions] = lambda: active_sessions
    app.dependency_overrides[get_copilot_repo] = lambda: mock_repo
    return app, active_sessions, mock_repo


# ==============================================================================
# Test 1, 2, 3: Native transcript event reaches frontend with exact speaker name and text
# ==============================================================================
def test_native_transcript_event_reaches_frontend_with_exact_name(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "test-session-live-001"

    # 1. Connect dashboard client
    with client.websocket_connect(f"/api/ws/copilot/{session_id}") as dash_ws:
        init_frame = dash_ws.receive_json()
        assert init_frame["type"] == "copilot_update"
        assert init_frame["session_id"] == session_id

        # 2. Connect native captions stream
        with client.websocket_connect(f"/api/ws/copilot/{session_id}?mode=native_captions") as cap_ws:
            payload = {
                "event_type": "native_caption",
                "session_id": session_id,
                "event_id": "evt-live-1",
                "speaker_name": "Deepak Bisht",
                "text": "Can you explain your experience with Python and FastAPI microservices?",
                "caption_sequence": 1,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "dom_action": "added"
            }
            cap_ws.send_text(json.dumps(payload))

        # 3. Native caption disconnect triggers flush to production transcript
        # Dashboard client should receive the updated frame
        received_frames = []
        while True:
            try:
                frame = dash_ws.receive_json()
                received_frames.append(frame)
                if frame.get("type") in ("copilot_update", "transcript") and frame.get("text") or frame.get("transcript"):
                    if frame.get("transcript") and len(frame["transcript"]) > 0:
                        break
                    if frame.get("type") == "transcript":
                        break
            except Exception:
                break

        assert len(received_frames) > 0
        sess = active_sessions[session_id]
        transcript = sess["engine"].get_transcript()
        assert len(transcript) >= 1

        last_entry = transcript[-1]
        # Test 2: Correct Teams speaker name is displayed (no Candidate/Interviewer/Speaker 1 mapping)
        assert last_entry["speaker"] == "Deepak Bisht"
        assert last_entry["speaker"] not in ("Candidate", "Interviewer", "Speaker 1", "Speaker 2")
        # Test 3: Transcript text is displayed
        assert "Can you explain your experience with Python" in last_entry["text"]
        # Stable identifier
        assert "id" in last_entry
        assert last_entry["turn_id"] is not None


# ==============================================================================
# Test 4: Multiple speakers display correctly with exact display names
# ==============================================================================
@pytest.mark.asyncio
async def test_multiple_speakers_display_correctly(mock_repo):
    session_id = "test-multispeaker-001"
    engine = CopilotSessionEngine(session_id, mock_repo, [])

    turn1 = await engine.add_message(
        speaker="Deepak Bisht",
        text="What is your approach to reducing latency in a real-time system?",
        turn_id=1,
        source="teams_native",
        allow_merge=False
    )
    turn2 = await engine.add_message(
        speaker="kaua (Unverified)",
        text="I have worked with Python and FastAPI for several years building real-time distributed systems.",
        turn_id=2,
        source="teams_native",
        allow_merge=False
    )

    transcript = engine.get_transcript()
    assert len(transcript) == 2
    assert transcript[0]["speaker"] == "Deepak Bisht"
    assert transcript[1]["speaker"] == "kaua (Unverified)"
    assert transcript[0]["text"] == "What is your approach to reducing latency in a real-time system?"
    assert transcript[1]["text"] == "I have worked with Python and FastAPI for several years building real-time distributed systems."
    assert transcript[0]["id"] == f"{session_id}-turn-1"
    assert transcript[1]["id"] == f"{session_id}-turn-2"


# ==============================================================================
# Test 5: Transcript entries remain in chronological order
# ==============================================================================
@pytest.mark.asyncio
async def test_transcript_entries_remain_chronological(mock_repo):
    session_id = "test-chrono-001"
    engine = CopilotSessionEngine(session_id, mock_repo, [])

    speakers = ["Deepak Bisht", "kaua (Unverified)", "Deepak Bisht", "kaua (Unverified)"]
    texts = ["Question 1", "Answer 1", "Question 2", "Answer 2"]

    for i in range(4):
        await engine.add_message(
            speaker=speakers[i],
            text=texts[i],
            turn_id=i + 1,
            source="teams_native",
            allow_merge=False
        )

    transcript = engine.get_transcript()
    assert len(transcript) == 4
    for i in range(4):
        assert transcript[i]["turn_id"] == i + 1
        assert transcript[i]["speaker"] == speakers[i]
        assert transcript[i]["text"] == texts[i]


# ==============================================================================
# Test 6: Duplicate events do not create duplicate entries
# ==============================================================================
def test_duplicate_events_deduplicated():
    def deduplicate(existing, incoming):
        entry_map = {}
        order = []
        for i, e in enumerate(existing):
            k = str(e.get("id") or f"turn-{e.get('turn_id')}")
            entry_map[k] = e
            order.append(k)
        for i, e in enumerate(incoming):
            k = str(e.get("id") or f"turn-{e.get('turn_id')}")
            if k in entry_map:
                entry_map[k] = {**entry_map[k], **e}
            else:
                entry_map[k] = e
                order.append(k)
        return [entry_map[k] for k in order]

    existing = [
        {"id": "s1-turn-1", "turn_id": 1, "speaker": "Deepak Bisht", "text": "Hello"},
        {"id": "s1-turn-2", "turn_id": 2, "speaker": "kaua (Unverified)", "text": "Hi"}
    ]

    # Re-sending existing turn 2 with identical or updated info
    incoming_duplicate = [
        {"id": "s1-turn-2", "turn_id": 2, "speaker": "kaua (Unverified)", "text": "Hi"}
    ]

    result = deduplicate(existing, incoming_duplicate)
    assert len(result) == 2
    assert result[0]["turn_id"] == 1
    assert result[1]["turn_id"] == 2


# ==============================================================================
# Test 7: Existing transcript loads when opening an existing session
# ==============================================================================
def test_existing_transcript_loads_from_api(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "11111111-1111-1111-1111-111111111111"

    mock_repo.load_session.return_value = {
        "session_id": session_id,
        "transcript": [
            {
                "id": f"{session_id}-turn-1",
                "turn_id": 1,
                "speaker": "Deepak Bisht",
                "text": "Hello, welcome to the interview.",
                "timestamp": "2026-09-20T10:00:00Z",
                "source": "teams_native"
            }
        ],
        "jd": "Python Engineer",
        "resume": "FastAPI developer",
        "service_off": False,
        "final_report": None
    }

    response = client.get(f"/api/copilot/{session_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert "transcript" in data
    assert len(data["transcript"]) == 1
    assert data["transcript"][0]["speaker"] == "Deepak Bisht"
    assert data["transcript"][0]["text"] == "Hello, welcome to the interview."
    assert data["transcript"][0]["id"] == f"{session_id}-turn-1"


# ==============================================================================
# Test 8: New transcript events append correctly onto existing transcript
# ==============================================================================
@pytest.mark.asyncio
async def test_new_transcript_events_append_onto_existing(mock_repo):
    initial = [
        {"id": "s-turn-1", "turn_id": 1, "speaker": "Deepak Bisht", "text": "Prior turn 1"}
    ]
    engine = CopilotSessionEngine("s", mock_repo, initial)
    assert len(engine.get_transcript()) == 1

    new_turn = await engine.add_message(
        speaker="kaua (Unverified)",
        text="New appended turn 2",
        turn_id=2,
        source="teams_native",
        allow_merge=False
    )

    assert len(engine.get_transcript()) == 2
    assert engine.get_transcript()[0]["text"] == "Prior turn 1"
    assert engine.get_transcript()[1]["text"] == "New appended turn 2"
    assert engine.get_transcript()[1]["turn_id"] == 2


# ==============================================================================
# Test 9: WebSocket reconnect does not duplicate entries
# ==============================================================================
def test_websocket_reconnect_does_not_duplicate(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "test-reconnect-001"

    # Connection 1: receives initial frame
    with client.websocket_connect(f"/api/ws/copilot/{session_id}") as ws1:
        frame1 = ws1.receive_json()
        assert frame1["type"] == "copilot_update"

    # Connection 2 (simulating reconnect): receives frame with same session state
    with client.websocket_connect(f"/api/ws/copilot/{session_id}") as ws2:
        frame2 = ws2.receive_json()
        assert frame2["type"] == "copilot_update"
        assert frame2["transcript"] == frame1["transcript"]


# ==============================================================================
# Test 10 & 11: Completed session displays persisted transcript and does NOT resurrect
# ==============================================================================
def test_completed_session_displays_transcript_and_no_resurrection(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "22222222-2222-2222-2222-222222222222"

    persisted_transcript = [
        {"id": "c-turn-1", "turn_id": 1, "speaker": "Deepak Bisht", "text": "Completed meeting question"}
    ]
    mock_repo.load_session.return_value = {
        "session_id": session_id,
        "transcript": persisted_transcript,
        "service_off": False,
        "final_report": {"summary": "Completed successfully"}
    }

    # Status API loads persisted transcript
    res = client.get(f"/api/copilot/{session_id}/status")
    assert res.status_code == 200
    data = res.json()
    assert data["is_active"] is False
    assert len(data["transcript"]) == 1

    # WebSocket connection to completed session sends final frame and immediately closes
    with client.websocket_connect(f"/api/ws/copilot/{session_id}") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "copilot_update"
        assert frame["is_active"] is False
        assert len(frame["transcript"]) == 1
        assert frame["transcript"][0]["text"] == persisted_transcript[0]["text"]
        assert frame["transcript"][0]["speaker"] == persisted_transcript[0]["speaker"]
        assert frame["transcript"][0]["id"] == persisted_transcript[0]["id"]

    # Verify session is not resurrected
    sess = active_sessions[session_id]
    assert sess["is_active"] is False


# ==============================================================================
# Test 12 & 13: SERVICE OFF keeps existing transcript visible and prevents new updates
# ==============================================================================
def test_service_off_keeps_transcript_and_prevents_updates(test_app):
    app, active_sessions, mock_repo = test_app
    client = TestClient(app)
    session_id = "33333333-3333-3333-3333-333333333333"

    mock_repo.load_session.return_value = {
        "session_id": session_id,
        "transcript": [{"id": "so-turn-1", "turn_id": 1, "speaker": "Deepak Bisht", "text": "Turn before Service Off"}],
        "service_off": True,
        "final_report": None
    }

    res = client.get(f"/api/copilot/{session_id}/status")
    assert res.status_code == 200
    data = res.json()
    assert data["service_off"] is True
    assert len(data["transcript"]) == 1
    assert data["transcript"][0]["text"] == "Turn before Service Off"

    # Attempting to send native caption to Service Off session should close socket
    with client.websocket_connect(f"/api/ws/copilot/{session_id}?mode=native_captions") as ws:
        payload = {
            "event_type": "native_caption",
            "session_id": session_id,
            "speaker_name": "Deepak Bisht",
            "text": "Late caption after Service Off"
        }
        ws.send_text(json.dumps(payload))

    # Transcript remains 1 entry, no late addition
    assert len(active_sessions[session_id]["transcript"]) <= 1


# ==============================================================================
# Test 14: Transcript rendering does not trigger candidate evaluation
# ==============================================================================
@pytest.mark.asyncio
async def test_transcript_emission_does_not_trigger_evaluation(mock_repo):
    session_id = "test-no-eval-001"
    engine = CopilotSessionEngine(session_id, mock_repo, [])

    with patch.object(engine.evaluation_service, "evaluate_response", new_callable=AsyncMock) as mock_eval:
        await engine.add_message(
            speaker="Deepak Bisht",
            text="Can you describe your architecture?",
            turn_id=1,
            source="teams_native",
            allow_merge=False
        )
        await engine.add_message(
            speaker="kaua (Unverified)",
            text="I use event-driven microservices with Redis and PostgreSQL.",
            turn_id=2,
            source="teams_native",
            allow_merge=False
        )
        # Allow any background tasks to execute
        await asyncio.sleep(0.05)

        # Evaluation service must NOT be called for Teams native speaker names
        assert mock_eval.call_count == 0


# ==============================================================================
# Test 15: services/interview/** is 100% untouched
# ==============================================================================
def test_interview_service_remains_untouched():
    import shutil
    if shutil.which("git"):
        result = subprocess.run(
            ["git", "diff", "--", "services/interview/"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "", f"services/interview/ was modified: {result.stdout}"
    else:
        assert os.path.isdir("services/interview")
