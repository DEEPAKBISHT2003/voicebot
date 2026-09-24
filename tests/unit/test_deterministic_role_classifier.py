import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.copilot.src.services.deterministic_role_classifier import DeterministicRoleClassifier
from services.copilot.src.services.role_identifier import ConversationalRoleIdentifier
from services.copilot.src.services.precompiler import CompactProfile
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository


@pytest.fixture
def mock_fallback_identifier():
    mock = MagicMock(spec=ConversationalRoleIdentifier)
    mock.identify_roles = AsyncMock(return_value={
        "speakers": {
            "Speaker 1": {"role": "interviewer", "confidence": 0.85, "reasoning": "LLM identified as interviewer"},
            "Speaker 2": {"role": "candidate", "confidence": 0.90, "reasoning": "LLM identified as candidate"}
        }
    })
    return mock


@pytest.mark.asyncio
async def test_priority1_resume_candidate_name_exact_match(mock_fallback_identifier):
    """Priority 1: Exact candidate name from resume identifies candidate and infers interviewer with 0 LLM calls."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    resume = "Alice Smith\nSenior Distributed Systems Engineer\nEmail: alice@example.com"
    participants = ["Alice Smith", "Bob Jones"]
    
    result = await classifier.identify_roles(
        transcript=[
            {"speaker": "Bob Jones", "text": "Welcome to the interview Alice."},
            {"speaker": "Alice Smith", "text": "Thank you Bob, excited to be here."}
        ],
        participants=participants,
        resume=resume
    )
    
    speakers = result["speakers"]
    assert speakers["Alice Smith"]["role"] == "candidate"
    assert speakers["Alice Smith"]["confidence"] >= 0.80
    assert speakers["Bob Jones"]["role"] == "interviewer"
    assert speakers["Bob Jones"]["confidence"] >= 0.80
    
    # 0 LLM calls made!
    assert mock_fallback_identifier.identify_roles.call_count == 0


@pytest.mark.asyncio
async def test_priority1_resume_candidate_name_with_corporate_tags(mock_fallback_identifier):
    """Priority 1: Candidate name matching handles Teams tags like (External) or (Guest)."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    resume = "Deepak Bisht\nLead Backend Architect\nPython, Distributed Systems"
    participants = ["Deepak Bisht (External)", "Sarah Connor"]
    
    result = await classifier.identify_roles(
        transcript=[],
        participants=participants,
        resume=resume
    )
    
    speakers = result["speakers"]
    assert speakers["Deepak Bisht (External)"]["role"] == "candidate"
    assert speakers["Deepak Bisht (External)"]["confidence"] >= 0.80
    assert speakers["Sarah Connor"]["role"] == "interviewer"
    assert speakers["Sarah Connor"]["confidence"] >= 0.80
    assert mock_fallback_identifier.identify_roles.call_count == 0


@pytest.mark.asyncio
async def test_priority1_compact_profile_candidate_name(mock_fallback_identifier):
    """Priority 1: Uses candidate_name from CompactProfile if available."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    compact_profile = CompactProfile(
        session_id="sess-1",
        candidate_name="Elena Rostova",
        target_role="AI Engineer"
    )
    participants = ["Elena Rostova", "Interviewer Dave"]
    
    result = await classifier.identify_roles(
        transcript=[],
        participants=participants,
        compact_profile=compact_profile
    )
    
    speakers = result["speakers"]
    assert speakers["Elena Rostova"]["role"] == "candidate"
    assert speakers["Elena Rostova"]["confidence"] >= 0.80
    assert speakers["Interviewer Dave"]["role"] == "interviewer"
    assert mock_fallback_identifier.identify_roles.call_count == 0


@pytest.mark.asyncio
async def test_priority2_teams_participant_role_keywords(mock_fallback_identifier):
    """Priority 2: Participant display names with explicit role labels are classified with 1.0 confidence."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    participants = ["Candidate", "Interviewer - Jane", "Appzlogic Observer"]
    
    result = await classifier.identify_roles(
        transcript=[],
        participants=participants
    )
    
    speakers = result["speakers"]
    assert speakers["Candidate"]["role"] == "candidate"
    assert speakers["Candidate"]["confidence"] == 1.0
    assert speakers["Interviewer - Jane"]["role"] == "interviewer"
    assert speakers["Interviewer - Jane"]["confidence"] == 1.0
    assert speakers["Appzlogic Observer"]["role"] == "unknown"
    assert mock_fallback_identifier.identify_roles.call_count == 0


