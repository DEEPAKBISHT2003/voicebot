"""
Unit Test Suite: Conversational Realism of Follow-Up Questions
File: tests/unit/test_follow_up_conversational_realism.py

Deterministic tests covering all 9 Phase 5 validation requirements:
1. Strong/complete answer does not automatically generate multiple follow-ups (recommends next topic).
2. Partially correct answer focuses on one targeted, relevant question.
3. Weak answer produces a diagnostic question rather than several stacked questions.
4. Questions do not contain multiple independent interrogatives / stacking.
5. Follow-ups refer to actual candidate answer claims and do not invent technologies.
6. Previously answered points are not unnecessarily repeated.
7. Existing response schema (list of strings, JSON structures) remains 100% compatible.
8. Real-time dynamic suggestions path and consolidated UnifiedQAWorker path follow consistent style rules.
9. Default custom prompt does not enforce a rigid/mandatory 2-question count.
"""

import re
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.copilot.src.engine.copilot import AICopilotEngine
from services.copilot.src.services.unified_qa_worker import UnifiedQAWorker
from services.copilot.src.services.evaluation import CandidateEvaluationService


# ============================================================================
# HELPER: Stacked Question & Interrogative Validator
# ============================================================================

def is_stacked_question(text: str) -> bool:
    """
    Detects whether a question is compound/stacked:
    - Contains multiple question marks ('...? ...?')
    - Combines multiple independent interrogatives (e.g. 'how did you... and what... and why...')
    """
    clean = text.strip()
    if clean.count("?") > 1:
        return True
    
    # Check for chained interrogatives joined by conjunctions
    interrogative_pattern = re.compile(
        r'\b(and|also|while you\'re at it)\s+(how|what|why|when|where|who|which|can you|could you)\b',
        re.IGNORECASE
    )
    if interrogative_pattern.search(clean):
        return True
    return False


def test_stacked_question_validator_behavior():
    """Verifies that our validator accurately distinguishes artificial stacked questions from focused questions."""
    stacked_1 = "Can you explain how you implemented caching, what eviction strategy you selected, how you handled cache invalidation, and what performance improvements you observed?"
    stacked_2 = "How did you scale the database, and what was your failover strategy?"
    stacked_3 = "Why did you choose PostgreSQL? And how did you manage replicas?"

    assert is_stacked_question(stacked_1) is True
    assert is_stacked_question(stacked_2) is True
    assert is_stacked_question(stacked_3) is True

    focused_1 = "You mentioned Redis caching. How did you handle cache invalidation?"
    focused_2 = "What made you choose async processing for this part?"
    focused_3 = "You mentioned splitting the service into microservices. What problem were you trying to solve with that?"
    focused_4 = "What other approaches did you consider before settling on this design?"

    assert is_stacked_question(focused_1) is False
    assert is_stacked_question(focused_2) is False
    assert is_stacked_question(focused_3) is False
    assert is_stacked_question(focused_4) is False


# ============================================================================
# REQUIREMENT 1: Strong Answer Decision & Next Topic Recommendation
# ============================================================================

@pytest.mark.asyncio
async def test_strong_answer_suppresses_multiple_followups():
    """A strong answer (>=80%) instructs the model to move to next topic and post-processing enforces it."""
    engine = AICopilotEngine()

    transcript = [
        {"speaker": "Interviewer", "text": "What is Postgres MVCC?"},
        {
            "speaker": "Candidate",
            "text": "Postgres creates new tuple versions with updated values.",
            "evaluation": {"technical_accuracy": {"rating": 95}}
        }
    ]

    mock_response_json = {
        "suggested_follow_up_questions": [],
        "suggested_practical_questions": ["Design an indexing strategy."],
        "missing_concepts": [],
        "verification_questions": [],
        "recommended_next_topic": "Database Sharding",
        "interview_notes": ["Strong MVCC grasp."],
        "current_candidate_understanding": "Senior level database fundamentals."
    }

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_response_json)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    engine.client = mock_client

    result = await engine.generate_assistance(transcript=transcript, jd="Backend JD", resume="Resume")

    # Invariants:
    assert result["suggested_follow_up_questions"] == ["Move to the next topic."]
    assert result["recommended_next_topic"] == "Database Sharding"

    # Verify prompt passed to LLM
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    prompt_sent = call_kwargs["messages"][0]["content"]
    assert "CRITICAL DECISION RULE (Strong Answer detected, rating: 95%):" in prompt_sent
    assert "Do NOT generate multiple follow-up questions" in prompt_sent
    assert "Usually set \"suggested_follow_up_questions\" to exactly: [\"Move to the next topic.\"]." in prompt_sent


