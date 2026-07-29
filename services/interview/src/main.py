from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from tortoise.contrib.fastapi import register_tortoise

from services.interview.src.core.config import Settings
from services.interview.src.api.interviews import router as interviews_router
from services.interview.src.repositories.postgres_repository import PostgresInterviewRepository

# Load dotenv
load_dotenv(override=True)
Settings.validate()

app = FastAPI(
    title="Interview Service",
    description="AI Mock Interviewer Backend API",
    version="1.0.0"
)

# Setup CORS middleware
allowed_origins = [o.strip() for o in Settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()] if Settings.CORS_ALLOWED_ORIGINS else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials="*" not in allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize interview repository
app.state.interview_repo = PostgresInterviewRepository()
app.state.active_sessions = {}

# Include API routers
app.include_router(interviews_router, tags=["Interviews"])

# Register Tortoise-ORM
register_tortoise(
    app,
    db_url=Settings.DATABASE_URL,
    modules={"models": ["services.interview.src.models.interview", "services.copilot.src.models.copilot"]},
    generate_schemas=True,
    add_exception_handlers=True,
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "interview"}
