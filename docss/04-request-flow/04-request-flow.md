# Section 4 — Request Flow

> **Cross-references:** [Architecture](../03-architecture/03-architecture.md) | [API Documentation](../06-api/06-api.md) | [AI Architecture](../08-ai-architecture/08-ai-architecture.md)

---

## 4.1 AI Voice Interview — Full Request Lifecycle

### Step-by-Step Description

1. **User opens New Interview page** — pastes JD and uploads resume.
2. **`POST /api/interviews/parse-resume`** — resume file is extracted to plain text via `DocumentParserFactory`.
3. **`POST /api/interviews/start`** — session created in DB/filesystem, `session_id` returned. Interview Service also pre-initializes a Copilot session via HTTP.
4. **Browser opens WebSocket** `ws://{host}/api/ws/interview/{session_id}` — raw 16kHz PCM audio streaming begins.
5. **Pipecat pipeline starts** — `InterviewPromptBuilder` builds system instruction from JD + resume. Pipeline is assembled: `Transport → MicGate → STT → Accumulator → Aggregator → LLM → TTS → PlaybackBuffer → Transport.output → AudioBuffer → MicUnmuter → AssistantAggregator`.
6. **AI greeting fires first** — `LLMRunFrame` is queued, LLM generates greeting, TTS converts to speech, sent back over WebSocket. MicGate blocks candidate audio until greeting ends.
7. **`MicUnmuterProcessor` triggers** — after first TTS audio completes, mic gate opens.
8. **Candidate speaks** — browser captures audio, downsamples 48kHz → 16kHz, sends raw PCM binary chunks over WebSocket.
9. **Deepgram STT processes audio** — returns transcript with speaker tags and endpointing.
10. **`TranscriptAccumulator` intercepts** — calls `transcript_callback` which saves to DB and forwards to Copilot engine.
11. **Silero VAD detects end-of-speech** — LLM context aggregator sends accumulated text to DeepSeek LLM.
12. **DeepSeek LLM generates response** — streaming text returned, TTS synthesizes to 16kHz PCM audio.
13. **Audio sent back to browser** — browser Web Audio API schedules smooth continuous playback.
14. **Cycle repeats** until session ends.

### Sequence Diagram

