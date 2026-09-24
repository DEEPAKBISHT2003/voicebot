import os
import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from services.copilot.src.services.precompiler import SessionPreCompiler, CompactProfile
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


@pytest.fixture
def temp_storage(tmp_path):
    storage_dir = str(tmp_path / "interviews")
    os.makedirs(storage_dir, exist_ok=True)
    return storage_dir


@pytest.fixture
def mock_llm_response():
    return {
        "candidate_name": "Alice Smith",
        "target_role": "Senior Backend Architect",
        "core_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "Redis", "Kafka", "AWS"],
        "project_claims": [
            "Designed high-throughput distributed message processing pipeline",
            "Migrated monolithic architecture to event-driven microservices",
            "Optimized database queries reducing latency by 45%"
        ],
        "initial_questions": [
            "Could you walk us through the architecture of your distributed messaging pipeline?",
            "How did you handle consistency and failover during your database optimization work?"
        ],
        "scenario_questions": [
            "How would you design a distributed rate limiter for millions of concurrent requests?",
            "What strategies would you employ to guarantee zero-downtime database migrations?",
            "How do you debug an intermittent memory leak across microservices?",
            "How would you architect a fault-tolerant multi-region failover mechanism?",
            "How do you prevent cache stampedes under high write contention?"
        ],
        "verification_questions": [
            "Can you explain your exact contribution to the distributed message pipeline?",
            "What specific trade-offs did you evaluate when choosing Kafka over RabbitMQ?",
            "How did you verify and measure the 45% latency reduction in production?",
            "Tell us about a production incident you personally resolved on this stack.",
            "If you were to redesign that system from scratch today, what would you change?"
        ]
    }


def create_mock_openai_client(response_data):
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(response_data)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    return mock_client


@pytest.mark.asyncio
async def test_compact_profile_model():
    """Test CompactProfile initialization and serialization."""
    profile = CompactProfile(
        session_id="session-123",
        candidate_name="Bob Developer",
        target_role="Full Stack Engineer",
        core_skills=["React", "Python"],
        project_claims=["Built auth system"],
        initial_questions=["Q1", "Q2"],
        scenario_questions=["S1", "S2", "S3", "S4", "S5"],
        verification_questions=["V1", "V2", "V3", "V4", "V5"]
    )
    d = profile.to_dict()
    assert d["session_id"] == "session-123"
    assert d["candidate_name"] == "Bob Developer"
    assert len(d["initial_questions"]) == 2
    assert len(d["scenario_questions"]) == 5
    assert len(d["verification_questions"]) == 5


@pytest.mark.asyncio
async def test_session_precompiler_compilation_and_persistence(temp_storage, mock_llm_response):
    """Test standard single LLM compile and exact artifact persistence."""
    mock_client = create_mock_openai_client(mock_llm_response)
    precompiler = SessionPreCompiler(client=mock_client)
    
    session_id = "test-session-001"
    jd = "Senior Python Developer with FastAPI and Kubernetes experience."
    resume = "Alice Smith. 8 years building distributed microservices and databases."
    
    profile = await precompiler.compile_session(
        session_id=session_id,
        jd=jd,
        resume=resume,
        storage_dir=temp_storage
    )
    
    assert profile.session_id == session_id
    assert profile.candidate_name == "Alice Smith"
    assert profile.target_role == "Senior Backend Architect"
    assert len(profile.initial_questions) == 2
    assert len(profile.scenario_questions) == 5
    assert len(profile.verification_questions) == 5
    
    # Verify disk persistence for all three artifacts
    session_dir = os.path.join(temp_storage, session_id)
    assert os.path.exists(os.path.join(session_dir, "compact_profile.json"))
    assert os.path.exists(os.path.join(session_dir, "initial_suggestions.json"))
    assert os.path.exists(os.path.join(session_dir, "static_questions.json"))
    
    # Verify initial_suggestions.json structure
    with open(os.path.join(session_dir, "initial_suggestions.json"), "r") as f:
        init_data = json.load(f)
        assert init_data["session_id"] == session_id
        assert len(init_data["initial_suggestions"]) == 2
        assert init_data["generation_count"] == 1
        
    # Verify static_questions.json structure
    with open(os.path.join(session_dir, "static_questions.json"), "r") as f:
        static_data = json.load(f)
        assert static_data["session_id"] == session_id
        assert len(static_data["scenario_questions"]) == 5
        assert len(static_data["verification_questions"]) == 5
        assert static_data["generation_count"] == 1


@pytest.mark.asyncio
async def test_session_precompiler_disk_cache_hit(temp_storage, mock_llm_response):
    """Test that second compilation with existing disk cache performs 0 LLM calls."""
    mock_client = create_mock_openai_client(mock_llm_response)
    precompiler = SessionPreCompiler(client=mock_client)
    session_id = "test-cache-hit"
    
    # 1st call: invokes LLM
    profile1 = await precompiler.compile_session(
        session_id=session_id,
        jd="Senior Architect",
        resume="Alice Smith",
        storage_dir=temp_storage
    )
    assert mock_client.chat.completions.create.call_count == 1
    
    # 2nd call: reads disk cache, 0 LLM calls
    mock_client.chat.completions.create.reset_mock()
    profile2 = await precompiler.compile_session(
        session_id=session_id,
        jd="Senior Architect",
        resume="Alice Smith",
        storage_dir=temp_storage
    )
    assert mock_client.chat.completions.create.call_count == 0
    assert profile2.candidate_name == profile1.candidate_name
    assert profile2.initial_questions == profile1.initial_questions