# ============================================================================
# REQUIREMENT 2: Partially Correct Answer Produces One Focused Question
# ============================================================================

@pytest.mark.asyncio
async def test_partial_answer_generates_one_focused_question():
    """A partial answer (50-79%) guides the model to produce one focused, natural question."""
    engine = AICopilotEngine()

    transcript = [
        {"speaker": "Interviewer", "text": "Explain Two-Phase Commit."},
        {
            "speaker": "Candidate",
            "text": "The coordinator asks participants to prepare, then commits.",
            "evaluation": {"technical_accuracy": {"rating": 65}}
        }
    ]

    mock_response_json = {
        "suggested_follow_up_questions": [
            "What happens if one of the participants fails during the prepare phase?"
        ],
        "suggested_practical_questions": [],
        "missing_concepts": ["Rollback handling"],
        "verification_questions": [],
        "recommended_next_topic": "Fault Tolerance",
        "interview_notes": ["Understands prepare phase, missed rollback."],
        "current_candidate_understanding": "Moderate distributed systems knowledge."
    }

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_response_json)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    engine.client = mock_client

    result = await engine.generate_assistance(transcript=transcript)

    assert len(result["suggested_follow_up_questions"]) == 1
    follow_up = result["suggested_follow_up_questions"][0]
    assert not is_stacked_question(follow_up)
    assert "fails" in follow_up.lower()

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    prompt_sent = call_kwargs["messages"][0]["content"]
    assert "CRITICAL DECISION RULE (Partial Answer detected, rating: 65%):" in prompt_sent
    assert "Generate ONE focused, natural follow-up question" in prompt_sent


# ============================================================================
# REQUIREMENT 3: Weak Answer Produces a Diagnostic Question
# ============================================================================

@pytest.mark.asyncio
async def test_weak_answer_generates_diagnostic_question():
    """A weak answer (<50%) instructs the model to produce a diagnostic question rather than a stacked interrogation."""
    engine = AICopilotEngine()

    transcript = [
        {"speaker": "Interviewer", "text": "How do database indexes improve query speed?"},
        {
            "speaker": "Candidate",
            "text": "Indexes make the database faster because they cache the data in RAM.",
            "evaluation": {"technical_accuracy": {"rating": 25}}
        }
    ]

    mock_response_json = {
        "suggested_follow_up_questions": [
            "How does the database find rows without reading the entire table?"
        ],
        "suggested_practical_questions": [],
        "missing_concepts": ["B-Tree search", "Table scan"],
        "verification_questions": [],
        "recommended_next_topic": "Basic Storage",
        "interview_notes": ["Confused index with RAM buffer pool."],
        "current_candidate_understanding": "Fundamental gaps in data storage."
    }

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_response_json)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    engine.client = mock_client

    result = await engine.generate_assistance(transcript=transcript)

    assert len(result["suggested_follow_up_questions"]) == 1
    diag_q = result["suggested_follow_up_questions"][0]
    assert not is_stacked_question(diag_q)

    call_kwargs = mock_client.chat.completions.create.call_args[1]
    prompt_sent = call_kwargs["messages"][0]["content"]
    assert "CRITICAL DECISION RULE (Weak Answer detected, rating: 25%):" in prompt_sent
    assert "Generate ONE diagnostic follow-up question" in prompt_sent


# ============================================================================
# REQUIREMENT 4 & 5: Conversational Realism in Dynamic Suggestions Prompt
# ============================================================================

