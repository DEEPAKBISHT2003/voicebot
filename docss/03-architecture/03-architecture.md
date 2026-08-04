# Section 3 — Complete Architecture

> **Cross-references:** [Project Overview](../01-project-overview/01-project-overview.md) | [Request Flow](../04-request-flow/04-request-flow.md) | [Docker Documentation](../10-docker/10-docker.md)

---

## 3.1 Overall System Architecture

![Overall System Architecture](images/overall_system_architecture.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
graph TB
    subgraph CLIENT["🌐 Client Layer"]
        B[Browser]
        B_WS[WebSocket - Raw PCM Audio]
        B_REST[REST - JSON]
    end

    subgraph NGINX["⚡ Nginx Reverse Proxy :80"]
        NX[Nginx]
        NX_STATIC[Static Files - React App]
        NX_PROXY_INT[/api/ws/interview/* → :8000]
        NX_PROXY_COP[/api/ws/copilot/* → :8001]
    end

    subgraph INTERVIEW["🎙️ Interview Service :8000"]
        IS_API[FastAPI HTTP Router]
        IS_WS[WebSocket Handler]
        IS_PIPE[Pipecat Pipeline]
        IS_STT[Deepgram STT]
        IS_LLM[DeepSeek LLM]
        IS_TTS[Deepgram TTS]
        IS_VAD[Silero VAD]
        IS_REPO[Session Repository]
        IS_BOT[Teams Playwright Bot]
    end

    subgraph COPILOT["🤖 Copilot Service :8001"]
        CS_API[FastAPI HTTP Router]
        CS_WS[WebSocket Handler]
        CS_ENGINE[CopilotSessionEngine]
        CS_EVAL[EvaluationService]
        CS_INTEL[IntelligenceEngine]
        CS_ASSIST[AICopilotEngine]
        CS_REPO[Copilot Repository]
        CS_SIM[Simulation API]
    end

    subgraph DB["🗄️ Data Layer"]
        PG[(PostgreSQL 15)]
        FS[File System - interviews/]
    end

    subgraph EXTERNAL["☁️ External APIs"]
        DG_STT[Deepgram STT API]
        DG_TTS[Deepgram TTS API]
        DS_LLM[DeepSeek API]
        GQ_LLM[Groq API]
        TEAMS[Microsoft Teams]
    end

    B -->|HTTP/WS| NGINX
    NGINX --> NX_STATIC
    NGINX --> NX_PROXY_INT
    NGINX --> NX_PROXY_COP

    NX_PROXY_INT --> IS_API
    NX_PROXY_INT --> IS_WS
    NX_PROXY_COP --> CS_API
    NX_PROXY_COP --> CS_WS

    IS_WS --> IS_PIPE
    IS_PIPE --> IS_STT
    IS_PIPE --> IS_VAD
    IS_PIPE --> IS_LLM
    IS_PIPE --> IS_TTS
    IS_PIPE --> IS_BOT

    IS_STT -->|API| DG_STT
    IS_TTS -->|API| DG_TTS
    IS_LLM -->|API| DS_LLM
    IS_REPO --> PG
    IS_REPO --> FS

    CS_WS --> CS_ENGINE
    CS_ENGINE --> CS_EVAL
    CS_ENGINE --> CS_INTEL
    CS_ENGINE --> CS_ASSIST
    CS_EVAL -->|API| GQ_LLM
    CS_INTEL -->|API| DS_LLM
    CS_ASSIST -->|API| DS_LLM
    CS_REPO --> PG
    CS_REPO --> FS

    IS_BOT -->|Playwright| TEAMS
    IS_BOT -->|WebSocket| CS_WS

    IS_API -->|HTTP POST| CS_API

    style CLIENT fill:#dbeafe,stroke:#3b82f6
    style NGINX fill:#fef9c3,stroke:#eab308
    style INTERVIEW fill:#dcfce7,stroke:#22c55e
    style COPILOT fill:#fce7f3,stroke:#ec4899
    style DB fill:#f3e8ff,stroke:#a855f7
    style EXTERNAL fill:#ffedd5,stroke:#f97316
```

</details>

---

## 3.2 Layered Architecture

![Layered Architecture](images/layered_architecture.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
graph TB
    subgraph P["Presentation Layer"]
        P1[React Pages - InterviewsList, NewInterview, InterviewSession]
        P2[React Pages - NewCopilot, CopilotSession]
        P3[Shared Components - Cards, Buttons, Layouts]
    end

    subgraph BL["Business Layer"]
        BL1[InterviewPromptBuilder - JD+Resume → System Instruction]
        BL2[SpeakerRoleClassifier - Hybrid Diarization]
        BL3[DecisionEngine - Strong/Partial/Weak Routing]
        BL4[UtteranceStitching - Same-Speaker Merge]
    end

    subgraph SL["Service Layer"]
        SL1[CopilotSessionEngine - Session Orchestrator]
        SL2[AICopilotEngine - Suggestion Generation]
        SL3[ConversationIntelligenceEngine - Coverage Analysis]
        SL4[CandidateEvaluationService - 6-Dimension Scoring]
        SL5[LocalPipecatPipelineBuilder - Audio Pipeline Factory]
        SL6[DocumentParserFactory - Resume Parsing]
    end

    subgraph RL["Repository Layer"]
        RL1[PostgresInterviewRepository]
        RL2[JSONInterviewRepository - dev fallback]
        RL3[CopilotRepository - PostgreSQL + File]
    end

    subgraph IL["Infrastructure Layer"]
        IL1[FastAPI Application - HTTP + WebSocket]
        IL2[Pipecat - Real-Time Audio Orchestration]
        IL3[Tortoise ORM - Async PostgreSQL]
        IL4[Nginx - Reverse Proxy + Static]
        IL5[Docker Compose - Container Orchestration]
        IL6[Deepgram SDK - STT + TTS]
        IL7[AsyncOpenAI Client - LLM Calls]
        IL8[Playwright - Browser Automation]
    end

    P --> BL
    BL --> SL
    SL --> RL
    RL --> IL

    style P fill:#dbeafe,stroke:#3b82f6
    style BL fill:#dcfce7,stroke:#22c55e
    style SL fill:#fce7f3,stroke:#ec4899
    style RL fill:#fef9c3,stroke:#eab308
    style IL fill:#f3e8ff,stroke:#a855f7
```

</details>

---

## 3.3 Deployment Architecture

![Deployment Architecture](images/deployment_architecture.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
graph TB
    subgraph HOST["Ubuntu EC2 / VM Host"]
        subgraph COMPOSE["Docker Compose Network: voicebot-net"]
            subgraph FE["voicebot-frontend"]
                FE_NGINX[Nginx :80]
                FE_STATIC[React Static Files]
            end

            subgraph INT["voicebot-interview-service"]
                INT_UV[Uvicorn :8000]
                INT_PY[Python 3.11 + PortAudio + ffmpeg]
                INT_VOL[Volume: ./interviews → /app/interviews]
            end

            subgraph COP["voicebot-copilot-service"]
                COP_UV[Uvicorn :8001]
                COP_PY[Python 3.12 + Playwright Chromium]
                COP_VOL[Volume: ./interviews → /app/interviews]
            end

            subgraph PG["voicebot-db"]
                PG_DB[PostgreSQL 15 Alpine :5432]
                PG_VOL[Volume: postgres_data]
            end
        end

        subgraph STORAGE["Host Storage"]
            S1[./interviews/ - shared bind mount]
        end
    end

    subgraph INTERNET["Internet"]
        USER[👤 User Browser]
        DG[Deepgram API]
        DS[DeepSeek API]
        GR[Groq API]
        TEAMS_NET[Microsoft Teams]
    end

    USER -->|:80| FE_NGINX
    FE_NGINX -->|/api/ws/interview/*| INT_UV
    FE_NGINX -->|/api/ws/copilot/*| COP_UV
    FE_NGINX --> FE_STATIC

    INT_UV -->|asyncpg| PG_DB
    COP_UV -->|asyncpg| PG_DB
    INT_UV -->|HTTP POST :8001| COP_UV

    INT_PY -->|HTTPS| DG
    INT_PY -->|HTTPS| DS
    COP_PY -->|HTTPS| DG
    COP_PY -->|HTTPS| DS
    COP_PY -->|HTTPS| GR
    COP_PY -->|Playwright| TEAMS_NET

    INT_VOL -.->|bind mount| S1
    COP_VOL -.->|bind mount| S1

    style HOST fill:#f0fdf4,stroke:#16a34a
    style INTERNET fill:#fff7ed,stroke:#f97316
    style COMPOSE fill:#eff6ff,stroke:#2563eb
```

</details>

---

## 3.4 Real-Time Audio Pipeline Architecture

![Realtime Audio Pipeline](images/realtime_audio_pipeline.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
flowchart LR
    subgraph BROWSER["Browser"]
        MIC[Microphone]
        SP[Script Processor]
        WSJS[WebSocket Client]
        PLAYBACK[Web Audio Playback]
    end

    subgraph PIPELINE["Pipecat Pipeline - Interview Service"]
        WSTRANS[FastAPI WebSocket Transport]
        MICGATE[MicGateProcessor - Blocks until greeting ends]
        STT[Deepgram STT - nova-2 diarize]
        USERACC[TranscriptAccumulator - User]
        USERAGG[LLM User Aggregator + Silero VAD]
        LLM[DeepSeek LLM - deepseek-v4-flash]
        ASSTACC[TranscriptAccumulator - Assistant]
        TTS[Deepgram TTS - aura-2-amalthea-en]
        PLAYBACK_BUF[PlaybackBufferProcessor]
        WSTRANS_OUT[Transport Output]
        AUDIOBUF[AudioBufferProcessor - Records WAV]
        MICUNMUTE[MicUnmuterProcessor]
        ASTAGG[LLM Assistant Aggregator]
    end

    MIC -->|48kHz Float32| SP
    SP -->|downsample to 16kHz Int16 PCM| WSJS
    WSJS -->|binary ArrayBuffer| WSTRANS
    WSTRANS --> MICGATE
    MICGATE --> STT
    STT -->|text + speaker tag| USERACC
    USERACC --> USERAGG
    USERAGG -->|LLMContext| LLM
    LLM -->|text tokens| ASSTACC
    ASSTACC --> TTS
    TTS -->|16kHz PCM audio| PLAYBACK_BUF
    PLAYBACK_BUF --> WSTRANS_OUT
    WSTRANS_OUT -->|binary ArrayBuffer| PLAYBACK
    WSTRANS_OUT --> AUDIOBUF
    AUDIOBUF --> MICUNMUTE
    MICUNMUTE --> ASTAGG

    style BROWSER fill:#dbeafe,stroke:#3b82f6
    style PIPELINE fill:#dcfce7,stroke:#22c55e
```

</details>

---

## 3.5 Copilot Service Architecture

![Copilot Service Architecture](images/copilot_service_architecture.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
flowchart TD
    subgraph INPUT["Input Sources"]
        WS_BROWSER[Browser Mic - WebSocket PCM]
        WS_BOT[Teams Bot - WebSocket PCM]
        WS_INTERVIEW[Interview Service - HTTP transcript push]
        WAV_UPLOAD[WAV File Upload - Simulation]
    end

    subgraph COPILOT_ENGINE["CopilotSessionEngine"]
        STITCH[Utterance Stitcher - Same-speaker merge]
        DISPATCH[Background Task Dispatcher - asyncio.create_task]
        TRANSCRIPT[In-Memory Transcript List]
        LAST_Q[Last Question Tracker]
    end

    subgraph PARALLEL["3x Parallel Async LLM Tasks"]
        EVAL[CandidateEvaluationService - Groq LLM - 6 dimensions]
        INTEL[ConversationIntelligenceEngine - DeepSeek - JD/Resume coverage]
        ASSIST[AICopilotEngine - DeepSeek - Suggestions]
    end

    subgraph DECISION["Decision Engine"]
        STRONG[Strong answer ≥80% → Move to next topic]
        PARTIAL[Partial answer 50-79% → 2-3 drill questions]
        WEAK[Weak answer <50% → Probing questions]
    end

    subgraph OUTPUT["Real-Time Output - WebSocket"]
        WS_DASH[Dashboard WebSocket - copilot_update JSON]
        PERSIST[PostgreSQL + File System persist]
    end

    INPUT --> STITCH
    STITCH --> TRANSCRIPT
    STITCH --> DISPATCH
    DISPATCH --> EVAL
    DISPATCH --> INTEL
    DISPATCH --> ASSIST
    EVAL --> DECISION
    DECISION --> STRONG
    DECISION --> PARTIAL
    DECISION --> WEAK
    ASSIST --> WS_DASH
    INTEL --> WS_DASH
    EVAL --> WS_DASH
    WS_DASH --> PERSIST

    style INPUT fill:#dbeafe,stroke:#3b82f6
    style COPILOT_ENGINE fill:#dcfce7,stroke:#22c55e
    style PARALLEL fill:#fce7f3,stroke:#ec4899
    style DECISION fill:#fef9c3,stroke:#eab308
    style OUTPUT fill:#f3e8ff,stroke:#a855f7
```

</details>

---

## 3.6 Teams Bot Integration Architecture

![Teams Bot Integration Sequence](images/teams_bot_integration_sequence.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant IS as Interview Service
    participant CS as Copilot Service
    participant PW as Playwright Bot
    participant TM as Microsoft Teams

    FE->>IS: POST /api/interviews/start {meeting_url}
    IS->>CS: POST /api/copilot/start {session_id, jd, resume}
    IS->>CS: POST /api/copilot/{id}/join-meeting {meeting_url}
    CS->>PW: subprocess.Popen(teams_bot.py, meeting_url, session_id)
    PW->>TM: Playwright browser opens Teams URL
    TM-->>PW: Teams meeting loads in headless Chromium
    PW->>PW: Inject CAMERA_BLOCK_JS (disable video, mute mic)
    PW->>PW: Inject INTERCEPT_JS (hook RTCPeerConnection)
    TM-->>PW: WebRTC audio streams flow to participants
    PW->>PW: Capture all participant audio via AudioContext
    PW->>IS: WebSocket ws://backend/api/ws/interview/{id}?mode=observer
    IS-->>PW: WebSocket accepted (observer mode)
    loop Every audio chunk (4096 samples @ 16kHz)
        PW->>IS: Binary PCM Int16 ArrayBuffer
        IS->>IS: Pipecat STT-only pipeline → Deepgram
        IS-->>CS: copilot_sessions[id] → add_message()
        CS->>CS: 3x parallel LLM tasks (background)
        CS-->>FE: WebSocket copilot_update JSON
    end
```

</details>

---

*Next: [Section 4 — Request Flow →](../04-request-flow/04-request-flow.md)*

