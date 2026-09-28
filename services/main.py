from fastapi import FastAPI
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
from services.auth.src.api.auth import router as auth_router
from services.auth.src.api.users import router as users_router
from services.auth.src.seed import seed_admin_user
from services.auth.src.security import validate_jwt_secret_configured

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

app.add_middleware(AllowAllWebSocketOriginsMiddleware)

# Setup CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
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

# 5. Authentication & User Management Routers (/api/auth/login, /api/auth/me, /api/users)
app.include_router(auth_router)
app.include_router(users_router)

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
    # 1. Validate JWT_SECRET_KEY is configured (Fails startup if missing)
    validate_jwt_secret_configured()
    logger.info("[Auth] JWT security configuration validated.")

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
        else:
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS final_report JSONB;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS meeting_started_at TIMESTAMPTZ;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS meeting_ended_at TIMESTAMPTZ;")
            await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS meeting_duration_seconds INT;")
            logger.info("[DB] Verified copilot_sessions schema (duration and report columns present)")
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
