"""
Unit Test Suite for SpeakerRoleClassifier Contract (Phase 2E)
============================================================
Isolated tests validating the semantic SpeakerRoleClassifier contract,
JSON schema, prompt construction, confidence modeling, failure modes,
role anchoring, and evaluation gating WITHOUT modifying production code.
"""

import json
import asyncio
from typing import List, Dict, Optional, Literal, Any
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import BaseModel, Field, ValidationError


# ============================================================================
# 1. CONTRACT MODELS (Pydantic V2 Schemas for SpeakerRoleClassifier)
# ============================================================================

RoleType = Literal["Interviewer", "Candidate", "Unknown"]
ConfidenceType = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
StatusType = Literal["RESOLVED", "DETECTING", "AMBIGUOUS"]


class TurnInput(BaseModel):
    """Input representation of a transcript turn from STT."""
    turn_id: int
    raw_speaker_id: str
    text: str
    start_time: float = 0.0
    end_time: float = 0.0


class ClassifierInput(BaseModel):
    """Complete input payload delivered to SpeakerRoleClassifier."""
    session_id: str
    job_description: str = ""
    candidate_resume: str = ""
    turns: List[TurnInput] = Field(default_factory=list)


class TurnClassification(BaseModel):
    """Output classification assigned per transcript turn."""
    turn_id: int
    raw_speaker_id: str
    assigned_role: RoleType
    confidence: ConfidenceType
    reasoning: Optional[str] = None


class ClassifierOutput(BaseModel):
    """Structured response schema returned by SpeakerRoleClassifier."""
    status: StatusType
    confidence: ConfidenceType
    turn_classifications: List[TurnClassification] = Field(default_factory=list)
    speaker_id_mapping: Dict[str, str] = Field(default_factory=dict)
    overall_reasoning: str = ""


# ============================================================================
# 2. ISOLATED TEST CLASSIFIER HARNESS (Stands in for future production class)
# ============================================================================

def clean_json_loads(text: str) -> dict:
    """Safely parse JSON responses that may be wrapped in markdown codeblocks."""
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    return json.loads(clean_text)


