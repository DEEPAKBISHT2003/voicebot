"""
Copilot LLM Usage & Cost Tracking Service.
==========================================
Provides centralized, deterministic token extraction, exact Decimal-based cost calculation,
and immutable persistence of per-invocation LLM usage records.
"""

import uuid
from decimal import Decimal
from typing import Any, Dict, Optional, Union
from loguru import logger

from services.copilot.src.core.pricing import get_model_pricing
from services.copilot.src.models.copilot import CopilotLLMUsageRecordModel


class CopilotUsageService:
    """
    Manages extraction, pricing calculation, and immutable persistence
    for all Copilot LLM invocations.
    """

    @staticmethod
    def calculate_cost(
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
    ) -> Decimal:
        """
        Calculates exact deterministic cost in USD using model pricing rates.
        All arithmetic is performed using exact Decimal precision.

        Cost Formula:
            cost = (cache_hit_tokens * cache_hit_rate)
                 + (cache_miss_tokens * cache_miss_rate)
                 + (completion_tokens * completion_rate)
        """
        pricing = get_model_pricing(model)

        p_tokens = max(0, int(prompt_tokens))
        c_tokens = max(0, int(completion_tokens))
        c_hit = max(0, int(cache_hit_tokens))
        c_miss = max(0, int(cache_miss_tokens))

        # If cache breakdown was not provided, treat all prompt tokens as cache misses
        if c_hit == 0 and c_miss == 0:
            c_miss = p_tokens

        cost = (
            (Decimal(str(c_hit)) * pricing.prompt_cache_hit_per_token)
            + (Decimal(str(c_miss)) * pricing.prompt_cache_miss_per_token)
            + (Decimal(str(c_tokens)) * pricing.completion_per_token)
        )
        return cost

    @staticmethod
    def extract_usage_metrics(raw_usage: Any) -> Dict[str, int]:
        """
        Safely extracts token counts from OpenAI-compatible completion usage objects,
        dictionaries, or generic response objects.

        Guarantees:
        - Non-negative integer values for all fields.
        - Missing cache fields default safely to 0 without errors.
        - Derived total_tokens if missing or zero.
        - Cache miss derivation if cache_hit is present.
        """
        if not raw_usage:
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
            }

        # Helper to read from dict or attribute
        def _get_val(*keys: str) -> int:
            for k in keys:
                if isinstance(raw_usage, dict) and k in raw_usage:
                    val = raw_usage[k]
                    if val is not None:
                        try:
                            return max(0, int(val))
                        except (ValueError, TypeError):
                            pass
                elif hasattr(raw_usage, k):
                    val = getattr(raw_usage, k)
                    if val is not None:
                        try:
                            return max(0, int(val))
                        except (ValueError, TypeError):
                            pass
            return 0

        prompt_tokens = _get_val("prompt_tokens", "input_tokens")
        completion_tokens = _get_val("completion_tokens", "output_tokens")
        total_tokens = _get_val("total_tokens")
        cache_hit_tokens = _get_val(
            "prompt_cache_hit_tokens",
            "cache_hit_tokens",
            "cached_tokens",
        )
        cache_miss_tokens = _get_val(
            "prompt_cache_miss_tokens",
            "cache_miss_tokens",
        )

        # Derive total_tokens if missing or zero
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens

        # Derive cache_miss_tokens if not explicitly provided
        if cache_miss_tokens == 0 and prompt_tokens > 0:
            if cache_hit_tokens > 0:
                cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)
            else:
                cache_miss_tokens = prompt_tokens

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_miss_tokens": cache_miss_tokens,
        }

    async def record_llm_usage(
        self,
        session_id: Union[str, uuid.UUID],
        stage: str,
        model: str,
        usage: Any,
        duration_ms: int = 0,
        call_identifier: Optional[str] = None,
    ) -> Optional[CopilotLLMUsageRecordModel]:
        """
        Records an immutable usage entry representing exactly ONE LLM invocation.

        Guarantees:
        - If usage is None or missing, logs a warning and does NOT create fake records.
        - Computes exact Decimal cost.
        - Fail-safe execution: database errors are logged and will not crash live callers.
        - Returns the persisted CopilotLLMUsageRecordModel instance.
        """
        if usage is None:
            logger.warning(
                f"[UsageService] record_llm_usage called with usage=None for session {session_id} (stage={stage})."
            )
            return None

        try:
            # Parse session_id to UUID (with deterministic uuid5 fallback for test/string IDs)
            if isinstance(session_id, uuid.UUID):
                sid_uuid = session_id
            else:
                try:
                    sid_uuid = uuid.UUID(str(session_id))
                except (ValueError, TypeError):
                    sid_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, str(session_id))
        except Exception as parse_err:
            logger.error(f"[UsageService] Invalid session_id '{session_id}': {parse_err}")
            return None

        metrics = self.extract_usage_metrics(usage)
        cost_usd = self.calculate_cost(
            model=model,
            prompt_tokens=metrics["prompt_tokens"],
            completion_tokens=metrics["completion_tokens"],
            cache_hit_tokens=metrics["cache_hit_tokens"],
            cache_miss_tokens=metrics["cache_miss_tokens"],
        )

        try:
            record = await CopilotLLMUsageRecordModel.create(
                session_id=sid_uuid,
                stage=str(stage)[:64],
                call_identifier=str(call_identifier)[:128] if call_identifier else None,
                model=str(model)[:64],
                prompt_tokens=metrics["prompt_tokens"],
                completion_tokens=metrics["completion_tokens"],
                total_tokens=metrics["total_tokens"],
                cache_hit_tokens=metrics["cache_hit_tokens"],
                cache_miss_tokens=metrics["cache_miss_tokens"],
                cost_usd=cost_usd,
                duration_ms=max(0, int(duration_ms)),
            )

            logger.info(
                f"[USAGE_RECORDED] session_id={sid_uuid} stage={stage} call_id={call_identifier} "
                f"tokens={metrics['total_tokens']} (hit={metrics['cache_hit_tokens']}, miss={metrics['cache_miss_tokens']}, out={metrics['completion_tokens']}) "
                f"cost=${cost_usd:.6f} duration={duration_ms}ms"
            )
            return record

        except Exception as db_err:
            logger.error(
                f"[UsageService] Error persisting usage record for session {sid_uuid} (stage={stage}): {db_err}"
            )
            return None


# Global singleton instance for usage across the service
usage_service = CopilotUsageService()
