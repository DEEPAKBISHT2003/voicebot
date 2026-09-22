import asyncio
import json
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from loguru import logger
from services.copilot.src.core.config import Settings

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
        question: str = ""
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
            chat_completion = await self.client.chat.completions.create(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                model=self.model,
                response_format={"type": "json_object"}
            )
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
        resume: str = ""
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

2. Technical/Factual Correctness — 0 to 100
   Are the technical concepts, facts, reasoning, terminology, and principles correct?

3. Resume/Experience Match — 0 to 100
   Is the candidate's claimed experience consistent with the candidate's resume?
   The answer does not need to literally appear in the resume.
   Evaluate whether the claimed experience is consistent with the documented background.

4. Completeness — 0 to 100
   Does the candidate sufficiently address the important aspects of the question?
   Do not judge completeness simply by answer length.

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
    "resume_match": 0,
    "completeness": 0
}}

No explanation.
No feedback.
No additional fields.
"""
        try:
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
            response_text = chat_completion.choices[0].message.content
            parsed = clean_json_loads(response_text)

            # Verify all 4 required sub-score fields exist
            required_keys = ["question_relevance", "technical_correctness", "resume_match", "completeness"]
            if not all(k in parsed for k in required_keys):
                logger.error(f"[Phase2V] Missing required sub-scores in LLM response: {parsed}")
                return None

            try:
                relevance = max(0, min(100, int(parsed["question_relevance"])))
                technical = max(0, min(100, int(parsed["technical_correctness"])))
                resume_match = max(0, min(100, int(parsed["resume_match"])))
                completeness = max(0, min(100, int(parsed["completeness"])))
            except (ValueError, TypeError) as parse_err:
                logger.error(f"[Phase2V] Invalid non-integer sub-scores in LLM response: {parse_err}")
                return None

            # Deterministic weighted scoring
            accuracy_score = round(
                relevance * 0.30
                + technical * 0.30
                + resume_match * 0.25
                + completeness * 0.15
            )
            accuracy_score = max(0, min(100, int(accuracy_score)))

            logger.info(
                f"[Phase2V] Evaluated Q+A accuracy: {accuracy_score}% "
                f"(relevance={relevance}, technical={technical}, resume_match={resume_match}, completeness={completeness})"
            )
            return {"accuracy_score": accuracy_score}

        except Exception as e:
            logger.error(f"[Phase2V] Error during evaluate_accuracy: {e}")
            return None
