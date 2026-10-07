import time
import datetime
from typing import Dict, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from loguru import logger

from services.auth.src.security import decode_access_token
from services.auth.src.models.user import UserModel, UserSessionModel

USER_ONLINE_THRESHOLD_SECONDS = 300
SESSION_STALE_TIMEOUT_SECONDS = 900
ACTIVITY_WRITE_THROTTLE_SECONDS = 60

# In-memory throttle map: key (session_id or user_id) -> last written timestamp (epoch seconds)
_activity_throttle_cache: Dict[str, float] = {}


def get_activity_throttle_cache() -> Dict[str, float]:
    return _activity_throttle_cache


def clear_activity_throttle_cache() -> None:
    _activity_throttle_cache.clear()


async def record_user_activity(user_id: str, session_id: Optional[str] = None, force: bool = False) -> bool:
    """
    Updates user.last_activity_at and session.last_seen_at.
    Throttles database writes using ACTIVITY_WRITE_THROTTLE_SECONDS unless force=True.
    """
    if not user_id:
        return False

    key = session_id or str(user_id)
    now_ts = time.time()
    last_write = _activity_throttle_cache.get(key, 0.0)

    if not force and (now_ts - last_write < ACTIVITY_WRITE_THROTTLE_SECONDS):
        return False

    _activity_throttle_cache[key] = now_ts
    now_dt = datetime.datetime.now(datetime.timezone.utc)

    try:
        await UserModel.filter(id=user_id).update(last_activity_at=now_dt)
        if session_id:
            await UserSessionModel.filter(session_id=session_id).update(
                last_seen_at=now_dt,
                is_active=True,
            )
        return True
    except Exception as e:
        logger.warning(f"[ActivityTracking] Failed to update activity for user {user_id}: {e}")
        return False


class ActivityTrackingMiddleware(BaseHTTPMiddleware):
    """
    Authenticated Middleware for tracking user presence and session last seen timestamps.
    Automatically updates user and session records with write throttling.
    Skips unauthenticated requests, health checks, static assets, and auth endpoints.
    """
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Skip paths: health checks, readiness, metrics, static assets, login, options
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or path.startswith(("/health", "/api/health", "/readiness", "/api/readiness", "/metrics", "/static"))
            or path in ("/api/auth/login", "/api/auth/heartbeat", "/api/auth/logout")
        ):
            return response

        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            try:
                payload = decode_access_token(token)
                user_id = payload.get("sub")
                session_id = payload.get("session_id")
                if user_id:
                    await record_user_activity(user_id=user_id, session_id=session_id, force=False)
            except Exception:
                # Do not disrupt request processing for token decoding issues in middleware
                pass

        return response