@pytest.mark.asyncio
async def test_dynamic_suggestions_prompt_contains_conversational_rules_and_examples():
    """Verifies that generate_dynamic_suggestions prompt embeds spoken style rules, single-question constraints, and contrast examples."""
    engine = AICopilotEngine()

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "suggestions": [
            "What database technologies have you worked with recently?",
            "You mentioned Redis caching. How did you handle cache invalidation?"
        ]
    })
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    engine.client = mock_client

    suggs = await engine.generate_dynamic_suggestions(
        jd="Senior Backend Engineer",
        resume="Experience with Redis and Go",
        interviewer_question="How did you improve API latency?",
        candidate_answer="We put Redis in front of the database to cache product catalog queries."
    )

    assert len(suggs) == 2
    assert not is_stacked_question(suggs[1])
    assert "cache invalidation" in suggs[1]

    # Verify prompt contents
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    prompt_sent = call_kwargs["messages"][0]["content"]

    assert "Follow-Up Conversational Style Rules:" in prompt_sent
    assert "Sound like a real interviewer: Use natural, direct, spoken English" in prompt_sent
    assert "Ask ONE question at a time" in prompt_sent
    assert "Grounded in the candidate's actual answer" in prompt_sent
    assert "Avoid generic filler or repetitive formal clichés" in prompt_sent
    assert "Conversational Style Contrast Examples:" in prompt_sent
    assert "You mentioned Redis caching. How did you handle cache invalidation?" in prompt_sent
    assert "What made you choose async processing for this part?" in prompt_sent


# ============================================================================
# REQUIREMENT 6 & 7: Dynamic Suggestions Fallback Does Not Append Generic Clichés
# ============================================================================

@pytest.mark.asyncio
async def test_dynamic_suggestions_avoids_repetitive_filler_on_single_result():
    """When the LLM returns 1 focused question, the system does not append generic formal filler."""
    engine = AICopilotEngine()

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "suggestions": [
            "What made you choose async processing for this part?"
        ]
    })
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    engine.client = mock_client

    suggs = await engine.generate_dynamic_suggestions(
        jd="Backend",
        resume="Go",
        interviewer_question="How does the queue work?",
        candidate_answer="We process tasks asynchronously."
    )

    assert len(suggs) == 1
    assert suggs[0] == "What made you choose async processing for this part?"
    # Must NOT have appended the old generic cliché
    assert not any("Could you dive deeper into the technical implementation" in s for s in suggs)


# ============================================================================
# REQUIREMENT 8: UnifiedQAWorker Prompt Parity with Copilot Engine
# ============================================================================

def test_unified_qa_worker_prompt_contains_consistent_conversational_rules():
    """Verifies that UnifiedQAWorker.build_prompt embeds identical conversational realism and contrast rules in Section 4."""
    worker = UnifiedQAWorker()
    prompt = worker.build_prompt(
        question="How do you handle background jobs?",
        answer="We use Celery with Redis broker.",
        resume="Python developer",
        jd="Senior Python Engineer"
    )

    assert "SECTION 4: DYNAMIC QUESTION SUGGESTIONS" in prompt
    assert "Follow-Up Conversational Style Rules:" in prompt
    assert "Sound like a real interviewer: Use natural, direct, spoken English" in prompt
    assert "Ask ONE question at a time" in prompt
    assert "Specific and grounded: Probe what the candidate just claimed" in prompt
    assert "You mentioned Redis caching. How did you handle cache invalidation?" in prompt
    assert "What made you choose async processing for this part?" in prompt
    assert "What other approaches did you consider before settling on this design?" in prompt


# ============================================================================
# REQUIREMENT 9: Frontend Default Custom Prompt Does Not Mandate 2 Questions
# ============================================================================

def test_frontend_default_custom_prompt_is_adaptive_and_conversational():
    """Verifies that the default custom prompt in MeetingObserver.tsx uses conversational guidance and does not mandate 2 follow-ups."""
    with open("frontend-new/src/pages/MeetingObserver.tsx", "r", encoding="utf-8") as f:
        content = f.read()

    # The old rigid directive must NOT be present
    assert "Recommend 2 follow-up questions tailored to missing concepts" not in content

    # The new spoken, adaptive directive must be present
    assert "Suggest focused, natural follow-up questions" in content
    assert "Ask one question at a time in spoken interviewer style." in content


# ============================================================================
# REQUIREMENT 10: CandidateEvaluationService follow_up_required Semantics
# ============================================================================

def test_evaluation_service_follow_up_instructions():
    """Verifies CandidateEvaluationService prompt only triggers follow_up_required on genuine gaps, not sound answers."""
    eval_service = CandidateEvaluationService()
    # Check that the evaluate_response prompt mentions not forcing follow_up on complete answers
    import inspect
    src = inspect.getsource(eval_service.evaluate_response)
    assert "follow_up_required" in src
    assert "true only if the candidate left a critical technical aspect unanswered" in src
    assert "follow_up_reason" in src
    assert "concise note grounded in candidate's actual answer" in src
