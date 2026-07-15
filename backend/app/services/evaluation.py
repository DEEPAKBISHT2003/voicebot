import json
from groq import AsyncGroq
from loguru import logger
from backend.app.core.config import Settings

class CandidateEvaluationService:
    """Evaluates candidate technical answers against target JDs and resumes using Groq LLM."""
    def __init__(self, api_key: str = Settings.GROQ_API_KEY, model: str = Settings.GROQ_MODEL):
        self.client = AsyncGroq(api_key=api_key)
        self.model = model

    async def evaluate_response(
        self, 
        candidate_response: str, 
        jd: str = "", 
        resume: str = "", 
        question: str = ""
    ) -> dict:
        """
        Submits candidate response to Groq LLM for multi-dimension rating and comments.
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
1. "technical_accuracy": {{ "rating": integer 1-100, "comment": "string explanation" }}
2. "confidence": {{ "rating": integer 1-100, "comment": "string explanation" }}
3. "completeness": {{ "rating": integer 1-100, "comment": "string explanation" }}
4. "practical_knowledge": {{ "rating": integer 1-100, "comment": "string explanation" }}
5. "communication": {{ "rating": integer 1-100, "comment": "string explanation" }}
6. "production_experience": {{ "rating": integer 1-100, "comment": "string explanation" }}
7. "missing_concepts": [ "list", "of", "missing", "technical", "concepts" ]
8. "knowledge_gaps": [ "list", "of", "apparent", "knowledge", "gaps" ]

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
            return json.loads(response_text)
        except Exception as e:
            logger.error(f"Error during candidate response evaluation: {e}")
            return self._get_empty_evaluation(f"Evaluation failed: {e}")

    def _get_empty_evaluation(self, comment: str) -> dict:
        return {
            "technical_accuracy": {"rating": 0, "comment": comment},
            "confidence": {"rating": 0, "comment": ""},
            "completeness": {"rating": 0, "comment": ""},
            "practical_knowledge": {"rating": 0, "comment": ""},
            "communication": {"rating": 0, "comment": ""},
            "production_experience": {"rating": 0, "comment": ""},
            "missing_concepts": [],
            "knowledge_gaps": []
        }
