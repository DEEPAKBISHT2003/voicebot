import asyncio
import json
import time
from typing import Optional, Dict, Any, Union
import uuid
from openai import AsyncOpenAI
from loguru import logger
from services.copilot.src.core.config import Settings
from services.copilot.src.services.usage_service import usage_service
from services.copilot.src.services.unified_qa_worker import (
    calculate_deterministic_accuracy_score,
    is_resume_match_applicable
)

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

class CandidateEvaluationService:
    """Evaluates candidate technical answers against target JDs and resumes using DeepSeek LLM."""
    def __init__(
        self, 
        api_key: str = Settings.DEEPSEEK_API_KEY, 
        model: str = Settings.DEEPSEEK_MODEL,
        base_url: str = Settings.DEEPSEEK_BASE_URL
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def evaluate_response(
        self, 
        candidate_response: str, 
        jd: str = "", 
        resume: str = "", 
        question: str = "",
        session_id: Optional[Union[str, uuid.UUID]] = None
    ) -> dict:
        """
        Submits candidate response to LLM for multi-dimension rating and comments.
        Returns a structured evaluation report.
        """
        if not candidate_response or not candidate_response.strip():
            return self._get_empty_evaluation("No response text provided.")

        prompt = f"""
You are an expert technical interviewer. Evaluate the candidate's last spoken response against the target job description, candidate resume, and the question asked (if available).

Context:
- Target Job Description: {jd}
- Candidate Resume: {resume}
- Question Asked: {question}

Candidate Response:
"{candidate_response}"

Evaluate the candidate's response and output a structured JSON object with the following fields:
1. "question_asker": "string (name of the role that asked the question, default is 'Interviewer')"
2. "answerer": "string (name of the role that answered, default is 'Candidate')"
3. "is_complete": boolean (true if the candidate's response fully answers the question, false otherwise)
4. "follow_up_required": boolean (true if the candidate missed crucial parts, exhibited gaps, or gave an incomplete answer requiring follow-up)
5. "follow_up_reason": "string (explanation of why follow-up is required and what to ask next, or empty if not required)"
6. "technical_accuracy": {{ "rating": integer 1-100, "comment": "string explanation" }}
7. "confidence": {{ "rating": integer 1-100, "comment": "string explanation" }}
8. "completeness": {{ "rating": integer 1-100, "comment": "string explanation" }}
9. "practical_knowledge": {{ "rating": integer 1-100, "comment": "string explanation" }}
10. "communication": {{ "rating": integer 1-100, "comment": "string explanation" }}
11. "production_experience": {{ "rating": integer 1-100, "comment": "string explanation" }}
12. "missing_concepts": [ "list", "of", "missing", "technical", "concepts" ]
13. "knowledge_gaps": [ "list", "of", "apparent", "knowledge", "gaps" ]

You must output ONLY valid JSON matching this schema. Do not output markdown code blocks or additional text.
"""
        try:
            t0 = time.monotonic()
            chat_completion = await self.client.chat.completions.create(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                model=self.model,
                response_format={"type": "json_object"}
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            if session_id and hasattr(chat_completion, "usage") and chat_completion.usage:
                try:
                    await usage_service.record_llm_usage(
                        session_id=session_id,
                        stage="qa_accuracy_legacy",
                        model=self.model,
                        usage=chat_completion.usage,
                        duration_ms=duration_ms,
                        call_identifier=None,
                    )
                except Exception as usage_err:
                    logger.warning(f"[EvaluationService] Failed to record usage telemetry for session {session_id}: {usage_err}")

            response_text = chat_completion.choices[0].message.content
            return clean_json_loads(response_text)
        except Exception as e:
            logger.error(f"Error during candidate response evaluation: {e}")
            return self._get_empty_evaluation(f"Evaluation failed: {e}")

    def _get_empty_evaluation(self, comment: str) -> dict:
        return {
            "question_asker": "Interviewer",
            "answerer": "Candidate",
            "is_complete": False,
            "follow_up_required": False,
            "follow_up_reason": "",
            "technical_accuracy": {"rating": 0, "comment": comment},
            "confidence": {"rating": 0, "comment": ""},
            "completeness": {"rating": 0, "comment": ""},
            "practical_knowledge": {"rating": 0, "comment": ""},
            "communication": {"rating": 0, "comment": ""},
            "production_experience": {"rating": 0, "comment": ""},
            "missing_concepts": [],
            "knowledge_gaps": []
        }

    async def evaluate_accuracy(
        self,
        question: str,
        answer: str,
        resume: str = "",
        session_id: Optional[Union[str, uuid.UUID]] = None,
        call_identifier: Optional[str] = None,
        stage: str = "qa_accuracy"
    ) -> Optional[dict]:
        """
        Phase 2V: Evaluates a confirmed candidate answer against the interviewer's question
        and the candidate's resume (NO JOB DESCRIPTION).

        Calculates deterministic weighted accuracy score:
            accuracy_score = round(
                question_relevance * 0.30
                + technical_correctness * 0.30
                + resume_match * 0.25
                + completeness * 0.15
            )
        Clamped to 0-100.
        Returns ONLY:
            {"accuracy_score": N}
        or None on failure / invalid input.
        """
        if not question or not question.strip() or not answer or not answer.strip():
            logger.warning("[Phase2V] evaluate_accuracy called with empty question or answer.")
            return None

        prompt = f"""You are an expert technical interview evaluator.

Evaluate the candidate's answer against the interviewer's question and the candidate's resume.

Do NOT use or assume any job description.

Evaluate these four dimensions:

1. Question Relevance — 0 to 100
   How directly does the candidate answer the interviewer's question?
   Do not penalize concise answers or alternative valid framing that addresses the prompt.

2. Technical/Factual Correctness — 0 to 100
   Are the technical concepts, facts, reasoning, terminology, and principles correct?
   - Proportional credit: Award credit in proportion to correctness; core concepts with minor omissions deserve proportional credit (75-90), not a failing score.
   - Technical equivalence: Accept valid alternative designs, equivalent terminology, and sound trade-offs.
   - Rigor: Materially incorrect, unsafe, or false technical claims must continue to be penalized (0-40). Plausible-sounding errors must not receive credit.

3. Resume/Experience Match — 0 to 100 or null
   Is the candidate's claimed experience consistent with the candidate's resume?
   - If the question or candidate's answer references personal experience, past projects, or claims from their resume, evaluate consistency with documented background (0-100). Penalize ONLY if claimed experience contradicts or materially exaggerates documented background.
   - For purely conceptual, technical, or algorithmic questions where no personal experience claims are made, output null (not applicable). Do NOT output 100 for neutrality, as Python deterministically excludes inapplicable dimensions and renormalizes remaining weights.

4. Completeness — 0 to 100
   Does the candidate sufficiently address the important aspects of the question?
   Do not judge completeness simply by answer length; concise answers that address the core question are complete.
   If transcript appears cut off or incomplete due to caption loss, evaluate fairly on available evidence without assuming incompetence. If completely unanswered, score 0.

Communication style: Do not penalize conversational fillers ("uh", "basically"), natural pauses, or accent.

Interviewer Question:
{question.strip()}

Candidate Answer:
{answer.strip()}

Candidate Resume:
{resume.strip() if resume and resume.strip() else "None provided"}

Return ONLY valid JSON:

{{
    "question_relevance": 0,
    "technical_correctness": 0,
    "resume_match": null,
    "completeness": 0
}}

No explanation.
No feedback.
No additional fields.
"""
        try:
            t0 = time.monotonic()
            chat_completion = await asyncio.wait_for(
                self.client.chat.completions.create(
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    model=self.model,
                    response_format={"type": "json_object"}
                ),
                timeout=12.0
            )
            duration_ms = int((time.monotonic() - t0) * 1000)

            if session_id and hasattr(chat_completion, "usage") and chat_completion.usage:
                try:
                    await usage_service.record_llm_usage(
                        session_id=session_id,
                        stage=stage,
                        model=self.model,
                        usage=chat_completion.usage,
                        duration_ms=duration_ms,
                        call_identifier=call_identifier,
                    )
                except Exception as usage_err:
                    logger.warning(f"[EvaluationService] Failed to record usage telemetry for session {session_id}: {usage_err}")

            response_text = chat_completion.choices[0].message.content
            parsed = clean_json_loads(response_text)

            # Verify required sub-score fields exist
            required_keys = ["question_relevance", "technical_correctness", "completeness"]
            if not all(k in parsed for k in required_keys):
                logger.error(f"[Phase2V] Missing required sub-scores in LLM response: {parsed}")
                return None

            try:
                relevance = max(0, min(100, int(parsed["question_relevance"])))
                technical = max(0, min(100, int(parsed["technical_correctness"])))
                completeness = max(0, min(100, int(parsed["completeness"])))
                raw_res = parsed.get("resume_match")
                if raw_res is not None and str(raw_res).strip().lower() not in ("null", "none", "n/a"):
                    resume_match = max(0, min(100, int(raw_res)))
                else:
                    resume_match = None
            except (ValueError, TypeError) as parse_err:
                logger.error(f"[Phase2V] Invalid non-integer sub-scores in LLM response: {parse_err}")
                return None

            is_applicable = is_resume_match_applicable(question=question, answer=answer)
            if resume_match is None:
                is_applicable = False

            # Deterministic weighted scoring via centralized calibrated engine
            accuracy_score = calculate_deterministic_accuracy_score(
                relevance=relevance,
                technical=technical,
                resume_match=resume_match,
                completeness=completeness,
                is_resume_applicable=is_applicable
            )

            logger.info(
                f"[Phase2V] Evaluated Q+A accuracy: {accuracy_score}% "
                f"(relevance={relevance}, technical={technical}, resume_match={resume_match}, completeness={completeness}, applicable={is_applicable})"
            )
            return {"accuracy_score": accuracy_score}

        except Exception as e:
            logger.error(f"[Phase2V] Error during evaluate_accuracy: {e}")
            return None
