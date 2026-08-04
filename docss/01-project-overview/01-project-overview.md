# Section 1 — Project Overview

> **Cross-references:** [Architecture](../03-architecture/03-architecture.md) | [AI Architecture](../08-ai-architecture/08-ai-architecture.md) | [API Documentation](../06-api/06-api.md)

---

## 1.1 What This Project Does

**VoiceBot** is an AI-powered real-time voice interview and interview-copilot platform. It enables organizations to:

1. **Conduct fully automated AI voice interviews** — an AI interviewer asks questions, listens to candidate responses via microphone, evaluates answers in real time, and adapts follow-up questions dynamically.
2. **Provide a real-time AI copilot for human interviewers** — an interviewer conducting a live interview (in-person, on Teams, or uploaded as a recording) receives instant AI-generated follow-up question suggestions, candidate evaluation scores, skill coverage metrics, and post-interview reports — all displayed in a live dashboard.

---

## 1.2 Problem It Solves

| Pain Point | How VoiceBot Solves It |
|---|---|
| Interviewer inconsistency — different interviewers ask different questions | AI copilot ensures consistent coverage against JD and resume |
| Missed follow-ups — interviewers don't always probe weak answers | Real-time follow-up question suggestions based on candidate evaluation scores |
| Post-interview subjectivity | Quantified 6-dimension candidate evaluation (accuracy, confidence, completeness, practical knowledge, communication, production experience) |
| Time-consuming scheduling | AI voice interviewer conducts first-round screening autonomously |
| No coverage tracking | JD skill coverage map and resume claim verification questions generated live |
| Teams meeting inaccessibility | Playwright bot joins Teams meetings, intercepts audio, and feeds it to the copilot engine |

---

## 1.3 Target Users

- **Recruiters and HR teams** — want structured, consistent first-round interviews without manual effort.
- **Technical interviewers** — need real-time coaching and question suggestions during live technical interviews.
- **Engineering managers** — require post-interview evaluation reports and candidate comparison across sessions.
- **Startups and scale-ups** — need to run high-volume interview pipelines without large recruiting teams.

---

## 1.4 Business Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VOICEBOT BUSINESS WORKFLOW                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. SETUP                                                           │
│     Recruiter pastes Job Description + uploads Candidate Resume     │
│     (Optional: adds custom interview instructions)                  │
│                                                                     │
│  2. INTERVIEW MODES (choose one)                                    │
│                                                                     │
│     a) AI Voice Interview                                           │
│        Browser mic → AI Interviewer speaks & listens               │
│        Deepgram STT → DeepSeek LLM → Deepgram TTS                 │
│                                                                     │
│     b) Human + Copilot (Live)                                       │
│        Human interviewer conducts the interview                    │
│        Copilot listens via browser mic, shows real-time tips        │
│                                                                     │
│     c) Human + Copilot (Teams)                                      │
│        Playwright bot joins Teams meeting URL                       │
│        Intercepts WebRTC audio, feeds to copilot engine             │
│                                                                     │
│     d) Simulation (Uploaded Recording)                              │
│        WAV file uploaded → normalized to 16kHz PCM                 │
│        Streamed through Deepgram STT → copilot engine               │
│                                                                     │
│  3. REAL-TIME ANALYSIS (all modes)                                  │
│     Per utterance (async, parallel, <5ms return):                  │
│     ├─ Candidate Evaluation (6 dimensions, 0–100 rating)            │
│     ├─ Conversation Intelligence (coverage maps, sentiment)         │
│     └─ AI Copilot Suggestions (follow-up Qs, missing concepts)     │
│                                                                     │
│  4. POST-INTERVIEW REPORT                                           │
│     Finalize button → full scoring report generated                 │
│     Transcript, evaluation, recommendations exported                │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1.5 Main Objectives

1. **Sub-5ms transcript-to-insight latency** — copilot suggestions appear before the next question is asked.
2. **Speaker-accurate diarization** — hybrid Deepgram + linguistic-pattern classifier to correctly label Candidate vs Interviewer speech.
3. **Teams meeting integration** — zero-friction copilot for existing interview workflows without changing tools.
4. **Structured evaluation** — eliminate subjective "gut feeling" with quantified 6-dimension scoring.
5. **Parallel AI processing** — 3 LLM tasks run concurrently per message to minimize wall-clock latency.
6. **Simulation mode** — replay past interviews through the copilot without live sessions.

---

## 1.6 High-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     CLIENT LAYER                         │
│  React 19 + Vite + TypeScript + TailwindCSS              │
│  Browser  →  WebSocket (PCM audio)  +  REST (JSON)       │
└────────────────────────┬─────────────────────────────────┘
                         │ HTTP/WS
                         ▼
