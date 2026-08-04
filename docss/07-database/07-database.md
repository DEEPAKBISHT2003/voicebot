# Section 7 — Database Documentation

> **Cross-references:** [Architecture](../03-architecture/03-architecture.md) | [Configuration](../12-configuration/12-configuration.md) | [Backend Docs](../17-backend/17-backend.md)

---

## 7.1 Database Overview

VoiceBot uses a **single shared PostgreSQL 15** database named `interview`, accessed by both backend services.

| Property | Value |
|---|---|
| Engine | PostgreSQL 15 (Alpine Docker image) |
| Database Name | `interview` |
| Default Port | 5432 |
| ORM | Tortoise ORM (async) |
| Driver | asyncpg |
| Schema Management | Tortoise `generate_schemas()` (auto on startup) |
| Connection Pooling | asyncpg built-in pool |

**File-system fallback:** Both services also maintain a JSON file mirror in `./interviews/` (development fallback when DB is unavailable).

---

## 7.2 Tables

### 7.2.1 `interview_sessions`

Stores complete state for each AI voice interview session.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, NOT NULL, DEFAULT `gen_random_uuid()` | Unique session identifier |
| `jd` | `TEXT` | NOT NULL | Job description text |
| `resume` | `TEXT` | NOT NULL | Parsed resume text |
| `custom_prompt` | `TEXT` | NULLABLE | Custom interview instructions |
| `resume_filename` | `VARCHAR(255)` | NULLABLE | Original resume filename |
| `resume_base64` | `TEXT` | NULLABLE | Base64-encoded original resume file |
| `transcript` | `JSONB` | NULLABLE, DEFAULT `[]` | Array of transcript entries |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `NOW()` | Session creation time |
| `is_active` | `BOOLEAN` | DEFAULT `true` | Whether session is currently active |
| `meeting_url` | `TEXT` | NULLABLE | Teams meeting URL if bot was used |

**Indexes:**
- Primary Key: `id`
- `idx_interview_sessions_timestamp` on `timestamp DESC` (inferred — for list sorting)

**Tortoise ORM Model:**
```python
class InterviewSession(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    jd = fields.TextField()
    resume = fields.TextField()
    custom_prompt = fields.TextField(null=True)
    resume_filename = fields.CharField(max_length=255, null=True)
    resume_base64 = fields.TextField(null=True)
    transcript = fields.JSONField(default=list)
    timestamp = fields.DatetimeField(auto_now_add=True)
    is_active = fields.BooleanField(default=True)
    meeting_url = fields.TextField(null=True)

    class Meta:
        table = "interview_sessions"
```

**Transcript JSONB Structure (array element):**
```json
{
  "speaker": "Candidate",
  "text": "I have 5 years of Python experience...",
  "timestamp": "2026-07-31T10:30:22.000Z",
  "evaluation": {
    "technical_accuracy": {"rating": 75, "feedback": "..."},
    "confidence": {"rating": 80, "feedback": "..."},
    "completeness": {"rating": 70, "feedback": "..."},
    "practical_knowledge": {"rating": 65, "feedback": "..."},
    "communication": {"rating": 85, "feedback": "..."},
    "production_experience": {"rating": 60, "feedback": "..."},
    "knowledge_gaps": ["Kubernetes networking", "Distributed tracing"]
  }
}
```

---

### 7.2.2 `copilot_sessions`

Stores complete state for each copilot session linked to an interview.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, NOT NULL | Session ID (same as interview session_id) |
| `jd` | `TEXT` | NOT NULL | Job description text |
| `resume` | `TEXT` | NOT NULL | Resume text |
| `custom_prompt` | `TEXT` | NULLABLE | Custom copilot instructions |
| `transcript` | `JSONB` | NULLABLE, DEFAULT `[]` | Transcript with per-utterance evaluations |
| `intelligence` | `JSONB` | NULLABLE | Latest intelligence analysis result |
| `assistance` | `JSONB` | NULLABLE | Latest copilot assistance result |
| `is_finalized` | `BOOLEAN` | DEFAULT `false` | Whether report has been finalized |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `NOW()` | Session creation time |

**Indexes:**
- Primary Key: `id`

**Tortoise ORM Model:**
```python
class CopilotSession(Model):
    id = fields.UUIDField(pk=True)
    jd = fields.TextField()
    resume = fields.TextField()
    custom_prompt = fields.TextField(null=True)
    transcript = fields.JSONField(default=list)
    intelligence = fields.JSONField(null=True)
    assistance = fields.JSONField(null=True)
    is_finalized = fields.BooleanField(default=False)
    timestamp = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "copilot_sessions"
```

**Intelligence JSONB Structure:**
```json
{
  "jd_coverage": [
    {"skill": "Python", "covered": 80, "depth": "advanced"},
    {"skill": "Kubernetes", "covered": 20, "depth": "surface"}
  ],
  "resume_coverage": [
    {"experience": "Redis migration project at XYZ Corp", "verified": false},
    {"experience": "Built REST API with FastAPI", "verified": true}
  ],
  "sentiment": "positive",
  "conversation_depth": "technical",
  "total_speakers_count": 2
}
```

