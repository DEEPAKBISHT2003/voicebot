# Section 12 — Configuration

> **Cross-references:** [Infrastructure](../11-infrastructure/11-infrastructure.md) | [Docker Documentation](../10-docker/10-docker.md) | [Security](../20-security/20-security.md)

---

## 12.1 Configuration Strategy

All configuration follows the **12-Factor App** methodology:
- Configuration stored in environment variables
- Separate config from code
- `.env` file for local development (git-ignored)
- Docker Compose injects env vars into containers via `env_file: .env`

---

## 12.2 Master Environment Variable Reference

All variables documented from `.env.example`:

### AI Service Keys
| Variable | Required | Example | Description |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | `sk-abc123...` | DeepSeek LLM API key (OpenAI-compatible) |
| `DEEPGRAM_API_KEY` | Yes | `abc123...` | Deepgram STT + TTS API key |
| `GROQ_API_KEY` | Yes | `gsk_abc123...` | Groq LLM API key (evaluation service) |

### Database
| Variable | Required | Example | Description |
|---|---|---|---|
| `POSTGRES_DB` | Yes | `interview` | PostgreSQL database name |
| `POSTGRES_USER` | Yes | `postgres` | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | `secretpassword` | PostgreSQL password |
| `DATABASE_URL` | Yes | `postgres://postgres:pass@db:5432/interview` | Full asyncpg connection string |

### Service URLs (Inter-container)
| Variable | Required | Default | Description |
|---|---|---|---|
| `COPILOT_URL` | No | `http://localhost:8001` | Interview service → Copilot service URL |
| `COPILOT_SERVICE_URL` | No | `http://localhost:8001` | Alias for COPILOT_URL |
| `BACKEND_WS_BASE` | No | `ws://localhost:8000` | Teams bot → Interview service WebSocket base |

### CORS
| Variable | Required | Default | Description |
|---|---|---|---|
| `CORS_ALLOWED_ORIGINS` | No | `*` | Comma-separated allowed browser origins |

### Frontend (Vite build-time)
| Variable | Required | Example | Description |
|---|---|---|---|
| `VITE_API_URL` | No | `http://localhost` or `https://yourdomain.com` | Backend base URL for Axios |
| `VITE_BACKEND_URL` | No | Same as above | Alternate env key (checked as fallback) |

### Service Ports
| Variable | Required | Default | Description |
|---|---|---|---|
| `INTERVIEW_SERVICE_PORT` | No | `8000` | Interview service Uvicorn port |
| `COPILOT_SERVICE_PORT` | No | `8001` | Copilot service Uvicorn port |

### AI Model Configuration (Interview Service)
| Variable | Required | Default | Description |
|---|---|---|---|
| `DEEPSEEK_BASE_URL` | No | `https://api.deepseek.com/v1` | DeepSeek OpenAI-compatible endpoint |
| `DEEPSEEK_MODEL` | No | `deepseek-chat` | LLM model name for DeepSeek |

---

## 12.3 Service Config Classes

### Interview Service Settings

**File:** `services/interview/src/core/config.py`

```python
from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    # API Keys
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    
    # LLM Configuration
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    
    # Storage
    DEFAULT_STORAGE_DIR: str = os.getenv("DEFAULT_STORAGE_DIR", "interviews")
    
    # Service URLs
    COPILOT_URL: str = os.getenv("COPILOT_URL", "http://localhost:8001")
    
    class Config:
        env_file = ".env"
        case_sensitive = True
```

### Copilot Service Settings

**File:** `services/copilot/src/core/config.py`

```python
class CopilotSettings(BaseSettings):
    # API Keys
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    
    # LLM Configuration
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Storage
    DEFAULT_STORAGE_DIR: str = os.getenv("DEFAULT_STORAGE_DIR", "interviews/copilots")
    
    # Inter-service
    BACKEND_WS_BASE: str = os.getenv("BACKEND_WS_BASE", "ws://localhost:8000")
    
    class Config:
        env_file = ".env"
```

---

## 12.4 Frontend Configuration

Frontend uses Vite's `import.meta.env` for build-time environment variables:

