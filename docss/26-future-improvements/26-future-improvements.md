# Section 26 — Future Improvements

> **Cross-references:** [Authentication](../09-authentication/09-authentication.md) | [Security](../20-security/20-security.md) | [Performance](../22-performance/22-performance.md) | [ADR](../27-adr/27-adr.md)

---

## 26.1 Critical (Must Fix Before Production)

### AUTH-01: Implement Authentication

**Current state:** All APIs are open — anyone with the URL can access any session.

**Recommended implementation:**
- JWT access tokens (15 min) + refresh tokens (7 days, HttpOnly cookie)
- `POST /auth/login` → email/password
- `POST /auth/refresh` → rotate refresh token
- Middleware on all routes to validate JWT
- Session ownership checks (`sessions.owner_id = current_user.id`)
- Estimated effort: 3-5 days

---

### AUTH-02: Add Rate Limiting

**Current state:** No rate limiting — a single client can spam all endpoints.

**Recommended implementation:**
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/interviews/start")
@limiter.limit("10/minute")
async def start_interview(request: Request, ...):
    ...
```

---

### INFRA-01: Database Migrations

**Current state:** `generate_schemas(safe=True)` on every startup — no migration tracking.

**Recommended:** Add Aerich (Tortoise ORM migration tool):
```bash
pip install aerich
aerich init -t services.interview.src.main.TORTOISE_ORM
aerich init-db
aerich migrate --name "add_is_finalized_column"
aerich upgrade
```

---

### SECURITY-01: CORS Origin Restriction

**Current state:** `CORS_ALLOWED_ORIGINS=*` by default.

**Fix:** Always set explicit origins in `.env` for any non-localhost deployment.

---

## 26.2 High Priority (Next Sprint)

### FEAT-01: Multi-User Support

Add `users` table with organization support:
```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  hashed_password TEXT NOT NULL,
  org_id UUID REFERENCES organizations(id),
  role VARCHAR(50) DEFAULT 'interviewer',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE interview_sessions ADD COLUMN owner_id UUID REFERENCES users(id);
ALTER TABLE copilot_sessions ADD COLUMN owner_id UUID REFERENCES users(id);
```

---

### FEAT-02: Redis for Session State

Replace in-process `active_sessions` dict with Redis for multi-worker support:
```python
import redis.asyncio as redis

# Replace: active_sessions[session_id] = {...}
# With:
await redis_client.setex(
    f"session:{session_id}",
    3600,  # 1 hour TTL
    json.dumps(session_data)
)
```

This enables horizontal scaling with multiple Uvicorn workers.

---

### FEAT-03: Session Transcript Search

Add full-text search on transcript JSONB:
```sql
-- GIN index for fast JSONB queries
CREATE INDEX CONCURRENTLY idx_sessions_transcript_gin
ON interview_sessions USING GIN (transcript jsonb_path_ops);

-- Search for sessions where candidate mentioned "Kubernetes"
SELECT id FROM interview_sessions
WHERE transcript @> '[{"text": "Kubernetes"}]';
```

---

### FEAT-04: Candidate Comparison Dashboard

Build a comparison view showing multiple candidates side-by-side:
- Overall score per candidate
- Per-dimension radar chart
- JD skill coverage comparison
- Recommendation ranking

---

### FEAT-05: Audio Quality Improvements

Replace deprecated `ScriptProcessorNode` with `AudioWorkletNode`:
```typescript
// Current (deprecated):
const scriptProcessor = audioCtx.createScriptProcessor(2048, 1, 1);

// Future (recommended):
await audioCtx.audioWorklet.addModule('/audio-processor.worklet.js');
const workletNode = new AudioWorkletNode(audioCtx, 'pcm-processor');
```

`AudioWorkletNode` runs in a dedicated thread, avoiding main thread jitter.

---

## 26.3 Medium Priority

### PERF-01: Streaming LLM Responses

Stream DeepSeek LLM tokens to TTS as they arrive (currently waits for full response):
```python
# Future: token streaming
async for chunk in client.chat.completions.create(..., stream=True):
    token = chunk.choices[0].delta.content
    if token:
        await tts_service.send_text(token)  # Send partial text to TTS
```

This reduces AI response latency from ~1-2s to ~200-400ms (first audio chunk).

---

### PERF-02: LLM Response Caching

Cache identical evaluation requests (same utterance + same question):
```python
import hashlib

cache_key = hashlib.md5(f"{candidate_response}:{jd}:{question}".encode()).hexdigest()
cached = await redis_client.get(f"eval:{cache_key}")
if cached:
    return json.loads(cached)
```

Reduces redundant API calls for repeated phrases or utterance stitching re-evaluations.

---

### FEAT-06: Official Teams Bot Integration

Replace Playwright browser automation with Microsoft Bot Framework:
- Register an Azure Bot resource
- Use Teams Bot SDK for official audio access
- Eliminates fragile DOM selectors
- Supports all Teams meeting types
- Estimated effort: 2-3 weeks + Azure setup

---

### FEAT-07: Multi-Language Support

Deepgram supports 30+ languages. Add language selection:
```python
DeepgramSTTService.Settings(
    language="es",  # Spanish
    endpointing=400,
    diarize=True
)
```

LLM prompts would need localization.

---

### OPS-01: Prometheus Metrics Endpoint

Add metrics to both services:
```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

Key metrics to track:
- `sessions_active_total` gauge
- `llm_request_duration_seconds` histogram
- `stt_bytes_processed_total` counter
- `tts_audio_generated_seconds` counter

---

## 26.4 Technical Debt

| Issue | Location | Impact | Fix |
|---|---|---|---|
| Duplicate router registration | `copilot/src/router.py` + `copilot/src/api/router.py` | Routes registered twice — unexpected behavior | Remove `router.py` or `api/router.py` |
| No session cleanup | Both services | `active_sessions` dict grows forever in long-running processes | Add TTL-based eviction |
| `ScriptProcessorNode` deprecated | `useInterviewAudio.ts` | Will be removed from browsers eventually | Migrate to `AudioWorkletNode` |
| `packages/` TypeScript stubs | `packages/` | Unused scaffolding adds confusion | Implement or remove |
| No formal DB migrations | Both services | Schema changes require manual SQL | Add Aerich |
| No file size limit on uploads | `api/interviews.py` | Large files could cause OOM | Add 10MB limit |
| Teams bot runs as root | Docker container | Security risk | Add non-root user in Dockerfile |
| No graceful shutdown for long sessions | Both services | Sessions may lose last transcript entries | Implement SIGTERM handler |
| LLM context window unbounded | `pipeline/builder.py` | Very long interviews may exceed token limits | Add rolling window truncation |

---

## 26.5 Scalability Roadmap

```
Phase 1 (Now): Single EC2, Docker Compose
  - Max ~5-10 concurrent sessions
  - 8GB RAM, 30GB disk

Phase 2 (Near): Multi-worker + Redis
  - Redis for session state
  - 4 Uvicorn workers per service
  - Max ~20-40 concurrent sessions

Phase 3 (Future): Kubernetes
  - HPA (Horizontal Pod Autoscaler) on session count
  - Separate Playwright bot service (stateful, scale-up on demand)
  - Managed PostgreSQL (RDS)
  - S3 for audio/file storage
  - Max ~200+ concurrent sessions

Phase 4 (Enterprise): Multi-region
  - CloudFront CDN for frontend
  - Multi-region RDS read replicas
  - Cross-region session replication
```

---

*Next: [Section 27 — Architecture Decision Records →](../27-adr/27-adr.md)*

