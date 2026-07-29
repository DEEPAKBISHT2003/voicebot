"""Integration tests for Interview and Copilot services"""

import pytest
from fastapi.testclient import TestClient
from services.interview.src.main import app as interview_app
from services.copilot.src.main import app as copilot_app


@pytest.fixture
def interview_client():
    """Create interview service test client"""
    return TestClient(interview_app)


@pytest.fixture
def copilot_client():
    """Create copilot service test client"""
    return TestClient(copilot_app)


def test_interview_copilot_integration(interview_client, copilot_client):
    """Test interview session creates copilot session"""
    # Create interview session
    interview_payload = {
        "job_description": "Senior Python Engineer",
        "resume": "Experienced backend developer...",
        "custom_prompt": None
    }
    interview_response = interview_client.post("/interviews", json=interview_payload)
    assert interview_response.status_code == 201
    interview_session_id = interview_response.json()["session_id"]
    
    # Create copilot session
    copilot_payload = {
        "user_id": "interview-user",
        "session_type": "technical",
        "context": {
            "interview_session_id": interview_session_id,
            "project": "AI Interview Platform"
        }
    }
    copilot_response = copilot_client.post("/copilot", json=copilot_payload)
    assert copilot_response.status_code == 201
    copilot_session_id = copilot_response.json()["session_id"]
    
    # Verify both sessions were created
    assert interview_session_id is not None
    assert copilot_session_id is not None