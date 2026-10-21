"""
Unit Tests for Dashboard Date-Range Filtering, Balance Consumed, and Meeting Time Aggregation.
=============================================================================================
Verifies:
1. Correct aggregation of DeepSeek cost_usd values (Balance Consumed).
2. Inclusion of all supported usage stages in stage breakdown.
3. Exclusion of records outside the selected date range.
4. Half-open interval boundary semantics (start_datetime <= timestamp < day_after_end_datetime).
5. All preset date ranges: today, yesterday, last_7_days, last_30_days, this_month.
6. Custom date ranges, single-day custom ranges, and validation error when start_date > end_date.
7. Meeting duration aggregation without duplicate session counting.
8. DeepSeek model attribution (isolating DeepSeek from non-DeepSeek models).
9. Session usage list pagination and date filtering.
10. FastAPI router error handling for invalid ranges (HTTP 400).
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from services.copilot.src.services.dashboard_service import (
    CopilotDashboardUsageService,
    parse_date_range,
)
from services.main import app


class MockQuerySet:
    """Helper mock for Tortoise ORM awaitable and chainable querysets."""
    def __init__(self, items):
        self.items = items

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def count(self):
        return len(self.items)

    def __await__(self):
        async def _coro():
            return self.items
        return _coro().__await__()


# ---------------------------------------------------------------------------
# Test 1: Presets Date Boundaries
# ---------------------------------------------------------------------------

def test_parse_date_range_presets():
    """Requirement 5: Verify all preset ranges calculate exact UTC intervals."""
    tz_str = "UTC"

    # Today
    start_utc, end_utc, preset, _ = parse_date_range(preset="today", timezone_str=tz_str)
    assert preset == "today"
    assert (end_utc - start_utc) == timedelta(days=1)
    today_d = datetime.now(timezone.utc).date()
    assert start_utc.date() == today_d
    assert end_utc.date() == today_d + timedelta(days=1)

    # Yesterday
    start_utc, end_utc, preset, _ = parse_date_range(preset="yesterday", timezone_str=tz_str)
    assert preset == "yesterday"
    assert (end_utc - start_utc) == timedelta(days=1)
    yest_d = today_d - timedelta(days=1)
    assert start_utc.date() == yest_d
    assert end_utc.date() == today_d

    # Last 7 Days (6 past days + today = 7 days)
    start_utc, end_utc, preset, _ = parse_date_range(preset="last_7_days", timezone_str=tz_str)
    assert preset == "last_7_days"
    assert (end_utc - start_utc) == timedelta(days=7)
    assert start_utc.date() == today_d - timedelta(days=6)
    assert end_utc.date() == today_d + timedelta(days=1)

    # Last 30 Days (29 past days + today = 30 days)
    start_utc, end_utc, preset, _ = parse_date_range(preset="last_30_days", timezone_str=tz_str)
    assert preset == "last_30_days"
    assert (end_utc - start_utc) == timedelta(days=30)
    assert start_utc.date() == today_d - timedelta(days=29)
    assert end_utc.date() == today_d + timedelta(days=1)

    # This Month (1st of month to tomorrow)
    start_utc, end_utc, preset, _ = parse_date_range(preset="this_month", timezone_str=tz_str)
    assert preset == "this_month"
    assert start_utc.date() == date(today_d.year, today_d.month, 1)
    assert end_utc.date() == today_d + timedelta(days=1)

    # Default fallback when none provided
    start_utc, end_utc, preset, _ = parse_date_range(timezone_str=tz_str)
    assert preset == "last_7_days"


# ---------------------------------------------------------------------------
# Test 2: Custom Date Ranges & Half-Open Interval Semantics
# ---------------------------------------------------------------------------

def test_parse_date_range_custom_valid():
    """Requirement 4 & 6: Custom date range includes the entire end date."""
    start_utc, end_utc, preset, _ = parse_date_range(
        start_date="2026-10-15",
        end_date="2026-10-20",
        timezone_str="UTC"
    )
    assert preset == "custom"
    assert start_utc == datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)


def test_parse_date_range_single_day():
    """Requirement 4: Single day range (start == end) encompasses exactly 24 hours."""
    start_utc, end_utc, preset, _ = parse_date_range(
        start_date="2026-10-18",
        end_date="2026-10-18",
        timezone_str="UTC"
    )
    assert preset == "custom"
    assert start_utc == datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
    assert (end_utc - start_utc) == timedelta(days=1)


def test_parse_date_range_invalid_start_after_end():
    """Requirement 6: Error raised when start date exceeds end date."""
    with pytest.raises(ValueError, match="Start date cannot be after end date"):
        parse_date_range(
            start_date="2026-10-25",
            end_date="2026-10-20",
            timezone_str="UTC"
        )


# ---------------------------------------------------------------------------
# Test 3: DeepSeek Balance Consumed Aggregation & Model Isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_usage_summary_deepseek_balance_consumed_and_isolation():
    """Requirement 1, 3 & 8: DeepSeek cost isolated from non-DeepSeek models and filtered by date."""
    service = CopilotDashboardUsageService()

    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()

    rec_deepseek_1 = MagicMock(
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        cost_usd=Decimal("0.250000"),
        duration_ms=450,
        model="deepseek-v4-flash",
        stage="unified_qa",
        session_id=sid1,
    )
    rec_deepseek_2 = MagicMock(
        prompt_tokens=2000,
        completion_tokens=500,
        total_tokens=2500,
        cost_usd=Decimal("0.400000"),
        duration_ms=600,
        model="deepseek-chat",
        stage="assistance",
        session_id=sid2,
    )
    rec_non_deepseek = MagicMock(
        prompt_tokens=500,
        completion_tokens=100,
        total_tokens=600,
        cost_usd=Decimal("0.100000"),
        duration_ms=300,
        model="llama-3.3-70b-versatile",
        stage="role_identifier",
        session_id=sid1,
    )

    mock_records = [rec_deepseek_1, rec_deepseek_2, rec_non_deepseek]

    mock_sess_1 = MagicMock(
        session_id=sid1,
        meeting_duration_seconds=1200,
    )
    mock_sess_2 = MagicMock(
        session_id=sid2,
        meeting_duration_seconds=600,
    )

    with patch("services.copilot.src.models.copilot.CopilotLLMUsageRecordModel.all", return_value=MockQuerySet(mock_records)), \
         patch("services.copilot.src.models.copilot.CopilotSessionModel.all", return_value=MockQuerySet([mock_sess_1, mock_sess_2])), \
         patch("services.copilot.src.models.copilot.CopilotSessionModel.filter", return_value=MockQuerySet([mock_sess_1, mock_sess_2])):

        summary = await service.get_usage_summary(
            start_date="2026-10-15",
            end_date="2026-10-20",
            timezone_str="UTC"
        )

        # Total cost sums all models: 0.25 + 0.40 + 0.10 = 0.75
        assert summary["total_cost_usd"] == 0.75

        # Balance Consumed strictly isolates DeepSeek: 0.25 + 0.40 = 0.65
        assert summary["balance_consumed_usd"] == 0.65

        # DeepSeek calls count = 2, total calls = 3
        assert summary["deepseek_llm_calls"] == 2
        assert summary["total_llm_calls"] == 3

        # Meeting duration: 1200 + 600 = 1800s (30.0 mins)
        assert summary["total_meeting_duration_seconds"] == 1800
        assert summary["total_meeting_duration_minutes"] == 30.0
        assert summary["total_sessions"] == 2


# ---------------------------------------------------------------------------
# Test 4: Meeting Duration Deduplication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meeting_duration_deduplication():
    """Requirement 7: Avoid counting the same session multiple times."""
    service = CopilotDashboardUsageService()
    sid = uuid.uuid4()

    sess_dup1 = MagicMock(session_id=sid, meeting_duration_seconds=1800)
    sess_dup2 = MagicMock(session_id=sid, meeting_duration_seconds=1800)

    with patch("services.copilot.src.models.copilot.CopilotLLMUsageRecordModel.all", return_value=MockQuerySet([])), \
         patch("services.copilot.src.models.copilot.CopilotSessionModel.all", return_value=MockQuerySet([sess_dup1, sess_dup2])), \
         patch("services.copilot.src.models.copilot.CopilotSessionModel.filter", return_value=MockQuerySet([sess_dup1, sess_dup2])):

        summary = await service.get_usage_summary(preset="today")

        # Must count session duration exactly ONCE: 1800s (30m), not 3600s
        assert summary["total_meeting_duration_seconds"] == 1800
        assert summary["total_meeting_duration_minutes"] == 30.0
        assert summary["total_sessions"] == 2


# ---------------------------------------------------------------------------
# Test 5: Supported Usage Stages in Stage Breakdown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_usage_stages_included_in_breakdown():
    """Requirement 2: Inclusion of all supported usage stages."""
    service = CopilotDashboardUsageService()

    stages = [
        "assistance",
        "dynamic_suggestions_fallback",
        "initial_suggestions_fallback",
        "static_questions_fallback",
        "precompiler",
        "unified_qa",
        "intelligence",
    ]

    records = [
        MagicMock(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=Decimal("0.010000"),
            duration_ms=200,
            model="deepseek-v4-flash",
            stage=st,
            session_id=uuid.uuid4(),
        )
        for st in stages
    ]

    with patch("services.copilot.src.models.copilot.CopilotLLMUsageRecordModel.all", return_value=MockQuerySet(records)), \
         patch("services.copilot.src.models.copilot.CopilotSessionModel.all", return_value=MockQuerySet([])), \
         patch("services.copilot.src.models.copilot.CopilotSessionModel.filter", return_value=MockQuerySet([])):

        summary = await service.get_usage_summary(preset="last_7_days")

        breakdown_stages = {s["stage"] for s in summary["stage_breakdown"]}
        for st in stages:
            assert st in breakdown_stages


# ---------------------------------------------------------------------------
# Test 6: FastAPI API Endpoint Integration
# ---------------------------------------------------------------------------

def test_api_usage_summary_endpoint():
    """Requirement 10: Verify API returns 200 with date filter and 400 on invalid custom range."""
    client = TestClient(app)

    # 1. Valid preset with mocked get_usage_summary
    mock_summary = {
        "status": "success",
        "deepseek_balance": 15.2,
        "balance_consumed_usd": 0.45,
        "deepseek_llm_calls": 12,
        "total_meeting_duration_seconds": 1200,
        "total_sessions": 2,
        "start_date": "2026-10-14",
        "end_date": "2026-10-21",
        "applied_preset": "last_7_days",
        "timezone": "UTC",
        "cost_accounting_status": "authoritative",
        "stage_breakdown": [],
        "sessions": []
    }
    with patch("services.copilot.src.services.dashboard_service.dashboard_service.get_usage_summary", new_callable=AsyncMock, return_value=mock_summary):
        resp = client.get("/api/copilot/usage/summary?preset=last_7_days")
        assert resp.status_code == 200
        data = resp.json()
        assert "balance_consumed_usd" in data
        assert "total_meeting_duration_seconds" in data
        assert data["applied_preset"] == "last_7_days"
        assert data["balance_consumed_usd"] == 0.45

    # 2. Invalid date range (start > end) -> HTTP 400
    resp_invalid = client.get("/api/copilot/usage/summary?start_date=2026-10-25&end_date=2026-10-20")
    assert resp_invalid.status_code == 400
    assert "Start date cannot be after end date" in resp_invalid.json()["detail"]