@pytest.mark.asyncio
async def test_priority3_existing_speaker_metadata(mock_fallback_identifier):
    """Priority 3: Preserves existing confident speaker roles from transcript history."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    transcript = [
        {"speaker": "User A", "speaker_role": "interviewer", "text": "Let us begin."},
        {"speaker": "User B", "speaker_role": "candidate", "text": "Sure."}
    ]
    participants = ["User A", "User B"]
    
    result = await classifier.identify_roles(
        transcript=transcript,
        participants=participants
    )
    
    speakers = result["speakers"]
    assert speakers["User A"]["role"] == "interviewer"
    assert speakers["User A"]["confidence"] >= 0.80
    assert speakers["User B"]["role"] == "candidate"
    assert speakers["User B"]["confidence"] >= 0.80
    assert mock_fallback_identifier.identify_roles.call_count == 0


@pytest.mark.asyncio
async def test_priority4_linguistic_heuristics_asymmetric_dialogue(mock_fallback_identifier):
    """Priority 4: Asymmetric interview dialogue (questions vs first-person answers) resolves roles."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    transcript = [
        {"speaker": "Speaker X", "text": "Can you walk me through your experience with microservices?"},
        {"speaker": "Speaker Y", "text": "In my previous project, I designed and built a distributed event processing system using Kafka and Python."},
        {"speaker": "Speaker X", "text": "How did you handle consistency and failovers across multiple database partitions?"},
        {"speaker": "Speaker Y", "text": "I was responsible for implementing two-phase commit protocols and read-replica routing."}
    ]
    participants = ["Speaker X", "Speaker Y"]
    
    result = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=""
    )
    
    speakers = result["speakers"]
    assert speakers["Speaker X"]["role"] == "interviewer"
    assert speakers["Speaker X"]["confidence"] >= 0.80
    assert speakers["Speaker Y"]["role"] == "candidate"
    assert speakers["Speaker Y"]["confidence"] >= 0.80
    assert mock_fallback_identifier.identify_roles.call_count == 0


@pytest.mark.asyncio
async def test_priority5_llm_fallback_when_confidence_below_threshold(mock_fallback_identifier):
    """Priority 5: Ambiguous dialogue with no name match triggers LLM fallback."""
    classifier = DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)
    
    # Ambiguous short utterances (cannot be resolved deterministically)
    transcript = [
        {"speaker": "Speaker 1", "text": "Hello."},
        {"speaker": "Speaker 2", "text": "Hi there, can you hear me?"},
        {"speaker": "Speaker 1", "text": "Yes I can hear you clearly."}
    ]
    participants = ["Speaker 1", "Speaker 2"]
    
    result = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=""
    )
    
    # Fallback must be triggered because deterministic confidence < 0.80
    assert mock_fallback_identifier.identify_roles.call_count == 1
    speakers = result["speakers"]
    assert speakers["Speaker 1"]["role"] == "interviewer"
    assert speakers["Speaker 2"]["role"] == "candidate"


@pytest.mark.asyncio
async def test_feature_flag_disabled_delegates_directly_to_llm(mock_fallback_identifier):
    """When ENABLE_DETERMINISTIC_ROLE_CLASSIFIER is False, delegates directly to LLM."""
    classifier = DeterministicRoleClassifier(
        fallback_identifier=mock_fallback_identifier,
        enable_deterministic=False
    )
    
    # Even with exact candidate name match, feature flag being False should bypass deterministic
    result = await classifier.identify_roles(
        transcript=[],
        participants=["Alice Smith", "Bob Jones"],
        resume="Alice Smith"
    )
    
    assert mock_fallback_identifier.identify_roles.call_count == 1


@pytest.mark.asyncio
async def test_session_engine_end_to_end_deterministic_role_resolution(tmp_path):
    """CopilotSessionEngine resolves roles deterministically and backfills transcript turns."""
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()
    
    engine = CopilotSessionEngine(
        session_id="test-engine-role-det",
        repo=mock_repo,
        initial_transcript=[],
        resume="Carol White\nPrincipal Cloud Engineer"
    )
    
    # Add turns that trigger role resolution: Carol White + Interviewer Dave
    await engine.add_message(speaker="Dave", text="Can you walk me through how you deployed the cluster?")
    await engine.add_message(speaker="Carol White", text="In my previous company, I designed the Terraform automation for AWS EKS.")
    
    # Give background tasks time to execute
    await asyncio.sleep(0.1)
    
    # Check that roles resolved deterministically
    assert engine.session_speaker_roles.get("Carol White") == "candidate"
    assert engine.session_speaker_roles.get("Dave") == "interviewer"
    
    # Check that transcript turns were backfilled with the resolved roles
    transcript = engine.get_transcript()
    carol_turn = [t for t in transcript if t["speaker"] == "Carol White"][0]
    dave_turn = [t for t in transcript if t["speaker"] == "Dave"][0]
    assert carol_turn["speaker_role"] == "candidate"
    assert dave_turn["speaker_role"] == "interviewer"
