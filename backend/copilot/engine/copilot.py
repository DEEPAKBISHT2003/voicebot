import json
from groq import AsyncGroq
from loguru import logger
from typing import List, Dict, Any
from backend.app.core.config import Settings

class AICopilotEngine:
    """
    Generates real-time assistance tips and follow-up questions for the interviewer
    based on conversation logs, job description, resume, and response evaluations.
    """
    def __init__(self, api_key: str = Settings.GROQ_API_KEY, model: str = Settings.GROQ_MODEL):
        self.client = AsyncGroq(api_key=api_key)
        self.model = model

    async def generate_assistance(
        self,
        transcript: List[Dict[str, Any]],
        jd: str = "",
        resume: str = ""
    ) -> dict:
        """
        Invokes Groq LLM to generate structured recommendations and observations.
        """
        if not transcript:
            return self._get_empty_state()

        # Build conversation log showing speaker text and evaluations if available
        conversation_log = []
        for msg in transcript:
            speaker = msg.get("speaker", "Unknown")
            text = msg.get("text", "")
            eval_info = ""
            if "evaluation" in msg:
                ev = msg["evaluation"]
                eval_info = f" (Evaluation - Accuracy: {ev.get('technical_accuracy', {}).get('rating')}, Gaps: {ev.get('knowledge_gaps')})"
            conversation_log.append(f"[{speaker}]: {text}{eval_info}")

        conversation_text = "\n".join(conversation_log)

        prompt = f"""
You are an expert technical co-pilot. Your job is to assist the INTERVIEWER in real-time. You must NEVER speak to the candidate directly.

Input:
- Job Description:
{jd}

- Candidate Resume:
{resume}

- Conversation Log and Evaluations So Far:
{conversation_text}

Provide suggestions and structured guidance for the interviewer. Output a structured JSON object with EXACTLY the following fields:

1. "suggested_follow_up_questions": array of strings - 2-3 deep follow-up questions that drill down on the candidate's last response or technical claims.

2. "suggested_practical_questions": array of strings - Scenario-based, coding, or architecture design questions related to the current discussion.

3. "missing_concepts": array of strings - Important concepts or tools from the JD or candidate resume that have not been adequately covered or were missed in candidate's answers.

4. "verification_questions": array of strings - Questions to verify the authenticity of project experiences listed on the candidate's resume based on what they've discussed.

5. "recommended_next_topic": string - What topic the interviewer should guide the candidate to next (e.g. "Ask about performance optimization", "Move to DB design").

6. "interview_notes": array of strings - Bullets of key observations (e.g. "Candidate has strong SQL index knowledge but struggles with sharding").

7. "current_candidate_understanding": string - Summary of current technical capability level, strengths, and major concerns observed so far.

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
            result = json.loads(response_text)
            logger.info("Copilot assistant recommendations generated successfully.")
            return result
        except Exception as e:
            logger.error(f"Error generating copilot assistant recommendations: {e}")
            return self._get_empty_state()

    def _get_empty_state(self) -> dict:
        return {
            "suggested_follow_up_questions": [],
            "suggested_practical_questions": [],
            "missing_concepts": [],
            "verification_questions": [],
            "recommended_next_topic": "",
            "interview_notes": [],
            "current_candidate_understanding": ""
        }
