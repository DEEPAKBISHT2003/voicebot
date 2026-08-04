# Section 5 — Component Documentation

> **Cross-references:** [Architecture](../03-architecture/03-architecture.md) | [Backend Docs](../17-backend/17-backend.md) | [Frontend Docs](../16-frontend/16-frontend.md)

---

## 5.1 Interview Service Components

### 5.1.1 LocalPipecatPipelineBuilder

**File:** `services/interview/src/pipeline/builder.py`

**Purpose:** Factory class that constructs the complete real-time voice AI pipeline using the Pipecat framework.

**Responsibilities:**
- Assembles STT → VAD → LLM → TTS pipeline chain
- Configures WebSocket or local audio transport
- Initializes Deepgram and DeepSeek service clients
- Sets up transcript accumulators and callback routing
- Manages observer mode (STT-only) vs full interview mode

**Inputs:**
- `deepgram_api_key: str` — Deepgram API key
- `deepseek_api_key: str` — DeepSeek API key
- `system_instruction: str` — LLM system prompt
- `session_id: Optional[str]` — Session ID for recording
- `transcript_callback: Optional[Callable]` — Function to save transcripts
- `websocket: Optional[Any]` — FastAPI WebSocket for browser streaming
- `is_observer: bool` — Observer mode flag (copilot Teams bot)
- `is_simulation: bool` — Simulation mode flag

**Outputs:**
- `Pipeline` — Pipecat pipeline instance
- `LLMContext` — LLM conversation context manager
- `PipelineWorker` — Async worker task

**Dependencies:**
- Pipecat framework (Pipeline, PipelineWorker, LLMContext)
- DeepgramSTTService, DeepgramTTSService
- OpenAILLMService (DeepSeek-compatible)
- SileroVADAnalyzer
- Custom processors: TranscriptAccumulator, PlaybackBufferProcessor, MicGateProcessor

**Configuration:**
- STT: `endpointing=400ms`, `diarize=True`, `smart_format=True`
- TTS: `voice=aura-2-amalthea-en`
- LLM: `model=deepseek-v4-flash`
- VAD: `confidence=0.8`, `min_volume=0.20`, `start_secs=0.3`, `stop_secs=1.0` (or 0.4 for simulation)

**Error Handling:**
- Catches audio device access errors
- Falls back to observer mode if TTS/LLM init fails
- Logs errors via loguru

**Used By:** `api/interviews.py` (WebSocket handler)

**Uses:** Pipecat services, Deepgram SDK, OpenAI SDK

---

### 5.1.2 TranscriptAccumulator

**File:** `services/interview/src/pipeline/accumulator.py`

**Purpose:** Pipecat frame processor that intercepts speech-to-text transcripts and forwards them to the session repository and copilot engine.

**Responsibilities:**
- Captures `TranscriptionFrame` events from STT
- Extracts speaker role, text, timestamp
- Invokes async callback with transcript entry
- Passes frames downstream unchanged

**Inputs:**
- `callback: Optional[Callable[[dict], None]]` — Async function to call with transcript entries

**Outputs:**
- Transcript entries: `{"speaker": str, "text": str, "timestamp": str}`

**Dependencies:**
- Pipecat `FrameProcessor`
- `TranscriptionFrame` from Pipecat

**Used By:** LocalPipecatPipelineBuilder

**Uses:** Session repository (via callback)

---

### 5.1.3 MicGateProcessor & MicUnmuterProcessor

**File:** `services/interview/src/pipeline/mic_gate.py`

**Purpose:** Implements microphone blocking until the AI greeting finishes, preventing audio overlap.

**Responsibilities:**
- **MicGateProcessor:** Blocks all audio input frames if `mic_enabled` is False
- **MicUnmuterProcessor:** Detects end of first TTS audio chunk and sets `mic_enabled = True`

**Inputs:**
- `shared_state: dict` — `{"mic_enabled": bool}` shared between processors

**Outputs:**
- Passes/blocks audio frames based on gate state

**Dependencies:**
- Pipecat `FrameProcessor`
- `AudioRawFrame` from Pipecat

