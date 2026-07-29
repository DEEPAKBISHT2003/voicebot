# Communication Patterns

## Data Flow
```
User → Frontend → Services → Database
              ↓
         Shared Packages
```

## Service Communication

### Interview → Copilot (HTTP)
```python
from services.interview.src.clients.copilot_client import CopilotClient

client = CopilotClient()
result = await client.start_copilot_session(
    jd="Senior Python Engineer",
    resume="Experienced backend developer..."
)
```

### Copilot → Interview (HTTP)
```python
from services.copilot.src.clients.interview_client import InterviewClient

client = InterviewClient()
result = await client.get_session_status(session_id="session-123")
```

## Database Access

### Interview Service
- Uses `PostgresInterviewRepository`
- Connects to `interview` database
- Manages interview sessions

### Copilot Service
- Uses `CopilotRepository`
- Connects to `copilot` database
- Manages copilot sessions

## API Gateway Pattern

For future scaling, consider adding an API gateway:
```
User → API Gateway (Nginx/Traefik) → Services
              ↓
         Rate Limiting
         Authentication
         Logging
```

## WebSocket Communication

### Interview WebSocket
- **Endpoint**: `/ws/interview/{session_id}`
- **Purpose**: Real-time voice data
- **Features**:
  - Audio streaming
  - Transcription updates
  - Session status

### Copilot WebSocket
- **Endpoint**: `/ws/copilot/{session_id}`
- **Purpose**: Real-time copilot updates
- **Features**:
  - Intelligence reports
  - Assistance updates
  - Transcript synchronization

## Error Handling

### Service-to-Service Errors
```python
try:
    result = await client.start_copilot_session(...)
except httpx.HTTPError as e:
    logger.error(f"Failed to start copilot: {e}")
    raise HTTPException(status_code=503, detail="Copilot service unavailable")
```

### Database Errors
```python
try:
    result = await repo.create_session(...)
except DatabaseError as e:
    logger.error(f"Database error: {e}")
    raise HTTPException(status_code=500, detail="Database error")
```