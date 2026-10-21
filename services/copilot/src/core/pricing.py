"""
Centralized Model Pricing Configuration for Copilot Service.
============================================================
Defines per-model pricing rates per 1,000,000 tokens using exact Decimal precision.
Supports cache-hit, cache-miss, completion pricing, and dynamic peak / off-peak schedules.

Authoritative source:
https://api-docs.deepseek.com/quick_start/pricing/
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Dict, Iterable, Optional, Set, Union
from loguru import logger

ONE_MILLION = Decimal("1000000")


class UnknownModelPricingError(KeyError):
    """Raised when pricing is requested for an unknown or unsupported model."""
    pass


class ModelPricing:
    """
    Pricing rate specification per 1M tokens.
    All calculations are performed using exact Decimal arithmetic.
    """

    def __init__(
        self,
        prompt_cache_hit_per_m: Union[Decimal, str, float],
        prompt_cache_miss_per_m: Union[Decimal, str, float],
        completion_per_m: Union[Decimal, str, float],
    ):
        self.prompt_cache_hit_per_m = Decimal(str(prompt_cache_hit_per_m))
        self.prompt_cache_miss_per_m = Decimal(str(prompt_cache_miss_per_m))
        self.completion_per_m = Decimal(str(completion_per_m))

    @property
    def prompt_cache_hit_per_token(self) -> Decimal:
        """Cost in USD for one prompt cache-hit token."""
        return self.prompt_cache_hit_per_m / ONE_MILLION

    @property
    def prompt_cache_miss_per_token(self) -> Decimal:
        """Cost in USD for one prompt cache-miss token."""
        return self.prompt_cache_miss_per_m / ONE_MILLION

    @property
    def completion_per_token(self) -> Decimal:
        """Cost in USD for one output / completion token."""
        return self.completion_per_m / ONE_MILLION


class ChineseHolidayCalendar:
    """
    Mainland China Statutory Public Holiday Calendar.

    DeepSeek official billing policy explicitly excludes Chinese statutory public
    holidays from peak pricing hours, billing them at off-peak rates.

    Limitation note:
    Statutory holidays in China are established annually by the State Council,
    incorporating lunar calendar festivals and adjusted weekend working days ('tiaoxiu').
    This calendar pre-populates official statutory holiday dates for 2024 through 2026.
    Additional dates can be added dynamically using `register_holiday` or `register_holidays`.
    """

    def __init__(self, initial_holidays: Optional[Iterable[date]] = None):
        self._holidays: Set[date] = set()
        self._load_statutory_holidays()
        if initial_holidays:
            self.register_holidays(initial_holidays)

    def _load_statutory_holidays(self) -> None:
        def _add_range(start: date, end: date) -> None:
            curr = start
            from datetime import timedelta
            while curr <= end:
                self._holidays.add(curr)
                curr += timedelta(days=1)

        # 2024 Official Statutory Public Holidays
        # New Year: Jan 1
        self._holidays.add(date(2024, 1, 1))
        # Spring Festival: Feb 10 - Feb 17
        _add_range(date(2024, 2, 10), date(2024, 2, 17))
        # Qingming: Apr 4 - Apr 6
        _add_range(date(2024, 4, 4), date(2024, 4, 6))
        # Labor Day: May 1 - May 5
        _add_range(date(2024, 5, 1), date(2024, 5, 5))
        # Dragon Boat: Jun 10
        self._holidays.add(date(2024, 6, 10))
        # Mid-Autumn: Sep 15 - Sep 17
        _add_range(date(2024, 9, 15), date(2024, 9, 17))
        # National Day: Oct 1 - Oct 7
        _add_range(date(2024, 10, 1), date(2024, 10, 7))

        # 2025 Official Statutory Public Holidays (State Council Schedule)
        # New Year: Jan 1
        self._holidays.add(date(2025, 1, 1))
        # Spring Festival: Jan 28 - Feb 4
        _add_range(date(2025, 1, 28), date(2025, 2, 4))
        # Qingming: Apr 4 - Apr 6
        _add_range(date(2025, 4, 4), date(2025, 4, 6))
        # Labor Day: May 1 - May 5
        _add_range(date(2025, 5, 1), date(2025, 5, 5))
        # Dragon Boat: May 31 - Jun 2
        _add_range(date(2025, 5, 31), date(2025, 6, 2))
        # Mid-Autumn & National Day: Oct 1 - Oct 8
        _add_range(date(2025, 10, 1), date(2025, 10, 8))

        # 2026 Official Statutory Public Holidays
        # New Year: Jan 1 - Jan 3
        _add_range(date(2026, 1, 1), date(2026, 1, 3))
        # Spring Festival: Feb 15 - Feb 23
        _add_range(date(2026, 2, 15), date(2026, 2, 23))
        # Qingming: Apr 4 - Apr 6
        _add_range(date(2026, 4, 4), date(2026, 4, 6))
        # Labor Day: May 1 - May 5
        _add_range(date(2026, 5, 1), date(2026, 5, 5))
        # Dragon Boat: Jun 19 - Jun 21
        _add_range(date(2026, 6, 19), date(2026, 6, 21))
        # Mid-Autumn: Sep 25
        self._holidays.add(date(2026, 9, 25))
        # National Day: Oct 1 - Oct 7
        _add_range(date(2026, 10, 1), date(2026, 10, 7))

    def register_holiday(self, holiday_date: date) -> None:
        """Register a single holiday date."""
        self._holidays.add(holiday_date)

    def register_holidays(self, holiday_dates: Iterable[date]) -> None:
        """Register multiple holiday dates."""
        for d in holiday_dates:
            self._holidays.add(d)

    def is_holiday(self, target_date: Union[date, datetime]) -> bool:
        """Checks if a given date or datetime falls on a recognized statutory holiday."""
        if isinstance(target_date, datetime):
            target_date = target_date.date()
        return target_date in self._holidays


# Global singleton calendar
chinese_holiday_calendar = ChineseHolidayCalendar()


def is_deepseek_peak(dt: datetime) -> bool:
    """
    Determines whether a given UTC timestamp falls within DeepSeek peak billing hours.

    Rules per DeepSeek Official Pricing:
    - Peak Windows: Monday–Friday, 01:00–04:00 and 06:00–10:00 UTC.
    - Off-Peak Exclusions:
        * Weekends (Saturday and Sunday UTC)
        * Chinese statutory public holidays
        * All other hours
    """
    if dt.tzinfo is None:
        # Default tz-naive datetimes to UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Weekends (5 = Saturday, 6 = Sunday)
    if dt.weekday() >= 5:
        return False

    # Chinese statutory public holidays are treated as off-peak
    if chinese_holiday_calendar.is_holiday(dt.date()):
        return False

    t = dt.time()
    in_morning_peak = time(1, 0) <= t < time(4, 0)
    in_afternoon_peak = time(6, 0) <= t < time(10, 0)

    return in_morning_peak or in_afternoon_peak


class ScheduledModelPricing:
    """
    Represents time-dependent model pricing with off-peak and peak rates.
    """

    def __init__(self, off_peak: ModelPricing, peak: ModelPricing):
        self.off_peak = off_peak
        self.peak = peak

    def resolve(self, request_time: Optional[datetime] = None) -> ModelPricing:
        """
        Resolves the appropriate ModelPricing for the provided UTC request timestamp.
        If request_time is omitted, defaults conservatively to off-peak rates.
        """
        if request_time is None:
            return self.off_peak
        if is_deepseek_peak(request_time):
            return self.peak
        return self.off_peak


# -------------------------------------------------------------
# Official DeepSeek Pricing Tiers (USD per 1,000,000 tokens)
# Source: https://api-docs.deepseek.com/quick_start/pricing/
# -------------------------------------------------------------

# DeepSeek Flash (and legacy deepseek-v4-flash alias)
# Off-peak: hit $0.003 / 1M | miss $0.150 / 1M | out $0.600 / 1M
# Peak:     hit $0.006 / 1M | miss $0.300 / 1M | out $1.200 / 1M
DEEPSEEK_FLASH_OFF_PEAK = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.003"),
    prompt_cache_miss_per_m=Decimal("0.150"),
    completion_per_m=Decimal("0.600"),
)
DEEPSEEK_FLASH_PEAK = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.006"),
    prompt_cache_miss_per_m=Decimal("0.300"),
    completion_per_m=Decimal("1.200"),
)
DEEPSEEK_FLASH_SCHEDULE = ScheduledModelPricing(
    off_peak=DEEPSEEK_FLASH_OFF_PEAK,
    peak=DEEPSEEK_FLASH_PEAK,
)

# DeepSeek Pro (deepseek-v4-pro)
# Off-peak: hit $0.022 / 1M | miss $0.660 / 1M | out $1.980 / 1M
# Peak:     hit $0.044 / 1M | miss $1.320 / 1M | out $3.960 / 1M
DEEPSEEK_PRO_OFF_PEAK = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.022"),
    prompt_cache_miss_per_m=Decimal("0.660"),
    completion_per_m=Decimal("1.980"),
)
DEEPSEEK_PRO_PEAK = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.044"),
    prompt_cache_miss_per_m=Decimal("1.320"),
    completion_per_m=Decimal("3.960"),
)
DEEPSEEK_PRO_SCHEDULE = ScheduledModelPricing(
    off_peak=DEEPSEEK_PRO_OFF_PEAK,
    peak=DEEPSEEK_PRO_PEAK,
)

# DeepSeek Chat / V3 (deepseek-chat)
# Flat: hit $0.070 / 1M | miss $0.270 / 1M | out $1.100 / 1M
DEEPSEEK_CHAT_PRICING = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.070"),
    prompt_cache_miss_per_m=Decimal("0.270"),
    completion_per_m=Decimal("1.100"),
)

# DeepSeek Reasoner / R1 (deepseek-reasoner, deepseek-r1)
# Flat: hit $0.140 / 1M | miss $0.550 / 1M | out $2.190 / 1M
DEEPSEEK_REASONER_PRICING = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.140"),
    prompt_cache_miss_per_m=Decimal("0.550"),
    completion_per_m=Decimal("2.190"),
)

# Groq Llama 3.3 70B (Versatile)
LLAMA_33_70B_PRICING = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.59"),
    prompt_cache_miss_per_m=Decimal("0.59"),
    completion_per_m=Decimal("0.79"),
)

# Groq Llama 3.1 8B (Instant)
LLAMA_31_8B_PRICING = ModelPricing(
    prompt_cache_hit_per_m=Decimal("0.05"),
    prompt_cache_miss_per_m=Decimal("0.05"),
    completion_per_m=Decimal("0.08"),
)

# Active catalog supporting direct lookup and scheduled resolution
MODEL_PRICING_CATALOG: Dict[str, Union[ModelPricing, ScheduledModelPricing]] = {
    "deepseek-flash": DEEPSEEK_FLASH_SCHEDULE,
    "deepseek-v4-flash": DEEPSEEK_FLASH_SCHEDULE,
    "deepseek-v4-pro": DEEPSEEK_PRO_SCHEDULE,
    "deepseek-chat": DEEPSEEK_CHAT_PRICING,
    "deepseek-reasoner": DEEPSEEK_REASONER_PRICING,
    "deepseek-r1": DEEPSEEK_REASONER_PRICING,
    "llama-3.3-70b-versatile": LLAMA_33_70B_PRICING,
    "llama-3.1-8b-instant": LLAMA_31_8B_PRICING,
}

# Backward compatibility map for modules accessing DEFAULT_MODEL_PRICING directly
DEFAULT_MODEL_PRICING: Dict[str, ModelPricing] = {
    "deepseek-flash": DEEPSEEK_FLASH_OFF_PEAK,
    "deepseek-v4-flash": DEEPSEEK_FLASH_OFF_PEAK,
    "deepseek-chat": DEEPSEEK_CHAT_PRICING,
    "deepseek-v4-pro": DEEPSEEK_PRO_OFF_PEAK,
    "deepseek-reasoner": DEEPSEEK_REASONER_PRICING,
    "deepseek-r1": DEEPSEEK_REASONER_PRICING,
    "llama-3.3-70b-versatile": LLAMA_33_70B_PRICING,
    "llama-3.1-8b-instant": LLAMA_31_8B_PRICING,
}


def get_model_pricing(
    model_name: Optional[str] = None,
    request_time: Optional[datetime] = None,
) -> ModelPricing:
    """
    Resolves the ModelPricing for a given model string and request timestamp.

    Guarantees:
    - Both 'deepseek-flash' and 'deepseek-v4-flash' resolve to Flash pricing.
    - Peak vs. off-peak pricing is evaluated using the request UTC timestamp.
    - Weekend requests and Chinese statutory holidays use off-peak rates.
    - Unknown or unsupported models raise UnknownModelPricingError.
      Silent fallbacks to unrelated models (e.g. deepseek-chat) are strictly prohibited.
    """
    if not model_name:
        raise UnknownModelPricingError(
            "Model name must be provided to determine pricing. Fallback pricing is disabled."
        )

    normalized = str(model_name).strip().lower()

    entry = MODEL_PRICING_CATALOG.get(normalized)

    # Prefix match if not an exact match (e.g., deepseek-chat-20241201 -> deepseek-chat)
    if entry is None:
        for key, val in MODEL_PRICING_CATALOG.items():
            if normalized.startswith(key):
                entry = val
                break

    if entry is None:
        logger.error(
            f"[Pricing] Unknown model '{model_name}'. No fallback price applied to prevent incorrect billing."
        )
        raise UnknownModelPricingError(
            f"No pricing rate configured for model '{model_name}'. "
            f"Fallback to unrelated model pricing is disabled to maintain accounting integrity."
        )

    if isinstance(entry, ScheduledModelPricing):
        return entry.resolve(request_time)
    return entry