**Used By:** LocalPipecatPipelineBuilder

**Uses:** Shared in-memory state dict

---

### 5.1.4 PlaybackBufferProcessor

**File:** `services/interview/src/pipeline/playback_buffer.py`

**Purpose:** Buffers TTS audio output chunks to prevent crackling/jitter in browser playback.

**Responsibilities:**
- Accumulates audio frames until buffer is full
- Flushes buffer as single consolidated chunk
- Smooths discontinuous audio streaming

**Inputs:**
- `buffer_size: int` — Number of frames to buffer (default: 5)

**Outputs:**
- Consolidated `AudioRawFrame` chunks

**Dependencies:**
- Pipecat `FrameProcessor`

**Used By:** LocalPipecatPipelineBuilder

**Uses:** Internal frame buffer

---

### 5.1.5 TeamsBot (Playwright)

**File:** `services/interview/src/pipeline/teams_bot.py`

**Purpose:** Headless Chromium bot that joins Microsoft Teams meetings and intercepts audio for copilot analysis.

**Responsibilities:**
- Opens Teams meeting URL in Playwright browser
- Injects JavaScript to disable camera and mute microphone
- Injects WebRTC audio interception script
- Captures all participant audio via Web Audio API
- Streams captured 16kHz PCM audio to copilot service via WebSocket

**Inputs:**
- `sys.argv[1]` — Teams meeting URL
- `sys.argv[2]` — Session ID
- Environment variable `BACKEND_WS_BASE` — WebSocket backend URL

**Outputs:**
- Binary 16kHz PCM audio streams over WebSocket

**Dependencies:**
- Playwright async API
- asyncio
- loguru

**Configuration:**
- Runs in headless mode
- Injects CAMERA_BLOCK_JS (blocks video, mutes mic)
- Injects INTERCEPT_JS (hooks RTCPeerConnection, captures audio)

**Error Handling:**
- Retries WebSocket connection on failure
- Logs all browser console messages
- Graceful shutdown on SIGINT/SIGTERM

**Used By:** `api/interviews.py` (spawned as subprocess)

**Uses:** Copilot service WebSocket endpoint

---

### 5.1.6 InterviewPromptBuilder

**File:** `services/interview/src/prompts/interview_prompt.py`

**Purpose:** Constructs the LLM system instruction from job description, resume, and custom instructions.

**Responsibilities:**
- Formats JD and resume into structured prompt
- Embeds custom interview instructions if provided
- Defines interviewer persona and behavior rules

**Inputs:**
- `jd: str` — Job description
- `resume: str` — Candidate resume text
- `custom_prompt: str` — Optional custom instructions

**Outputs:**
- System instruction string for LLM

**Dependencies:**
- None (pure string formatting)

**Used By:** `api/interviews.py` (WebSocket handler)

**Uses:** None

---

### 5.1.7 DocumentParserFactory

**File:** `services/interview/src/parsers/factory.py`

**Purpose:** Routes file parsing to appropriate parser based on file extension.

**Responsibilities:**
- Detects file type from filename
- Returns PDFParser, DOCXParser, or TXTParser instance

**Inputs:**
- `filename: str` — Filename with extension

**Outputs:**
- Parser instance (implements `parse(bytes, filename) -> str`)

**Dependencies:**
- `pdf_parser.py`, `docx_parser.py`, `txt_parser.py`

**Error Handling:**
- Raises `ValueError` for unsupported file types

**Used By:** `api/interviews.py` (resume parsing endpoint)

**Uses:** Parser implementations

---

## 5.2 Copilot Service Components

### 5.2.1 CopilotSessionEngine

**File:** `services/copilot/src/engine/session.py`

**Purpose:** Master orchestrator for copilot session state. Manages transcript, triggers parallel LLM tasks, and broadcasts real-time updates.

**Responsibilities:**
- Maintains in-memory transcript list
- Stitches rapid same-speaker utterances into unified thoughts
- Dispatches 3 parallel async LLM tasks per candidate message
- Tracks detected speakers for diarization
- Persists state to PostgreSQL + file system
- Broadcasts `copilot_update` JSON frames via WebSocket

