# Platform Architectural Reverse-Engineering Document

---

# PART 1 — REPOSITORY / PLATFORM STRUCTURE

The repository is structured as a unified monorepo hosting three Python backend micro-domains and one React TypeScript frontend:

```
c:\Users\Dell\Desktop\demo\
├── services/
│   ├── main.py                         # Unified FastAPI Gateway (Port 8000)
│   ├── interview/                      # Mia AI Mock Interviewer Service
│   │   └── src/
│   │       ├── api/interviews.py       # Interview REST & WebSocket APIs
│   │       ├── pipeline/               # Pipecat Audio/LLM/TTS Pipeline
│   │       │   ├── builder.py          # Pipeline constructor & Pipecat wiring
│   │       │   ├── accumulator.py      # Transcript & speaker tracking
│   │       │   ├── phrase_aggregator.py# Streaming Phrase/Clause chunking
│   │       │   ├── playback_buffer.py  # Audio crack & jitter prevention
│   │       │   ├── serializer.py       # 16-bit PCM serialization & profiling
│   │       │   └── mic_gate.py         # Echo & turn gating
│   │       ├── prompts/interview_prompt.py # Mia persona & interview system prompt
│   │       ├── models/interview.py     # Tortoise ORM InterviewSessionModel
│   │       ├── repositories/           # PostgreSQL & JSON session repositories
│   │       └── parsers/                # PDF/DOCX/TXT resume & JD parsers
│   ├── copilot/                        # Appz Interviewer Copilot & Intelligence Service
│   │   └── src/
│   │       ├── router.py               # Copilot REST router
│   │       ├── websocket/handler.py    # Copilot Dashboard WebSocket & audio STT
│   │       ├── engine/
│   │       │   ├── session.py          # CopilotSessionEngine & memory manager
│   │       │   ├── copilot.py          # AICopilotEngine (tips & questions)
│   │       │   └── intelligence.py     # ConversationIntelligenceEngine (JD/Resume coverage)
│   │       ├── services/
│   │       │   ├── evaluation.py       # CandidateEvaluationService (ratings & gaps)
│   │       │   └── repository.py       # Copilot database repository
│   │       ├── pipeline/               # Copilot standalone STT observer pipeline
│   │       ├── models/copilot.py       # Tortoise ORM CopilotSessionModel
│   │       └── api/simulation.py       # Audio file simulation test harness
│   └── browser/                        # Headless Playwright Browser Bot Service (Port 8002)
│       └── src/
│           ├── main.py                 # Browser Bot REST API (/join-meeting, /stop-meeting)
│           └── pipeline/teams_bot.py   # Teams Playwright bot & WebAudio WebRTC injector
├── frontend-new/                       # React 18 + Vite + Tailwind Dashboard (Port 3000)
│   └── src/
│       ├── pages/                      # Interview UI (NewInterview, InterviewSession)
│       └── copilot/pages/              # Copilot UI (CopilotSession, NewCopilot)
└── interviews/                         # Local filesystem storage for sessions, logs & WAVs
```

### Repository Service Map

| Service / Directory | Purpose | Entry Point | Important Files | Talks To |
| :--- | :--- | :--- | :--- | :--- |
| **`services/main.py`** | Unified API gateway for Interview, Copilot, and WebSocket routers. | `services/main.py` | `main.py` | Mounts `interviews.py`, `router.py`, `handler.py`, `simulation.py` |
| **`services/interview/`** | Hosts Mia AI Mock Interviewer, voice synthesis, DeepSeek LLM, and Teams bot runner. | `services/interview/src/api/interviews.py` | `builder.py`, `phrase_aggregator.py`, `accumulator.py`, `interview_prompt.py` | Deepgram STT/TTS, DeepSeek LLM, Browser Service (8002), PostgreSQL |
| **`services/copilot/`** | Observes interview, runs real-time evaluation, conversation intelligence, and suggestion engine. | `services/copilot/src/router.py` | `session.py`, `copilot.py`, `intelligence.py`, `evaluation.py`, `handler.py` | DeepSeek LLM, Deepgram STT, Copilot Dashboard UI |
| **`services/browser/`** | Controls headless Chromium via Playwright, joins Teams meetings, bridges WebRTC audio. | `services/browser/src/main.py` | `teams_bot.py`, `main.py` | Microsoft Teams WebRTC, FastAPI WebSocket (Port 8000) |
| **`frontend-new/`** | Web dashboard for conducting interviews and monitoring live Copilot intelligence. | `frontend-new/src/main.tsx` | `CopilotSession.tsx`, `InterviewSession.tsx`, `useCopilotAudio.ts` | Backend REST (8000), Backend WebSocket (8000) |