@pytest.mark.asyncio
async def test_session_precompiler_count_enforcement(temp_storage):
    """Test count enforcement when LLM returns non-conforming question counts."""
    non_conforming_response = {
        "candidate_name": "Charlie",
        "target_role": "Engineer",
        "core_skills": ["Python"],
        "project_claims": ["Claim 1"],
        "initial_questions": ["Only 1 initial question"], # Under count
        "scenario_questions": ["S1", "S2", "S3"],         # Under count (3 instead of 5)
        "verification_questions": ["V1", "V2", "V3", "V4", "V5", "V6", "V7"] # Over count (7 instead of 5)
    }
    mock_client = create_mock_openai_client(non_conforming_response)
    precompiler = SessionPreCompiler(client=mock_client)
    
    profile = await precompiler.compile_session(
        session_id="test-counts",
        jd="Software Engineer",
        resume="Charlie developer",
        storage_dir=temp_storage
    )
    # Must enforce exactly 2, 5, 5
    assert len(profile.initial_questions) == 2
    assert len(profile.scenario_questions) == 5
    assert len(profile.verification_questions) == 5


@pytest.mark.asyncio
async def test_session_precompiler_empty_inputs_deterministic(temp_storage):
    """Test that empty JD and Resume generate deterministic profile with 0 LLM calls."""
    mock_client = create_mock_openai_client({})
    precompiler = SessionPreCompiler(client=mock_client)
    
    profile = await precompiler.compile_session(
        session_id="test-empty",
        jd="",
        resume="",
        storage_dir=temp_storage
    )
    assert mock_client.chat.completions.create.call_count == 0
    assert profile.candidate_name == "Candidate"
    assert profile.target_role == "Software Engineer"
    assert len(profile.initial_questions) == 2
    assert len(profile.scenario_questions) == 5
    assert len(profile.verification_questions) == 5


@pytest.mark.asyncio
async def test_session_precompiler_legacy_artifacts_synthesis(temp_storage):
    """Test that existing legacy static_questions.json and initial_suggestions.json are synthesized with 0 LLM calls."""
    session_id = "test-legacy"
    session_dir = os.path.join(temp_storage, session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    # Write legacy files
    with open(os.path.join(session_dir, "initial_suggestions.json"), "w") as f:
        json.dump({"session_id": session_id, "initial_suggestions": ["Init Q1", "Init Q2"]}, f)
    with open(os.path.join(session_dir, "static_questions.json"), "w") as f:
        json.dump({
            "session_id": session_id,
            "scenario_questions": [f"Scen {i}" for i in range(5)],
            "verification_questions": [f"Verif {i}" for i in range(5)]
        }, f)
        
    mock_client = create_mock_openai_client({})
    precompiler = SessionPreCompiler(client=mock_client)
    
    profile = await precompiler.compile_session(
        session_id=session_id,
        jd="Senior Python Dev",
        resume="Dan Developer",
        storage_dir=temp_storage
    )
    assert mock_client.chat.completions.create.call_count == 0
    assert profile.initial_questions == ["Init Q1", "Init Q2"]
    assert len(profile.scenario_questions) == 5
    assert len(profile.verification_questions) == 5


@pytest.mark.asyncio
async def test_session_precompiler_llm_failure_graceful_fallback(temp_storage):
    """Test graceful fallback when LLM throws network error or timeout."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("DeepSeek API timeout"))
    precompiler = SessionPreCompiler(client=mock_client)
    
    profile = await precompiler.compile_session(
        session_id="test-timeout",
        jd="Lead DevOps Engineer",
        resume="Eve Engineer\nCloud and CI/CD specialist",
        storage_dir=temp_storage
    )
    # Must not crash, returns valid fallback profile
    assert len(profile.initial_questions) == 2
    assert len(profile.scenario_questions) == 5
    assert len(profile.verification_questions) == 5
    assert os.path.exists(os.path.join(temp_storage, "test-timeout", "compact_profile.json"))


@pytest.mark.asyncio
async def test_copilot_session_engine_precompiler_integration(mock_llm_response, tmp_path):
    """Test CopilotSessionEngine integration with SessionPreCompiler."""
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()
    
    mock_client = create_mock_openai_client(mock_llm_response)
    
    import uuid
    test_session_id = f"test-engine-precompile-{uuid.uuid4().hex[:8]}"
    engine = CopilotSessionEngine(
        session_id=test_session_id,
        repo=mock_repo,
        initial_transcript=[],
        jd="Senior Python Architect",
        resume="Alice Smith. Distributed systems engineer."
    )
    engine.client = mock_client
    
    # Calling precompile directly
    profile = await engine.precompile()
    assert profile.candidate_name == "Alice Smith"
    assert len(engine.initial_suggestions) == 2
    assert len(engine.scenario_questions) == 5
    assert len(engine.verification_questions) == 5
    assert engine.static_questions_generated is True
    assert engine._initial_suggestions_generated is True
    assert "compact_profile" in engine.assistance
    
    # Subsequent calls to generate_initial_suggestions and generate_static_scenario_verification_questions
    # do NOT call precompiler LLM again
    initial_suggs = await engine.generate_initial_suggestions()
    assert len(initial_suggs) == 2
    
    static_qs = await engine.generate_static_scenario_verification_questions()
    assert len(static_qs["scenario_questions"]) == 5
    assert len(static_qs["verification_questions"]) == 5
    
    # Exactly 1 LLM call was made across all operations!
    assert mock_client.chat.completions.create.call_count == 1
