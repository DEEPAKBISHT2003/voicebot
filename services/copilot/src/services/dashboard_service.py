"""
Copilot Usage & Credit Dashboard Service.
=========================================
Provides:
1. DeepSeek balance retrieval with in-memory TTL caching and safe error isolation.
2. Global usage aggregation across sessions and stages.
3. Paginated session usage summaries with meeting duration and token/cost rollups.
4. Detailed per-session breakdown with stage-level aggregation and chronological immutable call records.
"""

import time
import uuid
import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx
from loguru import logger
from tortoise.functions import Sum, Count

from services.copilot.src.core.config import Settings
from services.copilot.src.models.copilot import (
    CopilotSessionModel,
    CopilotLLMUsageRecordModel,
)


class CopilotDashboardUsageService:
    """
    Manages usage aggregation and external DeepSeek credit balance queries.
    """

    def __init__(self, cache_ttl_seconds: float = 60.0):
        self._cache_ttl_seconds: float = cache_ttl_seconds
        self._cached_balance: Optional[Dict[str, Any]] = None
        self._cache_expiry_monotonic: float = 0.0

    def clear_balance_cache(self) -> None:
        """Utility for testing: resets the in-memory balance cache."""
        self._cached_balance = None
        self._cache_expiry_monotonic = 0.0

    async def get_deepseek_balance(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Retrieves current DeepSeek credit/account balance with in-memory TTL caching.
        
        Guarantees:
        - Never logs or exposes API keys or Authorization headers.
        - Uses short HTTP timeout (5.0s).
        - Returns normalized response shape.
        - Isolates failures to never throw unhandled exceptions to callers.
        - Returns cached response if within TTL.
        """
        now_mono = time.monotonic()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. Return cached balance if still valid and not forcing refresh
        if not force_refresh and self._cached_balance is not None and now_mono < self._cache_expiry_monotonic:
            cached_copy = dict(self._cached_balance)
            cached_copy["cached"] = True
            return cached_copy

        api_key = Settings.DEEPSEEK_API_KEY
        if not api_key:
            logger.warning("[UsageDashboard] DeepSeek API key is not configured.")
            return {
                "is_available": False,
                "balance": None,
                "currency": "USD",
                "total_balance": None,
                "granted_balance": None,
                "topped_up_balance": None,
                "balance_infos": [],
                "error": "DeepSeek API key is not configured",
                "source": "deepseek",
                "cached": False,
                "retrieved_at": now_iso,
            }

        base_url = (Settings.DEEPSEEK_BASE_URL or "https://api.deepseek.com").rstrip("/")
        balance_url = f"{base_url}/user/balance"

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(balance_url, headers=headers)

            if resp.status_code != 200:
                logger.warning(
                    f"[UsageDashboard] DeepSeek balance endpoint returned HTTP {resp.status_code}"
                )
                if self._cached_balance is not None:
                    stale_copy = dict(self._cached_balance)
                    stale_copy["cached"] = True
                    stale_copy["stale"] = True
                    return stale_copy
                return {
                    "is_available": False,
                    "balance": None,
                    "currency": "USD",
                    "total_balance": None,
                    "granted_balance": None,
                    "topped_up_balance": None,
                    "balance_infos": [],
                    "error": f"Provider error (HTTP {resp.status_code})",
                    "source": "deepseek",
                    "cached": False,
                    "retrieved_at": now_iso,
                }

            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("Malformed balance response: expected JSON object")

            is_avail = bool(data.get("is_available", True))
            balance_infos = data.get("balance_infos", [])

            # Parse primary balance
            primary_balance_val: Optional[float] = None
            primary_currency: str = "USD"
            total_bal_str: Optional[str] = None
            granted_bal_str: Optional[str] = None
            topped_bal_str: Optional[str] = None

            if isinstance(balance_infos, list) and len(balance_infos) > 0:
                # Prefer USD entry if present
                selected_info = balance_infos[0]
                for info in balance_infos:
                    if isinstance(info, dict) and info.get("currency") == "USD":
                        selected_info = info
                        break

                if isinstance(selected_info, dict):
                    primary_currency = selected_info.get("currency", "USD")
                    total_bal_str = str(selected_info.get("total_balance", "0"))
                    granted_bal_str = str(selected_info.get("granted_balance", "0"))
                    topped_bal_str = str(selected_info.get("topped_up_balance", "0"))
                    try:
                        primary_balance_val = float(total_bal_str)
                    except (ValueError, TypeError):
                        primary_balance_val = 0.0
            elif "total_balance" in data or "balance" in data:
                raw_bal = data.get("total_balance", data.get("balance"))
                total_bal_str = str(raw_bal) if raw_bal is not None else None
                primary_currency = str(data.get("currency", "USD"))
                try:
                    primary_balance_val = float(raw_bal) if raw_bal is not None else None
                except (ValueError, TypeError):
                    primary_balance_val = None

            normalized = {
                "is_available": is_avail,
                "balance": primary_balance_val,
                "currency": primary_currency,
                "total_balance": total_bal_str,
                "granted_balance": granted_bal_str,
                "topped_up_balance": topped_bal_str,
                "balance_infos": balance_infos if isinstance(balance_infos, list) else [],
                "source": "deepseek",
                "cached": False,
                "retrieved_at": now_iso,
            }

            # Cache successful response
            self._cached_balance = dict(normalized)
            self._cache_expiry_monotonic = now_mono + self._cache_ttl_seconds
            return normalized

        except httpx.TimeoutException:
            logger.warning("[UsageDashboard] Timeout querying DeepSeek balance.")
            if self._cached_balance is not None:
                stale_copy = dict(self._cached_balance)
                stale_copy["cached"] = True
                stale_copy["stale"] = True
                return stale_copy
            return {
                "is_available": False,
                "balance": None,
                "currency": "USD",
                "total_balance": None,
                "granted_balance": None,
                "topped_up_balance": None,
                "balance_infos": [],
                "error": "Request timed out",
                "source": "deepseek",
                "cached": False,
                "retrieved_at": now_iso,
            }
        except Exception as err:
            logger.warning(f"[UsageDashboard] Error retrieving DeepSeek balance: {err}")
            if self._cached_balance is not None:
                stale_copy = dict(self._cached_balance)
                stale_copy["cached"] = True
                stale_copy["stale"] = True
                return stale_copy
            return {
                "is_available": False,
                "balance": None,
                "currency": "USD",
                "total_balance": None,
                "granted_balance": None,
                "topped_up_balance": None,
                "balance_infos": [],
                "error": f"Failed to retrieve balance: {type(err).__name__}",
                "source": "deepseek",
                "cached": False,
                "retrieved_at": now_iso,
            }

    async def get_usage_summary(self) -> Dict[str, Any]:
        """
        Calculates global dashboard-level usage totals from immutable records and sessions.
        """
        # 1. Aggregate usage metrics across all LLM usage records
        all_records = await CopilotLLMUsageRecordModel.all()

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_cost_dec = Decimal("0.000000")
        total_llm_calls = len(all_records)

        # Stage level map
        stage_map: Dict[str, Dict[str, Any]] = {}
        sessions_with_usage_set = set()

        for rec in all_records:
            p_tok = int(rec.prompt_tokens or 0)
            c_tok = int(rec.completion_tokens or 0)
            t_tok = int(rec.total_tokens or 0)
            cost_d = Decimal(str(rec.cost_usd or 0))
            dur_ms = int(rec.duration_ms or 0)

            total_prompt_tokens += p_tok
            total_completion_tokens += c_tok
            total_tokens += t_tok
            total_cost_dec += cost_d

            if rec.session_id:
                sessions_with_usage_set.add(str(rec.session_id))

            st = rec.stage or "unknown"
            if st not in stage_map:
                stage_map[st] = {
                    "stage": st,
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_dec": Decimal("0.000000"),
                    "duration_ms": 0,
                }
            stage_map[st]["calls"] += 1
            stage_map[st]["prompt_tokens"] += p_tok
            stage_map[st]["completion_tokens"] += c_tok
            stage_map[st]["total_tokens"] += t_tok
            stage_map[st]["cost_dec"] += cost_d
            stage_map[st]["duration_ms"] += dur_ms

        stage_breakdown = []
        for st_name, sdata in stage_map.items():
            stage_breakdown.append({
                "stage": st_name,
                "calls": sdata["calls"],
                "prompt_tokens": sdata["prompt_tokens"],
                "completion_tokens": sdata["completion_tokens"],
                "total_tokens": sdata["total_tokens"],
                "cost_usd": float(round(sdata["cost_dec"], 6)),
                "duration_ms": sdata["duration_ms"],
            })

        # Sort stages deterministically by cost descending, then stage name
        stage_breakdown.sort(key=lambda x: (-x["cost_usd"], x["stage"]))

        # 2. Aggregate session counts and meeting durations
        all_sessions = await CopilotSessionModel.all()
        total_sessions = len(all_sessions)
        total_meeting_seconds = 0

        for sess in all_sessions:
            if sess.meeting_duration_seconds is not None and sess.meeting_duration_seconds > 0:
                total_meeting_seconds += int(sess.meeting_duration_seconds)

        total_meeting_minutes = round(total_meeting_seconds / 60.0, 1)

        return {
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_cost_usd": float(round(total_cost_dec, 6)),
            "total_llm_calls": total_llm_calls,
            "total_meeting_duration_seconds": total_meeting_seconds,
            "total_meeting_duration_minutes": total_meeting_minutes,
            "total_sessions": total_sessions,
            "sessions_with_usage": len(sessions_with_usage_set),
            "stage_breakdown": stage_breakdown,
        }

    async def get_session_usage_list(
        self,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Provides paginated session-level usage list.
        """
        if page < 1:
            raise ValueError("Page must be greater than or equal to 1")
        if limit < 1 or limit > 100:
            raise ValueError("Limit must be between 1 and 100")

        total_sessions = await CopilotSessionModel.all().count()
        total_pages = (total_sessions + limit - 1) // limit if total_sessions > 0 else 0

        offset = (page - 1) * limit
        # Order by session timestamp descending so newest sessions always appear first
        sessions = (
            await CopilotSessionModel.all()
            .order_by("-timestamp")
            .offset(offset)
            .limit(limit)
        )

        if not sessions:
            return {
                "items": [],
                "page": page,
                "limit": limit,
                "total": total_sessions,
                "total_pages": total_pages,
            }

        session_ids = [s.session_id for s in sessions]

        # Batch fetch usage records for this page of sessions
        records = await CopilotLLMUsageRecordModel.filter(session_id__in=session_ids).all()
        session_usage_map: Dict[str, Dict[str, Any]] = {}
        for r in records:
            sid_str = str(r.session_id)
            if sid_str not in session_usage_map:
                session_usage_map[sid_str] = {
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_dec": Decimal("0.000000"),
                    "llm_call_count": 0,
                }
            session_usage_map[sid_str]["total_tokens"] += int(r.total_tokens or 0)
            session_usage_map[sid_str]["prompt_tokens"] += int(r.prompt_tokens or 0)
            session_usage_map[sid_str]["completion_tokens"] += int(r.completion_tokens or 0)
            session_usage_map[sid_str]["cost_dec"] += Decimal(str(r.cost_usd or 0))
            session_usage_map[sid_str]["llm_call_count"] += 1

        items = []
        for s in sessions:
            sid_str = str(s.session_id)
            usage_data = session_usage_map.get(sid_str, {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_dec": Decimal("0.000000"),
                "llm_call_count": 0,
            })

            dur_sec = s.meeting_duration_seconds
            dur_min = round(dur_sec / 60.0, 1) if dur_sec is not None else None

            items.append({
                "session_id": sid_str,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "meeting_started_at": s.meeting_started_at.isoformat() if s.meeting_started_at else None,
                "meeting_ended_at": s.meeting_ended_at.isoformat() if s.meeting_ended_at else None,
                "meeting_duration_seconds": dur_sec,
                "meeting_duration_minutes": dur_min,
                "total_tokens": usage_data["total_tokens"],
                "prompt_tokens": usage_data["prompt_tokens"],
                "completion_tokens": usage_data["completion_tokens"],
                "total_cost_usd": float(round(usage_data["cost_dec"], 6)),
                "llm_call_count": usage_data["llm_call_count"],
            })

        return {
            "items": items,
            "page": page,
            "limit": limit,
            "total": total_sessions,
            "total_pages": total_pages,
        }

    async def get_session_usage_detail(self, session_id: Union[str, uuid.UUID]) -> Optional[Dict[str, Any]]:
        """
        Provides complete session detail with stage breakdown and individual immutable call records.
        """
        try:
            if isinstance(session_id, uuid.UUID):
                sid_uuid = session_id
            else:
                sid_uuid = uuid.UUID(str(session_id))
        except (ValueError, TypeError):
            return None

        session = await CopilotSessionModel.get_or_none(session_id=sid_uuid)
        if not session:
            return None

        # Fetch immutable records in chronological order
        records = await CopilotLLMUsageRecordModel.filter(session_id=sid_uuid).order_by("created_at")

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_cost_dec = Decimal("0.000000")

        stage_map: Dict[str, Dict[str, Any]] = {}
        calls_list = []

        for r in records:
            p_tok = int(r.prompt_tokens or 0)
            c_tok = int(r.completion_tokens or 0)
            t_tok = int(r.total_tokens or 0)
            cost_d = Decimal(str(r.cost_usd or 0))
            dur_ms = int(r.duration_ms or 0)

            total_prompt_tokens += p_tok
            total_completion_tokens += c_tok
            total_tokens += t_tok
            total_cost_dec += cost_d

            st = r.stage or "unknown"
            if st not in stage_map:
                stage_map[st] = {
                    "stage": st,
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_dec": Decimal("0.000000"),
                    "duration_ms": 0,
                }
            stage_map[st]["calls"] += 1
            stage_map[st]["prompt_tokens"] += p_tok
            stage_map[st]["completion_tokens"] += c_tok
            stage_map[st]["total_tokens"] += t_tok
            stage_map[st]["cost_dec"] += cost_d
            stage_map[st]["duration_ms"] += dur_ms

            calls_list.append({
                "id": str(r.id),
                "stage": r.stage,
                "call_identifier": r.call_identifier,
                "model": r.model,
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": t_tok,
                "cache_hit_tokens": int(r.cache_hit_tokens or 0),
                "cache_miss_tokens": int(r.cache_miss_tokens or 0),
                "cost_usd": float(round(cost_d, 6)),
                "duration_ms": dur_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        stage_breakdown = []
        for st_name, sdata in stage_map.items():
            stage_breakdown.append({
                "stage": st_name,
                "calls": sdata["calls"],
                "prompt_tokens": sdata["prompt_tokens"],
                "completion_tokens": sdata["completion_tokens"],
                "total_tokens": sdata["total_tokens"],
                "cost_usd": float(round(sdata["cost_dec"], 6)),
                "duration_ms": sdata["duration_ms"],
            })
        stage_breakdown.sort(key=lambda x: (-x["cost_usd"], x["stage"]))

        dur_sec = session.meeting_duration_seconds
        dur_min = round(dur_sec / 60.0, 1) if dur_sec is not None else None

        return {
            "session_id": str(session.session_id),
            "timestamp": session.timestamp.isoformat() if session.timestamp else None,
            "meeting_started_at": session.meeting_started_at.isoformat() if session.meeting_started_at else None,
            "meeting_ended_at": session.meeting_ended_at.isoformat() if session.meeting_ended_at else None,
            "meeting_duration_seconds": dur_sec,
            "meeting_duration_minutes": dur_min,
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_cost_usd": float(round(total_cost_dec, 6)),
            "llm_call_count": len(records),
            "stage_breakdown": stage_breakdown,
            "calls": calls_list,
        }


# Global dashboard service singleton
dashboard_service = CopilotDashboardUsageService()
