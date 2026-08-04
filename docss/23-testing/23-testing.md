# Section 23 — Testing

> **Cross-references:** [Deployment Guide](../24-deployment/24-deployment.md) | [CI/CD Pipeline](../24-deployment/24-deployment.md#cicd)

---

## 23.1 Test Infrastructure

| Tool | Purpose | Location |
|---|---|---|
| pytest | Python unit + integration tests | `services/*/tests/` |
| pytest-asyncio | Async test support | same |
| pytest-cov | Coverage reporting | `--cov` flag |
| Codecov | Coverage upload in CI | `.github/workflows/ci.yml` |
| oxlint | TypeScript linting (frontend) | `frontend-new/.oxlintrc.json` |
| ruff | Python linting + formatting | `pyproject.toml` or `ruff.toml` |

---

## 23.2 Running Tests

### Python Tests

```bash
# Run all tests with coverage
pytest services/ --cov=services --cov-report=term-missing

# Run tests for interview service only
pytest services/interview/tests/ -v

# Run tests for copilot service only
pytest services/copilot/tests/ -v

# Run single test file
pytest services/interview/tests/test_speaker_classifier.py -v

# Run with specific marker
pytest -m "unit" -v
pytest -m "integration" -v
```

### Frontend Tests (Linting)

```bash
cd frontend-new

# Run oxlint
npx oxlint .

# TypeScript type check
npx tsc --noEmit

# Build check (catches import errors)
npm run build
```

---

## 23.3 Unit Tests

### Interview Service Unit Tests

**`test_speaker_classifier.py`** — Tests the hybrid speaker role classifier:
```python
import pytest
from services.interview.src.api.interviews import classify_speaker_role

def test_candidate_linguistic_markers():
    """Candidate linguistic patterns override diarization."""
    speaker_map = {}
    result = classify_speaker_role("sir, I have 5 years experience", "0", speaker_map)
    assert result == "Candidate"

def test_interviewer_question_pattern():
    """Question-ending utterances classified as interviewer."""
    speaker_map = {}
    result = classify_speaker_role("Can you describe your experience?", None, speaker_map)
    assert result == "Interviewer"

def test_diarization_first_speaker_is_candidate():
    """First diarized speaker (speaker_0) defaults to Candidate."""
    speaker_map = {}
    result = classify_speaker_role("hello", "0", speaker_map)
    assert result == "Candidate"
    assert speaker_map["0"] == "Candidate"

def test_diarization_second_speaker_is_interviewer():
    """Second diarized speaker defaults to Interviewer."""
    speaker_map = {"0": "Candidate"}
    result = classify_speaker_role("tell me more", "1", speaker_map)
    assert result == "Interviewer"
```

**`test_document_parser.py`** — Tests resume parsing:
```python
def test_pdf_parser_returns_text():
    from services.interview.src.parsers.pdf_parser import PDFParser
    parser = PDFParser()
    # Use sample PDF bytes
    text = parser.parse(sample_pdf_bytes, "resume.pdf")
    assert isinstance(text, str)
    assert len(text) > 0

def test_factory_routes_pdf():
    from services.interview.src.parsers.factory import DocumentParserFactory
    parser = DocumentParserFactory.get_parser("resume.pdf")
    assert parser.__class__.__name__ == "PDFParser"

def test_factory_rejects_unknown():
    with pytest.raises(ValueError):
        DocumentParserFactory.get_parser("resume.xyz")
```

### Copilot Service Unit Tests

**`test_copilot_engine.py`** — Tests decision engine and utterance stitching:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.copilot.src.engine.session import CopilotSessionEngine

@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.save_session = AsyncMock()
    return repo

@pytest.mark.asyncio
async def test_add_message_returns_instantly(mock_repo):
    """add_message() should return within acceptable time."""
    import time
    engine = CopilotSessionEngine("test-id", mock_repo, jd="test jd", resume="test resume")
    start = time.time()
    result = await engine.add_message("Candidate", "I have Python experience.")
    elapsed = time.time() - start
    assert elapsed < 0.1  # <100ms (generous threshold, should be <5ms)
    assert result["speaker"] == "Candidate"
    assert result["text"] == "I have Python experience."

@pytest.mark.asyncio
async def test_utterance_stitching(mock_repo):
    """Same-speaker consecutive messages should be merged."""
    engine = CopilotSessionEngine("test-id", mock_repo)
    await engine.add_message("Candidate", "I worked at")
    result = await engine.add_message("Candidate", "Google for 3 years.")
    assert "I worked at" in engine.transcript[-1]["text"]
    assert "Google for 3 years." in engine.transcript[-1]["text"]

def test_decision_engine_strong():
    from services.copilot.src.engine.copilot import AICopilotEngine
    engine = AICopilotEngine.__new__(AICopilotEngine)
    # Verify strong answer triggers correct decision prompt
    # (tested via integration or prompt inspection)
    pass
```

**`test_clean_json_loads.py`**:
```python
from services.copilot.src.engine.copilot import clean_json_loads

def test_plain_json():
    result = clean_json_loads('{"key": "value"}')
    assert result == {"key": "value"}

def test_markdown_wrapped_json():
    result = clean_json_loads('```json\n{"key": "value"}\n```')
    assert result == {"key": "value"}

def test_backtick_only_wrapped():
    result = clean_json_loads('```\n{"key": "value"}\n```')
    assert result == {"key": "value"}
```

---

## 23.4 Integration Tests

Integration tests run against a full Docker stack (started in CI):

```python
# tests/integration/test_interview_api.py
import httpx
import pytest

BASE_URL = "http://localhost:8000"

@pytest.mark.asyncio
async def test_start_and_stop_interview():
    async with httpx.AsyncClient() as client:
        # Start session
        resp = await client.post(f"{BASE_URL}/api/interviews/start", json={
            "jd": "Senior Python engineer with 5 years experience...",
            "resume": "John Smith, Python developer..."
        })
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        assert session_id

        # Verify session exists
        resp = await client.get(f"{BASE_URL}/api/interviews/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == session_id

        # Stop session
        resp = await client.post(f"{BASE_URL}/api/interviews/{session_id}/stop")
        assert resp.status_code == 200

@pytest.mark.asyncio
async def test_parse_resume_pdf():
    async with httpx.AsyncClient() as client:
        with open("tests/fixtures/sample_resume.pdf", "rb") as f:
            resp = await client.post(
                f"{BASE_URL}/api/interviews/parse-resume",
                files={"file": ("resume.pdf", f, "application/pdf")}
            )
        assert resp.status_code == 200
        assert "text" in resp.json()
        assert len(resp.json()["text"]) > 0
```

---

## 23.5 GitHub Actions CI Pipeline

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install ruff
      - run: ruff check services/

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_DB: interview_test
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: testpass
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 10s
    env:
      DATABASE_URL: postgres://postgres:testpass@localhost:5432/interview_test
      DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY_TEST }}
      DEEPGRAM_API_KEY: ${{ secrets.DEEPGRAM_API_KEY_TEST }}
      GROQ_API_KEY: ${{ secrets.GROQ_API_KEY_TEST }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r services/interview/requirements.txt
      - run: pip install -r services/copilot/requirements.txt
      - run: pytest services/ --cov=services --cov-report=xml
      - uses: codecov/codecov-action@v4
        with: { files: coverage.xml }

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build

  deploy:
    needs: [lint, test, build]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose push
```

---

## 23.6 Mocking Strategy

For unit tests, external services are mocked:

```python
# Mock Deepgram STT
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_pipeline_builds():
    with patch("services.interview.src.pipeline.builder.DeepgramSTTService") as mock_stt:
        with patch("services.interview.src.pipeline.builder.DeepgramTTSService") as mock_tts:
            with patch("services.interview.src.pipeline.builder.OpenAILLMService") as mock_llm:
                builder = LocalPipecatPipelineBuilder("fake-dg-key", "fake-ds-key")
                pipeline, context, worker = builder.build_pipeline(
                    system_instruction="Test prompt",
                    session_id="test-123"
                )
                assert pipeline is not None

# Mock AsyncOpenAI for LLM tests
@pytest.mark.asyncio
async def test_generate_assistance_with_mocked_llm():
    engine = AICopilotEngine(api_key="test", model="test", base_url="http://test")
    with patch.object(engine.client.chat.completions, "create") as mock_create:
        mock_create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content='{"suggested_follow_up_questions": ["Q1?"]}'))]
        )
        result = await engine.generate_assistance(
            transcript=[{"speaker": "Candidate", "text": "I know Python."}],
            jd="Python engineer",
            resume="John Smith"
        )
        assert "suggested_follow_up_questions" in result
```

---

## 23.7 Coverage Targets

| Module | Target Coverage | Notes |
|---|---|---|
| `api/interviews.py` | 70%+ | WebSocket paths hard to test without real audio |
| `engine/session.py` | 85%+ | Core business logic — high priority |
| `engine/copilot.py` | 80%+ | LLM calls mocked |
| `services/evaluation.py` | 80%+ | LLM calls mocked |
| `parsers/` | 90%+ | Pure functions, easy to test |
| `prompts/` | 90%+ | Pure string formatting |
| `pipeline/builder.py` | 60%+ | Pipecat internals hard to unit test |

---

*Next: [Section 24 — Deployment Guide →](../24-deployment/24-deployment.md)*

