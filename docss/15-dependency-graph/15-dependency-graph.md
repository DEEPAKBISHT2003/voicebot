# Section 15 — Dependency Graph

> **Cross-references:** [Architecture](../03-architecture/03-architecture.md) | [External Integrations](../18-integrations/18-integrations.md) | [Configuration](../12-configuration/12-configuration.md)

---

## 15.1 Full System Dependency Graph

![Full System Dependency Graph](images/full_system_dependency_graph.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
graph LR
    subgraph FE["Frontend"]
        REACT[React 19]
        VITE[Vite 8]
        TS[TypeScript]
        TAILWIND[TailwindCSS 4]
        ROUTER[React Router 7]
        QUERY[TanStack Query 5]
        AXIOS[Axios]
        ZOD[Zod]
        LUCIDE[Lucide React]
    end

    subgraph IS["Interview Service"]
        FASTAPI_IS[FastAPI]
        PIPECAT[Pipecat-AI 1.6.0]
        TORTOISE_IS[Tortoise ORM]
        ASYNCPG_IS[asyncpg]
        DEEPGRAM_SDK[Deepgram Python SDK]
        OPENAI_SDK_IS[AsyncOpenAI]
        LOGURU_IS[loguru]
        HTTPX_IS[httpx]
        PYPDF[PyPDF2]
        DOCX[python-docx]
        UVICORN_IS[uvicorn]
    end

    subgraph CS["Copilot Service"]
        FASTAPI_CS[FastAPI]
        TORTOISE_CS[Tortoise ORM]
        ASYNCPG_CS[asyncpg]
        OPENAI_SDK_CS[AsyncOpenAI]
        GROQ_SDK[AsyncOpenAI - Groq endpoint]
        PLAYWRIGHT[Playwright]
        LOGURU_CS[loguru]
        HTTPX_CS[httpx]
        PIPECAT_CS[Pipecat-AI - observer only]
        UVICORN_CS[uvicorn]
        NLTK[nltk]
    end

    subgraph INFRA["Infrastructure"]
        NGINX[Nginx]
        POSTGRES[(PostgreSQL 15)]
        DOCKER[Docker Compose]
        FS[(File System)]
    end

    subgraph EXTERNAL["External Services"]
        DG[Deepgram API]
        DS[DeepSeek API]
        GQ[Groq API]
        TEAMS[Microsoft Teams]
    end

    REACT --> ROUTER
    REACT --> QUERY
    REACT --> AXIOS
    REACT --> TAILWIND
    REACT --> ZOD
    REACT --> LUCIDE
    VITE --> REACT
    TS --> REACT

    FASTAPI_IS --> PIPECAT
    FASTAPI_IS --> TORTOISE_IS
    FASTAPI_IS --> UVICORN_IS
    PIPECAT --> DEEPGRAM_SDK
    PIPECAT --> OPENAI_SDK_IS
    DEEPGRAM_SDK --> DG
    OPENAI_SDK_IS --> DS
    TORTOISE_IS --> ASYNCPG_IS
    ASYNCPG_IS --> POSTGRES
    HTTPX_IS --> FASTAPI_CS
    IS -->|spawns subprocess| PLAYWRIGHT
    PLAYWRIGHT --> TEAMS

    FASTAPI_CS --> TORTOISE_CS
    FASTAPI_CS --> UVICORN_CS
    FASTAPI_CS --> PIPECAT_CS
    OPENAI_SDK_CS --> DS
    GROQ_SDK --> GQ
    TORTOISE_CS --> ASYNCPG_CS
    ASYNCPG_CS --> POSTGRES
    PIPECAT_CS --> DEEPGRAM_SDK

    NGINX --> FE
    NGINX --> IS
    NGINX --> CS
    DOCKER --> NGINX
    DOCKER --> IS
    DOCKER --> CS
    DOCKER --> POSTGRES
    IS --> FS
    CS --> FS

    style FE fill:#dbeafe,stroke:#3b82f6
    style IS fill:#dcfce7,stroke:#22c55e
    style CS fill:#fce7f3,stroke:#ec4899
    style INFRA fill:#f3e8ff,stroke:#a855f7
    style EXTERNAL fill:#ffedd5,stroke:#f97316
```

</details>

---

## 15.2 Python Package Dependencies

### Interview Service `requirements.txt`

| Package | Purpose |
|---|---|
| `fastapi` | HTTP + WebSocket framework |
| `uvicorn[standard]` | ASGI server |
| `pipecat-ai` | Real-time audio pipeline |
| `pipecat-ai[deepgram]` | Deepgram STT/TTS integration |
| `pipecat-ai[openai]` | OpenAI/DeepSeek LLM integration |
| `pipecat-ai[silero]` | Silero VAD |
| `tortoise-orm` | Async ORM |
| `asyncpg` | Async PostgreSQL driver |
| `deepgram-sdk` | Deepgram Python SDK |
| `openai` | OpenAI SDK (for DeepSeek compat) |
| `httpx` | Async HTTP client |
| `PyPDF2` | PDF text extraction |
| `python-docx` | DOCX text extraction |
| `python-multipart` | File upload support |
| `pydantic-settings` | Settings via env vars |
| `loguru` | Structured logging |
| `pyaudio` | Local audio device (fallback) |

### Copilot Service `requirements.txt`

| Package | Purpose |
|---|---|
| `fastapi` | HTTP + WebSocket framework |
| `uvicorn[standard]` | ASGI server |
| `pipecat-ai` | Observer pipeline |
| `pipecat-ai[deepgram]` | Deepgram STT |
| `tortoise-orm` | Async ORM |
| `asyncpg` | PostgreSQL driver |
| `openai` | DeepSeek + Groq SDK |
| `playwright` | Headless browser Teams bot |
| `httpx` | HTTP client |
| `pydantic-settings` | Settings |
| `loguru` | Logging |
| `nltk` | Natural language processing |
| `audioop-lts` | Audio resampling (simulation) |
| `python-multipart` | File upload |

### Frontend `package.json`

| Package | Version | Purpose |
|---|---|---|
| `react` | ^19.0.0 | UI framework |
| `react-dom` | ^19.0.0 | DOM rendering |
| `react-router-dom` | ^7.0.0 | Client routing |
| `@tanstack/react-query` | ^5.0.0 | Server state |
| `axios` | ^1.x | HTTP client |
| `react-hook-form` | ^7.x | Form state |
| `zod` | ^3.x | Schema validation |
| `@hookform/resolvers` | ^3.x | Zod + RHF bridge |
| `lucide-react` | ^0.x | Icon library |
| `tailwindcss` | ^4.0.0 | CSS utility |
| `vite` | ^8.0.0 | Build tool |
| `typescript` | ^5.x | TypeScript compiler |
| `@vitejs/plugin-react` | ^4.x | Vite React plugin |

---

## 15.3 Service Dependency Matrix

| Service | Depends On | Depended On By |
|---|---|---|
| PostgreSQL | — | Interview Service, Copilot Service |
| Interview Service | PostgreSQL, Deepgram API, DeepSeek API, Copilot Service (HTTP) | Frontend (via Nginx), Teams Bot |
| Copilot Service | PostgreSQL, Deepgram API, DeepSeek API, Groq API | Frontend (via Nginx), Interview Service |
| Frontend (Nginx) | Interview Service, Copilot Service | Browser clients |
| Teams Bot (subprocess) | Interview Service (WebSocket), Microsoft Teams | Interview Service (spawner) |

---

## 15.4 Data Flow Dependency Graph

![Data Flow Dependency Graph](images/data_flow_dependency_graph.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
graph TD
    subgraph SOURCES["Data Sources"]
        MIC[Browser Microphone]
        TEAMS_AUDIO[Teams WebRTC Audio]
        WAV[Uploaded WAV File]
        JD[Job Description text]
        RESUME[Resume text]
    end

    subgraph PROCESSING["Processing Pipeline"]
        STT_STREAM[Deepgram Streaming STT]
        STT_REST[Deepgram REST STT]
        LLM_INT[DeepSeek LLM - Interviewer]
        LLM_ASSIST[DeepSeek LLM - Copilot]
        LLM_INTEL[DeepSeek LLM - Intelligence]
        LLM_EVAL[Groq LLaMA - Evaluation]
        TTS_ENGINE[Deepgram TTS]
        VAD_ENGINE[Silero VAD]
    end

    subgraph STORAGE["Storage Layer"]
        POSTGRES_DB[(PostgreSQL)]
        FILE_SYS[(File System)]
    end

    subgraph OUTPUT["Output"]
        AUDIO_OUT[Browser Audio Playback]
        DASHBOARD[Copilot Dashboard]
        REPORT[Post-Interview Report]
    end

    MIC --> STT_STREAM
    TEAMS_AUDIO --> STT_STREAM
    WAV --> STT_REST

    JD --> LLM_INT
    JD --> LLM_ASSIST
    JD --> LLM_INTEL
    JD --> LLM_EVAL

    RESUME --> LLM_INT
    RESUME --> LLM_ASSIST
    RESUME --> LLM_INTEL
    RESUME --> LLM_EVAL

    STT_STREAM --> VAD_ENGINE
    VAD_ENGINE --> LLM_INT
    LLM_INT --> TTS_ENGINE
    TTS_ENGINE --> AUDIO_OUT

    STT_STREAM --> LLM_ASSIST
    STT_STREAM --> LLM_INTEL
    STT_STREAM --> LLM_EVAL
    STT_REST --> LLM_ASSIST
    STT_REST --> LLM_INTEL
    STT_REST --> LLM_EVAL

    LLM_ASSIST --> DASHBOARD
    LLM_INTEL --> DASHBOARD
    LLM_EVAL --> DASHBOARD

    STT_STREAM --> POSTGRES_DB
    LLM_EVAL --> POSTGRES_DB
    LLM_ASSIST --> POSTGRES_DB
    POSTGRES_DB --> REPORT
    MIC --> FILE_SYS
```

</details>

---

*Next: [Section 16 — Frontend Documentation →](../16-frontend/16-frontend.md)*

