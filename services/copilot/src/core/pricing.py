"""
Centralized Model Pricing Configuration for Copilot Service.
============================================================
Defines per-model pricing rates per 1,000,000 tokens using exact Decimal precision.
Supports cache-hit, cache-miss, and completion pricing.
"""

from decimal import Decimal
from typing import Dict, Optional


ONE_MILLION = Decimal("1000000")


class ModelPricing:
    """
    Pricing rate specification per 1M tokens.
    All calculations are performed using exact Decimal arithmetic.
    """

    def __init__(
        self,
        prompt_cache_hit_per_m: Decimal,
        prompt_cache_miss_per_m: Decimal,
        completion_per_m: Decimal,
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


# -------------------------------------------------------------
# Default Model Pricing Rates (USD per 1,000,000 tokens)
# Note: Rates are based on official published API pricing.
# -------------------------------------------------------------
DEFAULT_MODEL_PRICING: Dict[str, ModelPricing] = {
    # DeepSeek V3 / Standard Chat models
    # Cache hit: $0.014 / 1M | Cache miss: $0.14 / 1M | Output: $0.28 / 1M
    "deepseek-chat": ModelPricing(
        prompt_cache_hit_per_m=Decimal("0.014"),
        prompt_cache_miss_per_m=Decimal("0.14"),
        completion_per_m=Decimal("0.28"),
    ),
    "deepseek-v4-pro": ModelPricing(
        prompt_cache_hit_per_m=Decimal("0.014"),
        prompt_cache_miss_per_m=Decimal("0.14"),
        completion_per_m=Decimal("0.28"),
    ),
    # DeepSeek R1 / Reasoning models
    # Cache hit: $0.14 / 1M | Cache miss: $0.55 / 1M | Output: $2.19 / 1M
    "deepseek-reasoner": ModelPricing(
        prompt_cache_hit_per_m=Decimal("0.14"),
        prompt_cache_miss_per_m=Decimal("0.55"),
        completion_per_m=Decimal("2.19"),
    ),
    "deepseek-r1": ModelPricing(
        prompt_cache_hit_per_m=Decimal("0.14"),
        prompt_cache_miss_per_m=Decimal("0.55"),
        completion_per_m=Decimal("2.19"),
    ),
    # Groq Llama 3.3 70B (Versatile)
    "llama-3.3-70b-versatile": ModelPricing(
        prompt_cache_hit_per_m=Decimal("0.59"),
        prompt_cache_miss_per_m=Decimal("0.59"),
        completion_per_m=Decimal("0.79"),
    ),
    # Groq Llama 3.1 8B (Instant)
    "llama-3.1-8b-instant": ModelPricing(
        prompt_cache_hit_per_m=Decimal("0.05"),
        prompt_cache_miss_per_m=Decimal("0.05"),
        completion_per_m=Decimal("0.08"),
    ),
}


def get_model_pricing(model_name: Optional[str] = None) -> ModelPricing:
    """
    Resolves the ModelPricing for a given model string.
    Falls back gracefully to standard deepseek-chat pricing if unlisted.
    """
    if not model_name:
        return DEFAULT_MODEL_PRICING["deepseek-chat"]

    normalized = str(model_name).strip().lower()
    if normalized in DEFAULT_MODEL_PRICING:
        return DEFAULT_MODEL_PRICING[normalized]

    # Prefix match (e.g. deepseek-chat-20241201 -> deepseek-chat)
    for key, pricing in DEFAULT_MODEL_PRICING.items():
        if normalized.startswith(key):
            return pricing

    return DEFAULT_MODEL_PRICING["deepseek-chat"]
