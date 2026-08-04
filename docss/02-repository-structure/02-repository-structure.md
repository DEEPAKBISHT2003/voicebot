# Section 2 — Repository Structure

> **Cross-references:** [Project Overview](../01-project-overview/01-project-overview.md) | [Backend Docs](../17-backend/17-backend.md) | [Frontend Docs](../16-frontend/16-frontend.md)

---

## 2.1 Top-Level Layout

```
voicebot/
├── services/                    # Backend microservices
│   ├── interview/               # Interview Service (port 8000)
│   └── copilot/                 # Copilot Service (port 8001)
├── frontend-new/                # React + Vite frontend
├── packages/                    # Shared TypeScript domain packages (WIP stubs)
├── infrastructure/              # Kubernetes manifests (future)
├── docs/                        # Legacy documentation folder
├── docss/                       # THIS documentation
├── docker-compose.yml           # Full stack orchestration
├── .env                         # Runtime secrets (git-ignored)
├── .env.example                 # Documented env template
├── .github/workflows/ci.yml     # GitHub Actions CI/CD
├── .dockerignore                # Docker build exclusions
├── backend_api_docs.md          # Legacy API quick-reference
├── features_guide.md            # Product feature guide
└── AGENTS.md                    # AI-DLC workflow config
```

---

## 2.2 `/services/interview/` — Interview Service

**Purpose:** The core real-time voice interview engine. Manages sessions, runs the Pipecat audio AI pipeline, handles WebSocket audio streaming, controls the microphone gate, and spawns the Teams Playwright bot.

```
services/interview/
├── Dockerfile                   # Python 3.11-slim, PortAudio, ffmpeg
├── requirements.txt             # Python dependencies
└── src/
    ├── main.py                  # FastAPI app factory, lifespan, CORS, router mount
    ├── api/
    │   ├── interviews.py        # All HTTP + WebSocket interview endpoints
    │   └── deps.py              # FastAPI dependency injection (repo, active_sessions)
    ├── core/
    │   ├── config.py            # Settings (Pydantic BaseSettings, env vars)
    │   └── interfaces/
    │       └── pipeline_builder.py  # IPipelineBuilder interface
    ├── models/
    │   └── session.py           # Tortoise ORM model for interview_sessions table
    ├── parsers/
    │   ├── factory.py           # DocumentParserFactory (PDF/DOCX/TXT routing)
    │   ├── pdf_parser.py        # PyPDF2-based PDF text extractor
    │   ├── docx_parser.py       # python-docx DOCX extractor
    │   └── txt_parser.py        # Plain text parser
    ├── pipeline/
    │   ├── builder.py           # LocalPipecatPipelineBuilder — main pipeline factory
    │   ├── accumulator.py       # TranscriptAccumulator — intercepts STT frames
    │   ├── playback_buffer.py   # PlaybackBufferProcessor — prevents audio jitter
    │   ├── mic_gate.py          # MicGateProcessor + MicUnmuterProcessor
    │   ├── serializer.py        # RawPCMAudioSerializer (WebSocket binary framing)
    │   └── teams_bot.py         # Playwright Chromium Teams meeting bot
    ├── prompts/
    │   └── interview_prompt.py  # InterviewPromptBuilder — system instruction builder
    └── repositories/
        ├── json_repo.py         # File-system JSON session store (dev fallback)
        └── postgres_repo.py     # PostgreSQL session store (production)
```

### Key File Interactions
| File | Depends On | Used By |
|---|---|---|
| `api/interviews.py` | `pipeline/builder.py`, `prompts/`, `repositories/` | Nginx → FastAPI router |
| `pipeline/builder.py` | Pipecat, Deepgram, DeepSeek, `accumulator.py` | `api/interviews.py` |
| `pipeline/teams_bot.py` | Playwright, `api/interviews.py` WS URL | `api/interviews.py` (subprocess) |
| `prompts/interview_prompt.py` | JD + resume strings | `api/interviews.py` (WebSocket) |
| `core/config.py` | `.env` / environment | All modules via `Settings` |

---

## 2.3 `/services/copilot/` — Copilot Service

**Purpose:** The real-time interviewer assistant. Listens to a live interview (via WebSocket audio stream or from the Interview Service), runs 3 parallel LLM tasks per candidate utterance, and pushes structured suggestions to the interviewer's browser dashboard.