**File:** `frontend-new/src/api/axios.ts`
```typescript
const BASE_URL = import.meta.env.VITE_API_URL || '';
export const apiClient = axios.create({ baseURL: BASE_URL });
```

**File:** `frontend-new/src/hooks/useInterviewAudio.ts`
```typescript
const rawEnvUrl = import.meta.env.VITE_API_URL || 
                  import.meta.env.VITE_BACKEND_URL;
// Falls back to relative URL if env not set
```

**Vite env variable rules:**
- Must be prefixed with `VITE_` to be exposed to the browser
- Injected at build time — changing them requires a rebuild
- Available via `import.meta.env.VITE_*`

---

## 12.5 Environment Configurations by Stage

### Development (Local)

```bash
# .env (local development)
DEEPSEEK_API_KEY=sk-dev-key
DEEPGRAM_API_KEY=dev-deepgram-key
GROQ_API_KEY=gsk_dev-key

POSTGRES_DB=interview
POSTGRES_USER=postgres
POSTGRES_PASSWORD=devpassword123

DATABASE_URL=postgres://postgres:devpassword123@db:5432/interview
COPILOT_URL=http://copilot-service:8001
BACKEND_WS_BASE=ws://interview-service:8000

CORS_ALLOWED_ORIGINS=http://localhost,http://localhost:5173

VITE_API_URL=http://localhost
```

### Production

```bash
# .env (production)
DEEPSEEK_API_KEY=sk-prod-key
DEEPGRAM_API_KEY=prod-deepgram-key
GROQ_API_KEY=gsk_prod-key

POSTGRES_DB=interview_prod
POSTGRES_USER=voicebot_user
POSTGRES_PASSWORD=<32-char-random-password>

DATABASE_URL=postgres://voicebot_user:<password>@db:5432/interview_prod
COPILOT_URL=http://copilot-service:8001
BACKEND_WS_BASE=ws://interview-service:8000

CORS_ALLOWED_ORIGINS=https://app.yourdomain.com

VITE_API_URL=https://app.yourdomain.com
```

### Testing (CI)

```bash
# .env.test (GitHub Actions)
DEEPSEEK_API_KEY=test-key (mocked)
DEEPGRAM_API_KEY=test-key (mocked)
GROQ_API_KEY=test-key (mocked)

POSTGRES_DB=interview_test
POSTGRES_USER=postgres
POSTGRES_PASSWORD=testpassword

DATABASE_URL=postgres://postgres:testpassword@localhost:5432/interview_test
```

---

## 12.6 Runtime Configuration (Non-Env)

Some behavior is hard-coded in the application and requires code changes:

| Setting | Location | Current Value | Notes |
|---|---|---|---|
| VAD confidence threshold | `pipeline/builder.py` | `0.8` | Increase to reduce false triggers |
| VAD min volume | `pipeline/builder.py` | `0.20` | Decrease for quiet microphones |
| VAD stop_secs (live) | `pipeline/builder.py` | `1.0s` | Silence duration before LLM fires |
| VAD stop_secs (simulation) | `pipeline/builder.py` | `0.4s` | Faster for recorded audio |
| Deepgram endpointing | `pipeline/builder.py` | `400ms` | Transcript finalization wait |
| TTS voice | `pipeline/builder.py` | `aura-2-amalthea-en` | Change for different AI voice |
| Copilot context window | `engine/copilot.py` | Last 20 messages | Increase for longer context |
| Playback buffer size | `pipeline/builder.py` | `5 chunks` | Increase for smoother audio |
| Audio buffer size | `hooks/useInterviewAudio.ts` | `2048 samples` | Browser capture buffer |

---

## 12.7 Secrets Management

**Current approach:** `.env` file on server, git-ignored.

**Production recommendations:**
1. **AWS Secrets Manager** — store API keys, retrieve at startup
2. **AWS Parameter Store** — non-sensitive config with versioning
3. **Docker Secrets** (Swarm mode) — for self-hosted deployments
4. **HashiCorp Vault** — enterprise secret management

**Never:**
- Commit `.env` to git
- Hardcode API keys in source code
- Log API key values (even first few characters)
- Pass secrets as Docker build args (they appear in image history)

---

*Next: [Section 13 — Execution Flow →](../13-execution-flow/13-execution-flow.md)*