![Ai Voice Interview Sequence](images/ai_voice_interview_sequence.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
sequenceDiagram
    actor User as 👤 User
    participant FE as React Frontend
    participant NX as Nginx
    participant IS as Interview Service
    participant DG_STT as Deepgram STT
    participant DS_LLM as DeepSeek LLM
    participant DG_TTS as Deepgram TTS
    participant PG as PostgreSQL
    participant CS as Copilot Service

    User->>FE: Paste JD + Upload Resume
    FE->>NX: POST /api/interviews/parse-resume
    NX->>IS: POST /api/interviews/parse-resume
    IS-->>FE: { text: "...resume text..." }

    FE->>NX: POST /api/interviews/start {jd, resume}
    NX->>IS: POST /api/interviews/start
    IS->>PG: INSERT interview_sessions
    IS->>CS: POST /api/copilot/start (async background)
    IS-->>FE: { session_id: "abc123" }

    FE->>NX: WebSocket /api/ws/interview/abc123
    NX->>IS: WebSocket upgrade
    IS->>IS: Build Pipecat pipeline
    IS->>DS_LLM: Generate greeting (system prompt + LLMRunFrame)
    DS_LLM-->>IS: "Hello! I'm Sarah, your AI interviewer..."
    IS->>DG_TTS: Convert greeting to audio
    DG_TTS-->>IS: 16kHz PCM audio chunks
    IS-->>FE: Binary PCM audio (WebSocket)
    FE->>FE: Web Audio API plays greeting

    loop Interview Turns
        User->>FE: Speaks into microphone
        FE->>FE: Downsample 48kHz → 16kHz, Int16 PCM
        FE->>IS: Binary PCM ArrayBuffer (WebSocket)
        IS->>DG_STT: Stream audio (WebSocket)
        DG_STT-->>IS: Transcript + speaker_id
        IS->>IS: classify_speaker_role()
        IS->>PG: Save transcript entry
        IS->>CS: Forward to copilot engine (in-memory)
        IS->>IS: Silero VAD detects end-of-speech
        IS->>DS_LLM: POST with full conversation context
        DS_LLM-->>IS: AI interviewer response text
        IS->>DG_TTS: Text → speech synthesis
        DG_TTS-->>IS: 16kHz PCM chunks
        IS-->>FE: Binary PCM audio (WebSocket)
        FE->>FE: Schedule playback via AudioContext
        CS->>CS: Run 3 parallel LLM tasks (background)
    end

    User->>FE: Click "End Interview"
    FE->>NX: POST /api/interviews/{id}/stop
    NX->>IS: POST /api/interviews/{id}/stop
    IS->>IS: worker.cancel()
    IS->>PG: Mark session complete
    IS-->>FE: { status: "stopped" }
```

</details>

---

## 4.2 Copilot Dashboard — Real-Time Update Flow

![Copilot Dashboard Realtime Flow](images/copilot_dashboard_realtime_flow.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
sequenceDiagram
    actor HR as 👤 Interviewer (HR)
    participant FE as Copilot Dashboard
    participant NX as Nginx
    participant CS as Copilot Service
    participant DS as DeepSeek API
    participant GQ as Groq API
    participant PG as PostgreSQL

    HR->>FE: Open Copilot Session Page
    FE->>NX: WebSocket /api/ws/copilot/{id}
    NX->>CS: WebSocket upgrade (dashboard mode)
    CS-->>FE: Connected - current state JSON

    loop Per Candidate/Interviewer Utterance
        Note over CS: Transcript arrives (from IS or mic)
        CS->>CS: add_message() → <5ms return
        CS->>CS: Utterance stitching (same-speaker merge)
        CS->>CS: asyncio.create_task() - background tasks

        par 3 parallel LLM tasks
            CS->>GQ: CandidateEvaluationService (6-dimension scoring)
            GQ-->>CS: { technical_accuracy: 75, confidence: 60, ... }
        and
            CS->>DS: ConversationIntelligenceEngine (JD/resume coverage)
            DS-->>CS: { jd_coverage: [...], resume_coverage: [...] }
        and
            CS->>DS: AICopilotEngine (suggestions)
            DS-->>CS: { suggested_follow_up_questions: [...] }
        end

        CS->>CS: Decision engine (Strong/Partial/Weak)
        CS->>PG: Save updated session state
        CS-->>FE: WebSocket JSON {type: "copilot_update", transcript, intelligence, assistance}
        FE->>FE: Re-render suggestions panel (React state update)
        HR->>HR: Views follow-up questions, coverage map
    end

    HR->>FE: Click "Generate Report"
    FE->>NX: POST /api/copilot/{id}/finalize
    NX->>CS: finalize_report()
    CS->>DS: Final intelligence analysis
    CS->>GQ: Final evaluation pass
    CS->>PG: Save finalized report
    CS-->>FE: Full report JSON
    FE->>FE: Switch to report view
```

</details>

---

## 4.3 Resume Parse & Session Start Flow

![Resume Parse Session Start Flow](images/resume_parse_session_start_flow.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
flowchart TD
    A[User selects resume file] --> B{File type?}
    B -->|.pdf| C[PDF Parser - PyPDF2]
    B -->|.docx| D[DOCX Parser - python-docx]
    B -->|.txt| E[Text Parser - raw decode]
    B -->|other| F[HTTP 400 - Unsupported format]
    C --> G[Extracted text string]
    D --> G
    E --> G
    G --> H[POST /api/interviews/start]
    H --> I[Generate session_id UUID]
    I --> J[Create ./interviews/{id}/ directory]
    J --> K[Write jd.txt + resume.txt]
    K --> L[INSERT into interview_sessions]
    L --> M{Meeting URL provided?}
    M -->|Yes| N[POST to Copilot /join-meeting async]
    M -->|No| O[POST to Copilot /start async]
    N --> O
    O --> P[Return session_id to frontend]
    P --> Q[Frontend opens WebSocket]

    style F fill:#fecaca,stroke:#ef4444
    style P fill:#dcfce7,stroke:#22c55e
```

</details>

---

## 4.4 Simulation Mode Flow

![Simulation Mode Flow](images/simulation_mode_flow.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
flowchart TD
    A[HR clicks Simulate] --> B[POST /api/copilot/{id}/simulate]
    B --> C[Open WebSocket /api/ws/copilot/{id}/simulate]
    C --> D[Upload WAV file]
    D --> E[Read WAV bytes]
    E --> F[Validate: mono, PCM, get sample rate]
    F --> G{Sample rate = 16kHz?}
    G -->|No| H[Resample to 16kHz using audioop]
    G -->|Yes| I[Use as-is]
    H --> J[Normalize to Int16 PCM]
    I --> J
    J --> K[Stream 4096-sample chunks over WebSocket to frontend audio]
    K --> L[Send same chunks to Deepgram REST API]
    L --> M[Deepgram returns full transcript with timestamps]
    M --> N[Split transcript into utterances by timestamp]
    N --> O{More utterances?}
    O -->|Yes| P[engine.add_message speaker text]
    P --> Q[3 parallel LLM tasks run in background]
    Q --> R[copilot_update JSON pushed to dashboard WS]
    R --> O
    O -->|Done| S[Send type: simulation_complete]
    S --> T[Frontend shows Generate Report button]

    style J fill:#dcfce7,stroke:#22c55e
    style S fill:#dbeafe,stroke:#3b82f6
```

</details>

---

## 4.5 Speaker Role Classification Flow

![Speaker Role Classification Flow](images/speaker_role_classification_flow.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
flowchart TD
    A[STT returns text + raw_spk tag] --> B{Check linguistic patterns}
    B -->|starts with sir, my name, I am, I have...| C[Candidate]
    B -->|ends with ? or starts with tell me, can you...| D[Interviewer]
    B -->|no clear pattern| E{Check Deepgram diarization}
    E -->|raw_spk in speaker_map| F[Return mapped role]
    E -->|raw_spk not in map, map empty| G[Assign Candidate - first speaker]
    E -->|raw_spk not in map, map has 1 entry| H[Assign Interviewer - second speaker]
    E -->|raw_spk not in map, map has 2+ entries| I[Speaker N - additional]
    G --> J[Add to speaker_map]
    H --> J
    I --> J
    J --> K[Return classified role]
    C --> L[Final speaker label]
    D --> L
    F --> L
    K --> L

    style C fill:#dcfce7,stroke:#22c55e
    style D fill:#dbeafe,stroke:#3b82f6
```

</details>

---

*Next: [Section 5 — Component Documentation →](../05-components/05-components.md)*

