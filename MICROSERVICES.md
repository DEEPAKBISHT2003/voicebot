# Architecture Overview

This project uses a unified FastAPI backend architecture running on Port 8000 (`services/main.py`) paired with a Playwright browser worker on Port 8002 (`services/browser`).

## Services

### 1. Unified Backend Service (Port 8000)
- Handles AI interview sessions (`/api/interviews`, `/api/ws/interview/...`)
- Handles copilot sessions (`/api/copilot`, `/api/ws/copilot/...`)
- Coordinates voice pipelines & intelligence reports
- Single PostgreSQL database (`interview`)

### 2. Browser Service (Port 8002)
- Playwright worker for joining Microsoft Teams & meeting calls
- Intercepts WebRTC audio and relays to backend WebSockets

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Frontend (Port 3000)                      │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Unified Backend Service (8000)                  │
│                                                                     │
│  - Interview API & Pipeline (/api/interviews, /ws/interview/...)   │
│  - Copilot API & Pipeline (/api/copilot, /ws/copilot/...)          │
└─────────────────────────────────────────────────────────────────────┘
           │                                    │
           ▼                                    ▼
┌─────────────────────────────────┐    ┌─────────────────────────────┐
│   Database: interview           │    │   Browser Service (8002)    │
│   (PostgreSQL: 5432)            │    │   (Playwright Teams Bot)    │
└─────────────────────────────────┘    └─────────────────────────────┘
```

## Environment Variables

### Interview Service
- `DATABASE_URL` - PostgreSQL connection for interview database
- `COPILLOT_SERVICE_URL` - URL to copilot service (default: http://copilot-service:8001)
- `DEEPGRAM_API_KEY` - Deepgram API key
- `DEEPSEEK_API_KEY` - DeepSeek API key
- `GROQ_API_KEY` - Groq API key

### Copilot Service
- `DATABASE_URL` - PostgreSQL connection for copilot database
- `CORS_ALLOWED_ORIGINS` - CORS allowed origins

## Deployment

### Local Development
```bash
# Start all services
docker-compose up --build

# Start specific service
docker-compose up interview-service
docker-compose up copilot-service
```

### Access Points
- Frontend: http://localhost:3000
- Interview Service: http://localhost:8000
- Copilot Service: http://localhost:8001

### Database Ports
- Interview DB: localhost:5432
- Copilot DB: localhost:5433

## Communication

### Interview → Copilot
```python
from services.interview.src.clients.copilot_client import CopilotClient

client = CopilotClient()
result = await client.start_copilot_session(
    jd="Senior Python Engineer",
    resume="Experienced backend developer...",
    interview_session_id="session-123"
)
```

### Copilot → Interview
```python
from services.copilot.src.clients.interview_client import InterviewClient

client = InterviewClient()
result = await client.get_session_status(session_id="session-123")
```

## Benefits

1. **Independent Scaling** - Each service scales independently
2. **Technology Flexibility** - Services can use different technologies
3. **Fault Isolation** - Failure in one service doesn't affect others
4. **Team Autonomy** - Teams can work on different services independently
5. **Easier Testing** - Services can be tested independently
6. **Gradual Migration** - Services can be migrated incrementally

## Migration Notes

This architecture was created from a monolith by:

1. Extracting code into separate service directories
2. Creating independent `main.py` files
3. Setting up separate databases
4. Adding HTTP clients for inter-service communication
5. Updating docker-compose for multi-service orchestration
6. Separating dependencies for each service