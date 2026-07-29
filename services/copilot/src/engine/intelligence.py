import json
from openai import AsyncOpenAI
from loguru import logger
from typing import List, Dict, Any
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
2. "covered_skills": list of strings - Skills from JD or conversation that candidate has demonstrated.
3. "remaining_skills": list of strings - Skills from JD not yet covered or evaluated.
4. "resume_projects_covered": list of strings - Projects from candidate resume already discussed.
5. "resume_projects_remaining": list of strings - Key resume projects not yet explored.
6. "conversation_timeline": list of objects [{"topic": str, "timestamp": str}] tracking topic switches.
7. "interview_progress": object {{"total_skills": int, "covered_count": int, "percentage": int}} overall evaluation progress.

Respond ONLY with valid JSON.
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
            result = clean_json_loads(response_text)
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