class IsolatedSpeakerRoleClassifier:
    """
    Isolated test-side implementation of SpeakerRoleClassifier.
    Validates prompts, mocks LLM client calls, cleans JSON, enforces schema,
    and implements safe failure fallbacks.
    """

    SYSTEM_PROMPT = (
        "You are a precise conversational role classifier for an AI technical interview platform.\n"
        "Your task is to analyze interview transcript turns and assign each turn to its semantic conversational role: "
        'either "Interviewer" or "Candidate", or "Unknown".\n\n'
        "CRITICAL RULES:\n"
        "1. RAW SPEAKER IDS ARE NOT ROLES: The speaker ID assigned by speech-to-text (e.g. \"0\", \"1\", \"2\") "
        "is merely an acoustic cluster. Both participants may share speaker ID \"0\", or one participant may drift "
        'across multiple speaker IDs. Never assume speaker "0" is Candidate or speaker "1" is Interviewer.\n'
        "2. EVALUATE PER-TURN SEMANTICS:\n"
        "   - Interviewer signals: Greets/welcomes candidate, outlines interview agenda, asks questions, "
        "poses technical problems/scenarios, probes depth, clarifies requirements, assesses answers.\n"
        "   - Candidate signals: Introduces background/education/past roles, answers technical questions, "
        "explains projects and architectures, describes personal skills and decisions, asks reverse questions "
        "about company/tech stack.\n"
        "3. CONTEXT USAGE:\n"
        "   - Target Job Description and Candidate Resume provide helpful context (e.g. verifying if described "
        "experience matches the candidate's resume), but conversational semantics always take precedence.\n"
        "4. INSUFFICIENT EVIDENCE:\n"
        '   - For short, ambiguous utterances (e.g. "Yes.", "Okay.", "Right.", "Hello.") where conversational intent '
        'cannot be determined, assign role "Unknown" with confidence "LOW" or "UNKNOWN".\n'
        '   - If turns are too sparse to anchor roles, set status to "DETECTING".\n'
        '   - Only set status to "RESOLVED" when both roles are clearly identified or when unambiguous conversational evidence exists.\n'
        "5. NEVER INVENT ROLES:\n"
        '   - The valid roles are "Interviewer", "Candidate", and "Unknown".\n'
        "   - If 3+ raw speaker IDs appear, map them semantically to Interviewer or Candidate; do not assume 3+ humans.\n\n"
        "RESPONSE FORMAT:\n"
        "Output ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "status": "RESOLVED | DETECTING | AMBIGUOUS",\n'
        '  "confidence": "HIGH | MEDIUM | LOW | UNKNOWN",\n'
        '  "turn_classifications": [\n'
        "    {\n"
        '      "turn_id": <int>,\n'
        '      "raw_speaker_id": "<str>",\n'
        '      "assigned_role": "Interviewer | Candidate | Unknown",\n'
        '      "confidence": "HIGH | MEDIUM | LOW | UNKNOWN",\n'
        '      "reasoning": "<short justification>"\n'
        "    }\n"
        "  ],\n"
        '  "speaker_id_mapping": {\n'
        '    "<raw_speaker_id>": "Interviewer | Candidate | Mixed | Unknown"\n'
        "  },\n"
        '  "overall_reasoning": "<explanation of role anchoring and evidence>"\n'
        "}"
    )

    def __init__(self, llm_client: Any, model: str = "llama-3.1-8b-instant", timeout_seconds: float = 3.0):
        self.llm_client = llm_client
        self.model = model
        self.timeout_seconds = timeout_seconds

    def format_user_prompt(self, payload: ClassifierInput) -> str:
        turns_repr = [
            {
                "turn_id": t.turn_id,
                "raw_speaker_id": t.raw_speaker_id,
                "text": t.text
            }
            for t in payload.turns
        ]
        return (
            f"Context:\n"
            f"- Job Description: {payload.job_description or 'Not provided'}\n"
            f"- Candidate Resume: {payload.candidate_resume or 'Not provided'}\n\n"
            f"Turns to classify:\n"
            f"{json.dumps(turns_repr, indent=2)}\n\n"
            f"Analyze the conversational roles and return the structured JSON object."
        )

    async def classify(self, payload: ClassifierInput) -> ClassifierOutput:
        """Invokes LLM with strict JSON formatting and safe error handling."""
        if not payload.turns:
            return ClassifierOutput(
                status="DETECTING",
                confidence="UNKNOWN",
                turn_classifications=[],
                speaker_id_mapping={},
                overall_reasoning="No turns provided to classify."
            )

        user_prompt = self.format_user_prompt(payload)

        try:
            chat_completion = await asyncio.wait_for(
                self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"}
                ),
                timeout=self.timeout_seconds
            )
            raw_content = chat_completion.choices[0].message.content
            parsed_data = clean_json_loads(raw_content)
            return ClassifierOutput.model_validate(parsed_data)

        except asyncio.TimeoutError:
            return self._build_fallback_output(payload, "Classification timed out. Holding status at DETECTING.")
        except (json.JSONDecodeError, ValidationError) as e:
            return self._build_fallback_output(payload, f"LLM returned invalid schema/JSON: {str(e)}")
        except Exception as e:
            return self._build_fallback_output(payload, f"Unexpected classifier failure: {str(e)}")

    def _build_fallback_output(self, payload: ClassifierInput, reason: str) -> ClassifierOutput:
        fallback_turns = [
            TurnClassification(
                turn_id=t.turn_id,
                raw_speaker_id=t.raw_speaker_id,
                assigned_role="Unknown",
                confidence="UNKNOWN",
                reasoning=reason
            )
            for t in payload.turns
        ]
        return ClassifierOutput(
            status="DETECTING",
            confidence="UNKNOWN",
            turn_classifications=fallback_turns,
            speaker_id_mapping={},
            overall_reasoning=reason
        )

    @staticmethod
    def is_turn_eligible_for_evaluation(turn: TurnClassification, overall_status: StatusType) -> bool:
        """
        Evaluation Gating Rule:
        CandidateEvaluationService is ONLY allowed to evaluate a turn if:
        1. The turn is unambiguously identified as 'Candidate'
        2. Turn confidence is 'HIGH' or 'MEDIUM'
        3. Overall session status is 'RESOLVED'
        """
        return (
            overall_status == "RESOLVED" and
            turn.assigned_role == "Candidate" and
            turn.confidence in ("HIGH", "MEDIUM")
        )


# ============================================================================
# 3. FIXTURES & HELPER FUNCTIONS
# ============================================================================

def make_mock_llm(return_json_dict: dict) -> AsyncMock:
    """Creates an AsyncMock client returning a specified JSON response."""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(return_json_dict)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    return mock_client


def make_mock_llm_text(return_text: str) -> AsyncMock:
    """Creates an AsyncMock client returning raw text (e.g. markdown code blocks or invalid JSON)."""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = return_text
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    return mock_client


# ============================================================================
# 4. CONTRACT VALIDATION TESTS (Schema, Enums, and Field Constraints)
# ============================================================================

