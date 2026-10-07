import math
import datetime
from collections import Counter
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from tortoise.expressions import Q

from services.auth.src.models.user import UserModel, UserSessionModel
from services.auth.src.deps import require_admin
from services.auth.src.schemas import (
    UserActivityResponse,
    UserDetailWithSessionsResponse,
)
from services.auth.src.middleware import (
    USER_ONLINE_THRESHOLD_SECONDS,
    SESSION_STALE_TIMEOUT_SECONDS,
)

router = APIRouter(prefix="/api/admin/users", tags=["Admin User Activity"])


@router.get("/activity", response_model=UserActivityResponse)
async def get_users_activity(
    status: Optional[Literal["online", "offline"]] = None,
    role: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: Optional[str] = Query("last_activity", description="Sort field: last_login, last_activity, email, created_at"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    current_admin: UserModel = Depends(require_admin),
):
    """
    Admin-only: Retrieve user presence overview, summary statistics, and paginated user activities.
    Protects against N+1 queries by aggregating session counts at SQL level.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    online_cutoff = now - datetime.timedelta(seconds=USER_ONLINE_THRESHOLD_SECONDS)
    stale_cutoff = now - datetime.timedelta(seconds=SESSION_STALE_TIMEOUT_SECONDS)

    # 1. Summary Cards Aggregations
    total_users = await UserModel.all().count()
    online_users = await UserModel.filter(last_activity_at__gte=online_cutoff, is_active=True).count()
    offline_users = max(0, total_users - online_users)
    active_sessions = await UserSessionModel.filter(is_active=True, last_seen_at__gte=stale_cutoff).count()

    summary = {
        "total_users": total_users,
        "online_users": online_users,
        "offline_users": offline_users,
        "active_sessions": active_sessions,
    }

    # 2. Build filtered user query
    query = UserModel.all()

    if search:
        search_term = search.strip()
        query = query.filter(Q(email__icontains=search_term) | Q(name__icontains=search_term))

    if role:
        query = query.filter(role__iexact=role.strip())

    if status == "online":
        query = query.filter(last_activity_at__gte=online_cutoff, is_active=True)
    elif status == "offline":
        query = query.filter(Q(last_activity_at__lt=online_cutoff) | Q(last_activity_at__isnull=True) | Q(is_active=False))

    # 3. Sorting mapping
    sort_field_map = {
        "last_login": "last_login_at",
        "last_activity": "last_activity_at",
        "email": "email",
        "created_at": "created_at",
    }
    field_name = sort_field_map.get(sort_by, "last_activity_at")
    order_clause = f"-{field_name}" if sort_order.lower() == "desc" else field_name

    # 4. Total count for pagination
    total_items = await query.count()
    total_pages = max(1, math.ceil(total_items / page_size))
    offset = (page - 1) * page_size

    # 5. Fetch paginated users (SQL-level LIMIT and OFFSET)
    users = await query.order_by(order_clause).offset(offset).limit(page_size)

    # 6. Fetch active session counts for this page in a single query (zero N+1)
    user_ids = [u.id for u in users]
    session_counts = Counter()
    if user_ids:
        active_session_rows = await UserSessionModel.filter(
            user_id__in=user_ids,
            is_active=True,
            last_seen_at__gte=stale_cutoff,
        ).values("user_id")
        session_counts = Counter(str(r["user_id"]) for r in active_session_rows)

    items = []
    for u in users:
        is_online = bool(
            u.is_active
            and u.last_activity_at
            and (now - u.last_activity_at).total_seconds() <= USER_ONLINE_THRESHOLD_SECONDS
        )
        display_name = u.name or (u.email.split("@")[0].capitalize() if u.email else "User")
        items.append({
            "user_id": str(u.id),
            "name": display_name,
            "email": u.email,
            "role": u.role,
            "is_online": is_online,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "last_activity_at": u.last_activity_at.isoformat() if u.last_activity_at else None,
            "active_session_count": session_counts.get(str(u.id), 0),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return {
        "summary": summary,
        "pagination": {
            "total_items": total_items,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
        },
        "items": items,
    }


@router.get("/{user_id}/sessions", response_model=UserDetailWithSessionsResponse)
async def get_user_sessions(
    user_id: str,
    current_admin: UserModel = Depends(require_admin),
):
    """
    Admin-only: Retrieve detailed user activity profile and full session history.
    Never exposes passwords, hashes, tokens, or secrets.
    """
    target_user = await UserModel.get_or_none(id=user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found.",
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    online_cutoff = now - datetime.timedelta(seconds=USER_ONLINE_THRESHOLD_SECONDS)
    stale_cutoff = now - datetime.timedelta(seconds=SESSION_STALE_TIMEOUT_SECONDS)

    # Fetch sessions for this user, ordered by login_at descending
    sessions = await UserSessionModel.filter(user_id=target_user.id).order_by("-login_at").limit(50)

    # Active session count (non-stale and active)
    active_count = await UserSessionModel.filter(
        user_id=target_user.id,
        is_active=True,
        last_seen_at__gte=stale_cutoff,
    ).count()

    is_online = bool(
        target_user.is_active
        and target_user.last_activity_at
        and (now - target_user.last_activity_at).total_seconds() <= USER_ONLINE_THRESHOLD_SECONDS
    )
    display_name = target_user.name or (target_user.email.split("@")[0].capitalize() if target_user.email else "User")

    session_items = []
    for s in sessions:
        if not s.is_active or s.logout_at is not None:
            session_status = "Logged Out"
        elif (now - s.last_seen_at).total_seconds() > SESSION_STALE_TIMEOUT_SECONDS:
            session_status = "Stale"
        else:
            session_status = "Active"

        session_items.append({
            "id": str(s.id),
            "session_id": s.session_id,
            "login_at": s.login_at.isoformat() if s.login_at else "",
            "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else "",
            "logout_at": s.logout_at.isoformat() if s.logout_at else None,
            "is_active": s.is_active,
            "status": session_status,
        })

    return {
        "user": {
            "user_id": str(target_user.id),
            "name": display_name,
            "email": target_user.email,
            "role": target_user.role,
            "is_online": is_online,
            "last_login_at": target_user.last_login_at.isoformat() if target_user.last_login_at else None,
            "last_activity_at": target_user.last_activity_at.isoformat() if target_user.last_activity_at else None,
            "active_session_count": active_count,
            "created_at": target_user.created_at.isoformat() if target_user.created_at else None,
        },
        "sessions": session_items,
    }
