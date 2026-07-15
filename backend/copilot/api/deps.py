from fastapi import Request

async def get_copilot_repo(request: Request):
    """Dependency helper to fetch the global Copilot repository instance."""
    return request.app.state.copilot_repo

async def get_copilot_sessions(request: Request):
    """Dependency helper to fetch the active Copilot sessions dictionary."""
    return request.app.state.copilot_sessions