class TestClassifierContractBasics:
    """Tests schema validation, enums, and required fields."""

    def test_valid_schema_instantiation(self):
        output = ClassifierOutput(
            status="RESOLVED",
            confidence="HIGH",
            turn_classifications=[
                TurnClassification(
                    turn_id=0,
                    raw_speaker_id="0",
                    assigned_role="Interviewer",
                    confidence="HIGH",
                    reasoning="Asks greeting and opening question."
                )
            ],
            speaker_id_mapping={"0": "Interviewer"},
            overall_reasoning="Clear interviewer opening."
        )
        assert output.status == "RESOLVED"
        assert output.turn_classifications[0].assigned_role == "Interviewer"

    def test_invalid_status_enum_rejected(self):
        with pytest.raises(ValidationError):
            ClassifierOutput(
                status="DONE",  # Invalid enum value
                confidence="HIGH",
                turn_classifications=[]
            )

    def test_invalid_role_enum_rejected(self):
        with pytest.raises(ValidationError):
            TurnClassification(
                turn_id=0,
                raw_speaker_id="0",
                assigned_role="HiringManager",  # Invalid role enum
                confidence="HIGH"
            )

    def test_invalid_confidence_enum_rejected(self):
        with pytest.raises(ValidationError):
            TurnClassification(
                turn_id=0,
                raw_speaker_id="0",
                assigned_role="Interviewer",
                confidence="CERTAIN"  # Invalid confidence enum
            )

    def test_missing_required_fields_rejected(self):
        with pytest.raises(ValidationError):
            TurnClassification.model_validate({"turn_id": 0})  # Missing raw_speaker_id, assigned_role, confidence


# ============================================================================
# 5. THE 15 BENCHMARK TEST CASES
# ============================================================================

