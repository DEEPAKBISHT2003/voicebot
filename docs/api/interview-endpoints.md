# Interview API Endpoints

## Base URL
```
http://localhost:8000
```

## Endpoints

### Parse Resume
Parse PDF/DOCX resume file.

```
POST /api/interviews/parse-resume
```

**Request Body**:
- `file` (multipart/form-data): Resume file

**Response**:
```json
{
  "text": "Resume text content...",
  "filename": "resume.pdf"
}
```

### Start Interview Session
Create session & start engine.

```
POST /api/interviews/start
```

**Request Body**:
```json
{
  "jd": "Senior Python Engineer",
  "resume": "Experienced backend developer...",
  "custom_prompt": "Focus on system architecture",
  "resume_filename": "resume.pdf",
  "resume_base64": "",
  "meeting_url": ""
}
```

**Response**:
```json
{
  "session_id": "session-123",
  "status": "Connecting to audio stream..."
}
```

### Get Interview Sessions
Fetch all past interview sessions.

```
GET /api/interviews
```

**Response**:
```json
[
  {
    "session_id": "session-123",
    "timestamp": "2024-01-01T00:00:00Z",
    "jd": "Senior Python Engineer",
    "resume": "Experienced backend developer...",
    "custom_prompt": null,
    "transcript": []
  }
]
```

### Get Interview Session
Get specific interview session.

```
GET /api/interviews/{session_id}
```

**Response**:
```json
{
  "session_id": "session-123",
  "timestamp": "2024-01-01T00:00:00Z",
  "jd": "Senior Python Engineer",
  "resume": "Experienced backend developer...",
  "custom_prompt": null,
  "transcript": []
}
```

### Get Session Status
Get current session status.

```
GET /api/interviews/{session_id}/status
```

**Response**:
```json
{
  "session_id": "session-123",
  "is_active": true,
  "status": "Microphone online! Say 'Hello' to start.",
  "transcript": [],
  "custom_prompt": null
}
```

### Update Custom Prompt
Update interview prompt.

```
PATCH /api/interviews/{session_id}/prompt
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

### Stop Interview Session
Stop current interview session.

```
POST /api/interviews/{session_id}/stop
```

**Response**:
```json
{
  "status": "Stopped"
}
```

### Get Recording
Get interview recording.

```
GET /api/interviews/{session_id}/recording
```

**Response**: Audio file (WAV)

### Get Resume
Get uploaded resume.

```
GET /api/interviews/{session_id}/resume
```

**Response**: File download (PDF/DOCX/TXT)

### WebSocket Endpoint
Real-time voice interviewer pipeline.

```
WS /ws/interview/{session_id}
```

**Query Parameters**:
- `mode=observer` - Observer mode
- `simulate=true` - Simulation mode

**Messages**:
- **Incoming**: Audio frames
- **Outgoing**: Transcription updates, session status

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