**Inputs:**
- `session_id: str` — Session identifier
- `repo: CopilotRepository` — Data persistence layer
- `initial_transcript: List[Dict]` — Pre-existing transcript
- `jd: str` — Job description
- `resume: str` — Resume text
- `custom_prompt: str` — Custom instructions

**Outputs:**
- `add_message()` returns immediately (<5ms) with message dict
- Background tasks push `copilot_update` JSON to WebSocket
- Persisted state in DB + file system

**Dependencies:**
- `CandidateEvaluationService`
- `ConversationIntelligenceEngine`
- `AICopilotEngine`
- `CopilotRepository`
- asyncio

**Configuration:**
- Background task execution via `asyncio.create_task()`

**Error Handling:**
- Wraps all LLM tasks in try-except
- Returns empty state on failure
- Logs errors via loguru

**Used By:** Copilot router, WebSocket handler

**Uses:** All 3 LLM engines, repository

---

### 5.2.2 AICopilotEngine

**File:** `services/copilot/src/engine/copilot.py`

**Purpose:** Generates real-time interviewer assistance: follow-up questions, practical scenarios, missing concepts, and next topic recommendations.

**Responsibilities:**
- Analyzes last 20 transcript messages
- Detects answer quality (Strong/Partial/Weak) from evaluation rating
- Applies decision engine rules to question generation
- Returns structured JSON with 7 fields

**Inputs:**
- `transcript: List[Dict]` — Conversation history
- `jd: str` — Job description
- `resume: str` — Resume text
- `custom_prompt: str` — Custom instructions

**Outputs:**
```json
{
  "suggested_follow_up_questions": ["..."],
  "suggested_practical_questions": ["..."],
  "missing_concepts": ["..."],
  "verification_questions": ["..."],
  "recommended_next_topic": "...",
  "interview_notes": ["..."],
  "current_candidate_understanding": "..."
}
```

**Dependencies:**
- AsyncOpenAI client (DeepSeek API)
- loguru

**Configuration:**
- Uses `response_format={"type": "json_object"}` for structured output
- Decision engine rules:
  - Strong (≥80%): Return `["Move to the next topic."]`
  - Partial (50-79%): Generate 2-3 drill-down questions
  - Weak (<50%): Generate probing questions

**Error Handling:**
- JSON cleanup for markdown-wrapped responses
- Returns empty state on API failure

**Used By:** CopilotSessionEngine

**Uses:** DeepSeek API

---

### 5.2.3 ConversationIntelligenceEngine

**File:** `services/copilot/src/engine/intelligence.py`

**Purpose:** Analyzes skill coverage against JD requirements and resume claims.

**Responsibilities:**
- Extracts JD skills and maps coverage percentage
- Identifies resume experiences and tracks verification
- Detects conversation sentiment and depth
- Counts total speakers

**Inputs:**
- `transcript: List[Dict]`
- `jd: str`
- `resume: str`

**Outputs:**
```json
{
  "jd_coverage": [{"skill": "...", "covered": 80, "depth": "..."}],
  "resume_coverage": [{"experience": "...", "verified": false}],
  "sentiment": "positive",
  "conversation_depth": "technical",
  "total_speakers_count": 2
}
```

**Dependencies:**
- AsyncOpenAI (DeepSeek or Groq)
- loguru

**Error Handling:**
- Returns empty coverage arrays on failure

**Used By:** CopilotSessionEngine

**Uses:** DeepSeek/Groq API

---

### 5.2.4 CandidateEvaluationService

**File:** `services/copilot/src/services/evaluation.py`

**Purpose:** Scores candidate responses across 6 dimensions using LLM-based evaluation.

**Responsibilities:**
- Evaluates single candidate utterance against interviewer question
- Returns 0-100 ratings for 6 dimensions
- Provides detailed feedback strings

**Inputs:**
- `candidate_response: str`
- `jd: str`
- `resume: str`
- `question: str` — Last interviewer question