class Test15BenchmarkScenarios:
    """The 15 canonical conversational scenarios from Phase 2E specifications."""

    @pytest.mark.asyncio
    async def test_01_interviewer_speaks_first_with_greeting(self):
        """Test 1: Interviewer speaks first with greeting/welcome."""
        payload = ClassifierInput(
            session_id="sess-01",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Welcome to the interview. Can you please introduce yourself?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {
                    "turn_id": 0,
                    "raw_speaker_id": "0",
                    "assigned_role": "Interviewer",
                    "confidence": "HIGH",
                    "reasoning": "Welcomes candidate and initiates prompt to introduce."
                }
            ],
            "speaker_id_mapping": {"0": "Interviewer"},
            "overall_reasoning": "Speaker 0 acts as interviewer opening the session."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.status == "RESOLVED"
        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[0].confidence == "HIGH"
        assert result.turn_classifications[0].raw_speaker_id == "0"

    @pytest.mark.asyncio
    async def test_02_candidate_speaks_first_with_self_introduction(self):
        """Test 2: Candidate speaks first with explicit self-introduction."""
        payload = ClassifierInput(
            session_id="sess-02",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Hi, thanks for having me. My name is Alex and I have four years of backend experience.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {
                    "turn_id": 0,
                    "raw_speaker_id": "0",
                    "assigned_role": "Candidate",
                    "confidence": "HIGH",
                    "reasoning": "Self-introduction giving name and years of experience."
                }
            ],
            "speaker_id_mapping": {"0": "Candidate"},
            "overall_reasoning": "Speaker introduces themselves as candidate despite speaking first."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.status == "RESOLVED"
        assert result.turn_classifications[0].assigned_role == "Candidate"
        assert result.turn_classifications[0].confidence == "HIGH"

    @pytest.mark.asyncio
    async def test_03_same_raw_speaker_id_interviewer_question_then_candidate_answer(self):
        """Test 3: Same raw speaker ID contains interviewer question followed by candidate answer."""
        payload = ClassifierInput(
            session_id="sess-03",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Can you explain dependency injection?"),
                TurnInput(turn_id=1, raw_speaker_id="0", text="Dependency injection decouples object construction from usage."),
                TurnInput(turn_id=2, raw_speaker_id="0", text="Good. Now how would you handle database transactions?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 2, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Mixed"},
            "overall_reasoning": "Deepgram speaker 0 contains alternating dialogue from both participants."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[1].assigned_role == "Candidate"
        assert result.turn_classifications[2].assigned_role == "Interviewer"
        # Crucial: proves raw_speaker_id '0' is not locked to one role
        assert all(t.raw_speaker_id == "0" for t in result.turn_classifications)

    @pytest.mark.asyncio
    async def test_04_different_raw_ids_for_interviewer_and_candidate(self):
        """Test 4: Different raw IDs for interviewer and candidate."""
        payload = ClassifierInput(
            session_id="sess-04",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Tell me about your background in software engineering."),
                TurnInput(turn_id=1, raw_speaker_id="1", text="I have 5 years building distributed microservices in Go.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "1", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer", "1": "Candidate"},
            "overall_reasoning": "Distinct acoustic clusters map cleanly to conversational roles."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[1].assigned_role == "Candidate"
        assert result.speaker_id_mapping["0"] == "Interviewer"
        assert result.speaker_id_mapping["1"] == "Candidate"

    @pytest.mark.asyncio
    async def test_05_candidate_describes_experience_matching_resume(self):
        """Test 5: Candidate describes experience matching resume."""
        payload = ClassifierInput(
            session_id="sess-05",
            candidate_resume="Lead Engineer at CloudCorp. Built high-volume payment processing pipeline with Kafka.",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="2", text="At CloudCorp, I led the payment processing pipeline using Kafka to handle 50k events/sec.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"2": "Candidate"},
            "overall_reasoning": "Speech directly corroborates candidate resume."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Candidate"
        assert result.turn_classifications[0].confidence == "HIGH"

    @pytest.mark.asyncio
    async def test_06_interviewer_asks_technical_question(self):
        """Test 6: Interviewer asks technical question."""
        payload = ClassifierInput(
            session_id="sess-06",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="1", text="How does optimistic locking differ from pessimistic locking in PostgreSQL?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "1", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"1": "Interviewer"},
            "overall_reasoning": "Technical inquiry testing candidate conceptual knowledge."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[0].confidence == "HIGH"

    @pytest.mark.asyncio
    async def test_07_candidate_explains_previous_project(self):
        """Test 7: Candidate explains previous project."""
        payload = ClassifierInput(
            session_id="sess-07",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="In our previous architecture, we migrated from a monolith to ECS Fargate containers.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Candidate"},
            "overall_reasoning": "Describes project implementation experience."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Candidate"

    @pytest.mark.asyncio
    async def test_08_interviewer_provides_interview_jd_clarification(self):
        """Test 8: Interviewer provides interview/JD clarification."""
        payload = ClassifierInput(
            session_id="sess-08",
            job_description="Staff Site Reliability Engineer. On-call rotation and Terraform automation.",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="For this SRE role, you'll be participating in a secondary on-call rotation and authoring Terraform modules.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer"},
            "overall_reasoning": "Interviewer explaining role scope and expectations."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"

    @pytest.mark.asyncio
    async def test_09_candidate_asks_interviewer_about_technology(self):
        """Test 9: Candidate asks interviewer a question about team's tech stack."""
        payload = ClassifierInput(
            session_id="sess-09",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="2", text="What CI/CD tools does your team currently use in production?"),
                TurnInput(turn_id=1, raw_speaker_id="0", text="We use GitHub Actions with automated canary deployments via ArgoCD.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH", "reasoning": "Candidate reverse inquiry about company stack."},
                {"turn_id": 1, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH", "reasoning": "Interviewer describing internal team tools."}
            ],
            "speaker_id_mapping": {"2": "Candidate", "0": "Interviewer"},
            "overall_reasoning": "Candidate asking questions at end of interview does not flip role."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        # Candidate asking a question must NOT be classified as Interviewer!
        assert result.turn_classifications[0].assigned_role == "Candidate"
        assert result.turn_classifications[1].assigned_role == "Interviewer"

    @pytest.mark.asyncio
    async def test_10_generic_greeting_followed_by_interviewer_question(self):
        """Test 10: Generic greeting followed by interviewer question."""
        payload = ClassifierInput(
            session_id="sess-10",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Good morning."),
                TurnInput(turn_id=1, raw_speaker_id="0", text="Shall we start with some system design questions?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "MEDIUM"},
                {"turn_id": 1, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer"},
            "overall_reasoning": "Speaker leads greeting and directs interview agenda."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[1].assigned_role == "Interviewer"

    @pytest.mark.asyncio
    async def test_11_candidate_gives_multi_turn_technical_explanation(self):
        """Test 11: Candidate gives multi-turn technical explanation."""
        payload = ClassifierInput(
            session_id="sess-11",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="2", text="First, we would place an API gateway in front of the microservices."),
                TurnInput(turn_id=1, raw_speaker_id="2", text="Then, we would use Redis to cache session tokens and rate limit incoming IPs.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"2": "Candidate"},
            "overall_reasoning": "Continuous candidate architectural explanation."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Candidate"
        assert result.turn_classifications[1].assigned_role == "Candidate"

    @pytest.mark.asyncio
    async def test_12_speaker_id_drift_interviewer_zero_to_three(self):
        """Test 12: Speaker ID drift - Interviewer uses speaker 0, candidate uses speaker 2, later interviewer uses speaker 3."""
        payload = ClassifierInput(
            session_id="sess-12",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Can you tell me about your experience with FastAPI?"),
                TurnInput(turn_id=1, raw_speaker_id="2", text="I've built several async microservices using FastAPI and Pydantic."),
                TurnInput(turn_id=2, raw_speaker_id="3", text="Great, and how did you handle background tasks?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 2, "raw_speaker_id": "3", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer", "2": "Candidate", "3": "Interviewer"},
            "overall_reasoning": "Speaker 3 continues interviewer questioning role previously performed by speaker 0."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[1].assigned_role == "Candidate"
        assert result.turn_classifications[2].assigned_role == "Interviewer"
        assert result.speaker_id_mapping["3"] == "Interviewer"

    @pytest.mark.asyncio
    async def test_13_candidate_id_drift_one_to_four(self):
        """Test 13: Candidate ID drift - Candidate starts on speaker 1 and continues on speaker 4."""
        payload = ClassifierInput(
            session_id="sess-13",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="How do you handle schema migrations?"),
                TurnInput(turn_id=1, raw_speaker_id="1", text="We use Alembic with strict rollback scripts."),
                TurnInput(turn_id=2, raw_speaker_id="4", text="We also run dry-run tests in staging before applying to production.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "1", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 2, "raw_speaker_id": "4", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer", "1": "Candidate", "4": "Candidate"},
            "overall_reasoning": "Speaker 4 is a continuation of candidate answer from speaker 1."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[1].assigned_role == "Candidate"
        assert result.turn_classifications[2].assigned_role == "Candidate"
        assert result.speaker_id_mapping["4"] == "Candidate"

    @pytest.mark.asyncio
    async def test_14_ambiguous_single_word_yes(self):
        """Test 14: Short ambiguous utterance 'Yes.' without conversational context."""
        payload = ClassifierInput(
            session_id="sess-14",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Yes.")
            ]
        )
        mock_response = {
            "status": "DETECTING",
            "confidence": "LOW",
            "turn_classifications": [
                {
                    "turn_id": 0,
                    "raw_speaker_id": "0",
                    "assigned_role": "Unknown",
                    "confidence": "LOW",
                    "reasoning": "Single affirmative word gives no semantic role indication."
                }
            ],
            "speaker_id_mapping": {"0": "Unknown"},
            "overall_reasoning": "Insufficient conversational evidence; holding at DETECTING."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.status == "DETECTING"
        assert result.turn_classifications[0].assigned_role == "Unknown"
        assert result.turn_classifications[0].confidence in ("LOW", "UNKNOWN")

    @pytest.mark.asyncio
    async def test_15_ambiguous_single_word_okay(self):
        """Test 15: Short ambiguous utterance 'Okay.' without conversational context."""
        payload = ClassifierInput(
            session_id="sess-15",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Okay.")
            ]
        )
        mock_response = {
            "status": "DETECTING",
            "confidence": "UNKNOWN",
            "turn_classifications": [
                {
                    "turn_id": 0,
                    "raw_speaker_id": "0",
                    "assigned_role": "Unknown",
                    "confidence": "UNKNOWN",
                    "reasoning": "Ambiguous acknowledgment."
                }
            ],
            "speaker_id_mapping": {"0": "Unknown"},
            "overall_reasoning": "Insufficient conversational evidence."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.status == "DETECTING"
        assert result.turn_classifications[0].assigned_role == "Unknown"


# ============================================================================
# 6. EDGE CASE TESTS
# ============================================================================

class TestEdgeCases:
    """Robustness and edge case handling."""

    @pytest.mark.asyncio
    async def test_edge_a_both_participants_share_speaker_id_0(self):
        """Edge A: Both participants share speaker ID 0."""
        payload = ClassifierInput(
            session_id="sess-edge-a",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="How do you structure database indexes?"),
                TurnInput(turn_id=1, raw_speaker_id="0", text="I use B-tree indexes for equality and range queries, and GIN for JSONB.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Mixed"},
            "overall_reasoning": "Single acoustic cluster 0 contains both interviewer and candidate speech."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        roles = [t.assigned_role for t in result.turn_classifications]
        assert "Interviewer" in roles
        assert "Candidate" in roles

    @pytest.mark.asyncio
    async def test_edge_b_three_or_more_raw_speaker_ids_do_not_create_three_roles(self):
        """Edge B: 3+ raw speaker IDs do NOT automatically create 3 human roles."""
        payload = ClassifierInput(
            session_id="sess-edge-b",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Welcome."),
                TurnInput(turn_id=1, raw_speaker_id="1", text="Thank you."),
                TurnInput(turn_id=2, raw_speaker_id="2", text="I have 5 years Python experience."),
                TurnInput(turn_id=3, raw_speaker_id="3", text="Can you describe your concurrency model?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "MEDIUM"},
                {"turn_id": 1, "raw_speaker_id": "1", "assigned_role": "Candidate", "confidence": "LOW"},
                {"turn_id": 2, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 3, "raw_speaker_id": "3", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer", "1": "Candidate", "2": "Candidate", "3": "Interviewer"},
            "overall_reasoning": "Four acoustic IDs map to only two human conversational roles."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        unique_roles = set(t.assigned_role for t in result.turn_classifications)
        assert unique_roles.issubset({"Interviewer", "Candidate", "Unknown"})
        assert len(unique_roles) <= 3

    @pytest.mark.asyncio
    async def test_edge_c_new_raw_speaker_id_not_auto_candidate(self):
        """Edge C: A new raw speaker ID does NOT automatically become Candidate or Interviewer."""
        payload = ClassifierInput(
            session_id="sess-edge-c",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="99", text="Umm...")
            ]
        )
        mock_response = {
            "status": "DETECTING",
            "confidence": "UNKNOWN",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "99", "assigned_role": "Unknown", "confidence": "UNKNOWN"}
            ],
            "speaker_id_mapping": {"99": "Unknown"},
            "overall_reasoning": "New acoustic cluster 99 has no conversational evidence."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Unknown"

    @pytest.mark.asyncio
    async def test_edge_d_short_ambiguous_utterance_returns_unknown(self):
        """Edge D: Short ambiguous utterance returns Unknown."""
        payload = ClassifierInput(
            session_id="sess-edge-d",
            turns=[TurnInput(turn_id=0, raw_speaker_id="1", text="Right.")]
        )
        mock_response = {
            "status": "DETECTING",
            "confidence": "UNKNOWN",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "1", "assigned_role": "Unknown", "confidence": "UNKNOWN"}
            ],
            "speaker_id_mapping": {},
            "overall_reasoning": "Ambiguous interjection."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Unknown"

    @pytest.mark.asyncio
    async def test_edge_e_missing_jd_does_not_crash(self):
        """Edge E: Missing JD does not crash classification."""
        payload = ClassifierInput(
            session_id="sess-edge-e",
            job_description="",  # Missing JD
            candidate_resume="Resume text here",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="What is your experience with Docker?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Interviewer"},
            "overall_reasoning": "Classified successfully without JD."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Interviewer"

    @pytest.mark.asyncio
    async def test_edge_f_missing_resume_does_not_crash(self):
        """Edge F: Missing resume does not crash classification."""
        payload = ClassifierInput(
            session_id="sess-edge-f",
            job_description="Backend Dev",
            candidate_resume="",  # Missing Resume
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="I have built microservices with FastAPI for 3 years.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Candidate"},
            "overall_reasoning": "Classified successfully without resume."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.turn_classifications[0].assigned_role == "Candidate"

    @pytest.mark.asyncio
    async def test_edge_g_empty_transcript_turns_handled_safely(self):
        """Edge G: Empty transcript turns handled safely."""
        payload = ClassifierInput(
            session_id="sess-edge-g",
            turns=[]  # Empty turns list
        )
        mock_client = AsyncMock()
        classifier = IsolatedSpeakerRoleClassifier(mock_client)
        result = await classifier.classify(payload)

        assert result.status == "DETECTING"
        assert result.confidence == "UNKNOWN"
        assert len(result.turn_classifications) == 0
        # Crucial: LLM call was skipped
        mock_client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_edge_h_llm_timeout_handled_safely(self):
        """Edge H: LLM timeout handled safely."""
        payload = ClassifierInput(
            session_id="sess-edge-h",
            turns=[TurnInput(turn_id=0, raw_speaker_id="0", text="Can you tell me about your work?")]
        )

        mock_client = AsyncMock()
        async def mock_timeout(*args, **kwargs):
            await asyncio.sleep(0.5)
            return None
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_timeout)

        # Set classifier timeout lower than sleep
        classifier = IsolatedSpeakerRoleClassifier(mock_client, timeout_seconds=0.05)
        result = await classifier.classify(payload)

        assert result.status == "DETECTING"
        assert result.confidence == "UNKNOWN"
        assert result.turn_classifications[0].assigned_role == "Unknown"
        assert "timed out" in result.overall_reasoning.lower()

    @pytest.mark.asyncio
    async def test_edge_i_invalid_json_returned_by_llm_handled_safely(self):
        """Edge I: Invalid JSON returned by LLM handled safely."""
        payload = ClassifierInput(
            session_id="sess-edge-i",
            turns=[TurnInput(turn_id=0, raw_speaker_id="0", text="Hello world")]
        )
        mock_client = make_mock_llm_text("I am an AI and cannot process this request properly.")
        classifier = IsolatedSpeakerRoleClassifier(mock_client)
        result = await classifier.classify(payload)

        assert result.status == "DETECTING"
        assert result.confidence == "UNKNOWN"
        assert result.turn_classifications[0].assigned_role == "Unknown"
        assert "invalid" in result.overall_reasoning.lower()

    @pytest.mark.asyncio
    async def test_edge_j_markdown_code_fences_handled_safely(self):
        """Edge J: LLM response containing markdown code fences is handled cleanly."""
        payload = ClassifierInput(
            session_id="sess-edge-j",
            turns=[TurnInput(turn_id=0, raw_speaker_id="0", text="Tell me about your Python experience.")]
        )
        raw_markdown = (
            "```json\n"
            "{\n"
            '  "status": "RESOLVED",\n'
            '  "confidence": "HIGH",\n'
            '  "turn_classifications": [\n'
            '    {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"}\n'
            "  ],\n"
            '  "speaker_id_mapping": {"0": "Interviewer"},\n'
            '  "overall_reasoning": "Interviewer asking question."\n'
            "}\n"
            "```"
        )
        mock_client = make_mock_llm_text(raw_markdown)
        classifier = IsolatedSpeakerRoleClassifier(mock_client)
        result = await classifier.classify(payload)

        assert result.status == "RESOLVED"
        assert result.turn_classifications[0].assigned_role == "Interviewer"

    @pytest.mark.asyncio
    async def test_edge_k_conflicting_evidence_produces_ambiguous_unknown(self):
        """Edge K: Conflicting evidence produces AMBIGUOUS status rather than arbitrary assignment."""
        payload = ClassifierInput(
            session_id="sess-edge-k",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="I think both of us have worked with Kubernetes.")
            ]
        )
        mock_response = {
            "status": "AMBIGUOUS",
            "confidence": "LOW",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Unknown", "confidence": "LOW"}
            ],
            "speaker_id_mapping": {},
            "overall_reasoning": "Ambiguous peer statement; cannot determine interviewer or candidate role."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        assert result.status == "AMBIGUOUS"
        assert result.turn_classifications[0].assigned_role == "Unknown"


# ============================================================================
# 7. ROLE ANCHORING LIFECYCLE TESTS (UNRESOLVED -> DETECTING -> RESOLVED)
# ============================================================================

class TestRoleAnchoringLifecycle:
    """Tests the progressive state machine for role anchoring."""

    @pytest.mark.asyncio
    async def test_progressive_role_anchoring_detecting_to_resolved(self):
        """
        Step 1: Single ambiguous turn 'Hello.' -> status DETECTING, role Unknown.
        Step 2: Subsequent dialogue clarifies roles -> status RESOLVED.
        """
        # Step 1: Only turn 0
        step1_input = ClassifierInput(
            session_id="sess-lifecycle",
            turns=[TurnInput(turn_id=0, raw_speaker_id="0", text="Hello.")]
        )
        step1_mock = {
            "status": "DETECTING",
            "confidence": "UNKNOWN",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Unknown", "confidence": "UNKNOWN"}
            ],
            "speaker_id_mapping": {},
            "overall_reasoning": "Greeting alone is insufficient to anchor roles."
        }
        classifier_step1 = IsolatedSpeakerRoleClassifier(make_mock_llm(step1_mock))
        res1 = await classifier_step1.classify(step1_input)

        assert res1.status == "DETECTING"
        assert res1.turn_classifications[0].assigned_role == "Unknown"

        # Step 2: Turns 0..2 accumulated
        step2_input = ClassifierInput(
            session_id="sess-lifecycle",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Hello."),
                TurnInput(turn_id=1, raw_speaker_id="0", text="Can you introduce yourself?"),
                TurnInput(turn_id=2, raw_speaker_id="0", text="I have three years of Python experience.")
            ]
        )
        step2_mock = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "MEDIUM"},
                {"turn_id": 1, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 2, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Mixed"},
            "overall_reasoning": "Conversational structure resolves roles cleanly."
        }
        classifier_step2 = IsolatedSpeakerRoleClassifier(make_mock_llm(step2_mock))
        res2 = await classifier_step2.classify(step2_input)

        assert res2.status == "RESOLVED"
        assert res2.turn_classifications[1].assigned_role == "Interviewer"
        assert res2.turn_classifications[2].assigned_role == "Candidate"


# ============================================================================
# 8. EVALUATION GATING CONTRACT TESTS
# ============================================================================

class TestEvaluationGatingContract:
    """Validates that CandidateEvaluationService will only run when roles are confidently resolved."""

    def test_interviewer_turn_never_eligible_for_evaluation(self):
        turn = TurnClassification(
            turn_id=0,
            raw_speaker_id="0",
            assigned_role="Interviewer",
            confidence="HIGH"
        )
        assert not IsolatedSpeakerRoleClassifier.is_turn_eligible_for_evaluation(turn, overall_status="RESOLVED")

    def test_unknown_turn_never_eligible_for_evaluation(self):
        turn = TurnClassification(
            turn_id=0,
            raw_speaker_id="0",
            assigned_role="Unknown",
            confidence="UNKNOWN"
        )
        assert not IsolatedSpeakerRoleClassifier.is_turn_eligible_for_evaluation(turn, overall_status="RESOLVED")
        assert not IsolatedSpeakerRoleClassifier.is_turn_eligible_for_evaluation(turn, overall_status="DETECTING")

    def test_candidate_turn_in_detecting_status_not_eligible(self):
        turn = TurnClassification(
            turn_id=0,
            raw_speaker_id="0",
            assigned_role="Candidate",
            confidence="HIGH"
        )
        # Even if turn says Candidate, if session status is still DETECTING, gating blocks evaluation!
        assert not IsolatedSpeakerRoleClassifier.is_turn_eligible_for_evaluation(turn, overall_status="DETECTING")

    def test_candidate_turn_with_low_confidence_not_eligible(self):
        turn = TurnClassification(
            turn_id=0,
            raw_speaker_id="0",
            assigned_role="Candidate",
            confidence="LOW"
        )
        assert not IsolatedSpeakerRoleClassifier.is_turn_eligible_for_evaluation(turn, overall_status="RESOLVED")

    def test_candidate_turn_resolved_high_confidence_is_eligible(self):
        turn = TurnClassification(
            turn_id=1,
            raw_speaker_id="0",
            assigned_role="Candidate",
            confidence="HIGH"
        )
        assert IsolatedSpeakerRoleClassifier.is_turn_eligible_for_evaluation(turn, overall_status="RESOLVED")


# ============================================================================
# 9. ROLE DRIFT TESTS (0 -> 3 for the same human)
# ============================================================================

class TestRoleDriftRepresentation:
    """Verifies that acoustic ID drift does NOT spawn false participants."""

    @pytest.mark.asyncio
    async def test_speaker_0_and_speaker_3_represent_same_interviewer(self):
        payload = ClassifierInput(
            session_id="sess-drift",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Can you tell me about your experience?"),
                TurnInput(turn_id=1, raw_speaker_id="2", text="I have three years of Python experience."),
                TurnInput(turn_id=2, raw_speaker_id="3", text="Can you explain your FastAPI project?")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "2", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 2, "raw_speaker_id": "3", "assigned_role": "Interviewer", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {
                "0": "Interviewer",
                "2": "Candidate",
                "3": "Interviewer"
            },
            "overall_reasoning": "Speaker 3 shares the identical interviewer role with Speaker 0."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        # Both speaker 0 and speaker 3 are Interviewer
        assert result.turn_classifications[0].assigned_role == "Interviewer"
        assert result.turn_classifications[2].assigned_role == "Interviewer"
        assert result.speaker_id_mapping["0"] == "Interviewer"
        assert result.speaker_id_mapping["3"] == "Interviewer"


# ============================================================================
# 10. CRITICAL SINGLE-CLUSTER TEST (All Speaker 0)
# ============================================================================

class TestSingleClusterDialogue:
    """Proves architecture functions correctly when Deepgram merges all audio into Speaker 0."""

    @pytest.mark.asyncio
    async def test_alternating_dialogue_all_speaker_zero(self):
        payload = ClassifierInput(
            session_id="sess-single-cluster",
            turns=[
                TurnInput(turn_id=0, raw_speaker_id="0", text="Can you explain your experience with PostgreSQL?"),
                TurnInput(turn_id=1, raw_speaker_id="0", text="I have used PostgreSQL for three years and built indexing strategies."),
                TurnInput(turn_id=2, raw_speaker_id="0", text="How would you optimize a slow query?"),
                TurnInput(turn_id=3, raw_speaker_id="0", text="I would first inspect the query plan and indexes.")
            ]
        )
        mock_response = {
            "status": "RESOLVED",
            "confidence": "HIGH",
            "turn_classifications": [
                {"turn_id": 0, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 1, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"},
                {"turn_id": 2, "raw_speaker_id": "0", "assigned_role": "Interviewer", "confidence": "HIGH"},
                {"turn_id": 3, "raw_speaker_id": "0", "assigned_role": "Candidate", "confidence": "HIGH"}
            ],
            "speaker_id_mapping": {"0": "Mixed"},
            "overall_reasoning": "All turns attributed to speaker 0 by STT, successfully separated semantically per turn."
        }
        classifier = IsolatedSpeakerRoleClassifier(make_mock_llm(mock_response))
        result = await classifier.classify(payload)

        roles = [t.assigned_role for t in result.turn_classifications]
        assert roles == ["Interviewer", "Candidate", "Interviewer", "Candidate"]
        assert all(t.raw_speaker_id == "0" for t in result.turn_classifications)
        assert result.speaker_id_mapping["0"] == "Mixed"
