import os
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from tortoise.contrib.fastapi import register_tortoise
from loguru import logger

# Load environment variables
load_dotenv(override=True)

from services.interview.src.core.config import Settings as InterviewSettings
from services.copilot.src.core.config import Settings as CopilotSettings

# Validate settings
InterviewSettings.validate()
CopilotSettings.validate()

from services.interview.src.api.interviews import router as interviews_router, get_recording, get_resume
from services.copilot.src.router import router as copilot_router
from services.copilot.src.websocket.handler import router as copilot_ws_router
from services.copilot.src.api.simulation import router as simulation_router
from services.copilot.src.api.reports import router as reports_router
from services.auth.src.api.auth import router as auth_router
from services.auth.src.api.users import router as users_router
from services.auth.src.api.admin_activity import router as admin_activity_router
from services.auth.src.middleware import ActivityTrackingMiddleware
from services.auth.src.seed import seed_admin_user
from services.auth.src.security import validate_jwt_secret_configured
from services.auth.src.microsoft import validate_microsoft_sso_config
from services.auth.src.rate_limiter import validate_trusted_proxies_config

from services.interview.src.repositories.postgres_repository import PostgresInterviewRepository
from services.copilot.src.services.repository import CopilotRepository

class AllowAllWebSocketOriginsMiddleware:
    """ASGI Middleware to normalize Origin headers on WebSocket handshakes to prevent 403 Forbidden."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            headers = []
            host_val = b"localhost:8000"
            for k, v in scope.get("headers", []):
                if k.lower() == b"host":
                    host_val = v
                if k.lower() != b"origin":
                    headers.append((k, v))
            headers.append((b"origin", b"http://" + host_val))
            scope["headers"] = headers
        await self.app(scope, receive, send)

app = FastAPI(
    title="Voicebot Platform API",
    description="Unified AI Mock Interviewer & Copilot Backend API",
    version="1.0.0"
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        body_str = body.decode("utf-8", errors="replace")
    except Exception:
        body_str = "<could not read body>"

    # Prevent logging or returning sensitive authentication payloads (passwords, SSO tokens)
    is_auth_route = request.url.path.startswith("/api/auth")
    logged_body = "<redacted auth credentials>" if is_auth_route else body_str[:500]
    response_body = "<redacted>" if is_auth_route else body_str[:500]

    logger.error(f"[Validation-Error-422] Path: {request.url.path} | Method: {request.method} | Errors: {exc.errors()} | Body: {logged_body}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body_received": response_body},
    )

app.add_middleware(AllowAllWebSocketOriginsMiddleware)
app.add_middleware(ActivityTrackingMiddleware)

from urllib.parse import urlparse

def get_cors_origins() -> list[str]:
    is_prod = os.getenv("ENVIRONMENT", "").lower() == "production"
    raw_cors = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if raw_cors.startswith("="):
        raw_cors = raw_cors.lstrip("=").strip()
    frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")

    origins: list[str] = []

    if is_prod:
        # In production, require explicit configured origins; reject wildcards, paths, and localhost
        candidate_origins = []
        if frontend_url:
            candidate_origins.append(frontend_url)
        if raw_cors:
            for item in raw_cors.split(","):
                cand = item.strip().rstrip("/")
                if cand:
                    candidate_origins.append(cand)

        for origin in candidate_origins:
            if origin == "*":
                raise ValueError("Wildcard '*' CORS origin is strictly forbidden in production with credentials.")
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(f"Invalid CORS origin scheme in production: {origin}")
            if not parsed.netloc or "*" in parsed.netloc:
                raise ValueError(f"Invalid CORS origin hostname (wildcards prohibited): {origin}")
            if parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
                raise ValueError(f"Localhost/loopback CORS origin is forbidden in production: {origin}")
            if parsed.path and parsed.path != "/":
                raise ValueError(f"Invalid CORS origin: paths are not allowed in origin specifications ({origin})")
            if parsed.query or parsed.fragment:
                raise ValueError(f"Invalid CORS origin: queries/fragments not allowed ({origin})")

            normalized = f"{parsed.scheme}://{parsed.netloc}"
            if normalized not in origins:
                origins.append(normalized)

        if not origins:
            logger.warning("[CORS] No production CORS origins specified in FRONTEND_URL or CORS_ALLOWED_ORIGINS.")
    else:
        # Development / local defaults
        origins = [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
        if frontend_url and frontend_url not in origins:
            origins.append(frontend_url)
        if raw_cors and raw_cors != "*":
            for origin in raw_cors.split(","):
                clean = origin.strip().rstrip("/")
                if clean and clean not in origins:
                    origins.append(clean)

    return origins

cors_origins = get_cors_origins()
raw_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip().lstrip("=").strip()
is_production = os.getenv("ENVIRONMENT", "").lower() == "production"
cors_regex = r".*" if (not is_production and raw_cors_env == "*") else None

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=cors_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Initialize application states for both Interview and Copilot domains
app.state.interview_repo = PostgresInterviewRepository()
app.state.active_sessions = {}

app.state.copilot_repo = CopilotRepository()
app.state.copilot_sessions = {}

# Include Routers
# 1. Interview Service Routers (e.g., /api/interviews, /api/questions, /api/ws/interview/...)
app.include_router(interviews_router, tags=["Interviews"])

# Root alias routes for direct media downloads (/interviews/{session_id}/recording and /interviews/{session_id}/resume)
@app.get("/interviews/{session_id}/recording", tags=["Interviews"])
def get_recording_root_alias(session_id: str):
    return get_recording(session_id)

@app.get("/interviews/{session_id}/resume", tags=["Interviews"])
def get_resume_root_alias(session_id: str):
    return get_resume(session_id)

# 2. Copilot Service Routers (e.g., /api/copilot/start, /api/copilot/{id}/join-meeting...)
app.include_router(copilot_router, prefix="/api/copilot", tags=["Copilot"])

# 3. Copilot Live WebSocket Router (/api/ws/copilot/{session_id})
app.include_router(copilot_ws_router, tags=["Copilot WebSocket"])

# 4. Simulation Router (/api/copilot/{id}/upload-audio, /api/ws/copilot/{id}/simulate)
app.include_router(simulation_router, prefix="/api", tags=["Simulation"])

# 5. Reports Router (/api/reports/{session_id}/pdf)
app.include_router(reports_router, prefix="/api", tags=["Reports"])

# 6. Authentication & User Management Routers (/api/auth/login, /api/auth/me, /api/users, /api/admin/users)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(admin_activity_router)

# Register Tortoise-ORM with Interview, Copilot, and Auth model definitions
db_url = CopilotSettings.DATABASE_URL or InterviewSettings.DATABASE_URL
register_tortoise(
    app,
    db_url=db_url,
    modules={
        "models": [
            "services.interview.src.models.interview",
            "services.copilot.src.models.copilot",
            "services.auth.src.models.user",
        ]
    },
    generate_schemas=True,
    add_exception_handlers=True,
)

@app.on_event("startup")
async def startup_auth_and_schema():
    # 1. Validate JWT_SECRET_KEY is configured (Fails startup if missing or weak in production)
    validate_jwt_secret_configured()
    logger.info("[Auth] JWT security configuration validated.")

    # 1b. Validate TRUSTED_PROXIES IP/CIDR syntax
    validate_trusted_proxies_config()
    logger.info("[Auth] Trusted proxies configuration validated.")

    # 1c. Validate Microsoft SSO configuration
    is_prod = os.getenv("ENVIRONMENT", "").lower() == "production"
    require_sso = os.getenv("REQUIRE_MICROSOFT_SSO", "false").lower() == "true"
    validate_microsoft_sso_config(fail_on_missing=(is_prod and require_sso))
    logger.info("[Auth] Microsoft SSO configuration validated.")

    # 2. Ensure Copilot schema compatibility
    try:
        from tortoise import Tortoise
        conn = Tortoise.get_connection("default")
        dialect = getattr(conn.capabilities, "dialect", "")
        if dialect == "sqlite":
            _, rows = await conn.execute_query("PRAGMA table_info(copilot_sessions);")
            if rows:
                cols = [r["name"] for r in rows]
                if "final_report" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN final_report JSON;")
                    logger.info("[DB] Added final_report column to copilot_sessions (SQLite)")
                if "meeting_started_at" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN meeting_started_at TIMESTAMP;")
                    logger.info("[DB] Added meeting_started_at column to copilot_sessions (SQLite)")
                if "meeting_ended_at" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN meeting_ended_at TIMESTAMP;")
                    logger.info("[DB] Added meeting_ended_at column to copilot_sessions (SQLite)")
                if "meeting_duration_seconds" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN meeting_duration_seconds INT;")
                    logger.info("[DB] Added meeting_duration_seconds column to copilot_sessions (SQLite)")
                if "user_id" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN user_id CHAR(36);")
                    logger.info("[DB] Added user_id column to copilot_sessions (SQLite)")
                if "organizer_email" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN organizer_email VARCHAR(255);")
                    logger.info("[DB] Added organizer_email column to copilot_sessions (SQLite)")
                if "interviewer" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN interviewer VARCHAR(255);")
                    logger.info("[DB] Added interviewer column to copilot_sessions (SQLite)")
                if "candidate_name" not in cols:
                    await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN candidate_name VARCHAR(255);")
                    logger.info("[DB] Added candidate_name column to copilot_sessions (SQLite)")

            _, int_rows = await conn.execute_query("PRAGMA table_info(interview_sessions);")
            if int_rows:
                int_cols = [r["name"] for r in int_rows]
                if "user_id" not in int_cols:
                    await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN user_id CHAR(36);")
                    logger.info("[DB] Added user_id column to interview_sessions (SQLite)")
                if "organizer_email" not in int_cols:
                    await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN organizer_email VARCHAR(255);")
                    logger.info("[DB] Added organizer_email column to interview_sessions (SQLite)")
                if "interviewer" not in int_cols:
                    await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN interviewer VARCHAR(255);")
                    logger.info("[DB] Added interviewer column to interview_sessions (SQLite)")
                if "candidate_name" not in int_cols:
                    await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN candidate_name VARCHAR(255);")
                    logger.info("[DB] Added candidate_name column to interview_sessions (SQLite)")

            _, user_rows = await conn.execute_query("PRAGMA table_info(users);")
            if user_rows:
                user_cols = [r["name"] for r in user_rows]
                if "name" not in user_cols:
                    await conn.execute_query("ALTER TABLE users ADD COLUMN name VARCHAR(255);")
                    logger.info("[DB] Added name column to users (SQLite)")
                if "last_login_at" not in user_cols:
                    await conn.execute_query("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP;")
                    logger.info("[DB] Added last_login_at column to users (SQLite)")
                if "last_activity_at" not in user_cols:
                    await conn.execute_query("ALTER TABLE users ADD COLUMN last_activity_at TIMESTAMP;")
                    logger.info("[DB] Added last_activity_at column to users (SQLite)")

                # Ensure password_hash is nullable in SQLite
                pwd_row = next((r for r in user_rows if r["name"] == "password_hash"), None)
                pwd_dict = dict(pwd_row) if pwd_row is not None else {}
                if pwd_dict.get("notnull") == 1:
                    await conn.execute_query("PRAGMA foreign_keys=off;")
                    await conn.execute_query("""
                        CREATE TABLE IF NOT EXISTS users_migration_temp (
                            id CHAR(36) NOT NULL PRIMARY KEY,
                            email VARCHAR(255) NOT NULL UNIQUE,
                            password_hash TEXT,
                            role VARCHAR(20) NOT NULL,
                            is_active INT NOT NULL,
                            created_at TIMESTAMP NOT NULL,
                            updated_at TIMESTAMP NOT NULL,
                            name VARCHAR(255),
                            employee_id BIGINT,
                            last_login_at TIMESTAMP,
                            last_activity_at TIMESTAMP
                        );
                    """)
                    await conn.execute_query("""
                        INSERT INTO users_migration_temp (id, email, password_hash, role, is_active, created_at, updated_at, name, employee_id, last_login_at, last_activity_at)
                        SELECT id, email, password_hash, role, is_active, created_at, updated_at, name, employee_id, last_login_at, last_activity_at FROM users;
                    """)
                    await conn.execute_query("DROP TABLE users;")
                    await conn.execute_query("ALTER TABLE users_migration_temp RENAME TO users;")
                    await conn.execute_query("CREATE INDEX IF NOT EXISTS idx_users_email_133a6f ON users (email);")
                    await conn.execute_query("CREATE INDEX IF NOT EXISTS idx_users_employe_b299e5 ON users (employee_id);")
                    await conn.execute_query("PRAGMA foreign_keys=on;")
                    logger.info("[DB] Migrated users table to allow NULL password_hash (SQLite)")

            # Ensure user_external_identities table exists in SQLite
            await conn.execute_query("""
                CREATE TABLE IF NOT EXISTS user_external_identities (
                    id CHAR(36) PRIMARY KEY,
                    user_id CHAR(36) NOT NULL,
                    provider VARCHAR(50) NOT NULL DEFAULT 'microsoft',
                    tenant_id VARCHAR(100) NOT NULL,
                    provider_user_id VARCHAR(100) NOT NULL,
                    email VARCHAR(255),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (provider, tenant_id, provider_user_id)
                );
            """)
        else:
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS final_report JSONB;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS meeting_started_at TIMESTAMPTZ;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS meeting_ended_at TIMESTAMPTZ;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS meeting_duration_seconds INT;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS user_id UUID;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS organizer_email VARCHAR(255);")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS interviewer VARCHAR(255);")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS candidate_name VARCHAR(255);")

            await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS user_id UUID;")
            await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS organizer_email VARCHAR(255);")
            await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS interviewer VARCHAR(255);")
            await conn.execute_query("ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS candidate_name VARCHAR(255);")

            await conn.execute_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(255);")
            await conn.execute_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;")
            await conn.execute_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;")
            await conn.execute_query("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;")
            await conn.execute_query("""
                CREATE TABLE IF NOT EXISTS user_external_identities (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL,
                    provider VARCHAR(50) NOT NULL DEFAULT 'microsoft',
                    tenant_id VARCHAR(100) NOT NULL,
                    provider_user_id VARCHAR(100) NOT NULL,
                    email VARCHAR(255),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_user_external_identities UNIQUE (provider, tenant_id, provider_user_id)
                );
            """)
            logger.info("[DB] Verified schema (copilot_sessions, interview_sessions, users, user_sessions, user_external_identities present)")
    except Exception as e:
        logger.warning(f"[DB] Schema migration check notice: {e}")

    # 3. Seed initial administrator if none exists
    await seed_admin_user()



@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "voicebot-unified-backend",
        "active_interviews": len(app.state.active_sessions),
        "active_copilots": len(app.state.copilot_sessions)
    }


@app.get("/api/health")
async def api_health_check():
    return await health_check()
