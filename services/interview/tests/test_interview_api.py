"""Tests for Interview API"""

import pytest
from fastapi.testclient import TestClient
from services.interview.src.main import app


@pytest.fixture
def client():
    """Create test client"""
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_interview_session(client):
    """Test creating a new interview session"""
    payload = {
        "job_description": "Senior Python Engineer",
        "resume": "Experienced backend developer...",
        "custom_prompt": "Focus on system architecture"
    }
    response = client.post("/interviews", json=payload)
    assert response.status_code == 201
    assert "session_id" in response.json()


def test_get_interview_session(client):
    """Test getting an interview session"""
    # First create a session
    payload = {
        "job_description": "Senior Python Engineer",
        "resume": "Experienced backend developer...",
        "custom_prompt": None
    }
    create_response = client.post("/interviews", json=payload)
    session_id = create_response.json()["session_id"]
    
    # Then get it
    response = client.get(f"/interviews/{session_id}")
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
