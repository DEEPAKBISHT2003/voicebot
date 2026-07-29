from fastapi import Request, WebSocket

def get_repo(request: Request):
    return request.app.state.interview_repo

def get_active_sessions(request: Request):
    return request.app.state.active_sessions

def get_repo_ws(websocket: WebSocket):
    return websocket.app.state.interview_repo

def get_active_sessions_ws(websocket: WebSocket):
    return websocket.app.state.active_sessions
