# Copilot API Endpoints

## Base URL
```
http://localhost:8001
```

## Endpoints

### Start Copilot Session
Create new copilot session.

```
POST /api/copilot/start
```

**Request Body**:
```json
{
  "jd": "Senior Python Engineer",
  "resume": "Experienced backend developer...",
  "custom_prompt": "Focus on system architecture"
}
```

**Response**:
```json
{
  "session_id": "session-123",
  "status": "Connecting to audio stream..."
}
```

### List Copilot Sessions
List all copilot sessions.

```
GET /api/copilot
```

**Response**:
```json
[
  {
    "session_id": "session-123",
    "jd": "Senior Python Engineer",
    "resume": "Experienced backend developer...",
    "custom_prompt": null,
    "timestamp": "2024-01-01T00:00:00Z"
  }
]
```

### Get Copilot Session
Get specific copilot session.

```
GET /api/copilot/{session_id}
```

**Response**:
```json
{
  "session_id": "session-123",
  "jd": "Senior Python Engineer",
  "resume": "Experienced backend developer...",
  "custom_prompt": null,
  "transcript": [],
  "timestamp": "2024-01-01T00:00:00Z"
}
```

### Add Transcript
Add transcript entry to copilot session.

```
POST /api/copilot/{session_id}/transcript
```

**Request Body**:
```json
{
  "speaker": "Interviewer",
  "text": "Can you tell me about your experience with Python?"
}
```

**Response**:
```json
{
  "message_id": "msg-123",
  "speaker": "Interviewer",
  "text": "Can you tell me about your experience with Python?"
}
```

### Update Copilot Prompt
Update custom prompt for copilot session.

```
PATCH /api/copilot/{session_id}/prompt
```

**Request Body**:
```json
{
  "custom_prompt": "Focus on system architecture"
}
```

**Response**:
```json
{
  "status": "success",
  "session_id": "session-123",
  "custom_prompt": "Focus on system architecture"
}
```

### Get Copilot Status
Get copilot session status and intelligence.

```
GET /api/copilot/{session_id}/status
```

**Response**:
```json
{
  "session_id": "session-123",
  "is_active": true,
  "status": "Listening for audio stream...",
  "transcript": [],
  "intelligence": {},
  "assistance": {},
  "custom_prompt": null
}
```

### Stop Copilot Session
Stop copilot session.

```
POST /api/copilot/{session_id}/stop
```

**Response**:
```json
{
  "status": "stopped"
}
```

### Finalize Copilot Report
Finalize copilot session and generate report.

```
POST /api/copilot/{session_id}/finalize
```

**Response**:
```json
{
  "session_id": "session-123",
  "report": {
    "intelligence": {},
    "assistance": {},
    "recommendations": []
  }
}
```

## WebSocket Communication

### Copilot WebSocket
```
WS /api/copilot/{session_id}
```

**Messages**:
- **Incoming**: Audio frames, transcript updates
- **Outgoing**: Intelligence reports, assistance updates

## Error Responses

### 400 Bad Request
```json
{
  "detail": "Invalid request parameters"
}
```

### 404 Not Found
```json
{
  "detail": "Session not found"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Internal server error"
}
```