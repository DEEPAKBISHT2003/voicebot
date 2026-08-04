# Section 22 — Performance

> **Cross-references:** [AI Architecture](../08-ai-architecture/08-ai-architecture.md) | [Database Documentation](../07-database/07-database.md) | [Execution Flow](../13-execution-flow/13-execution-flow.md)

---

## 22.1 Performance Design Principles

VoiceBot is built around two core latency requirements:

1. **`add_message()` returns in <5ms** — the transcript is appended instantly; all LLM work happens in a background task.
2. **End-to-end audio round trip <2s** — from candidate speech ending to AI response audio starting.

---

## 22.2 Async Architecture

The entire backend is **fully async** (FastAPI + asyncio + asyncpg + AsyncOpenAI). No blocking I/O calls exist in the hot path:

```python
# All DB calls are async
session = await InterviewSession.get(id=session_id)

# All LLM calls are async
response = await self.client.chat.completions.create(...)

# All HTTP inter-service calls are async
async with httpx.AsyncClient() as client:
    resp = await client.post(copilot_url, json=data)

# All file I/O is wrapped to not block (audio buffer in background)
asyncio.create_task(save_audio_recording())
```

**Benefit:** A single Uvicorn worker can handle hundreds of concurrent WebSocket sessions without thread blocking.

---

## 22.3 Parallel LLM Processing

The most critical performance optimisation in VoiceBot is running 3 LLM tasks concurrently instead of sequentially:

```python
# Without parallelism: ~3-6 seconds serial
evaluation = await eval_service.evaluate_response(...)   # 1-2s
intelligence = await intel_engine.analyze(...)           # 1-2s
assistance = await copilot_engine.generate_assistance(...) # 1-2s
# Total: ~3-6 seconds

# With asyncio.gather: ~1-2 seconds (slowest task)
evaluation, intelligence, assistance = await asyncio.gather(
    eval_task, intel_task, assist_task,
    return_exceptions=True
)
# Total: max(1-2s, 1-2s, 1-2s) = ~1-2 seconds
```

**Speedup:** 3x faster than serial execution.

---

## 22.4 Utterance Stitching

Same-speaker rapid utterances are merged rather than triggering redundant LLM calls:

```python
# Without stitching: 5 rapid utterances = 5 LLM calls = 5-10s
# With stitching: 5 rapid utterances merged = 1 LLM call = 1-2s

if last_entry.get("speaker") == speaker:
    # Merge into existing message bubble
    last_entry["text"] = (last_entry.get("text", "") + " " + clean_text).strip()
    # Reset background task with merged text
    asyncio.create_task(self._update_all_background_llm_tasks(last_entry, ...))
    return last_entry  # Return instantly
```

