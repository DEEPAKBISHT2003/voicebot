import json
from openai import AsyncOpenAI
from loguru import logger
from typing import List, Dict, Any
from backend.app.core.config import Settings


class ConversationIntelligenceEngine:
    """
    Analyzes the live interview conversation against the JD and Resume to track:
    - Current topic being discussed
    - Skills covered vs remaining
    - Resume projects covered vs remaining
    - Conversation timeline
    - Overall interview progress
    """
    def __init__(
        self, 
        api_key: str = Settings.DEEPSEEK_API_KEY, 
        model: str = Settings.DEEPSEEK_MODEL,
        base_url: str = Settings.DEEPSEEK_BASE_URL
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def analyze(
        self,
        transcript: List[Dict[str, Any]],
        jd: str = "",
        resume: str = ""
    ) -> dict:
        """
        Sends the full conversation memory, JD, and resume to Groq LLM.
        Returns a structured intelligence state dict.
        """
        if not transcript:
            return self._get_empty_state()

        # Build a readable conversation log for the LLM (using last 20 messages max for prompt efficiency)
        recent_transcript = transcript[-20:] if len(transcript) > 20 else transcript
        conversation_text = "\n".join(
            f"[{msg.get('speaker', 'Unknown')}]: {msg.get('text', '')}"
            for msg in recent_transcript
        )

        prompt = f"""
You are an expert interview analyst. Analyze the following interview conversation in the context of the target job description and candidate resume.

Job Description:
{jd}

Candidate Resume:
{resume}

Conversation So Far:
{conversation_text}

Analyze the conversation and output a structured JSON object with EXACTLY these fields:

1. "current_topic": string - The topic currently being discussed (e.g. "System Design", "React Hooks", "Database Optimization"). Empty string if no clear topic.

2. "covered_skills": array of strings - Technical skills from the JD that have been discussed or demonstrated so far.

3. "remaining_skills": array of strings - Technical skills from the JD that have NOT been discussed yet.

4. "resume_projects_covered": array of strings - Projects or experiences from the resume that have been referenced or discussed.

5. "resume_projects_remaining": array of strings - Projects or experiences from the resume that have NOT been discussed yet.

6. "conversation_timeline": array of objects - Each object has {{"topic": "string", "summary": "one-line summary", "message_count": integer}} representing distinct conversation phases in order.

7. "interview_progress": object with {{"total_skills": integer, "covered_count": integer, "percentage": integer 0-100}} representing how much of the JD skills have been covered.

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
            logger.info(f"Intelligence analysis complete: {result.get('interview_progress', {})}")
            return result
        except Exception as e:
            logger.error(f"Error during conversation intelligence analysis: {e}")
            return self._get_empty_state()

    def _get_empty_state(self) -> dict:
        return {
            "current_topic": "",
            "covered_skills": [],
            "remaining_skills": [],
            "resume_projects_covered": [],
            "resume_projects_remaining": [],
            "conversation_timeline": [],
            "interview_progress": {
                "total_skills": 0,
                "covered_count": 0,
                "percentage": 0
            }
        }
