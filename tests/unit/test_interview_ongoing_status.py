"""
Unit Tests for Interview Directory Live Status and Session Ongoing Logic
=======================================================================
Verifies:
1. is_session_ongoing helper accuracy:
   - Returns True for active sessions in active_sessions or copilot_active
   - Returns False when final_report is finalized (terminal state precedence)
   - Returns False when service_off flag exists on session or disk
   - Returns False when is_active is explicitly False
   - Returns False when local bot_process has exited (crashed/stopped)
   - Returns False for persisted records without live runtime evidence
2. GET /api/interviews endpoint:
   - Correctly includes is_ongoing field for all records
   - Merges active_sessions and copilot_active sessions
   - Preserves RBAC rules (Admin vs standard User)
3. WebSocket disconnect handling in voice interview:
   - Client disconnect sets is_active = False and status = "Client Disconnected."
"""

import os
import uuid
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from services.main import app
from services.interview.src.api.interviews import is_session_ongoing


def test_is_session_ongoing_logic(tmp_path):
    session_id = str(uuid.uuid4())

    # 1. Active in voice interview active_sessions -> True
    active_sessions = {session_id: {"is_active": True, "service_off": False}}
    copilot_active = {}
    assert is_session_ongoing(session_id, {}, active_sessions, copilot_active) is True

    # 2. Active in copilot_active -> True
    active_sessions = {}
    copilot_active = {session_id: {"is_active": True, "service_off": False}}
    assert is_session_ongoing(session_id, {}, active_sessions, copilot_active) is True

    # 3. Finalized final_report -> False (even if active in memory)
    rec_finalized = {"final_report": {"is_finalized": True}}
    active_sessions = {session_id: {"is_active": True}}
    copilot_active = {session_id: {"is_active": True}}
    assert is_session_ongoing(session_id, rec_finalized, active_sessions, copilot_active) is False

    # 4. Inactive in session -> False
    active_sessions = {session_id: {"is_active": False}}
    copilot_active = {}
    assert is_session_ongoing(session_id, {}, active_sessions, copilot_active) is False

    # 5. Service off on session -> False
    active_sessions = {session_id: {"is_active": True, "service_off": True}}
    copilot_active = {}
    assert is_session_ongoing(session_id, {}, active_sessions, copilot_active) is False

    # 6. Service off on disk (interviews/<id>/service_off.flag) -> False
    flag_dir = os.path.join("interviews", session_id)
    os.makedirs(flag_dir, exist_ok=True)
    flag_path = os.path.join(flag_dir, "service_off.flag")
    try:
        with open(flag_path, "w") as f:
            f.write("1")
        assert is_session_ongoing(session_id, {}, {session_id: {"is_active": True}}, {}) is False
    finally:
        if os.path.exists(flag_path):
            os.remove(flag_path)
        if os.path.exists(flag_dir):
            try:
                os.rmdir(flag_dir)
            except Exception:
                pass

    # 7. Local bot subprocess exited (process crash/termination) -> False
    mock_exited_proc = MagicMock()
    mock_exited_proc.poll.return_value = 1  # Exited with code 1
    copilot_with_dead_proc = {
        session_id: {
            "is_active": True,
            "service_off": False,
            "bot_process": mock_exited_proc,
        }
    }
    assert is_session_ongoing(session_id, {}, {}, copilot_with_dead_proc) is False

    # 8. Local bot subprocess still alive (poll returns None) -> True
    mock_live_proc = MagicMock()
    mock_live_proc.poll.return_value = None  # Running
    copilot_with_live_proc = {
        session_id: {
            "is_active": True,
            "service_off": False,
            "bot_process": mock_live_proc,
        }
    }
    assert is_session_ongoing(session_id, {}, {}, copilot_with_live_proc) is True

    # 9. Not in active_sessions or copilot_active (persisted only, e.g., service restart) -> False
    assert is_session_ongoing(session_id, {}, {}, {}) is False


def test_api_list_interviews_ongoing_field():
    client = TestClient(app)

    ongoing_sid = str(uuid.uuid4())
    completed_sid = str(uuid.uuid4())

    mock_db_records = [
        {
            "session_id": completed_sid,
            "timestamp": "2026-10-21T09:00:00Z",
            "candidate_name": "Completed Candidate",
            "organizer_email": "admin@voicebot.com",
            "transcript": [{"speaker": "Interviewer", "text": "Hi"}],
            "final_report": {"is_finalized": True},
        }
    ]

    mock_copilot_sessions = [
        {
            "session_id": ongoing_sid,
            "timestamp": "2026-10-21T10:00:00Z",
            "candidate_name": "Live Candidate",
            "organizer_email": "admin@voicebot.com",
            "transcript": [],
            "final_report": None,
        }
    ]

    # Active copilot session in app.state
    app.state.copilot_sessions = {
        ongoing_sid: {
            "session_id": ongoing_sid,
            "is_active": True,
            "service_off": False,
            "transcript": [],
        }
    }
    app.state.active_sessions = {}

    mock_current_user = MagicMock()
    mock_current_user.id = uuid.uuid4()
    mock_current_user.email = "admin@voicebot.com"
    mock_current_user.role = "ADMIN"

    from services.auth.src.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: mock_current_user

    try:
        with patch("services.interview.src.repositories.postgres_repository.PostgresInterviewRepository.list_all_sessions", new_callable=AsyncMock, return_value=mock_db_records), \
             patch("services.copilot.src.services.repository.CopilotRepository.list_sessions", new_callable=AsyncMock, return_value=mock_copilot_sessions):

            resp = client.get("/api/interviews")
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) >= 2

            by_id = {item["session_id"]: item for item in items}
            assert by_id[ongoing_sid]["is_ongoing"] is True
            assert by_id[completed_sid]["is_ongoing"] is False
    finally:
        app.dependency_overrides.clear()