```
services/copilot/
├── Dockerfile                   # Python 3.12-slim, Playwright Chromium
├── requirements.txt             # Python dependencies
└── src/
    ├── main.py                  # FastAPI app factory, DB init, router mounts
    ├── router.py                # Primary REST API routes (copilot CRUD)
    ├── api/
    │   ├── router.py            # Secondary router (duplicate mount — see note)
    │   └── simulation.py        # WAV upload + simulation WebSocket endpoint
    ├── core/
    │   └── config.py            # CopilotSettings (env vars)
    ├── engine/
    │   ├── session.py           # CopilotSessionEngine — master session state manager
    │   ├── copilot.py           # AICopilotEngine — generates follow-up Qs and tips
    │   └── intelligence.py     # ConversationIntelligenceEngine — JD/resume coverage
    ├── models/
    │   └── session.py           # Tortoise ORM model for copilot_sessions
    ├── pipeline/
    │   └── builder.py           # Observer-mode Pipecat pipeline (STT only)
    ├── services/
    │   ├── evaluation.py        # CandidateEvaluationService — 6-dimension scoring
    │   └── repository.py        # CopilotRepository — PostgreSQL + file system
    └── websocket/
        └── handler.py           # Dual-mode WebSocket: audio_producer + dashboard
```

> **Note (inferred):** `router.py` and `api/router.py` both register overlapping routes and are both mounted in `main.py`. This appears to be a migration artifact where routes were refactored to `api/router.py` but the original `router.py` was not removed. Both files currently register `/api/copilot/*` routes. The `api/router.py` version should be treated as authoritative.

### Key File Interactions
| File | Depends On | Used By |
|---|---|---|
| `engine/session.py` | `engine/copilot.py`, `engine/intelligence.py`, `services/evaluation.py` | `router.py`, `websocket/handler.py` |
| `engine/copilot.py` | DeepSeek API (AsyncOpenAI), transcript | `engine/session.py` |
| `engine/intelligence.py` | DeepSeek/Groq API, transcript | `engine/session.py` |
| `services/evaluation.py` | Groq API, candidate utterance | `engine/session.py` |
| `websocket/handler.py` | `engine/session.py`, Pipecat observer pipeline | Nginx → WebSocket |
| `api/simulation.py` | Deepgram REST, `engine/session.py` | Nginx → WebSocket |

---

## 2.4 `/frontend-new/` — React Frontend

**Purpose:** Browser-based UI for both interview modes (AI voice interview and copilot dashboard). Handles WebSocket binary audio streaming using the Web Audio API and renders real-time copilot suggestions.

```
frontend-new/
├── Dockerfile                   # Node build → Nginx static serving
├── nginx.conf                   # Nginx config: proxy /api/* to backend services
├── index.html                   # Vite entry point
├── package.json                 # Dependencies + scripts
├── vite.config.ts               # Vite build config
├── tsconfig.json                # TypeScript compiler config
├── .oxlintrc.json               # Oxlint linting config
└── src/
    ├── main.tsx                 # React root, QueryClientProvider, Router
    ├── App.tsx                  # Route declarations
    ├── api/
    │   ├── axios.ts             # Interview service Axios instance (base URL)
    │   ├── copilot-axios.ts     # Copilot service Axios instance
    │   ├── interview.ts         # Interview REST API functions
    │   └── copilot.ts           # Copilot REST API functions
    ├── hooks/
    │   ├── useInterviewAudio.ts # WebSocket + Web Audio API for voice interview
    │   └── useCopilotAudio.ts   # WebSocket state manager for copilot dashboard
    ├── interview/
    │   └── pages/
    │       ├── InterviewsList.tsx   # List all sessions
    │       ├── NewInterview.tsx     # Create session (JD + resume input)
    │       └── InterviewSession.tsx # Live AI voice interview UI
    ├── copilot/
    │   └── pages/
    │       ├── NewCopilot.tsx       # Create copilot session
    │       └── CopilotSession.tsx   # Live copilot dashboard
    ├── components/              # Shared UI components (buttons, cards, etc.)
    ├── types/                   # TypeScript interfaces and type definitions
    └── utils/                   # Shared utilities
```

