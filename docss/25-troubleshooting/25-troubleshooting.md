# Section 25 — Troubleshooting Guide

> **Cross-references:** [Error Handling](../19-error-handling/19-error-handling.md) | [Logging & Monitoring](../21-logging-monitoring/21-logging-monitoring.md) | [Deployment Guide](../24-deployment/24-deployment.md)

---

## 25.1 Docker & Startup Issues

### Problem: `no space left on device` during docker build

**Symptom:**
```
failed to write compressed diff: failed to create diff tar stream:
write ...: no space left on device
```

**Cause:** Insufficient disk space on the host for Docker layer storage (Playwright Chromium is ~600MB).

**Solution:**
```bash
# Check disk usage
df -h

# Free build cache (safe - doesn't affect running containers)
docker builder prune -f

# Free all unused images (safe if no other projects using them)
docker system prune -a -f

# If still insufficient, resize EBS volume (AWS):
# 1. AWS Console → EC2 → Volumes → Modify Volume → 30GB+
# 2. On instance: sudo growpart /dev/xvda 1 && sudo resize2fs /dev/xvda1
```

---

### Problem: Copilot service crashes on startup with `No space left on device`

**Symptom:**
```
OSError: [Errno 28] No space left on device: 'interviews/copilots'
```

**Cause:** Disk is full — `os.makedirs()` cannot write even an empty directory.

**Solution:** Free disk space as above. The service will recover on restart.

---

### Problem: `voicebot-copilot-service is unhealthy` — interview service won't start

**Cause:** The interview service has `depends_on: copilot-service: condition: service_healthy` and the copilot service failed its health check.

**Solution:**
```bash
# Check why copilot service is unhealthy
docker compose logs copilot-service

# Fix the underlying issue (usually disk space or missing env var)
# Then restart
docker compose restart copilot-service
```

---

### Problem: Database tables not created on startup

**Symptom:** `relation "interview_sessions" does not exist` errors.

**Cause:** `generate_schemas(safe=True)` failed silently, or service started before DB was healthy.

**Solution:**
```bash
# Verify DB health
docker exec voicebot-db pg_isready -U postgres -d interview

# Check if tables exist
docker exec voicebot-db psql -U postgres -d interview \
  -c "\dt"

# If missing, restart services (will re-run generate_schemas)
docker compose restart interview-service copilot-service
```

---

## 25.2 Audio & WebSocket Issues

### Problem: No audio from AI interviewer (WebSocket connects but silent)

**Possible causes and checks:**
```bash
# 1. Check Deepgram TTS API key is valid
docker compose logs interview-service | grep -i "deepgram\|tts\|error"

# 2. Check DeepSeek LLM is responding
docker compose logs interview-service | grep -i "deepseek\|llm\|openai"

# 3. Check WebSocket connection stays open
# (Browser DevTools → Network → WS tab → check messages)

# 4. Verify Nginx WebSocket proxy timeout is set
# nginx.conf must have: proxy_read_timeout 3600s;
```

---

### Problem: Microphone not working / no transcript appearing

**Checks:**
1. Browser: Check mic permissions (lock icon in address bar)
2. Browser: `navigator.mediaDevices.getUserMedia` requires HTTPS on non-localhost origins
3. Check sample rate: browser may capture at 44.1kHz instead of 48kHz — downsampling handles this

```bash
# Check if STT is receiving audio
docker compose logs interview-service | grep -i "deepgram\|transcript\|stt"

# Check for VAD issues (audio too quiet)
# Increase browser volume or speak closer to mic
# VAD min_volume=0.20 may be too high for quiet rooms
```

---

### Problem: WebSocket disconnects after ~60 seconds

**Cause:** Nginx default timeout. Must set `proxy_read_timeout 3600s` in nginx.conf.

**Fix:**
```nginx
# nginx.conf — ensure these are set for WebSocket locations
location /api/ {
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    ...
}
```

```bash
# Rebuild frontend container to apply nginx.conf changes
docker compose up -d --build frontend
```

---

### Problem: TTS audio plays choppy / with gaps

**Cause:** `PlaybackBufferProcessor` buffer too small or browser AudioContext scheduling issue.

**Fixes:**
1. Increase `PlaybackBufferProcessor(buffer_size=10)` in `pipeline/builder.py`
2. Check browser CPU load — high CPU causes audio scheduling jitter
3. Ensure `nextPlayTimeRef` scheduling logic is not accumulating drift

