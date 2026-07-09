from fastapi import FastAPI
from dotenv import load_dotenv
from backend.app.core.config import Settings
from backend.app.repositories.json_repository import JSONFileInterviewRepository
from backend.app.api.interviews import router as interviews_router

# Load dotenv
load_dotenv(override=True)
Settings.validate()

app = FastAPI(title="AI Mock Interviewer Backend")

# Initialize and attach state to app.state
app.state.repo = JSONFileInterviewRepository()
app.state.active_sessions = {}

# Include modular API routers
app.include_router(interviews_router)