### Key File Interactions
| File | Depends On | Used By |
|---|---|---|
| `hooks/useInterviewAudio.ts` | WebSocket, Web Audio API, `api/axios.ts` | `InterviewSession.tsx` |
| `hooks/useCopilotAudio.ts` | WebSocket, `api/copilot.ts` | `CopilotSession.tsx` |
| `api/interview.ts` | `api/axios.ts` | Interview pages |
| `api/copilot.ts` | `api/copilot-axios.ts` | Copilot pages |
| `App.tsx` | React Router, all pages | `main.tsx` |

---

## 2.5 `/packages/` — Shared TypeScript Packages (WIP)

**Purpose:** Domain-model, adapter, and type packages intended to be shared across frontend and any future Node.js services.

```
packages/
├── domain-models/    # Core domain entity types
├── adapters/         # External service adapter interfaces
├── infrastructure/   # Infrastructure-level utilities
└── types/            # Shared TypeScript type definitions
```

> **Status (inferred):** These packages contain `package.json` scaffolding but source files are not yet fully implemented. They represent a future monorepo architecture intention and are not currently imported by the frontend or backend services.

---

## 2.6 `/infrastructure/` — Kubernetes Manifests

**Purpose:** Production-grade Kubernetes deployment manifests for scaling beyond Docker Compose.

```
infrastructure/
└── kubernetes/
    ├── interview-service/   # Deployment, Service, ConfigMap
    ├── copilot-service/     # Deployment, Service, ConfigMap
    ├── frontend/            # Deployment, Service, Ingress
    └── postgres/            # StatefulSet, PVC, Service
```

> **Status (inferred):** Present in repository but Docker Compose is the current primary deployment method. Kubernetes manifests are likely used for staging/production cloud deployment.

---

## 2.7 `/docs/` — Legacy Documentation

**Purpose:** Original documentation set created during initial development. Superseded by this `/docss/` documentation.

```
docs/
├── 01-requirements/     # Business rules, feature list, functional requirements
├── 02-technical/        # Backend/frontend workflow, tech stack, system workflow
├── 03-architecture/     # HLD, LLD, component diagrams, sequence diagrams
├── 04-database/         # Schema, ER diagram, migrations, SQL
└── 05-api/              # API overview, endpoints, WebSocket, OpenAPI spec
```

---

## 2.8 `/.github/workflows/ci.yml` — CI/CD Pipeline

**Purpose:** Automated quality gates on every push/PR to ensure code correctness before deployment.

```yaml
# Stages:
# 1. lint        — ruff (Python linter), oxlint (TypeScript linter)
# 2. test        — pytest with coverage → Codecov upload
# 3. build       — Docker image builds for all 3 services
# 4. integration — Integration tests against running containers
# 5. deploy      — DockerHub push (main branch only)
```

See [Section 24 — Deployment Guide](../24-deployment/24-deployment.md) for full CI/CD details.

---

## 2.9 Root Configuration Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Orchestrates all 4 containers with env vars, volumes, networks, health checks |
| `.env.example` | Documents all required environment variables with descriptions |
| `.env` | Actual secrets (never committed — git-ignored) |
| `.dockerignore` | Excludes `node_modules`, `.venv`, `__pycache__`, `interviews/` from Docker context |
| `.gitignore` | Excludes `.env`, `node_modules`, `__pycache__`, `dist`, `interviews/` |
| `backend_api_docs.md` | Legacy quick-reference API documentation |
| `features_guide.md` | Product-level feature overview for non-technical stakeholders |

---

## 2.10 Runtime-Generated Directories

These directories are created at runtime and are git-ignored:

```
interviews/                       # Session data root (shared Docker volume)
├── {session_id}/                 # Interview session directory
│   ├── session.json              # Full session state (transcript, JD, resume)
│   ├── jd.txt                    # Job description text
│   ├── resume.txt                # Resume text (parsed)
│   ├── resume.pdf                # Original resume upload
│   ├── recording.wav             # Full session audio recording
│   └── uploaded_audio.wav        # Simulation mode uploaded WAV
└── copilots/
    └── {session_id}/             # Copilot session directory
        └── session.json          # Copilot session state
```

---

*Next: [Section 3 — Complete Architecture →](../03-architecture/03-architecture.md)*

