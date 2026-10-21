"""
Unit Tests for DeepSeek Pricing Schedules, Usage Telemetry & Cost Accounting.
=============================================================================
Verifies:
1. 'deepseek-v4-flash' resolves to Flash pricing.
2. 'deepseek-flash' resolves to the same Flash pricing schedule.
3. Unknown model names never silently inherit unrelated pricing.
4. Peak and off-peak rates are selected using the request UTC timestamp.
5. Weekend requests use off-peak pricing.
6. Chinese statutory public holidays use off-peak pricing (calendar interface).
7. Cache-hit, cache-miss, and output costs are calculated correctly with Decimal precision.
8. Token categories are not double-counted (hit + miss partition prompt tokens).
9. Missing usage metadata does not become falsely authenticated zero-cost usage.
10. Confirmed unmetered call paths invoke the usage recorder exactly once.
11. Timeouts and exceptions do not fabricate usage.
12. Existing session aggregation and response contracts remain compatible.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.copilot.src.core.pricing import (
    UnknownModelPricingError,
    chinese_holiday_calendar,
    get_model_pricing,
    is_deepseek_peak,
)
from services.copilot.src.engine.copilot import AICopilotEngine
from services.copilot.src.engine.intelligence import ConversationIntelligenceEngine
from services.copilot.src.services.usage_service import CopilotUsageService, usage_service


# ---------------------------------------------------------------------------
# Test 1 & 2: Model Resolution and Alias Mapping
# ---------------------------------------------------------------------------

def test_deepseek_v4_flash_resolves_to_flash_pricing():
    """Requirement 1: 'deepseek-v4-flash' maps to official Flash tier."""
    pricing = get_model_pricing("deepseek-v4-flash")
    # Base off-peak rates for Flash
    assert pricing.prompt_cache_hit_per_m == Decimal("0.003")
    assert pricing.prompt_cache_miss_per_m == Decimal("0.150")
    assert pricing.completion_per_m == Decimal("0.600")


def test_deepseek_flash_resolves_to_same_flash_schedule():
    """Requirement 2: 'deepseek-flash' and 'deepseek-v4-flash' resolve identically."""
    flash_pricing = get_model_pricing("deepseek-flash")
    alias_pricing = get_model_pricing("deepseek-v4-flash")

    assert flash_pricing.prompt_cache_hit_per_m == alias_pricing.prompt_cache_hit_per_m
    assert flash_pricing.prompt_cache_miss_per_m == alias_pricing.prompt_cache_miss_per_m
    assert flash_pricing.completion_per_m == alias_pricing.completion_per_m


# ---------------------------------------------------------------------------
# Test 3: Unknown Models Never Silently Inherit Unrelated Pricing
# ---------------------------------------------------------------------------

def test_unknown_models_raise_error_no_silent_fallback():
    """Requirement 3: Unsupported models raise UnknownModelPricingError."""
    with pytest.raises(UnknownModelPricingError):
        get_model_pricing("unknown-model-xyz")

    with pytest.raises(UnknownModelPricingError):
        get_model_pricing(None)

    with pytest.raises(UnknownModelPricingError):
        get_model_pricing("gpt-4o")

    # calculate_cost must also raise UnknownModelPricingError on unsupported models
    with pytest.raises(UnknownModelPricingError):
        CopilotUsageService.calculate_cost("unknown-model-xyz", 100, 50)


# ---------------------------------------------------------------------------
# Test 4: Peak vs Off-Peak Rate Selection via Request Timestamp
# ---------------------------------------------------------------------------

def test_peak_and_off_peak_selection_using_timestamp():
    """Requirement 4: Rates are determined by UTC request time."""
    # Peak Window 1: Wednesday 02:30 UTC
    peak_morning = datetime(2026, 4, 15, 2, 30, tzinfo=timezone.utc)
    pricing_peak_am = get_model_pricing("deepseek-v4-flash", request_time=peak_morning)
    assert pricing_peak_am.prompt_cache_hit_per_m == Decimal("0.006")
    assert pricing_peak_am.prompt_cache_miss_per_m == Decimal("0.300")
    assert pricing_peak_am.completion_per_m == Decimal("1.200")

    # Peak Window 2: Wednesday 08:15 UTC
    peak_afternoon = datetime(2026, 4, 15, 8, 15, tzinfo=timezone.utc)
    pricing_peak_pm = get_model_pricing("deepseek-v4-flash", request_time=peak_afternoon)
    assert pricing_peak_pm.prompt_cache_hit_per_m == Decimal("0.006")
    assert pricing_peak_pm.prompt_cache_miss_per_m == Decimal("0.300")
    assert pricing_peak_pm.completion_per_m == Decimal("1.200")

    # Off-Peak: Wednesday 05:00 UTC (between peak windows)
    off_peak_midday = datetime(2026, 4, 15, 5, 0, tzinfo=timezone.utc)
    pricing_off_peak = get_model_pricing("deepseek-v4-flash", request_time=off_peak_midday)
    assert pricing_off_peak.prompt_cache_hit_per_m == Decimal("0.003")
    assert pricing_off_peak.prompt_cache_miss_per_m == Decimal("0.150")
    assert pricing_off_peak.completion_per_m == Decimal("0.600")

    # Off-Peak: Wednesday 16:00 UTC (evening)
    off_peak_evening = datetime(2026, 4, 15, 16, 0, tzinfo=timezone.utc)
    pricing_evening = get_model_pricing("deepseek-v4-flash", request_time=off_peak_evening)
    assert pricing_evening.prompt_cache_hit_per_m == Decimal("0.003")


# ---------------------------------------------------------------------------
# Test 5: Weekend Requests Always Use Off-Peak Pricing
# ---------------------------------------------------------------------------

def test_weekend_requests_use_off_peak_pricing():
    """Requirement 5: Weekend invocations (UTC) are always off-peak."""
    # Saturday 02:30 UTC (would be peak if it were a weekday)
    sat_time = datetime(2026, 4, 18, 2, 30, tzinfo=timezone.utc)
    sat_pricing = get_model_pricing("deepseek-v4-flash", request_time=sat_time)
    assert sat_pricing.prompt_cache_hit_per_m == Decimal("0.003")
    assert not is_deepseek_peak(sat_time)

    # Sunday 07:30 UTC
    sun_time = datetime(2026, 4, 19, 7, 30, tzinfo=timezone.utc)
    sun_pricing = get_model_pricing("deepseek-v4-flash", request_time=sun_time)
    assert sun_pricing.prompt_cache_hit_per_m == Decimal("0.003")
    assert not is_deepseek_peak(sun_time)


# ---------------------------------------------------------------------------
# Test 6: Chinese Public Holidays Use Off-Peak Pricing
# ---------------------------------------------------------------------------

def test_chinese_public_holidays_use_off_peak_pricing():
    """Requirement 6: Statutory Chinese holidays are excluded from peak rates."""
    # 2026-05-01 is Labor Day in China (a Friday) at 02:30 UTC
    holiday_time = datetime(2026, 5, 1, 2, 30, tzinfo=timezone.utc)
    assert chinese_holiday_calendar.is_holiday(holiday_time)
    assert not is_deepseek_peak(holiday_time)

    holiday_pricing = get_model_pricing("deepseek-v4-flash", request_time=holiday_time)
    assert holiday_pricing.prompt_cache_hit_per_m == Decimal("0.003")
    assert holiday_pricing.prompt_cache_miss_per_m == Decimal("0.150")

    # Dynamic registration test
    custom_holiday = date(2026, 11, 11)
    test_dt = datetime(2026, 11, 11, 2, 30, tzinfo=timezone.utc)
    # Before registering, Wednesday 02:30 UTC is peak
    assert is_deepseek_peak(test_dt)

    try:
        chinese_holiday_calendar.register_holiday(custom_holiday)
        # After registering, it becomes off-peak
        assert not is_deepseek_peak(test_dt)
        pricing = get_model_pricing("deepseek-v4-flash", request_time=test_dt)
        assert pricing.prompt_cache_hit_per_m == Decimal("0.003")
    finally:
        # Cleanup registered test holiday
        chinese_holiday_calendar._holidays.discard(custom_holiday)


# ---------------------------------------------------------------------------
# Test 7: Cost Calculation Arithmetic & Decimal Precision
# ---------------------------------------------------------------------------

def test_cost_calculation_categories_arithmetic():
    """Requirement 7: Exact Decimal arithmetic across token categories."""
    off_peak_time = datetime(2026, 4, 15, 5, 0, tzinfo=timezone.utc)

    # 1,000,000 hit, 1,000,000 miss, 1,000,000 completion tokens
    cost = CopilotUsageService.calculate_cost(
        model="deepseek-v4-flash",
        prompt_tokens=2000000,
        completion_tokens=1000000,
        cache_hit_tokens=1000000,
        cache_miss_tokens=1000000,
        request_time=off_peak_time,
    )

    # Expected: (1 * 0.003) + (1 * 0.150) + (1 * 0.600) = 0.753000 USD
    assert cost == Decimal("0.753000")

    # Peak rates calculation
    peak_time = datetime(2026, 4, 15, 2, 30, tzinfo=timezone.utc)
    cost_peak = CopilotUsageService.calculate_cost(
        model="deepseek-v4-flash",
        prompt_tokens=2000000,
        completion_tokens=1000000,
        cache_hit_tokens=1000000,
        cache_miss_tokens=1000000,
        request_time=peak_time,
    )
    # Expected: (1 * 0.006) + (1 * 0.300) + (1 * 1.200) = 1.506000 USD
    assert cost_peak == Decimal("1.506000")


# ---------------------------------------------------------------------------
# Test 8: Token Categories Are Not Double-Counted
# ---------------------------------------------------------------------------

def test_token_categories_not_double_counted():
    """Requirement 8: Prompt cache hits and misses partition prompt tokens."""
    # Standard breakdown: 1500 prompt tokens = 1000 hits + 500 misses
    metrics = CopilotUsageService.extract_usage_metrics({
        "prompt_tokens": 1500,
        "prompt_cache_hit_tokens": 1000,
        "completion_tokens": 500,
    })
    assert metrics["prompt_tokens"] == 1500
    assert metrics["cache_hit_tokens"] == 1000
    assert metrics["cache_miss_tokens"] == 500
    assert metrics["cache_hit_tokens"] + metrics["cache_miss_tokens"] == metrics["prompt_tokens"]

    # When no cache details provided: all prompt tokens become misses
    metrics_no_cache = CopilotUsageService.extract_usage_metrics({
        "prompt_tokens": 1200,
        "completion_tokens": 300,
    })
    assert metrics_no_cache["cache_hit_tokens"] == 0
    assert metrics_no_cache["cache_miss_tokens"] == 1200
    assert metrics_no_cache["total_tokens"] == 1500

    # From OpenAI-style prompt_tokens_details.cached_tokens
    class MockUsageDetails:
        cached_tokens = 400

    class MockUsage:
        prompt_tokens = 600
        completion_tokens = 200
        total_tokens = 800
        prompt_tokens_details = MockUsageDetails()

    metrics_details = CopilotUsageService.extract_usage_metrics(MockUsage())
    assert metrics_details["cache_hit_tokens"] == 400
    assert metrics_details["cache_miss_tokens"] == 200
    assert metrics_details["completion_tokens"] == 200


# ---------------------------------------------------------------------------
# Test 9: Missing Usage Metadata Does Not Become Zero-Cost Usage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_usage_metadata_not_zero_cost():
    """Requirement 9: None or empty usage does not create unverified 0-cost records."""
    # When usage is None
    record_none = await usage_service.record_llm_usage(
        session_id=uuid.uuid4(),
        stage="test",
        model="deepseek-v4-flash",
        usage=None,
    )
    assert record_none is None

    # When usage is an empty dictionary
    record_empty = await usage_service.record_llm_usage(
        session_id=uuid.uuid4(),
        stage="test",
        model="deepseek-v4-flash",
        usage={},
    )
    assert record_empty is None


# ---------------------------------------------------------------------------
# Test 10: Unmetered Call Paths Invoke Usage Recorder Exactly Once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unmetered_call_paths_invoke_usage_recorder_once():
    """Requirement 10: Active fallback paths record usage with correct attributes."""
    session_id = uuid.uuid4()
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150
    mock_usage.prompt_cache_hit_tokens = 0
    mock_usage.prompt_cache_miss_tokens = 100

    mock_choice = MagicMock()
    mock_choice.message.content = '{"suggestions": ["Question 1", "Question 2"]}'

    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage = mock_usage

    with patch.object(usage_service, "record_llm_usage", new_callable=AsyncMock) as mock_record:
        mock_record.return_value = MagicMock()
        engine = AICopilotEngine()
        engine.client.chat.completions.create = AsyncMock(return_value=mock_resp)

        # 1. generate_dynamic_suggestions
        suggestions = await engine.generate_dynamic_suggestions(
            jd="Python Engineer",
            resume="Experienced developer",
            interviewer_question="How does GIL work?",
            candidate_answer="It protects access to Python objects.",
            session_id=session_id,
        )
        assert len(suggestions) >= 1
        assert mock_record.call_count == 1
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["session_id"] == session_id
        assert call_kwargs["stage"] == "dynamic_suggestions_fallback"
        assert call_kwargs["model"] == engine.model
        assert call_kwargs["call_identifier"] == "dynamic_suggestions"
        assert "request_time" in call_kwargs

    # 2. ConversationIntelligenceEngine.analyze
    mock_choice_intel = MagicMock()
    mock_choice_intel.message.content = '{"current_topic": "Python", "covered_skills": []}'
    mock_resp_intel = MagicMock()
    mock_resp_intel.choices = [mock_choice_intel]
    mock_resp_intel.usage = mock_usage

    with patch.object(usage_service, "record_llm_usage", new_callable=AsyncMock) as mock_record_intel:
        mock_record_intel.return_value = MagicMock()
        intel_engine = ConversationIntelligenceEngine()
        intel_engine.client.chat.completions.create = AsyncMock(return_value=mock_resp_intel)

        res = await intel_engine.analyze(
            transcript=[{"speaker": "Interviewer", "text": "Hello"}],
            jd="Python",
            resume="Resume",
            session_id=session_id,
        )
        assert res.get("current_topic") == "Python"
        assert mock_record_intel.call_count == 1
        assert mock_record_intel.call_args.kwargs["stage"] == "intelligence"


# ---------------------------------------------------------------------------
# Test 11: Timeouts and Exceptions Do Not Fabricate Usage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeouts_and_exceptions_do_not_fabricate_usage():
    """Requirement 11: Timeouts gracefully fallback without fabricating usage records."""
    session_id = uuid.uuid4()

    with patch.object(usage_service, "record_llm_usage", new_callable=AsyncMock) as mock_record:
        engine = AICopilotEngine()
        engine.client.chat.completions.create = AsyncMock(side_effect=TimeoutError("Request timed out"))

        # Should return fallback suggestions without crashing
        res = await engine.generate_dynamic_suggestions(
            jd="JD",
            resume="Resume",
            session_id=session_id,
        )
        assert len(res) >= 1
        # Usage recorder must NEVER be called when request timed out
        assert mock_record.call_count == 0

    with patch.object(usage_service, "record_llm_usage", new_callable=AsyncMock) as mock_record_intel:
        intel_engine = ConversationIntelligenceEngine()
        intel_engine.client.chat.completions.create = AsyncMock(side_effect=Exception("API connection reset"))

        # Should return empty state without crashing
        res_intel = await intel_engine.analyze(
            transcript=[{"speaker": "Interviewer", "text": "Hi"}],
            session_id=session_id,
        )
        assert res_intel["current_topic"] == ""
        assert mock_record_intel.call_count == 0


# ---------------------------------------------------------------------------
# Test 12: Existing Session Aggregation & Contract Compatibility
# ---------------------------------------------------------------------------

def test_copilot_usage_service_calculation_compatibility():
    """Requirement 12: Zero-token or partial calls produce predictable Decimal output."""
    # Zero tokens
    zero_cost = CopilotUsageService.calculate_cost("deepseek-v4-flash", 0, 0, 0, 0)
    assert zero_cost == Decimal("0.000000")

    # DeepSeek Chat pricing verification
    chat_cost = CopilotUsageService.calculate_cost(
        "deepseek-chat",
        prompt_tokens=1000,
        completion_tokens=500,
        cache_hit_tokens=800,
        cache_miss_tokens=200,
    )
    # Expected: (800 * 0.07 / 1M) + (200 * 0.27 / 1M) + (500 * 1.10 / 1M)
    # = 0.000056 + 0.000054 + 0.000550 = 0.000660 USD
    assert chat_cost == Decimal("0.000660")