**Assistance JSONB Structure:**
```json
{
  "suggested_follow_up_questions": ["Describe your caching strategy."],
  "suggested_practical_questions": ["Design a URL shortener at scale."],
  "missing_concepts": ["Kubernetes networking", "Observability"],
  "verification_questions": ["Walk me through your Redis migration."],
  "recommended_next_topic": "Ask about distributed systems experience.",
  "interview_notes": ["Strong Python skills, gaps in distributed systems."],
  "current_candidate_understanding": "Mid-level engineer with solid Python..."
}
```

---

## 7.3 ER Diagram

![Database Er Diagram](images/database_er_diagram.svg)

<details>
<summary>View Mermaid Source</summary>

```mermaid
erDiagram
    INTERVIEW_SESSIONS {
        uuid id PK
        text jd
        text resume
        text custom_prompt
        varchar resume_filename
        text resume_base64
        jsonb transcript
        timestamptz timestamp
        boolean is_active
        text meeting_url
    }

    COPILOT_SESSIONS {
        uuid id PK
        text jd
        text resume
        text custom_prompt
        jsonb transcript
        jsonb intelligence
        jsonb assistance
        boolean is_finalized
        timestamptz timestamp
    }

    TRANSCRIPT_ENTRY {
        string speaker
        string text
        string timestamp
        json evaluation
    }

    EVALUATION_ENTRY {
        json technical_accuracy
        json confidence
        json completeness
        json practical_knowledge
        json communication
        json production_experience
        array knowledge_gaps
    }

    INTELLIGENCE_DATA {
        array jd_coverage
        array resume_coverage
        string sentiment
        string conversation_depth
        int total_speakers_count
    }

    ASSISTANCE_DATA {
        array suggested_follow_up_questions
        array suggested_practical_questions
        array missing_concepts
        array verification_questions
        string recommended_next_topic
        array interview_notes
        string current_candidate_understanding
    }

    INTERVIEW_SESSIONS ||--o| COPILOT_SESSIONS : "session_id links"
    INTERVIEW_SESSIONS ||--o{ TRANSCRIPT_ENTRY : "transcript JSONB array"
    COPILOT_SESSIONS ||--o{ TRANSCRIPT_ENTRY : "transcript JSONB array"
    TRANSCRIPT_ENTRY ||--o| EVALUATION_ENTRY : "evaluation JSON"
    COPILOT_SESSIONS ||--o| INTELLIGENCE_DATA : "intelligence JSONB"
    COPILOT_SESSIONS ||--o| ASSISTANCE_DATA : "assistance JSONB"
```

</details>

---

## 7.4 Database Connection Configuration

**Interview Service (Tortoise ORM init):**
```python
TORTOISE_ORM = {
    "connections": {
        "default": os.getenv("DATABASE_URL", "postgres://user:pass@localhost:5432/interview")
    },
    "apps": {
        "models": {
            "models": ["services.interview.src.models.session"],
            "default_connection": "default"
        }
    }
}
```

**Copilot Service:**
```python
TORTOISE_ORM = {
    "connections": {
        "default": os.getenv("DATABASE_URL", "postgres://user:pass@localhost:5432/interview")
    },
    "apps": {
        "models": {
            "models": ["services.copilot.src.models.session"],
            "default_connection": "default"
        }
    }
}
```

Both services connect to the **same database** and manage their own table schemas independently via `generate_schemas()`.

---

## 7.5 File System Storage

Beyond PostgreSQL, both services persist session data to disk:

```
interviews/
├── {session_id}/
│   ├── session.json          # Full session state JSON
│   ├── jd.txt                # Job description plain text
│   ├── resume.txt            # Resume plain text
│   ├── resume.pdf            # Original uploaded resume
│   ├── recording.wav         # Complete interview audio WAV
│   └── uploaded_audio.wav    # Simulation uploaded WAV
└── copilots/
    └── {session_id}/
        └── session.json      # Full copilot session state JSON
```

**`session.json` Schema (Interview):**
```json
{
  "session_id": "3f9d2a1b",
  "timestamp": "2026-07-31T10:30:00.000Z",
  "jd": "...",
  "resume": "...",
  "custom_prompt": "...",
  "transcript": [...]
}
```

---

## 7.6 Schema Management

**Strategy:** Tortoise ORM `generate_schemas(safe=True)` is called on every application startup in the FastAPI lifespan handler. This creates tables if they don't exist but does **not** run migrations for schema changes.

**Migrations:** No formal migration tool (e.g. Aerich) is currently configured. Schema changes require manual `ALTER TABLE` statements or dropping and recreating tables.

> **Technical Debt:** Production deployments should integrate Aerich for Tortoise ORM migrations to safely evolve the schema. See [Section 26 — Future Improvements](../26-future-improvements/26-future-improvements.md).

---

## 7.7 Performance Considerations

- **JSONB columns** for `transcript`, `intelligence`, `assistance` allow Postgres to parse and index JSON fields. Operator queries like `->>` and `@>` can be used for transcript search.
- **asyncpg** provides true async PostgreSQL access with built-in connection pooling.
- Large transcript arrays grow unbounded per session. For long interviews (>1 hour), transcript JSONB fields can exceed 1MB. Consider periodic archiving.
- Sessions are never automatically deleted — a cleanup/archival policy is recommended for production.

---

*Next: [Section 8 — AI Architecture →](../08-ai-architecture/08-ai-architecture.md)*

