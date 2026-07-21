import json
from openai import AsyncOpenAI
from loguru import logger
from backend.app.core.config import Settings

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
