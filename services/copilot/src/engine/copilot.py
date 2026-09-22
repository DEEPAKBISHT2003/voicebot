import json
from openai import AsyncOpenAI
from loguru import logger
from typing import List, Dict, Any
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
        resume: str = "",
        custom_prompt: str = ""
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

        custom_instructions = ""
        if custom_prompt and custom_prompt.strip():
            custom_instructions = f"""
- CUSTOM INTERVIEW & COPILOT SYSTEM INSTRUCTIONS:
\"\"\"
{custom_prompt.strip()}
\"\"\"
Adhere strictly to the custom instructions above.
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

{custom_instructions}

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
            "initial_suggestions": [],
            "dynamic_suggestions": [],
            "scenario_questions": [],
            "suggested_follow_up_questions": [],
            "suggested_practical_questions": [],
            "missing_concepts": [],
            "verification_questions": [],
            "recommended_next_topic": "",
            "interview_notes": [],
            "current_candidate_understanding": ""
        }

    async def generate_initial_suggestions(
        self,
        jd: str = "",
        resume: str = ""
    ) -> List[str]:
        """
        Phase 2S: Generates exactly 2 initial interview question suggestions based on
        the Job Description and Candidate Resume before conversational dialogue begins.
        """
        prompt = f"""You are an expert technical interviewer assistant.
Based on the Job Description and Candidate Resume below, generate EXACTLY 2 initial interview question suggestions for the interviewer to start the interview.

Input:
- Job Description:
{jd.strip() if jd else "N/A"}

- Candidate Resume:
{resume.strip() if resume else "N/A"}