---

# PART 2 — MIA / INTERVIEW SERVICE

1. **Service Start**: Initialized via `uvicorn services.main:app --host 0.0.0.0 --port 8000`.
2. **Session Creation**: Client calls `POST /api/interviews/start` ([`interviews.py:L140-L200`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/api/interviews.py#L140-L200)). An entry is created in PostgreSQL (`InterviewSessionModel`) and active memory (`app.state.active_sessions`).
3. **Teams Spawning**: If `meeting_url` is provided, `spawn_teams_bot()` sends an HTTP POST to `http://localhost:8002/join-meeting` (or spawns `teams_bot.py` subprocess).
4. **Teams Connection**: Playwright navigates Chromium to the Teams URL, bypasses login, enters display name `"Mia"`, turns camera OFF, unmutes mic, and clicks Join.
5. **Audio Input**: Chromium intercepts incoming remote audio (`mainAudio-*`), downsamples 48kHz $\rightarrow$ 16kHz PCM, and sends binary chunks over `ws://localhost:8000/api/ws/interview/{session_id}`.
6. **Audio Output**: Deepgram TTS outputs 16kHz 16-bit PCM frames $\rightarrow$ WebSocket $\rightarrow$ Browser Jitter Buffer (350ms) $\rightarrow$ WebAudio `window.__virtualMicTrack` $\rightarrow$ Teams WebRTC sender.
7. **Pipecat Construction**: `LocalPipecatPipelineBuilder.build_pipeline()` in [`builder.py:L40-L195`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/builder.py#L40-L195) creates the reactive processing graph.
8. **Context & Generation**: `LLMContextAggregatorPair` accumulates transcript tokens and triggers DeepSeek LLM.
9. **Persistence**: `TranscriptAccumulator` intercepts finalized speech frames and persists them to PostgreSQL `interview_sessions.transcript`.

---

# PART 3 — PIPECAT PIPELINE ARCHITECTURE

```
[Teams Browser WebSocket Input]
         │ (InputAudioRawFrame: 16kHz 16-bit PCM mono)
         ▼
[1. transport.input()] (FastAPIWebsocketInputTransport)
         │
         ▼
[2. mic_gate] (MicGateProcessor — blocks mic during Mia speech)
         │
         ▼
[3. stt] (DeepgramSTTService — nova-2 endpointing=400ms)
         │ (TranscriptionFrame / InterimTranscriptionFrame)
         ▼
[4. user_accumulator] (TranscriptAccumulator — forwards user text to DB & Copilot)
         │
         ▼
[5. user_aggregator] (LLMUserAggregator — appends user utterance to LLMContext)
         │ (LLMContextFrame)
         ▼
[6. llm] (OpenAILLMService — DeepSeek deepseek-v4-flash)
         │ (LLMTextFrame stream)
         ▼
[7. assistant_accumulator] (TranscriptAccumulator — captures Mia text on LLMFullResponseEndFrame)
         │
         ▼
[8. tts] (DeepgramTTSService + StreamingPhraseTextAggregator)
         │ (OutputAudioRawFrame: 40ms 16kHz PCM frames)
         ▼
[9. playback_buffer] (PlaybackBufferProcessor — buffer size=3)
         │
         ▼
[10. transport.output()] (FastAPIWebsocketOutputTransport via RawPCMAudioSerializer)
         │
         ▼
[11. audio_buffer] (AudioBufferProcessor — writes recording.wav)
         │
         ▼
[12. mic_unmuter] (MicUnmuterProcessor — unmuting gate 300ms after TTSStoppedFrame)
         │
         ▼
[13. assistant_aggregator] (LLMAssistantAggregator — completes context loop)
```

### Pipeline Component Manifest

| Component | File | Class | Input | Output | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Input Transport** | `pipecat.transports` | `FastAPIWebsocketInputTransport` | WebSocket bytes | `InputAudioRawFrame` | Ingests meeting audio from browser |
| **Mic Gate** | [`mic_gate.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/mic_gate.py) | `MicGateProcessor` | `InputAudioRawFrame` | `InputAudioRawFrame` | Drops candidate audio frames while Mia speaks |
| **STT** | `pipecat.services.deepgram` | `DeepgramSTTService` | `InputAudioRawFrame` | `TranscriptionFrame` | Transcribes candidate audio via Deepgram Nova-2 |
| **User Accumulator** | [`accumulator.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/accumulator.py) | `TranscriptAccumulator` | `TranscriptionFrame` | `TranscriptionFrame` | Emits transcript callback to DB & Copilot |
| **User Aggregator** | `pipecat.processors` | `LLMUserAggregator` | `TranscriptionFrame` | `LLMContextFrame` | Manages user conversational turn context |
| **LLM Service** | `pipecat.services.openai` | `OpenAILLMService` | `LLMContextFrame` | `LLMTextFrame` | Generates Mia's response using DeepSeek |
| **Assistant Accumulator** | [`accumulator.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/accumulator.py) | `TranscriptAccumulator` | `LLMTextFrame` | `LLMTextFrame` | Collects full assistant response text for logging |
| **TTS Service** | `pipecat.services.deepgram` | `DeepgramTTSService` | `LLMTextFrame` | `OutputAudioRawFrame` | Synthesizes voice via `aura-2-amalthea-en` |
| **Phrase Aggregator** | [`phrase_aggregator.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/phrase_aggregator.py) | `StreamingPhraseTextAggregator` | Text tokens | `Aggregation` | Splits text on clauses ( $\ge 28$ chars) for low latency |
| **Playback Buffer** | [`playback_buffer.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/playback_buffer.py) | `PlaybackBufferProcessor` | `OutputAudioRawFrame` | `OutputAudioRawFrame` | Smooths initial audio burst (buffer size=3) |
| **Output Transport** | `pipecat.transports` | `FastAPIWebsocketOutputTransport` | `OutputAudioRawFrame` | WebSocket bytes | Serializes PCM audio to browser WebSocket |
| **Audio Buffer** | `pipecat.processors` | `AudioBufferProcessor` | Audio frames | None | Writes complete session to `recording.wav` |
| **Mic Unmuter** | [`mic_gate.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/mic_gate.py) | `MicUnmuterProcessor` | `TTSStoppedFrame` | Unmute signal | Unmutes candidate mic 300ms after TTS ends |

---

# PART 4 — SPEECH-TO-TEXT (STT)

* **Provider**: Deepgram WebSocket Streaming API (`wss://api.deepgram.com/v1/listen`).
* **Model**: `nova-2` (Multilingual/English, `smart_format=True`, `diarize=True`, `endpointing=400`).
* **Audio Format**: 16,000 Hz, 16-bit Mono Linear PCM.
* **Partial vs Final**: Deepgram emits `is_final=False` (`InterimTranscriptionFrame`) and `is_final=True` (`TranscriptionFrame`). Only finalized frames trigger context pushes and transcript logging.
* **Speaker Diarization**: Deepgram word-level speaker tags are extracted in [`accumulator.py:L13-L48`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/accumulator.py#L13-L48) (`extract_speaker_from_frame`).

---

# PART 5 — LARGE LANGUAGE MODELS (LLMs)

| LLM Instance | Provider | Model | Class / Location | Calling Mechanism | Output Format | Output Destination |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Mia Interviewer LLM** | DeepSeek | `deepseek-v4-flash` | `OpenAILLMService` ([`builder.py:L126`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/builder.py#L126)) | Streaming (Tokens) | Text / Conversational speech | `phrase_aggregator.py` $\rightarrow$ Deepgram TTS |
| **Copilot Assistance LLM** | DeepSeek | `deepseek-v4-flash` | `AICopilotEngine` ([`copilot.py:L20`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/copilot.py#L20)) | Non-streaming (`json_object`) | Structured JSON | Frontend Copilot Suggestions UI |
| **Candidate Evaluation LLM** | DeepSeek | `deepseek-v4-flash` | `CandidateEvaluationService` ([`evaluation.py:L18`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/services/evaluation.py#L18)) | Non-streaming (`json_object`) | Structured JSON | Frontend Candidate Evaluation Card |
| **Conversation Intelligence LLM**| DeepSeek | `deepseek-v4-flash` | `ConversationIntelligenceEngine` ([`intelligence.py:L19`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/intelligence.py#L19)) | Non-streaming (`json_object`) | Structured JSON | Frontend JD/Resume Coverage Matrix |

---

# PART 6 — MIA PROMPT & RESPONSE FLOW

* **Prompt Construction**: Defined in [`services/interview/src/prompts/interview_prompt.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/prompts/interview_prompt.py). Assembles:
  1. System Role: `"You are Miaa, a professional, warm, and encouraging mock interviewer..."`
  2. Job Description (JD) text.
  3. Candidate Resume text.
  4. Optional Custom Prompt instructions.
* **Context Accumulation**: `LLMContext` maintains message history `[{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]`.
* **Output Stream**: DeepSeek streams tokens $\rightarrow$ `assistant_accumulator` records full text on `LLMFullResponseEndFrame` $\rightarrow$ `StreamingPhraseTextAggregator` aggregates clauses $\rightarrow$ Deepgram TTS.

---

# PART 7 — TEXT-TO-SPEECH (TTS)

* **Provider**: Deepgram Aura Streaming TTS (`wss://api.deepgram.com/v1/speak`).
* **Voice**: `aura-2-amalthea-en`.
* **Text Aggregation**: Handled by [`StreamingPhraseTextAggregator`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/phrase_aggregator.py) (Min 28 chars, splits on `,`, `;`, `:`, `—`, `-`, `\n`, sentence endings, or max 120 chars).
* **Audio Format**: 16,000 Hz 16-bit Mono PCM chunks (~40ms / 1,280 bytes each).
* **Delivery**: Sent through `PlaybackBufferProcessor(3)` $\rightarrow$ `RawPCMAudioSerializer` $\rightarrow$ WebSocket $\rightarrow$ Browser Jitter Buffer $\rightarrow$ Teams WebRTC sender.

---

# PART 8 — WEBSOCKET ARCHITECTURE

| WebSocket Route | Client | Server | Purpose | Message Types | File |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`/api/ws/interview/{session_id}`** | Browser Bot (`teams_bot.py`) | FastAPI (Port 8000) | Full-duplex audio stream for Mia interviewer | Binary ArrayBuffer (16kHz PCM audio both directions) | [`interviews.py:L202`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/api/interviews.py#L202) |
| **`/api/ws/copilot/{session_id}`** | Frontend Dashboard / Browser Bot | FastAPI (Port 8000) | Live transcript & Copilot intelligence updates / Audio In | JSON (`copilot_update`, `session_state`), Binary PCM | [`handler.py:L26`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/websocket/handler.py#L26) |
| **`/api/ws/copilot/{session_id}/simulate`** | Frontend Test Harness | FastAPI (Port 8000) | Audio file simulation testing stream | JSON (`simulation_complete`), Binary PCM audio | [`simulation.py:L142`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/api/simulation.py#L142) |

---

# PART 9 — REST API ARCHITECTURE

| Endpoint | Method | Service | Request Body | Response | Caller | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`/api/interviews/start`** | POST | Interview | `{jd, resume, meeting_url, custom_prompt}` | `{session_id, status}` | Frontend / Script | Creates session & spawns browser bot |
| **`/api/interviews/{id}/stop`** | POST | Interview | None | `{status: "stopped"}` | Frontend UI | Stops interview & terminates bot |
| **`/api/interviews/parse-resume`** | POST | Interview | Multipart File | `{text, filename}` | Frontend UI | Parses PDF/DOCX resume to text |
| **`/api/copilot/start`** | POST | Copilot | `{jd, resume, custom_prompt, session_id}` | `{session_id, status}` | Frontend UI | Initializes Copilot session engine |
| **`/api/copilot/{id}/stop`** | POST | Copilot | None | `{status: "stopped"}` | Frontend UI | Stops Copilot session & closes WS |
| **`/api/copilot/{id}/message`** | POST | Copilot | `{speaker, text}` | `{speaker, text, evaluation}` | Frontend / API | Injects manual speech turn |
| **`/join-meeting`** | POST | Browser (8002) | `{session_id, meeting_url, bot_role, bot_name}`| `{status, pid}` | Interview / Copilot | Spawns Playwright Chromium process |
| **`/stop-meeting`** | POST | Browser (8002) | `{session_id}` | `{status, pid}` | Interview / Copilot | Terminates Playwright bot process |

---

# PART 10 — COPILOT SERVICE

* **Role**: Real-time interviewer assistant, evaluating candidate responses and providing suggested follow-up questions and competency analytics.
* **Engine Lifecycle**: [`CopilotSessionEngine`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/session.py) holds active transcript and state in memory.
* **Background Tasks**: On each message via `add_message()`:
  1. `CandidateEvaluationService.evaluate_response()` (if candidate speaker)
  2. `ConversationIntelligenceEngine.analyze()`
  3. `AICopilotEngine.generate_assistance()`
  Executed concurrently via `asyncio.gather()`; results are broadcasted over WebSocket to `CopilotSession.tsx`.

---

# PART 11 — MIA ↔ COPILOT CONNECTION

### Critical Finding: **CONFIRMED DIRECT IN-PROCESS CONNECTION**

Mia and Copilot are **not completely isolated runtime services**; they share the same FastAPI process (`services/main.py`) on port 8000:

1. **Shared State Access**:
   - In [`services/interview/src/api/interviews.py:L254-L270`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/api/interviews.py#L254-L270), the Interview WebSocket callback directly reads `websocket.app.state.copilot_sessions`.
2. **Forwarding Mechanism**:
   ```python
   # services/interview/src/api/interviews.py:L254-269
   copilot_sessions = getattr(websocket.app.state, "copilot_sessions", None)
   if copilot_sessions and sid in copilot_sessions:
       copilot_sess = copilot_sessions[sid]
       engine = copilot_sess.get("engine")
       if engine:
           speaker = classify_speaker_role(text_content, raw_spk, speaker_map)
           last_msg = await engine.add_message(speaker, text_content, websocket=copilot_ws)
   ```
3. **Data Flow**:
   - Every candidate utterance transcribed by Deepgram STT in the Mia pipeline is forwarded into `CopilotSessionEngine`.
   - Every Mia assistant response generated by DeepSeek in the Mia pipeline is forwarded into `CopilotSessionEngine`.
   - **Copilot output NEVER flows back into Mia**. (One-way forward only).

---

# PART 12 — EVALUATION CONNECTION

* **Trigger**: Invoked asynchronously in [`session.py:L64-L71`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/session.py#L64-L71) whenever `speaker == "Candidate"`.
* **Input**: Candidate response text + JD + Resume + Last Question asked.
* **Output**: `{technical_accuracy: {rating, comment}, relevance, depth, communication_clarity, strengths, knowledge_gaps, follow_up_required, follow_up_reason}`.
* **Storage**: Attached to `message["evaluation"]` in `self.transcript` and broadcasted to the frontend Copilot UI. It does **not** feed into Mia's pipeline.

---

# PART 13 — TRANSCRIPT ARCHITECTURE

| Transcript Instance | Owner | Source | Storage | Consumers |
| :--- | :--- | :--- | :--- | :--- |
| **STT Transcript** | `DeepgramSTTService` | Audio streaming | Ephemeral frame | Pipecat Aggregator |
| **Mia History** | `LLMContext` | STT + Mia generation | In-memory frame list | DeepSeek LLM prompt |
| **Interview DB Transcript** | `PostgresInterviewRepository` | `assistant/user_accumulator` | PostgreSQL `interview_sessions` | Interview history APIs |
| **Copilot Transcript** | `CopilotSessionEngine` | Forwarded from Mia or Copilot STT | PostgreSQL `copilot_sessions` | Copilot UI, Intelligence, Evaluation |
| **Dashboard UI Transcript**| React `CopilotSession.tsx` | WebSocket `copilot_update` | React state (`transcript`) | Live Transcript Log UI |

---

# PART 14 — DATABASE & PERSISTENCE

* **ORM**: Tortoise ORM on PostgreSQL (or SQLite fallback).
* **Tables**:
  1. `interview_sessions` (`session_id`, `timestamp`, `jd`, `resume`, `custom_prompt`, `transcript`)
  2. `copilot_sessions` (`session_id`, `timestamp`, `jd`, `resume`, `custom_prompt`, `transcript`)
* **Filesystem**: Artifacts and reference WAV recordings stored under `interviews/{session_id}/`.

---

# PART 15 — FRONTEND / COPILOT DASHBOARD

* **File**: [`frontend-new/src/copilot/pages/CopilotSession.tsx`](file:///c:/Users/Dell/Desktop/demo/frontend-new/src/copilot/pages/CopilotSession.tsx)
* **Real-Time Hook**: `useCopilotAudio.ts` opens WebSocket `ws://localhost:8000/api/ws/copilot/{session_id}`.
* **State Updates**: On `"copilot_update"` JSON event:
  - `transcript` $\rightarrow$ updates `Live Transcript Log` accordion.
  - `assistance` $\rightarrow$ updates `Recommended Next Topic`, `Follow-up Questions`, and `Interview Notes`.
  - `intelligence` $\rightarrow$ updates `JD Requirements Coverage` and `Resume Claims Verification`.

---

# PART 16 — COMPLETE END-TO-END INTERVIEW TURN TRACE

```
1. Candidate speaks in Teams meeting: "I built an ETL pipeline using Snowflake and Python."
2. Teams WebRTC remote track captures audio in Chromium (`teams_bot.py`).
3. Downsampled 16kHz PCM sent over WebSocket (`ws://localhost:8000/api/ws/interview/{id}`).
4. `mic_gate` passes frame $\rightarrow$ `DeepgramSTTService` transcribes it.
5. `user_accumulator` intercepts `TranscriptionFrame` $\rightarrow$ calls `make_transcript_callback`.
6. Callback forwards text to `CopilotSessionEngine.add_message("Candidate", text)`:
   - Copilot saves candidate text to `copilot_sessions`.
   - Spawns background evaluation, intelligence, and assistance LLMs.
   - Pushes update to frontend Copilot Dashboard.
7. `user_aggregator` appends text to `LLMContext` $\rightarrow$ triggers `OpenAILLMService` (DeepSeek).
8. DeepSeek generates response: "That sounds interesting. How did you handle data deduplication in Snowflake?"
9. `assistant_accumulator` captures complete response on `LLMFullResponseEndFrame`.
10. `StreamingPhraseTextAggregator` breaks response into clauses $\rightarrow$ Deepgram TTS synthesizes audio.
11. Output audio frames serialized $\rightarrow$ sent over WebSocket to Chromium.
12. Browser Jitter Buffer accumulates 350ms $\rightarrow$ WebAudio plays through virtual mic track.
13. Candidate in Teams hears Mia speak.
14. Callback forwards Mia response to `CopilotSessionEngine.add_message("Interviewer", text)`.
```

---

# PART 17 — MASTER ARCHITECTURE DIAGRAM

```
                             ┌──────────────────────────────────┐
                             │    Microsoft Teams Meeting       │
                             │  (Candidate + Human Recruiter)   │
                             └─────────────────┬────────────────┘
                                               │ WebRTC Audio (In/Out)
                                               ▼
                             ┌──────────────────────────────────┐
                             │ Playwright Browser Bot (Port 8002)│
                             │         (teams_bot.py)           │
                             └─────────────────┬────────────────┘
                                               │ Full-Duplex PCM WebSocket
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           Unified FastAPI Backend (Port 8000)                           │
│                                                                                         │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                        MIA INTERVIEW SERVICE (Pipecat)                          │   │
│   │                                                                                 │   │
│   │   [Input WS] ──> [MicGate] ──> [Deepgram STT] ──> [User Accumulator]            │   │
│   │                                                          │                      │   │
│   │   [Output WS] <── [PlaybackBuffer] <── [Deepgram TTS] <── [LLM (DeepSeek)] <────┤   │
│   │                                                                                 │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────┘   │
│                                              │ In-Memory Forwarding                     │
│                                              ▼ (via app.state.copilot_sessions)         │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                         COPILOT SERVICE (Intelligence)                          │   │
│   │                                                                                 │   │
│   │   CopilotSessionEngine.add_message()                                            │   │
│   │       ├── CandidateEvaluationService (DeepSeek) ──> Technical Rating & Gaps     │   │
│   │       ├── ConversationIntelligenceEngine (DeepSeek) ──> JD & Resume Coverage    │   │
│   │       └── AICopilotEngine (DeepSeek) ──> Suggested Follow-ups & Notes           │   │
│   │                                                                                 │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────┘   │
│                                              │                                          │
│                                              │ JSON Broadcast WebSocket                 │
│                                              ▼                                          │
└──────────────────────────────────────────────┼──────────────────────────────────────────┘
                                               │
                                               ▼
                             ┌──────────────────────────────────┐
                             │   Frontend Copilot Dashboard     │
                             │    (CopilotSession.tsx: 3000)    │
                             └──────────────────────────────────┘
```

---

# PART 18 — FILE OWNERSHIP MAP

| File | Responsibility | Service | Called By | Calls |
| :--- | :--- | :--- | :--- | :--- |
| [`services/main.py`](file:///c:/Users/Dell/Desktop/demo/services/main.py) | Application root & router aggregator | Core Gateway | Uvicorn | Mounts all sub-routers |
| [`services/interview/src/api/interviews.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/api/interviews.py) | Interview REST & WS endpoints | Interview | `main.py` | `builder.py`, `teams_bot.py`, `CopilotSessionEngine` |
| [`services/interview/src/pipeline/builder.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/builder.py) | Pipecat pipeline builder | Interview | `interviews.py` | STT, LLM, TTS, Accumulators, MicGate |
| [`services/interview/src/pipeline/phrase_aggregator.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/phrase_aggregator.py) | Streaming phrase/clause aggregator | Interview | `builder.py` | Deepgram TTS Speak |
| [`services/interview/src/prompts/interview_prompt.py`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/prompts/interview_prompt.py) | System prompt construction | Interview | `interviews.py` | OpenAILLMService |
| [`services/copilot/src/engine/session.py`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/session.py) | Copilot state & background task coordinator | Copilot | `interviews.py`, `handler.py` | `copilot.py`, `intelligence.py`, `evaluation.py` |
| [`services/copilot/src/engine/copilot.py`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/copilot.py) | Generates suggested follow-ups & notes | Copilot | `session.py` | DeepSeek LLM |
| [`services/copilot/src/services/evaluation.py`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/services/evaluation.py) | Evaluates candidate answer accuracy | Copilot | `session.py` | DeepSeek LLM |
| [`services/copilot/src/engine/intelligence.py`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/intelligence.py) | Generates JD coverage & scores | Copilot | `session.py` | DeepSeek LLM |
| [`services/browser/src/pipeline/teams_bot.py`](file:///c:/Users/Dell/Desktop/demo/services/browser/src/pipeline/teams_bot.py) | Playwright Teams automation & audio bridge | Browser | `browser/src/main.py` | Teams WebRTC, FastAPI WebSocket |

---

# PART 19 — CONNECTION MATRIX

| From | To | Connected? | Mechanism | What Data | Exact File & Function |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Mia** | **Copilot** | **YES** | In-Memory Function Call | Speech text (`entry`) | [`interviews.py:L269`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/api/interviews.py#L269) (`add_message`) |
| **Copilot** | **Mia** | **NO** | None | None | No connection path exists |
| **Mia** | **Teams** | **YES** | Playwright WebAudio WebRTC | 16kHz PCM audio | [`teams_bot.py:L270`](file:///c:/Users/Dell/Desktop/demo/services/browser/src/pipeline/teams_bot.py#L270) (`drainPlaybackQueue`) |
| **Copilot** | **Teams** | **YES (Optional)** | Playwright WebAudio Listener | 16kHz PCM audio in | [`handler.py:L146`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/websocket/handler.py#L146) (`on_stt_transcript`) |
| **STT** | **Mia** | **YES** | Pipecat Frame Processing | `TranscriptionFrame` | [`builder.py:L88`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/pipeline/builder.py#L88) (`stt`) |
| **STT** | **Copilot** | **YES** | Pipecat Frame Processing | `TranscriptionFrame` | [`builder.py:L60`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/pipeline/builder.py#L60) (`accumulator`) |
| **Mia** | **Database** | **YES** | Tortoise ORM | Session & Transcript | [`postgres_repository.py:L59`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/repositories/postgres_repository.py#L59) (`save_session`) |
| **Copilot** | **Database** | **YES** | Tortoise ORM | Session & Transcript | [`repository.py:L57`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/services/repository.py#L57) (`save_session`) |
| **Evaluation** | **Copilot** | **YES** | Async function call | Evaluation JSON dict | [`session.py:L65`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/session.py#L65) (`evaluate_response`) |
| **Intelligence**| **Copilot** | **YES** | Async function call | Intelligence JSON dict | [`session.py:L76`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/session.py#L76) (`analyze`) |
| **Copilot Backend** | **Dashboard** | **YES** | WebSocket JSON push | Full State Frame | [`session.py:L118`](file:///c:/Users/Dell/Desktop/demo/services/copilot/src/engine/session.py#L118) (`send_json`) |
| **Interview Backend** | **Dashboard** | **YES** | REST HTTP Polling | Session Status & Audio | [`interviews.py:L680`](file:///c:/Users/Dell/Desktop/demo/services/interview/src/api/interviews.py#L680) (`get_interview_session`) |

---

# PART 20 — CONFIRMED VS INFERRED FINDINGS

* **CONFIRMED FROM CODE**:
  * Mia and Copilot run in the same Python process on Port 8000 managed by `services/main.py`.
  * `interviews.py` forwards every user transcription and assistant response into `app.state.copilot_sessions[sid]["engine"].add_message()`.
  * Copilot’s evaluation, intelligence, and assistance engines output structured JSON and never send audio or text back to Mia’s pipeline.
  * Browser Bot runs as an external Playwright Chromium process communicating over `ws://localhost:8000/api/ws/interview/{sid}`.
* **INFERRED FROM CODE**:
  * The dual capability (Mia as Interviewer vs. Copilot as Standalone Observer) is designed to support both full automated AI mock interviews and human-led interviews with an AI assistant.
