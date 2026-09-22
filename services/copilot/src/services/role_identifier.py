import json
import re
from typing import List, Dict, Any, Optional
from loguru import logger
from openai import AsyncOpenAI
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


class ConversationalRoleIdentifier:
    """
    Identifies the conversational roles of Teams participants ('candidate', 'interviewer', 'unknown')
    by analyzing the live interview conversation in combination with the candidate's Resume,
    Job Description (JD), and active participant names using the existing Copilot LLM.
    """

    CONFIDENCE_THRESHOLD = 0.80

    def __init__(
        self,
        api_key: str = Settings.DEEPSEEK_API_KEY,
        model: str = Settings.DEEPSEEK_MODEL,
        base_url: str = Settings.DEEPSEEK_BASE_URL,
        client: Optional[AsyncOpenAI] = None
    ):
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def identify_roles(
        self,
        transcript: List[Dict[str, Any]],
        participants: List[str],
        jd: str = "",
        resume: str = ""
    ) -> Dict[str, Any]:
        """
        Submits recent conversational turns, JD, resume, and participant list to the LLM.
        Returns a structured dictionary mapping exact participant names to their conversational roles and confidence:
        {
            "speakers": {
                "<name>": {"role": "candidate" | "interviewer" | "unknown", "confidence": float, "reasoning": str}
            }
        }
        """
        if not participants:
            return {"speakers": {}}

        # Default fallback dictionary where all participants are initially unknown
        fallback = {
            "speakers": {
                p: {"role": "unknown", "confidence": 0.0, "reasoning": "Insufficient conversational context"}
                for p in participants
            }
        }

        if not transcript:
            return fallback

        # Format up to the last 20 conversational messages for LLM context
        recent_transcript = transcript[-20:] if len(transcript) > 20 else transcript
        conversation_text = "\n".join(
            f"[{msg.get('speaker', 'Unknown')}]: {msg.get('text', '').strip()}"
            for msg in recent_transcript
            if msg.get("text", "").strip()
        )

        if not conversation_text.strip():
            return fallback

        system_prompt = """You are an expert conversation analyst for technical interviews.
Your task is to analyze the ongoing interview conversation in conjunction with the target Job Description and Candidate Resume to determine the conversational role of each Teams participant:
either "candidate", "interviewer", or "unknown".

CRITICAL RULES:
1. COMBINE RESUME CONTEXT WITH CONVERSATION:
   - Use the Candidate Resume to understand the candidate's target profile (skills, tools, projects, background).
   - Use the conversation to determine which Teams participant is actively demonstrating that knowledge and answering interview questions.
   - The Teams display name does NOT need to match the candidate name on the resume. For example, if the resume is for "Deepak Bisht" and the participant speaking candidate answers is "Rahul", classify "Rahul" as "candidate". Do NOT rename or prove real-world identity.
2. CONVERSATIONAL BEHAVIOR IS THE PRIMARY ROLE SIGNAL:
   - "interviewer": asks technical questions, introduces topics, follows up on answers, probes candidate experience, assesses responses.
   - "candidate": answers interview questions, describes personal experience, explains projects and architectures, discusses skills from the resume, explains technical decisions.
   - "unknown": participants who have only exchanged generic greetings (e.g. "Hello", "Hi"), observers, HR/coordinators who do not participate in technical questioning, or whose role cannot be determined from the conversation.
3. THREE+ PARTICIPANTS:
   - Do NOT force every non-candidate into "interviewer". If there are multiple non-candidates, classify observers or passive participants as "unknown".
4. INSUFFICIENT EVIDENCE:
   - For sparse or ambiguous exchanges (e.g. only "Hi", "Hello", "Can you hear me?"), assign role "unknown".
5. NEVER RENAME OR INVENT PARTICIPANTS:
   - You must output EXACTLY the participant display names provided in the Known Participants list.
   - Allowed roles are strictly: "candidate", "interviewer", "unknown"."""

        user_prompt = f"""Context:
- Target Job Description:
{jd or 'Not provided'}

- Candidate Resume:
{resume or 'Not provided'}

- Known Participants:
{json.dumps(participants, indent=2)}

- Recent Conversation:
{conversation_text}

Analyze the dialogue and return a structured JSON object with EXACTLY this schema:
{{
  "speakers": {{
    "<exact_participant_name>": {{
      "role": "candidate" | "interviewer" | "unknown",
      "confidence": <float between 0.0 and 1.0>,
      "reasoning": "<concise justification based on conversational actions>"
    }}
  }}
}}

You must output ONLY valid JSON matching this schema. Do not output markdown code blocks or additional text."""

        try:
            chat_completion = await self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=self.model,
                response_format={"type": "json_object"}
            )
            response_text = chat_completion.choices[0].message.content
            raw_result = clean_json_loads(response_text)

            # Validate and normalize structured output
            normalized_speakers: Dict[str, Dict[str, Any]] = {}
            speakers_dict = raw_result.get("speakers", {}) if isinstance(raw_result, dict) else {}

            for p in participants:
                # Check for direct or case-insensitive match in LLM response
                matched_val = speakers_dict.get(p)
                if not matched_val:
                    for k, v in speakers_dict.items():
                        if k.strip().lower() == p.strip().lower():
                            matched_val = v
                            break

                if isinstance(matched_val, dict):
                    role = str(matched_val.get("role", "unknown")).strip().lower()
                    try:
                        confidence = float(matched_val.get("confidence", 0.0))
                    except (ValueError, TypeError):
                        confidence = 0.0

                    reasoning = str(matched_val.get("reasoning", ""))

                    # Enforce allowed roles and confidence threshold
                    if role not in ("candidate", "interviewer", "unknown"):
                        role = "unknown"

                    # Any role with confidence below threshold falls back to unknown
                    if role != "unknown" and confidence < self.CONFIDENCE_THRESHOLD:
                        logger.info(
                            f"[RoleIdentifier] Participant '{p}' assigned '{role}' with low confidence "
                            f"({confidence:.2f} < {self.CONFIDENCE_THRESHOLD}) -> downgraded to 'unknown'."
                        )
                        role = "unknown"

                    normalized_speakers[p] = {
                        "role": role,
                        "confidence": confidence,
                        "reasoning": reasoning
                    }
                else:
                    normalized_speakers[p] = {
                        "role": "unknown",
                        "confidence": 0.0,
                        "reasoning": "Not classified by LLM"
                    }

            logger.info(f"[RoleIdentifier] Conversational role classification complete: {normalized_speakers}")
            return {"speakers": normalized_speakers}

        except Exception as e:
            logger.error(f"[RoleIdentifier] Error during conversational role identification: {e}")
            return fallback
