from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from tortoise.contrib.fastapi import register_tortoise

from backend.app.core.config import Settings
from backend.app.repositories.postgres_repository import PostgresInterviewRepository
from backend.app.api.interviews import router as interviews_router

# Load dotenv
load_dotenv(override=True)
Settings.validate()

app = FastAPI(title="AI Mock Interviewer Backend")

# Setup CORS middleware strictly from FRONTEND_URL environment variable (supports comma-separated list)
allowed_origins = []
if Settings.FRONTEND_URL:
    for origin in Settings.FRONTEND_URL.split(","):
        cleaned_origin = origin.strip()
        if cleaned_origin and cleaned_origin not in allowed_origins:
            allowed_origins.append(cleaned_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize and attach state to app.state
app.state.repo = PostgresInterviewRepository()
app.state.active_sessions = {}

# Include modular API routers
app.include_router(interviews_router)

# Register Tortoise-ORM
register_tortoise(
    app,
    db_url=Settings.DATABASE_URL,
    modules={"models": ["backend.app.models.interview"]},
    generate_schemas=True,
    add_exception_handlers=True,
)