---

## 25.3 AI / LLM Issues

### Problem: Copilot shows no suggestions

**Checks:**
```bash
# 1. Check DeepSeek API key
docker compose logs copilot-service | grep -i "deepseek\|openai\|error"

# 2. Check Groq API key
docker compose logs copilot-service | grep -i "groq\|evaluation"

# 3. Check transcript is being received
docker compose logs copilot-service | grep "Forwarding segment"

# 4. Verify background tasks are running
docker compose logs copilot-service | grep "Background evaluation"
```

---

### Problem: LLM returns invalid JSON despite `response_format`

**Symptom:** `json.JSONDecodeError` in logs.

**Cause:** Some LLMs ignore `response_format={"type": "json_object"}` occasionally.

**Solution:** Already handled by `clean_json_loads()`. If still failing:
```python
# Strengthen the prompt with explicit JSON-only instruction:
prompt += "\n\nCRITICAL: Output ONLY valid JSON. No markdown, no text, no explanation."
```

---

### Problem: Teams bot joins meeting but no transcript in copilot

**Checks:**
```bash
# 1. Check if bot subprocess was spawned
docker compose logs interview-service | grep -i "teamsbot"

# 2. Check Playwright is working inside container
docker exec voicebot-copilot-service python -c "from playwright.sync_api import sync_playwright; print('OK')"

# 3. Check WebSocket from bot to interview service
docker compose logs interview-service | grep "observer"

# 4. Teams URL may require authentication — bot cannot login to personal accounts
# Ensure the Teams meeting is set to allow anonymous join
```

---

## 25.4 Database Issues

### Problem: `asyncpg.exceptions.ConnectionDoesNotExistError`

**Cause:** DB connection pool exhausted or PostgreSQL restarted.

**Solution:**
```bash
# Restart backend services to re-initialize connection pool
docker compose restart interview-service copilot-service

# Check active connections
docker exec voicebot-db psql -U postgres -d interview \
  -c "SELECT count(*) FROM pg_stat_activity WHERE state != 'idle';"
```

---

### Problem: Large transcript JSONB slows queries

**Symptom:** `GET /api/interviews/{id}` takes >500ms for long sessions.

**Solution:**
```sql
-- Add GIN index on transcript JSONB for fast queries
CREATE INDEX CONCURRENTLY idx_interview_transcript 
ON interview_sessions USING GIN (transcript);

-- Or add index on timestamp for list queries
CREATE INDEX idx_interview_timestamp 
ON interview_sessions (timestamp DESC);
```

---

## 25.5 Frontend Issues

### Problem: API calls fail with CORS error

**Symptom:** Browser console: `Access to XMLHttpRequest blocked by CORS policy`

**Fix:**
```bash
# Set CORS_ALLOWED_ORIGINS in .env to include your frontend origin
CORS_ALLOWED_ORIGINS=https://yourdomain.com,http://localhost:5173
docker compose restart interview-service copilot-service
```

---

### Problem: WebSocket uses wrong URL (wss vs ws)

**Symptom:** WebSocket connect to wrong protocol/host in browser console.

**Fix:** Ensure `VITE_API_URL` is set correctly for production:
```bash
VITE_API_URL=https://yourdomain.com  # HTTPS in production
# This triggers wss:// automatically in useInterviewAudio.ts
```

Then rebuild frontend:
```bash
docker compose up -d --build frontend
```

---

## 25.6 Quick Diagnostic Commands

```bash
# Complete health check
docker compose ps && docker stats --no-stream

# View all error logs from last hour
docker compose logs --since=1h 2>&1 | grep -i "error\|exception\|failed"

# Check disk space (most common cause of issues)
df -h && docker system df

# Verify DB tables
docker exec voicebot-db psql -U postgres -d interview -c "\dt"

# Count sessions in DB
docker exec voicebot-db psql -U postgres -d interview \
  -c "SELECT COUNT(*) FROM interview_sessions; SELECT COUNT(*) FROM copilot_sessions;"

# Restart everything cleanly (preserves data)
docker compose down && docker compose up -d

# Nuclear reset (DESTROYS ALL DATA)
docker compose down -v && docker compose up -d --build
```

---

*Next: [Section 26 — Future Improvements →](../26-future-improvements/26-future-improvements.md)*

