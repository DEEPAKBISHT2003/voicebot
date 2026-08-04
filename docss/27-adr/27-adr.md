# Section 27 — Architecture Decision Records

> **Cross-references:** [Project Overview](../01-project-overview/01-project-overview.md) | [AI Architecture](../08-ai-architecture/08-ai-architecture.md) | [Future Improvements](../26-future-improvements/26-future-improvements.md)

---

## ADR-01: FastAPI as the Web Framework

**Date:** Initial design  
**Status:** Active

**Context:** Needed an async Python web framework capable of handling both HTTP REST and WebSocket connections simultaneously for real-time audio streaming.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| FastAPI | Native async, WebSocket support, Pydantic validation, auto OpenAPI docs, excellent performance | Newer, smaller ecosystem than Django |
| Django + Channels | Battle-tested, large ecosystem | Django is synchronous by default; Channels adds complexity |
| Flask + Flask-SocketIO | Simple, familiar | Synchronous core, WebSocket support is bolted-on |
| aiohttp | Pure async | Low-level, less developer ergonomics, no built-in validation |

**Decision:** FastAPI

**Rationale:**
- Native async/await throughout eliminates event loop blocking
- WebSocket endpoints are first-class citizens (same decorator pattern as HTTP)
- Pydantic models provide request validation and auto-documentation
- `Depends()` injection makes session state and repository injection clean
- Starlette underneath provides excellent WebSocket performance

---

## ADR-02: Pipecat for Real-Time Audio Pipeline

**Date:** Initial design  
**Status:** Active

**Context:** Building a real-time voice AI pipeline requires orchestrating: audio transport, STT, VAD, LLM, TTS in a chained, async, streaming manner.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| Pipecat-AI | Purpose-built for this exact use case, abstracts transport/service wiring, active development | Newer library, less documentation |
| Custom asyncio chain | Full control | Significant engineering effort, prone to bugs |
| LiveKit Agents | Production-grade, cloud-native | Requires LiveKit cloud or self-hosted media server |
| Daily Bots | Managed service | Vendor lock-in, cost at scale |

**Decision:** Pipecat-AI

**Rationale:**
- Exactly solves the "chain STT + VAD + LLM + TTS" problem
- Processor pipeline pattern makes it easy to insert custom processors (MicGate, PlaybackBuffer, TranscriptAccumulator)
- Supports both local audio and WebSocket transport with same interface
- Being open-source, can be forked or patched if needed

---

## ADR-03: DeepSeek as Primary LLM

**Date:** Initial design  
**Status:** Active

**Context:** Needed a capable LLM for both conversational interview conduct and structured JSON generation for copilot suggestions.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| DeepSeek | OpenAI-compatible API, very cost-effective, strong performance | Less proven than GPT-4 |
| OpenAI GPT-4o | Industry standard, excellent quality | Expensive at scale, not open-source |
| Anthropic Claude | Strong reasoning | Higher cost, different API |
| Groq + LLaMA | Ultra-fast inference | Less capable for complex prompts |
| Mistral | Open-source, self-hostable | Lower capability for complex tasks |

**Decision:** DeepSeek (primary) + Groq (evaluation tasks)

**Rationale:**
- DeepSeek's OpenAI-compatible API means zero code changes if switching to GPT-4
- Cost is ~10x lower than GPT-4 for equivalent tasks
- `response_format={"type": "json_object"}` support enables structured output
- Groq used specifically for evaluation because its ultra-low latency (via H100) is critical for per-utterance scoring

---

## ADR-04: Deepgram for STT and TTS

**Date:** Initial design  
**Status:** Active

**Context:** Needed real-time streaming STT with speaker diarization, and natural-sounding TTS for the AI interviewer voice.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| Deepgram | Best-in-class streaming STT latency, native diarization, Pipecat has built-in support | Cost per minute |
| OpenAI Whisper (self-hosted) | Free, open-source | Batch only (not streaming), high GPU cost for real-time |
| Google Speech-to-Text | Reliable, diarization support | Higher latency than Deepgram for streaming |
| AWS Transcribe | AWS-native | Complex setup, mediocre streaming performance |
| ElevenLabs TTS | Very natural voice | Higher cost, no Pipecat native support |

**Decision:** Deepgram (STT + TTS)

**Rationale:**
- Deepgram is consistently benchmarked as the lowest-latency streaming STT
- Native `diarize=True` with `endpointing` support is exactly what's needed
- Pipecat has first-class `DeepgramSTTService` and `DeepgramTTSService` — no custom integration
- Single vendor for both STT and TTS simplifies API key management
- Aura-2 voices are natural enough for interview use cases

---

## ADR-05: PostgreSQL as the Database

**Date:** Initial design  
**Status:** Active

**Context:** Sessions contain structured metadata plus large semi-structured JSON blobs (transcripts with nested evaluations).

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| PostgreSQL | JSONB native support, ACID, asyncpg for async, mature | Slightly more ops overhead than SQLite |
| MongoDB | Native JSON document storage | Separate deployment, no ACID transactions, additional learning curve |
| SQLite | Zero setup, file-based | Not suitable for concurrent writes, no native async driver |
| DynamoDB | Serverless, auto-scaling | Vendor lock-in, complex queries |

**Decision:** PostgreSQL 15

