import os
import uuid
import datetime
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from tortoise import Tortoise

# Ensure JWT secret is set for tests
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-admin-activity-dashboard-12345"
os.environ["JWT_ALGORITHM"] = "HS256"

from services.main import app
from services.auth.src.models.user import UserModel, UserSessionModel
from services.auth.src.security import hash_password, create_access_token, decode_access_token
from services.auth.src.middleware import (
    USER_ONLINE_THRESHOLD_SECONDS,
    SESSION_STALE_TIMEOUT_SECONDS,
    clear_activity_throttle_cache,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={
            "models": [
                "services.auth.src.models.user",
                "services.copilot.src.models.copilot",
                "services.interview.src.models.interview",
            ]
        },
    )
    await Tortoise.generate_schemas()
    clear_activity_throttle_cache()
    yield
    await Tortoise.close_connections()


@pytest_asyncio.fixture
async def admin_user():
    user = await UserModel.create(
        email="admin@appzlogic.com",
        name="Admin User",
        password_hash=hash_password("AdminPass123"),
        role="ADMIN",
        is_active=True,
    )
    return user


@pytest_asyncio.fixture
async def regular_user():
    user = await UserModel.create(
        email="john@appzlogic.com",
        name="John Doe",
        password_hash=hash_password("UserPass123"),
        role="USER",
        is_active=True,
    )
    return user


@pytest.mark.asyncio
async def test_login_creates_session_and_embeds_jwt(regular_user):
    """Test that login creates a UserSession and includes session_id in the JWT payload."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/login",
            json={"email": "john@appzlogic.com", "password": "UserPass123"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "session_id" in data
        session_id = data["session_id"]
        assert session_id is not None

        # Verify JWT payload contains session_id
        payload = decode_access_token(data["access_token"])
        assert payload["sub"] == str(regular_user.id)
        assert payload["session_id"] == session_id
        assert payload["role"] == "USER"

        # Verify DB session created
        session = await UserSessionModel.get_or_none(session_id=session_id)
        assert session is not None
        assert session.user_id == regular_user.id
        assert session.is_active is True
        assert session.login_at is not None
        assert session.last_seen_at is not None

        # Verify user last_login_at and last_activity_at updated
        u = await UserModel.get(id=regular_user.id)
        assert u.last_login_at is not None
        assert u.last_activity_at is not None


@pytest.mark.asyncio
async def test_logout_closes_specific_session(regular_user):
    """Test that logout terminates only the current session, leaving other sessions intact."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login first session (e.g. desktop)
        res1 = await client.post(
            "/api/auth/login",
            json={"email": "john@appzlogic.com", "password": "UserPass123"},
        )
        token1 = res1.json()["access_token"]
        session1_id = res1.json()["session_id"]

        # 2. Login second session (e.g. mobile)
        res2 = await client.post(
            "/api/auth/login",
            json={"email": "john@appzlogic.com", "password": "UserPass123"},
        )
        token2 = res2.json()["access_token"]
        session2_id = res2.json()["session_id"]

        assert session1_id != session2_id

        # 3. Logout session 1
        logout_res = await client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert logout_res.status_code == 200
        assert logout_res.json()["status"] == "ok"

        # Session 1 must be inactive with logout_at
        s1 = await UserSessionModel.get(session_id=session1_id)
        assert s1.is_active is False
        assert s1.logout_at is not None

        # Session 2 must remain active
        s2 = await UserSessionModel.get(session_id=session2_id)
        assert s2.is_active is True
        assert s2.logout_at is None