**Outputs:**
```json
{
  "technical_accuracy": {"rating": 75, "feedback": "..."},
  "confidence": {"rating": 60, "feedback": "..."},
  "completeness": {"rating": 70, "feedback": "..."},
  "practical_knowledge": {"rating": 65, "feedback": "..."},
  "communication": {"rating": 80, "feedback": "..."},
  "production_experience": {"rating": 50, "feedback": "..."},
  "knowledge_gaps": ["..."]
}
```

**Dependencies:**
- AsyncOpenAI (Groq API)
- loguru

**Configuration:**
- Uses `response_format={"type": "json_object"}`

**Error Handling:**
- Returns empty evaluation on failure

**Used By:** CopilotSessionEngine

**Uses:** Groq API

---

### 5.2.5 CopilotRepository

**File:** `services/copilot/src/services/repository.py`

**Purpose:** Dual-layer persistence: PostgreSQL for structured data, file system for JSON backups.

**Responsibilities:**
- Creates session directories
- Saves session state to JSON files
- Inserts/updates PostgreSQL copilot_sessions table
- Retrieves session state from DB or file

**Inputs:**
- `session_id: str`
- Session data dict

**Outputs:**
- Session state dict

**Dependencies:**
- Tortoise ORM
- File system I/O
- loguru

**Configuration:**
- Default directory: `./interviews/copilots/`

**Error Handling:**
- Falls back to file system if DB unavailable

**Used By:** CopilotSessionEngine, router

**Uses:** PostgreSQL, file system

---

## 5.3 Frontend Components

### 5.3.1 useInterviewAudio Hook

**File:** `frontend-new/src/hooks/useInterviewAudio.ts`

**Purpose:** Manages WebSocket audio streaming and Web Audio API for voice interview sessions.

**Responsibilities:**
- Establishes WebSocket connection
- Requests microphone access
- Downsamples audio from 48kHz → 16kHz
- Streams raw PCM binary to backend
- Receives and plays back TTS audio
- Measures microphone volume (RMS)

**Inputs:**
- `sessionId: string | null`

**Outputs:**
- `status: 'disconnected' | 'connecting' | 'connected' | 'error'`
- `error: string | null`
- `micVolumeRef: Ref<number>`
- `startConnection: () => Promise<void>`
- `stopConnection: () => void`

**Dependencies:**
- WebSocket API
- Web Audio API (AudioContext, ScriptProcessorNode, MediaStreamSource)
- getUserMedia

**Configuration:**
- Audio constraints: `channelCount=1`, `sampleRate=16000`, `echoCancellation=true`, `noiseSuppression=true`, `autoGainControl=true`
- Buffer size: 2048 samples

**Error Handling:**
- Catches mic permission errors
- Reconnects WebSocket on close
- Cleans up audio resources on unmount

**Used By:** InterviewSession.tsx

**Uses:** Interview Service WebSocket

---

### 5.3.2 useCopilotAudio Hook

**File:** `frontend-new/src/hooks/useCopilotAudio.ts`

**Purpose:** Manages WebSocket connection for copilot dashboard, receives real-time suggestions and transcript updates.

**Responsibilities:**
- Opens WebSocket to copilot service
- Receives `copilot_update` JSON frames
- Manages transcript, intelligence, assistance state
- Handles question pinning/unpinning
- Updates UI state reactively

**Inputs:**
- `sessionId: string | null`

**Outputs:**
- `status: AudioConnectionStatus`
- `error: string | null`
- `transcript: Array<TranscriptEntry>`
- `intelligence: IntelligenceState`
- `assistance: AssistanceState`
- `questions: PinnedQuestion[]`
- `startConnection: () => void`
- `stopConnection: () => void`
- `togglePinQuestion: (q: string) => void`

**Dependencies:**
- WebSocket API
- React useState, useEffect, useRef

**Error Handling:**
- Reconnects on WebSocket close
- Logs errors to console

**Used By:** CopilotSession.tsx

**Uses:** Copilot Service WebSocket

---

*Next: [Section 6 — API Documentation →](../06-api/06-api.md)*

