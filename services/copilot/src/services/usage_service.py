"""
Copilot LLM Usage & Cost Tracking Service.
==========================================
Provides centralized, deterministic token extraction, exact Decimal-based cost calculation,
and immutable persistence of per-invocation LLM usage records.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Union
from loguru import logger

from services.copilot.src.core.pricing import (
    UnknownModelPricingError,
    get_model_pricing,
)
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
        request_time: Optional[datetime] = None,
    ) -> Decimal:
        """
        Calculates exact deterministic cost in USD using model pricing rates.
        All arithmetic is performed using exact Decimal precision.

        Cost Formula:
            cost = (cache_hit_tokens * cache_hit_rate / 1,000,000)
                 + (cache_miss_tokens * cache_miss_rate / 1,000,000)
                 + (completion_tokens * completion_rate / 1,000,000)
        """
        pricing = get_model_pricing(model, request_time=request_time)

        p_tokens = max(0, int(prompt_tokens))
        c_tokens = max(0, int(completion_tokens))
        c_hit = max(0, int(cache_hit_tokens))
        c_miss = max(0, int(cache_miss_tokens))

        # Reconcile prompt token partitions to prevent double-counting or gaps
        if c_hit == 0 and c_miss == 0:
            c_miss = p_tokens
        elif c_hit > 0 and c_miss == 0:
            c_miss = max(0, p_tokens - c_hit)
        elif (c_hit + c_miss != p_tokens) and p_tokens > 0:
            # When breakdown does not sum to prompt_tokens, enforce disjoint partition
            if c_hit <= p_tokens:
                c_miss = p_tokens - c_hit
            else:
                c_hit = p_tokens
                c_miss = 0

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
        - Missing cache fields default safely without errors.
        - Derived total_tokens if missing or zero.
        - Cache hit and cache miss tokens strictly partition prompt_tokens (no double counting).
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

        # Check prompt_tokens_details.cached_tokens (standard OpenAI/DeepSeek response structure)
        cached_from_details = 0
        if hasattr(raw_usage, "prompt_tokens_details") and raw_usage.prompt_tokens_details:
            cached_from_details = getattr(raw_usage.prompt_tokens_details, "cached_tokens", 0) or 0
        elif (
            isinstance(raw_usage, dict)
            and "prompt_tokens_details" in raw_usage
            and isinstance(raw_usage["prompt_tokens_details"], dict)
        ):
            cached_from_details = raw_usage["prompt_tokens_details"].get("cached_tokens", 0) or 0

        cache_hit_tokens = _get_val(
            "prompt_cache_hit_tokens",
            "cache_hit_tokens",
            "cached_tokens",
        ) or max(0, int(cached_from_details))

        cache_miss_tokens = _get_val(
            "prompt_cache_miss_tokens",
            "cache_miss_tokens",
        )

        # Derive total_tokens if missing or zero
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens

        # Ensure cache hit does not exceed prompt tokens
        if prompt_tokens > 0:
            if cache_hit_tokens > prompt_tokens:
                cache_hit_tokens = prompt_tokens
            if cache_miss_tokens == 0:
                cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)
            elif cache_hit_tokens + cache_miss_tokens != prompt_tokens:
                cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)

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
        request_time: Optional[datetime] = None,
    ) -> Optional[CopilotLLMUsageRecordModel]:
        """
        Records an immutable usage entry representing exactly ONE LLM invocation.

        Guarantees:
        - If usage is None or missing, logs a warning and does NOT create fake zero-cost records.
        - Computes exact Decimal cost using verified pricing schedules and UTC request timestamp.
        - Unknown model pricing errors log an explicit error and prevent recording fabricated costs.
        - Fail-safe execution: database errors are logged and will not crash live callers.
        - Returns the persisted CopilotLLMUsageRecordModel instance.
        """
        if usage is None:
            logger.warning(
                f"[UsageService] record_llm_usage called with usage=None for session {session_id} (stage={stage}). "
                f"No unverified zero-cost record created."
            )
            return None

        metrics = self.extract_usage_metrics(usage)

        # Protect against empty/unpopulated usage metadata (missing tokens)
        if (
            metrics["total_tokens"] == 0
            and metrics["prompt_tokens"] == 0
            and metrics["completion_tokens"] == 0
        ):
            logger.warning(
                f"[UsageService] Missing token metrics in usage object for session {session_id} (stage={stage}). "
                f"Incomplete usage record not persisted."
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

        # Resolve effective request timestamp for pricing tier determination
        req_dt = request_time or datetime.now(timezone.utc)
        if req_dt.tzinfo is None:
            req_dt = req_dt.replace(tzinfo=timezone.utc)

        try:
            cost_usd = self.calculate_cost(
                model=model,
                prompt_tokens=metrics["prompt_tokens"],
                completion_tokens=metrics["completion_tokens"],
                cache_hit_tokens=metrics["cache_hit_tokens"],
                cache_miss_tokens=metrics["cache_miss_tokens"],
                request_time=req_dt,
            )
        except UnknownModelPricingError as pricing_err:
            logger.error(
                f"[UsageService] Cannot record cost for unsupported model '{model}' in session {sid_uuid} (stage={stage}): "
                f"{pricing_err}"
            )
            return None

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
