from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from tortoise.contrib.fastapi import register_tortoise

from backend.app.core.config import Settings
from backend.app.repositories.postgres_repository import PostgresInterviewRepository
from backend.app.api.interviews import router as interviews_router
from backend.copilot.api.router import router as copilot_api_router
from backend.copilot.websocket.handler import router as copilot_ws_router

# Load dotenv
load_dotenv(override=True)
Settings.validate()

app = FastAPI(title="AI Mock Interviewer Backend")

# Setup CORS middleware strictly from CORS_ALLOWED_ORIGINS environment variable
allowed_origins = [o.strip() for o in Settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()] if Settings.CORS_ALLOWED_ORIGINS else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials="*" not in allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize and attach state to app.state
app.state.repo = PostgresInterviewRepository()
app.state.active_sessions = {}

# Include modular API routers
app.include_router(interviews_router)
app.include_router(copilot_api_router)
app.include_router(copilot_ws_router)

# Register Tortoise-ORM
register_tortoise(
    app,
    db_url=Settings.DATABASE_URL,
    modules={"models": ["backend.app.models.interview"]},
    generate_schemas=True,
    add_exception_handlers=True,
)

