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

# Register Tortoise-ORM with both Interview and Copilot model definitions
db_url = CopilotSettings.DATABASE_URL or InterviewSettings.DATABASE_URL
register_tortoise(
    app,
    db_url=db_url,
    modules={
        "models": [
            "services.interview.src.models.interview",
            "services.copilot.src.models.copilot",
        ]
    },
    generate_schemas=True,
    add_exception_handlers=True,
)

@app.on_event("startup")
async def ensure_copilot_schema():
    try:
        from tortoise import Tortoise
        conn = Tortoise.get_connection("default")
        await conn.execute_query("ALTER TABLE copilot_sessions ADD COLUMN IF NOT EXISTS final_report JSONB;")
        logger.info("[DB] Verified copilot_sessions schema (final_report column present)")
    except Exception as e:
        logger.warning(f"[DB] Schema migration check notice: {e}")


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
