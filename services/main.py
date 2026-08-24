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

from services.interview.src.api.interviews import router as interviews_router
from services.copilot.src.router import router as copilot_router
from services.copilot.src.websocket.handler import router as copilot_ws_router
from services.copilot.src.api.simulation import router as simulation_router

from services.interview.src.repositories.postgres_repository import PostgresInterviewRepository
from services.copilot.src.services.repository import CopilotRepository

app = FastAPI(
    title="Voicebot Platform API",
    description="Unified AI Mock Interviewer & Copilot Backend API",
    version="1.0.0"
)

# Setup CORS middleware
cors_origins_raw = CopilotSettings.CORS_ALLOWED_ORIGINS or InterviewSettings.CORS_ALLOWED_ORIGINS or "*"
allowed_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials="*" not in allowed_origins,
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