**Rationale:**
- JSONB columns allow flexible schema evolution — transcript structure can grow without migrations
- asyncpg provides true async access matching FastAPI's async model
- Tortoise ORM simplifies schema definition and async queries
- ACID transactions ensure transcript entries are never partially saved
- Single database for both services reduces operational complexity (same connection string)

---

## ADR-06: No Authentication in V1

**Date:** Initial design  
**Status:** Temporary — to be replaced

**Context:** Under time pressure to ship a working product for internal demos. Implementing a full auth system (JWT, refresh tokens, RBAC) requires 1-2 weeks.

**Decision:** Skip authentication in V1. Use UUID session IDs as the only access control mechanism.

**Rationale:**
- Product was initially deployed to a private internal network
- All users are trusted employees
- UUID provides practical obscurity (122 bits of entropy)
- CORS restricts browser origins to known domains

**Consequences:**
- Any user with a session URL can view/modify/stop that session
- No audit trail of who started which interview
- Cannot implement per-user data isolation

**Migration path:** See [ADR Future Auth Implementation](../26-future-improvements/26-future-improvements.md#auth-01-implement-authentication)

---

## ADR-07: Docker Compose for Deployment

**Date:** Initial design  
**Status:** Active (for current scale)

**Context:** Needed a simple, reproducible deployment that any developer could run locally and that could be pushed to a VM.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| Docker Compose | Simple, reproducible, no cloud dependency, `docker compose up` | Single-node, no orchestration |
| Kubernetes (K8s) | Auto-scaling, self-healing, cloud-native | Complex, overkill for initial scale |
| AWS ECS | Managed, integrates with ECR/ALB | AWS lock-in, more setup |
| Bare metal / systemd | Simplest | No isolation, environment drift |

**Decision:** Docker Compose (with Kubernetes manifests prepared)

**Rationale:**
- `docker compose up --build` is the entire deployment command — zero learning curve for contributors
- Same containers run locally and in production — eliminates "works on my machine"
- Kubernetes manifests already exist in `infrastructure/kubernetes/` for future migration
- At current scale (<10 concurrent sessions), Compose is entirely sufficient

---

## ADR-08: Playwright for Teams Integration

**Date:** After initial release  
**Status:** Active (with known limitations)

**Context:** Needed to integrate with Microsoft Teams meetings without requiring meeting organizers to install any add-ins or bots.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| Playwright browser automation | Works immediately, no Azure setup, no Teams admin permissions | Fragile (UI changes break it), unofficial |
| Microsoft Bot Framework | Official, stable, full Teams API | Requires Azure Bot registration, admin consent, 2-3 week setup |
| Teams Calling API (Graph API) | Official REST API | Requires special Microsoft permissions, complex OAuth |
| No Teams integration | Simple | Doesn't solve the customer use case |

**Decision:** Playwright automation (tactical)

**Rationale:**
- Immediate value delivery without Azure bureaucracy
- Works for the primary use case (demo meetings with guest access)
- Planned replacement with Bot Framework once the product validates demand

**Risk mitigation:**
- Teams UI changes are the main failure mode — bot join logic is isolated in `teams_bot.py` and easy to update
- Session is unaffected if bot fails — copilot works without Teams audio via browser mic mode

---

## ADR-09: Microservices Architecture (2 services)

**Date:** Initial design  
**Status:** Active

**Context:** The interview pipeline (real-time audio, STT, LLM, TTS) and the copilot engine (parallel LLM analysis, WebSocket dashboard) have different scaling profiles, different Python version requirements, and different runtime dependencies.

**Decision:** Split into two services: `interview-service` (Python 3.11) and `copilot-service` (Python 3.12 + Playwright)

**Rationale:**
- Playwright requires Python 3.12 and ~600MB of Chromium — no reason to include this in the interview service
- Interview service can be scaled independently of copilot service
- Clear domain separation: voice pipeline vs. AI analysis
- Services communicate via HTTP (session init) and shared DB (state) — minimal coupling

**Trade-offs accepted:**
- Two services to maintain, monitor, and deploy
- Inter-service HTTP calls add latency to session initialization (~50ms)
- Shared database creates implicit coupling (both services write to the same DB)

---

## ADR-10: Asyncio Background Tasks (No Celery)

**Date:** Initial design  
**Status:** Active

**Context:** The copilot engine needs to run 3 LLM tasks per message without blocking the main WebSocket response path.

**Options Considered:**

| Option | Pros | Cons |
|---|---|---|
| asyncio.create_task() | Zero infrastructure, same process | Tasks lost on process restart |
| Celery + Redis broker | Distributed, persistent, retry support | Adds Redis + Celery workers to ops |
| FastAPI BackgroundTasks | Framework-native | Sequential, not truly parallel |
| asyncio.gather() | Parallel within a request | Blocks response until all tasks complete |

**Decision:** `asyncio.create_task()` for fire-and-forget background LLM tasks

**Rationale:**
- LLM tasks are triggered at WebSocket message rate — results pushed via WS when ready
- No need for task persistence — if service restarts, in-progress sessions reconnect and fresh analysis runs
- Celery would add Redis, worker containers, and task routing complexity for a use case that doesn't require it
- `asyncio.create_task()` + `asyncio.gather(return_exceptions=True)` gives parallel execution with proper error isolation

---

*Next: [Section 28 — All Diagrams →](../28-diagrams/28-diagrams.md)*