This prevents LLM thrashing when a candidate speaks in short bursts (common with Deepgram's 400ms endpointing).

---

## 22.5 Audio Streaming Performance

### Browser-side Downsampling
Audio is captured at 48kHz (browser native) and downsampled to 16kHz before sending:

```typescript
// Efficient linear interpolation downsampling (not FFT-based)
const downsampleBuffer = (buffer: Float32Array, inputRate: number, outputRate: number): Int16Array => {
    const sampleRateRatio = inputRate / outputRate;  // 48000/16000 = 3
    const newLength = Math.round(buffer.length / sampleRateRatio);
    // ... linear interpolation
};
```

**Bandwidth saved:** 48kHz → 16kHz = 3x reduction. At 16-bit mono, this is `16000 * 2 = 32KB/s` vs `96KB/s` at 48kHz.

### Scheduled Audio Playback
TTS audio chunks are scheduled using `AudioContext.currentTime` to prevent gaps:

```typescript
const startTime = Math.max(nextPlayTimeRef.current, audioCtx.currentTime);
sourceNode.start(startTime);
const chunkDuration = floatData.length / 16000;
nextPlayTimeRef.current = startTime + chunkDuration;  // Chain next chunk
```

This ensures gapless playback even when chunks arrive with variable network latency.

### PlaybackBufferProcessor
Server-side: 5 audio chunks are buffered before sending to prevent micro-gaps from TTS generation variability:

```python
PlaybackBufferProcessor(buffer_size=5)
```

---

## 22.6 Database Performance

### asyncpg Connection Pooling
asyncpg maintains a connection pool automatically:
```python
# Tortoise ORM + asyncpg handles pooling
# Default pool size: min=1, max=10 connections
```

### JSONB vs TEXT
- `transcript`, `intelligence`, `assistance` are stored as JSONB (not TEXT JSON strings)
- PostgreSQL can natively query JSONB fields with operators like `@>`, `->>`
- No deserialization overhead on read — PostgreSQL parses JSON internally

### In-Memory Session State
Active sessions are stored in `app.state.active_sessions` (Python dict) — zero DB reads for in-progress sessions:
```python
# Hot path: check in-memory first
if session_id in active_sessions:
    sess = active_sessions[session_id]  # O(1) dict lookup
    # No DB call needed
```

DB is only read when loading a resumed session or listing all sessions.

---

## 22.7 Silero VAD Performance

Silero VAD runs entirely **in-process** (PyTorch model, embedded via Pipecat):
- No network call required for voice activity detection
- Inference: ~2-5ms per audio chunk on CPU
- Configuration tuned for accuracy vs. speed:
  ```python
  VADParams(
      confidence=0.8,     # High confidence threshold → fewer false positives
      min_volume=0.20,    # Ignore background noise
      start_secs=0.3,     # Ignore brief clicks/pops
      stop_secs=1.0       # 1s silence before triggering LLM (tune for speed vs accuracy)
  )
  ```

Reducing `stop_secs` to `0.5s` would make the AI respond faster but risks cutting off slow speakers.

---

## 22.8 Context Window Management

LLM context window is a performance and cost consideration:

| LLM Call | Context Sent | Est. Tokens |
|---|---|---|
| AI Interviewer | Full conversation + JD + resume | 2,000-10,000 |
| Copilot assistance | Last 20 transcript entries + JD + resume | 3,000-8,000 |
| Candidate evaluation | Single utterance + last question + JD + resume | 1,000-3,000 |
| Intelligence analysis | Last 20 entries + JD + resume | 3,000-8,000 |

**Cost optimization:** The copilot engines truncate to the last 20 messages, keeping token counts bounded regardless of session length.

---

## 22.9 Performance Benchmarks (Inferred)

Based on code analysis (not measured in production):

| Operation | Expected Latency | Notes |
|---|---|---|
| `add_message()` return | <5ms | In-memory dict append only |
| Deepgram STT (streaming) | 200-400ms | Streaming, endpointing=400ms |
| Deepgram TTS first chunk | 100-300ms | Streaming synthesis |
| DeepSeek LLM (JSON mode) | 500ms-2s | Depends on response length |
| Groq evaluation | 100-400ms | Ultra-fast inference |
| PostgreSQL session save | 5-20ms | asyncpg async write |
| Full AI response cycle | 800ms-3s | STT end → TTS first audio |
| Browser audio playback start | +50ms | Schedule + Web Audio API |
| Total perceived latency | 850ms-3.5s | From speech end to AI audio |

---

## 22.10 Scaling Considerations

**Current bottlenecks for scaling:**

1. **Single Uvicorn worker** — one process per service. Add `--workers 4` to Uvicorn for multi-core:
   ```yaml
   CMD ["uvicorn", "services.interview.src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
   ```
   
   **Caveat:** `active_sessions` and `copilot_sessions` are in-process dicts — multi-worker requires Redis for session state sharing.

2. **Playwright Chromium** — one process per Teams session, ~300MB RAM each. For >10 concurrent Teams sessions, memory becomes the bottleneck.

3. **Deepgram/DeepSeek/Groq rate limits** — external API rate limits cap concurrent sessions.

4. **PostgreSQL** — single instance, no read replicas. Acceptable for <100 concurrent sessions.

---

*Next: [Section 23 — Testing →](../23-testing/23-testing.md)*

