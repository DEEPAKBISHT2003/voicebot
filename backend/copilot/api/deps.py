from fastapi import Request, WebSocket

async def get_copilot_repo(request: Request):
    """Dependency helper to fetch the global Copilot repository instance."""
    return request.app.state.copilot_repo

async def get_copilot_sessions(request: Request):
    """Dependency helper to fetch the active Copilot sessions dictionary."""
    return request.app.state.copilot_sessions

async def get_copilot_repo_ws(websocket: WebSocket):
    """Dependency helper to fetch the global Copilot repository instance for WebSockets."""
    return websocket.app.state.copilot_repo

async def get_copilot_sessions_ws(websocket: WebSocket):
    """Dependency helper to fetch the active Copilot sessions dictionary for WebSockets."""
    return websocket.app.state.copilot_sessions

