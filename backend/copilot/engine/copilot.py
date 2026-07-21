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


class AICopilotEngine:
    """
    Generates real-time assistance tips and follow-up questions for the interviewer
    based on conversation logs, job description, resume, and response evaluations.
    """
    def __init__(
        self, 
        api_key: str = Settings.DEEPSEEK_API_KEY, 
        model: str = Settings.DEEPSEEK_MODEL,
        base_url: str = Settings.DEEPSEEK_BASE_URL
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
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

        # Build conversation log showing speaker text and evaluations if available (last 20 messages for speed)
        recent_transcript = transcript[-20:] if len(transcript) > 20 else transcript
        conversation_log = []
        for msg in recent_transcript:
            speaker = msg.get("speaker", "Unknown")
            text = msg.get("text", "")
            eval_info = ""
            if "evaluation" in msg:
                ev = msg["evaluation"]
                eval_info = f" (Evaluation - Accuracy: {ev.get('technical_accuracy', {}).get('rating')}, Gaps: {ev.get('knowledge_gaps')})"
            conversation_log.append(f"[{speaker}]: {text}{eval_info}")

        conversation_text = "\n".join(conversation_log)

        # Decision engine layer: Determine classification of the latest candidate evaluation
        candidate_evals = [msg for msg in transcript if msg.get("speaker") == "Candidate" and "evaluation" in msg]
        decision = None
        rating = None
        if candidate_evals:
            latest_cand = candidate_evals[-1]
            rating = latest_cand.get("evaluation", {}).get("technical_accuracy", {}).get("rating")
            if rating is not None:
                if rating >= 80:
                    decision = "STRONG"
                elif rating >= 50:
                    decision = "PARTIAL"
                else:
                    decision = "WEAK"

        decision_prompt = ""
        if decision == "STRONG":
            decision_prompt = f"""
CRITICAL DECISION RULE (Strong Answer detected, rating: {rating}%):
The candidate provided a Strong Answer. Do NOT generate any follow-up questions in "suggested_follow_up_questions".
Instead, you must set "suggested_follow_up_questions" to exactly: ["Move to the next topic."].
"""
        elif decision == "PARTIAL":
            decision_prompt = f"""
CRITICAL DECISION RULE (Partial Answer detected, rating: {rating}%):
The candidate provided a Partial Answer. You must generate exactly 2-3 follow-up questions in "suggested_follow_up_questions" that drill down on their claims or missing aspects.
"""
        elif decision == "WEAK":
            decision_prompt = f"""
CRITICAL DECISION RULE (Weak Answer detected, rating: {rating}%):
The candidate provided a Weak Answer. You must generate probing questions in "suggested_follow_up_questions" to verify their basic understanding or uncover critical gaps.
"""

        prompt = f"""
You are an expert technical co-pilot. Your job is to assist the INTERVIEWER in real-time. You must NEVER speak to the candidate directly.

Input:
- Job Description:
{jd}

- Candidate Resume:
{resume}

- Conversation Log and Evaluations So Far:
{conversation_text}

{decision_prompt}

Provide suggestions and structured guidance for the interviewer. Output a structured JSON object with EXACTLY the following fields:

1. "suggested_follow_up_questions": array of strings - follow-up questions matching the critical decision rule.

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
            result = clean_json_loads(response_text)
            
            # Python post-processing enforcement of decision engine rules
            if decision == "STRONG":
                result["suggested_follow_up_questions"] = ["Move to the next topic."]
            elif decision == "PARTIAL":
                questions = result.get("suggested_follow_up_questions", [])
                if not isinstance(questions, list):
                    questions = []
                if len(questions) > 3:
                    result["suggested_follow_up_questions"] = questions[:3]
            
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
