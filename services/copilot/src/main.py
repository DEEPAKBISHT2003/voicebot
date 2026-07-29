from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from tortoise.contrib.fastapi import register_tortoise

from services.copilot.src.core.config import Settings
from services.copilot.src.router import router as copilot_router
from services.copilot.src.websocket.handler import router as copilot_ws_router
from services.copilot.src.api.simulation import router as simulation_router
from services.copilot.src.services.repository import CopilotRepository

# Load dotenv
load_dotenv(override=True)
Settings.validate()

app = FastAPI(
    title="Copilot Service",
    description="AI Copilot Backend API",
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

# Initialize copilot repository and sessions state
app.state.copilot_repo = CopilotRepository()
app.state.copilot_sessions = {}

# Include API routers
app.include_router(copilot_router, prefix="/api/copilot", tags=["Copilot"])
app.include_router(copilot_ws_router, tags=["Copilot WebSocket"])
app.include_router(simulation_router, prefix="/api", tags=["Simulation"])

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
    return {"status": "ok", "service": "copilot"}
