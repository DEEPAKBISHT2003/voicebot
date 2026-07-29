"""Tests for Copilot API"""

import pytest
from fastapi.testclient import TestClient
from services.copilot.src.main import app


@pytest.fixture
def client():
    """Create test client"""
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_copilot_session(client):
    """Test creating a new copilot session"""
    payload = {
        "user_id": "test-user-123",
        "session_type": "technical",
        "context": {"project": "AI Interview Platform"}
    }
    response = client.post("/copilot", json=payload)
    assert response.status_code == 201
    assert "session_id" in response.json()