@pytest.mark.asyncio
async def test_heartbeat_updates_activity(regular_user):
    """Test that heartbeat endpoint updates last_activity_at and session last_seen_at."""
    session_id = str(uuid.uuid4())
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)
    regular_user.last_activity_at = past
    await regular_user.save()

    session = await UserSessionModel.create(
        user_id=regular_user.id,
        session_id=session_id,
        login_at=past,
        last_seen_at=past,
        is_active=True,
    )

    token = create_access_token({
        "sub": str(regular_user.id),
        "email": regular_user.email,
        "role": regular_user.role,
        "session_id": session_id,
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/auth/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

        # Check DB updated to current time
        updated_user = await UserModel.get(id=regular_user.id)
        assert (datetime.datetime.now(datetime.timezone.utc) - updated_user.last_activity_at).total_seconds() < 5

        updated_session = await UserSessionModel.get(session_id=session_id)
        assert (datetime.datetime.now(datetime.timezone.utc) - updated_session.last_seen_at).total_seconds() < 5


@pytest.mark.asyncio
async def test_stale_session_and_online_status_calculation(regular_user, admin_user):
    """Test online status (<=300s) and stale session (>900s) calculation in Admin API."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. Admin is online (activity 30 seconds ago) with 1 active fresh session
    admin_user.last_activity_at = now - datetime.timedelta(seconds=30)
    await admin_user.save()
    await UserSessionModel.create(
        user_id=admin_user.id,
        session_id=str(uuid.uuid4()),
        login_at=now - datetime.timedelta(minutes=10),
        last_seen_at=now - datetime.timedelta(seconds=30),
        is_active=True,
    )

    # 2. Regular user is offline (activity 400 seconds ago) with 1 stale session (1000s ago)
    regular_user.last_activity_at = now - datetime.timedelta(seconds=400)
    await regular_user.save()
    await UserSessionModel.create(
        user_id=regular_user.id,
        session_id=str(uuid.uuid4()),
        login_at=now - datetime.timedelta(minutes=30),
        last_seen_at=now - datetime.timedelta(seconds=1000),  # Stale (> 900s)
        is_active=True,
    )

    admin_token = create_access_token({
        "sub": str(admin_user.id),
        "email": admin_user.email,
        "role": admin_user.role,
        "session_id": "admin-session-1",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/admin/users/activity",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()

        # Summary check
        assert data["summary"]["total_users"] == 2
        assert data["summary"]["online_users"] == 1
        assert data["summary"]["offline_users"] == 1
        assert data["summary"]["active_sessions"] == 1  # Only 1 active, because regular user's session is stale

        # Items check
        items_by_email = {u["email"]: u for u in data["items"]}
        assert items_by_email["admin@appzlogic.com"]["is_online"] is True
        assert items_by_email["admin@appzlogic.com"]["active_session_count"] == 1

        assert items_by_email["john@appzlogic.com"]["is_online"] is False
        assert items_by_email["john@appzlogic.com"]["active_session_count"] == 0  # Stale session does not count as active


@pytest.mark.asyncio
async def test_admin_api_sessions_history_detail(regular_user, admin_user):
    """Test user session history endpoint returns accurate Active, Logged Out, and Stale statuses."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Active session
    await UserSessionModel.create(
        user_id=regular_user.id,
        session_id="session-active",
        login_at=now - datetime.timedelta(minutes=5),
        last_seen_at=now - datetime.timedelta(seconds=20),
        is_active=True,
    )
    # Logged Out session
    await UserSessionModel.create(
        user_id=regular_user.id,
        session_id="session-logged-out",
        login_at=now - datetime.timedelta(hours=2),
        last_seen_at=now - datetime.timedelta(hours=1),
        logout_at=now - datetime.timedelta(hours=1),
        is_active=False,
    )
    # Stale session (crashed tab, >900s)
    await UserSessionModel.create(
        user_id=regular_user.id,
        session_id="session-stale",
        login_at=now - datetime.timedelta(hours=5),
        last_seen_at=now - datetime.timedelta(seconds=1200),
        is_active=True,
    )

    admin_token = create_access_token({
        "sub": str(admin_user.id),
        "email": admin_user.email,
        "role": admin_user.role,
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/admin/users/{regular_user.id}/sessions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["user"]["email"] == "john@appzlogic.com"
        assert len(data["sessions"]) == 3

        statuses = {s["session_id"]: s["status"] for s in data["sessions"]}
        assert statuses["session-active"] == "Active"
        assert statuses["session-logged-out"] == "Logged Out"
        assert statuses["session-stale"] == "Stale"


@pytest.mark.asyncio
async def test_admin_api_pagination_search_and_filters(admin_user):
    """Test search, filtering by status and role, and pagination in Admin Activity API."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Create multiple users
    for i in range(15):
        is_online = i % 2 == 0
        role = "ADMIN" if i < 3 else "USER"
        u = await UserModel.create(
            email=f"user{i}@test.com",
            name=f"User {i}",
            password_hash=hash_password("Pass1234"),
            role=role,
            is_active=True,
            last_activity_at=now - datetime.timedelta(seconds=60 if is_online else 600),
            last_login_at=now - datetime.timedelta(days=i),
        )
        if is_online:
            await UserSessionModel.create(
                user_id=u.id,
                session_id=str(uuid.uuid4()),
                login_at=now,
                last_seen_at=now,
                is_active=True,
            )

    admin_token = create_access_token({
        "sub": str(admin_user.id),
        "email": admin_user.email,
        "role": admin_user.role,
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test Pagination (page=1, page_size=5)
        res_page1 = await client.get(
            "/api/admin/users/activity?page=1&page_size=5",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_page1.status_code == 200
        p1 = res_page1.json()
        assert len(p1["items"]) == 5
        assert p1["pagination"]["total_items"] == 16  # 15 users + admin_user
        assert p1["pagination"]["total_pages"] == 4

        # Test Search by email substring
        res_search = await client.get(
            "/api/admin/users/activity?search=user1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_search.status_code == 200
        search_data = res_search.json()
        for item in search_data["items"]:
            assert "user1" in item["email"] or "user1" in item["name"].lower()

        # Test Filter by status=online
        res_online = await client.get(
            "/api/admin/users/activity?status=online",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_online.status_code == 200
        for item in res_online.json()["items"]:
            assert item["is_online"] is True

        # Test Filter by role=admin
        res_role = await client.get(
            "/api/admin/users/activity?role=admin",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_role.status_code == 200
        for item in res_role.json()["items"]:
            assert item["role"] == "ADMIN"


@pytest.mark.asyncio
async def test_rbac_non_admin_forbidden(regular_user):
    """Test that non-admin receives 403 Forbidden on all activity admin endpoints."""
    user_token = create_access_token({
        "sub": str(regular_user.id),
        "email": regular_user.email,
        "role": regular_user.role,
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Activity overview endpoint
        res1 = await client.get(
            "/api/admin/users/activity",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert res1.status_code == 403
        assert "Administrative privileges required" in res1.json()["detail"]

        # 2. Session history endpoint
        res2 = await client.get(
            f"/api/admin/users/{regular_user.id}/sessions",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert res2.status_code == 403
        assert "Administrative privileges required" in res2.json()["detail"]