┌──────────────────────────────────────────────────────────┐
│                   NGINX REVERSE PROXY                    │
│  /api/ws/interview/*  → interview-service:8000           │
│  /api/ws/copilot/*   → copilot-service:8001             │
│  /*                  → frontend (static files)          │
└────────────┬─────────────────────────┬───────────────────┘
             │                         │
             ▼                         ▼
┌────────────────────┐    ┌────────────────────────────┐
│  Interview Service │    │     Copilot Service        │
│  FastAPI  :8000    │    │     FastAPI  :8001         │
│                    │    │                            │
│  Pipecat Pipeline  │    │  CopilotSessionEngine      │
│  ├─ Deepgram STT   │◄───│  ├─ EvaluationService      │
│  ├─ Silero VAD     │    │  ├─ IntelligenceEngine     │
│  ├─ DeepSeek LLM   │    │  └─ AICopilotEngine        │
│  └─ Deepgram TTS   │    │                            │
│                    │    │  Teams Playwright Bot       │
│  Speaker Diarize   │    │  WebSocket Handler          │
└──────────┬─────────┘    └────────────┬───────────────┘
           │                           │
           └─────────┬─────────────────┘
                     ▼
        ┌────────────────────────┐
        │   PostgreSQL 15        │
        │   interview_sessions   │
        │   copilot_sessions     │
        └────────────────────────┘
        ┌────────────────────────┐
        │   File System          │
        │  ./interviews/{id}/    │
        │  session.json          │
        │  recording.wav         │
        │  resume.pdf            │
        └────────────────────────┘
```

---

## 1.7 Major Features

### Interview Service
| Feature | Description |
|---|---|
| AI Voice Interview | Full real-time voice pipeline: STT → LLM → TTS via Pipecat |
| WebSocket Streaming | Raw 16kHz PCM audio bidirectional streaming |
| Resume Parsing | PDF/DOCX/TXT parsing via DocumentParserFactory |
| Session Recording | Entire interview audio saved as WAV |
| Speaker Diarization | Hybrid linguistic + Deepgram pitch-tag classifier |
| Teams Bot Spawning | Playwright subprocess spawned for Teams URL |
| Mic Gate | AI greeting plays first before enabling candidate mic |
| Simulation Mode | Pre-recorded WAV replayed through pipeline |

### Copilot Service
| Feature | Description |
|---|---|
| Real-Time Suggestions | AI-generated follow-up questions per candidate utterance |
| 3-Parallel LLM Tasks | Evaluation + Intelligence + Assistance run concurrently |
| 6-Dimension Evaluation | technical_accuracy, confidence, completeness, practical_knowledge, communication, production_experience |
| JD Skill Coverage | Tracks which JD requirements have been discussed |
| Resume Claim Verification | Generates questions to verify stated projects/experience |
| Utterance Stitching | Merges rapid same-speaker chunks into unified thoughts |
| Decision Engine | Strong/Partial/Weak answer classification drives question routing |
| Teams Meeting Bot | Playwright Chromium joins Teams, intercepts WebRTC audio |
| Simulation Streaming | WAV file → Deepgram REST → copilot engine |
| Report Generation | Full post-interview evaluation and recommendation report |

---

## 1.8 Technology Stack

### Programming Languages
| Language | Version | Used For |
|---|---|---|
| Python | 3.11 / 3.12 | Both backend microservices |
| TypeScript | 5.x | Frontend (React app) |
| JavaScript | ES2022+ | Browser audio processing (Web Audio API) |
| SQL | PostgreSQL dialect | Database schema, migrations |
| YAML | — | Docker Compose, GitHub Actions CI |

### Frameworks & Libraries
| Name | Version | Purpose |
|---|---|---|
| FastAPI | 0.100+ | HTTP + WebSocket API framework (both services) |
| Pipecat-AI | 1.6.0 | Real-time audio AI pipeline orchestration |
| Tortoise ORM | 0.20+ | Async PostgreSQL ORM |
| React | 19.x | Frontend UI framework |
| Vite | 8.x | Frontend build tooling |
| TailwindCSS | 4.x | Utility-first CSS |
| TanStack Query | 5.x | Server-state management / data fetching |
| React Router | 7.x | Client-side routing |
| React Hook Form | — | Form state + validation |
| Zod | — | TypeScript schema validation |
| Playwright | — | Headless browser Teams bot |
| httpx | — | Async HTTP client for inter-service calls |
| loguru | — | Structured Python logging |

### Databases
| Database | Version | Used For |
|---|---|---|
| PostgreSQL | 15 (Alpine) | Primary persistent store (sessions, transcripts) |
| File System | — | Audio recordings, resume files, JSON session backups |

### AI Models & Services
| Service | Model | Purpose |
|---|---|---|
| Deepgram | Nova-2 | Speech-to-Text (streaming, diarization) |
| Deepgram | Aura-2-Amalthea | Text-to-Speech |
| DeepSeek | deepseek-v4-flash | Primary LLM (OpenAI-compatible API) |
| Groq | llama-3.3-70b-versatile | LLM fallback / evaluation |
| Silero VAD | — | Voice Activity Detection (embedded in pipeline) |
| Deepgram REST | — | Batch transcription for simulation mode |

### Infrastructure & DevOps
| Tool | Purpose |
|---|---|
| Docker | Container packaging for all 4 services |
| Docker Compose | Multi-container orchestration |
| Nginx | Reverse proxy, static file serving, WebSocket proxying |
| GitHub Actions | CI/CD pipeline (lint, test, build, deploy) |
| asyncpg | Async PostgreSQL driver |

### External Services
| Service | Purpose |
|---|---|
| Deepgram API | STT + TTS API calls |
| DeepSeek API | LLM completions (OpenAI-compatible endpoint) |
| Groq API | LLM fallback completions |
| Microsoft Teams | Meeting platform (browser-automated via Playwright) |

---

*Next: [Section 2 — Repository Structure →](../02-repository-structure/02-repository-structure.md)*