Rules:
1. Generate EXACTLY 2 useful interview questions.
2. Questions must be relevant to the Job Description.
3. Questions must be informed by the candidate's Resume (projects, background, technical skills).
4. Questions should help assess the candidate's technical depth and suitability.
5. Avoid duplicate questions.
6. Avoid generic filler (e.g. do NOT suggest "Can you hear me?" or "Tell me about yourself").
7. Do not answer the questions.
8. Output ONLY valid JSON matching this schema:
{{
  "initial_suggestions": [
    "...",
    "..."
  ]
}}
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
            raw_questions = result.get("initial_suggestions", [])
            if not isinstance(raw_questions, list):
                raw_questions = []

            # Filter valid non-empty string suggestions
            valid_questions = [
                str(q).strip() for q in raw_questions
                if q and isinstance(q, str) and str(q).strip()
            ]

            # Enforce EXACTLY 2 suggestions (if > 2, keep first 2)
            if len(valid_questions) > 2:
                valid_questions = valid_questions[:2]

            logger.info(f"[Phase2S] Generated {len(valid_questions)} initial suggestions: {valid_questions}")
            return valid_questions
        except Exception as e:
            logger.error(f"[Phase2S] Error generating initial suggestions: {e}")
            return []

    async def generate_dynamic_suggestions(
        self,
        jd: str = "",
        resume: str = "",
        interviewer_question: str = "",
        candidate_answer: str = ""
    ) -> List[str]:
        """
        Phase 2T: Generates exactly 2 dynamic interview question suggestions following
        a valid interviewer-question + candidate-answer pair:
        - Suggestion 1: Based on JD + Resume (What else should we explore from candidate's background that is relevant to the JD?).
        - Suggestion 2: Based on actual interviewer question + candidate answer (What is a useful follow-up drilling down into what the candidate just said?).
        """
        prompt = f"""You are an expert technical co-pilot assisting an interviewer in real-time.
An interviewer just asked a question, and the candidate gave an answer.
Generate EXACTLY 2 interview question suggestions for the interviewer's next step.

Input:
- Job Description:
{jd.strip() if jd else "N/A"}

- Candidate Resume:
{resume.strip() if resume else "N/A"}

- Preceding Interviewer Question:
{interviewer_question.strip() if interviewer_question else "N/A"}

- Candidate Answer:
{candidate_answer.strip() if candidate_answer else "N/A"}

Rules:
1. You must generate EXACTLY 2 questions in the "suggestions" array:
   - Suggestion 1 (JD + Resume based): A question exploring an important skill, project, or topic from the candidate's resume that is relevant to the Job Description, ensuring comprehensive coverage of the candidate's background.
   - Suggestion 2 (Conversation follow-up based): A targeted, deep follow-up question drilling down into the specific answer the candidate just gave, probing technical accuracy, implementation trade-offs, or potential gaps.
2. The questions must be tailored to the interviewer to ask. Do NOT answer the questions.
3. Do NOT suggest generic questions like "Tell me more" or "What else did you do?".
4. Return ONLY valid JSON matching this schema:
{{
  "suggestions": [
    "<Suggestion 1: JD + Resume based question>",
    "<Suggestion 2: Follow-up to candidate answer>"
  ]
}}
"""
        try:
            chat_completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            response_text = chat_completion.choices[0].message.content
            result = clean_json_loads(response_text)
            if isinstance(result, list):
                raw_questions = result
            elif isinstance(result, dict):
                raw_questions = result.get("suggestions") or result.get("dynamic_suggestions") or []
            else:
                raw_questions = []

            if not isinstance(raw_questions, list):
                raw_questions = []

            # Filter valid non-empty string suggestions
            valid_questions = [
                str(q).strip() for q in raw_questions
                if q and isinstance(q, str) and str(q).strip()
            ]

            # Enforce EXACTLY 2 suggestions
            if len(valid_questions) > 2:
                valid_questions = valid_questions[:2]
            elif len(valid_questions) < 1:
                valid_questions.append("Can you elaborate on your experience from your resume relevant to this position?")
                valid_questions.append("Could you dive deeper into the technical implementation and trade-offs of what you just described?")
            elif len(valid_questions) == 1:
                valid_questions.append("Could you dive deeper into the technical implementation and trade-offs of what you just described?")

            logger.info(f"[Phase2T] Generated {len(valid_questions)} dynamic suggestions: {valid_questions}")
            return valid_questions
        except Exception as e:
            logger.error(f"[Phase2T] Error generating dynamic suggestions: {e}")
            return [
                "Can you elaborate on your experience from your resume relevant to this position?",
                "Could you dive deeper into the technical implementation and trade-offs of what you just described?"
            ]

    async def generate_static_scenario_verification_questions(
        self,
        jd: str = "",
        resume: str = ""
    ) -> Dict[str, List[str]]:
        """
        Phase 2X: Generates EXACTLY 5 Scenario questions and EXACTLY 5 Verification questions
        based purely on the Job Description and Candidate Resume.
        
        DO NOT pass transcript, conversation history, or any live context.
        Validation:
        - scenario_questions: exactly 5 valid non-empty strings
        - verification_questions: exactly 5 valid non-empty strings
        - If fewer than 5 valid strings for either, fail generation (return empty sets).
        - If more than 5 valid strings, enforce first 5.
        """
        prompt = f"""You are an expert technical interviewer assistant.
Based on the Job Description and Candidate Resume below, generate EXACTLY 5 Scenario questions and EXACTLY 5 Verification questions for the interview.

Input:
- Job Description:
{jd.strip() if jd else "N/A"}

- Candidate Resume:
{resume.strip() if resume else "N/A"}

Rules:
1. Generate EXACTLY 5 Scenario questions:
   - Practical problem-solving, real-world troubleshooting, system architecture, or hands-on implementation challenges directly relevant to the JD and candidate's claimed skills.
2. Generate EXACTLY 5 Verification questions:
   - Targeted technical questions that verify the authenticity, depth, tools, and project experiences claimed on the candidate's resume against the requirements of the JD.
3. Every question must be a non-empty, actionable question for the interviewer to ask.
4. Do NOT answer the questions.
5. Do NOT include generic filler.
6. Output ONLY valid JSON matching this schema:
{{
  "scenario_questions": [
    "...",
    "...",
    "...",
    "...",
    "..."
  ],
  "verification_questions": [
    "...",
    "...",
    "...",
    "...",
    "..."
  ]
}}
"""
        try:
            chat_completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            response_text = chat_completion.choices[0].message.content
            result = clean_json_loads(response_text)
            if not isinstance(result, dict):
                logger.error("[Phase2X] LLM response is not a dict")
                return {"scenario_questions": [], "verification_questions": []}

            raw_scenarios = result.get("scenario_questions", [])
            raw_verifications = result.get("verification_questions", [])

            if not isinstance(raw_scenarios, list) or not isinstance(raw_verifications, list):
                logger.error("[Phase2X] scenario_questions or verification_questions is not a list")
                return {"scenario_questions": [], "verification_questions": []}

            valid_scenarios = [
                str(q).strip() for q in raw_scenarios
                if q and isinstance(q, str) and str(q).strip()
            ]
            valid_verifications = [
                str(q).strip() for q in raw_verifications
                if q and isinstance(q, str) and str(q).strip()
            ]

            # Validation: EXACTLY 5 required for both categories.
            # 0-4 or 6+ questions in either category must be rejected completely.
            if len(valid_scenarios) != 5 or len(valid_verifications) != 5:
                logger.error(
                    f"[Phase2X] Failed validation: got {len(valid_scenarios)} scenario and "
                    f"{len(valid_verifications)} verification questions. Exactly 5 required each."
                )
                return {"scenario_questions": [], "verification_questions": []}

            logger.info("[Phase2X] Successfully generated exactly 5 Scenario and 5 Verification questions")
            return {
                "scenario_questions": valid_scenarios,
                "verification_questions": valid_verifications
            }
        except Exception as e:
            logger.error(f"[Phase2X] Error generating static scenario & verification questions: {e}")
            return {"scenario_questions": [], "verification_questions": []}

